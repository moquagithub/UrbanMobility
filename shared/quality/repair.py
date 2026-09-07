"""
Actions de réparation automatique pour les issues qualité détectées par checks.py.

Chaque `repair_*` reçoit une QualityIssue et retourne (success: bool, detail: str).
Le dispatch se fait via repair_issue() qui sélectionne la bonne fonction selon issue_type.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

from shared.llm.router import AllProvidersExhausted
from shared.quality.checks import QualityIssue

log = logging.getLogger("quality.repair")


class RepairUnavailable(RuntimeError):
    """
    La réparation n'a pas pu être TENTÉE : aucun provider LLM n'a répondu
    (panne réseau, tous les providers en erreur…). À distinguer d'un échec de
    réparation — le correctif n'a jamais été évalué. L'appelant doit
    l'enregistrer en 'skipped' pour ne pas alimenter la soupape
    anti-acharnement (voir update_fix_result(skipped=True)).
    """
    pass


def _get_conn():
    from shared.db.connection import get_connection
    return get_connection()


def _fetch_sample_df_for_algo(algo_id: int, n_rows: int = 5000):
    """
    Récupère un échantillon du dataset réel associé à un algorithme (via son
    problème), pour valider un skeleton par exécution réelle avant de le committer.
    Retourne un DataFrame pandas ou None si indisponible.
    """
    import json as _json
    import pandas as pd

    conn = _get_conn()
    if not conn:
        return None
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute("""
                SELECT d.data_json, d.file_path
                FROM algorithms a
                JOIN problems p ON a.problem_id = p.id
                JOIN datasets d ON d.problem_id = p.id
                WHERE a.id = %s
                LIMIT 1
            """, (algo_id,))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return None

    dj = (row.get("data_json") or "")
    try:
        if dj and not dj.startswith("[MySQL table:"):
            data = _json.loads(dj)
            df = pd.DataFrame(data) if isinstance(data, list) else pd.DataFrame.from_dict(data)
        elif row.get("file_path"):
            conn2 = _get_conn()
            try:
                with conn2.cursor(dictionary=True) as cur:
                    cur.execute(f"SELECT * FROM `{row['file_path']}` LIMIT %s", (n_rows,))
                    df = pd.DataFrame(cur.fetchall())
            finally:
                conn2.close()
        else:
            return None
    except Exception:
        return None

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
    return df.head(n_rows) if len(df) > n_rows else df


def _dataset_context_for_algo(algo_id: int) -> str:
    """
    Description des colonnes RÉELLES du dataset de l'algorithme, à insérer dans les
    prompts de réparation.

    Sans ça, le prompt demandait au modèle de « reconstruire la colonne manquante à
    partir des colonnes disponibles » sans jamais lui dire lesquelles existent : il
    réparait à l'aveugle et devait deviner le schéma. Le prompt d'adaptation S2, lui,
    fournissait déjà cette information — l'écart expliquait des rejets répétés sur des
    erreurs de colonne ('vehicle_id', "None of [Index(['x','y'])]").

    Retourne une chaîne vide si le dataset est indisponible : le prompt reste alors
    exploitable, simplement sans contexte.
    """
    try:
        df = _fetch_sample_df_for_algo(algo_id, n_rows=200)
    except Exception:
        return ""
    if df is None or df.empty:
        return ""

    cols = ", ".join(f"{c} ({df[c].dtype})" for c in df.columns)
    try:
        sample = df.head(3).to_string(index=False, max_colwidth=18)
    except Exception:
        sample = ""
    return (
        "\n\nCOLONNES RÉELLEMENT PRÉSENTES dans le DataFrame `df` passé à run() :\n"
        f"{cols}\n"
        "N'utilise QUE ces colonnes. Si l'algorithme en réclame une qui n'y figure pas, "
        "reconstruis-la à partir de celles-ci (ex. une vitesse à partir de positions "
        "successives et des timestamps) — n'invente pas de nom de colonne.\n"
        f"Trois premières lignes :\n{sample}\n"
    )


def _validate_skeleton_execution(
    skeleton: str, algo_id: int, timeout_sec: Optional[int] = None, n_runs: int = 2,
) -> Tuple[bool, str]:
    """
    Wrapper réactif : récupère le dataset réel associé à l'algorithme en DB, puis
    délègue à shared.validation.skeleton_execution (partagé avec la validation
    proactive appelée par automatisation_1/steps/s4_datasets.py juste après la
    génération du dataset, où le df est déjà en mémoire).

    timeout_sec=None laisse le budget d'exécution au contrat partagé
    (EXECUTION_TIMEOUT_SEC). Ce paramètre valait 30 s en dur, ce qui réintroduisait
    localement le décalage validation/production tout juste corrigé : le défaut du
    contrat était bien passé à 300 s, mais ce wrapper l'écrasait à chaque appel de
    réparation.
    """
    from shared.validation.skeleton_execution import validate_skeleton_execution

    df = _fetch_sample_df_for_algo(algo_id)
    return validate_skeleton_execution(skeleton, df, timeout_sec=timeout_sec, n_runs=n_runs)


# Budget de tokens pour une réécriture de skeleton. Le défaut du routeur (2000)
# est insuffisant : le plus gros skeleton fait ~1900 tokens à lui seul, si bien que
# la réponse était coupée en plein milieu et revenait en « SyntaxError :
# unterminated string literal ». Le correctif était alors jugé mauvais et les
# 3 tentatives se consumaient sur une simple troncature (3 des 14 échecs du cycle
# du 2026-07-28 00:31). Doublé en cas de troncature détectée.
_SKELETON_MAX_TOKENS = 4000
_SKELETON_MAX_TOKENS_CEILING = 8000


def _llm_fix_skeleton_with_retry(
    algo_id: int, skeleton: str, build_prompt, max_attempts: int = 3,
) -> Tuple[bool, str]:
    """
    Boucle correctif LLM -> ast.parse -> validation par exécution réelle, en
    réinjectant l'erreur de la tentative précédente dans le prompt suivant.

    Sans ce feedback, un correctif rejeté repart de zéro au prochain cycle de
    scan avec le même prompt et peut réintroduire indéfiniment des bugs
    similaires (observé sur traces_gps_p11 : remplacer les imports natifs
    rtree/shapely/pyproj cassait une autre partie du code à chaque tentative,
    sans que le LLM ne sache jamais pourquoi — 25 issues forbidden_import
    bloquées à l'identique sur 12 cycles consécutifs faute de ce feedback).
    `build_prompt(current_skeleton, last_error)` construit le message à
    envoyer ; `last_error` est None à la première tentative.
    """
    from shared.llm.router import chat_with_meta
    import ast

    current_skeleton = skeleton
    llm_answered = False   # au moins un provider a renvoyé du contenu
    max_tokens = _SKELETON_MAX_TOKENS
    attempt = -1

    # Contexte constant sur toute la boucle : colonnes réelles du dataset.
    dataset_context = _dataset_context_for_algo(algo_id)

    # Historique CUMULÉ des rejets, pas seulement le dernier. Ne montrer que la
    # dernière erreur laissait le modèle osciller : il corrigeait A, cassait B,
    # puis en recorrigeant B réintroduisait A, sans jamais voir qu'il tournait en
    # rond. Les trois tentatives se consumaient sur deux erreurs alternées.
    errors_seen: List[str] = []
    infra_error: Optional[str] = None

    while attempt + 1 < max_attempts:
        attempt += 1
        if not errors_seen:
            feedback_arg = None
        elif len(errors_seen) == 1:
            feedback_arg = errors_seen[0]
        else:
            feedback_arg = (
                errors_seen[-1]
                + "\n\nTentatives précédentes déjà rejetées, ne les reproduis pas :\n"
                + "\n".join(f"  - {e}" for e in errors_seen[:-1])
                + "\nCorrige TOUS ces problèmes à la fois : n'en résous pas un en "
                  "réintroduisant un autre."
            )
        # Contrat d'environnement ET catalogue algolib sont ajoutés ici, en un seul
        # point, plutôt que dans chaque build_prompt : ils s'appliquent à TOUTE
        # réécriture de skeleton, quelle que soit l'issue d'origine.
        #
        # Le catalogue n'était affiché que dans le prompt forbidden_import, et les
        # deux réparations se défaisaient l'une l'autre : alg4/5/6/7 portent à la
        # fois un forbidden_import et une algo_execution_error. La première retirait
        # l'import interdit au profit d'algolib ; la seconde réécrivait ensuite le
        # skeleton SANS savoir qu'algolib existe, et réintroduisait GPy, networkx ou
        # pyproj. Les quatre tournaient ainsi en rond d'un cycle à l'autre.
        prompt = (_ENVIRONMENT_CONTRACT_TRAPS + "\n" + _FORBIDDEN_IMPORT_RECIPES + "\n"
                  + build_prompt(current_skeleton, feedback_arg) + dataset_context)
        messages = [{"role": "user", "content": prompt}]
        resp = chat_with_meta(messages, temperature=0.2, profile="code",
                              max_tokens=max_tokens)
        if not resp.get("success"):
            # Panne d'infrastructure : ne PAS l'ajouter à errors_seen, sinon on
            # renverrait au modèle « corrige ceci » à propos d'une indisponibilité
            # réseau dont son code n'est pas responsable.
            infra_error = f"aucun provider LLM disponible : {resp.get('error')}"
            continue
        llm_answered = True
        new_skeleton = (resp.get("content") or "").strip()
        new_skeleton = new_skeleton.replace("```python", "").replace("```", "").strip()

        if resp.get("truncated"):
            # Réponse coupée par max_tokens : ce n'est pas une erreur du modèle.
            # On élargit le budget et on redemande, au lieu de lui renvoyer une
            # « SyntaxError » qu'il ne peut pas corriger puisqu'elle vient de nous.
            if max_tokens < _SKELETON_MAX_TOKENS_CEILING:
                max_tokens = min(max_tokens * 2, _SKELETON_MAX_TOKENS_CEILING)
                # La troncature vient de notre budget, pas du modèle : ce tour ne
                # doit pas consommer une des tentatives de correction, ni entrer
                # dans l'historique des rejets renvoyé au modèle.
                attempt -= 1
                log.info("[REPAIR] réponse tronquée — budget porté à %d tokens", max_tokens)
            else:
                errors_seen.append("réponse tronquée malgré un budget de "
                                   f"{max_tokens} tokens ; réponds plus court")
            continue

        if not new_skeleton or len(new_skeleton) < 30:
            errors_seen.append("LLM n'a pas retourné de skeleton valide")
            continue

        try:
            ast.parse(new_skeleton)
        except SyntaxError as e:
            errors_seen.append(f"SyntaxError : {e}")
            current_skeleton = new_skeleton
            continue

        valid, valid_detail = _validate_skeleton_execution(new_skeleton, algo_id)
        if valid:
            conn = _get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("UPDATE algorithms SET python_skeleton=%s WHERE id=%s",
                                (new_skeleton, algo_id))
                conn.commit()
            finally:
                conn.close()
            tries = f", tentative {attempt + 1}/{max_attempts}" if attempt else ""
            return True, f"Corrigé et validé ({valid_detail}{tries})"

        errors_seen.append(valid_detail)
        current_skeleton = new_skeleton

    if not llm_answered:
        # Aucune tentative n'a produit de correctif à évaluer : c'est une panne
        # d'infrastructure, pas un échec de réparation.
        raise RepairUnavailable(infra_error or "aucun provider LLM disponible")

    last_error = errors_seen[-1] if errors_seen else "raison inconnue"
    return False, f"correctif LLM rejeté après {max_attempts} tentatives : {last_error}"


# ── Réparation skeletons via LLM ─────────────────────────────────────────────

# Recettes de remplacement pour les bibliothèques interdites les plus fréquentes,
# établies en relevant (2026-07-21) les imports réels des 25 algorithmes traces_gps
# actuellement bloqués par check_algorithms_forbidden_imports. Un prompt générique
# ("remplace par une bibliothèque autorisée") laisse le LLM réinventer ces
# équivalences à chaque tentative, souvent en échouant sur les cas spatiaux/graphes
# (voir forbidden_import_repair_stuck_loop_fixed_2026-07-21) — lui donner la recette
# directement augmente les chances de succès dès les 3 tentatives disponibles.
_FORBIDDEN_IMPORT_RECIPES = """
IMPORTANT — le projet fournit déjà ces primitives, testées, dans `shared.algolib`.
NE LES RÉIMPLÉMENTE PAS : remplace simplement l'import interdit par l'appel correspondant.
`shared.algolib` est autorisé et importable depuis les notebooks.

  from shared.algolib import (haversine_km, nearest_neighbors, dtw_distance, dtw_path,
                              kalman_smooth, cwt, mst_edges, shortest_paths,
                              connected_components, gp_fit_predict, change_points)

Correspondances :
- tslearn / fastdtw          → `dtw_distance(a, b, radius=30)`, `dtw_path(a, b, radius=30)`
                               (radius borne la déformation : indispensable au-delà de
                               ~1000 points, sinon le calcul dépasse le temps imparti)
- pykalman / filterpy        → `kalman_smooth(z, process_var, measurement_var)`
                               retourne (position_lissée, vitesse) ; gère les NaN
- pywt                       → `cwt(x, scales)` → tableau (len(scales), len(x))
                               (`scipy.signal.cwt` a été RETIRÉ de SciPy, ne l'utilise pas)
- networkx / osmnx           → `mst_edges(adj)`, `shortest_paths(adj)`,
                               `connected_components(adj)` à partir d'une matrice d'adjacence
- GPy                        → `gp_fit_predict(X, y, X_new)` → (moyenne, écart-type)
- geopy / shapely / pyproj / rtree
                             → `haversine_km(lat1, lon1, lat2, lon2)` (vectorisé) et
                               `nearest_neighbors(lat, lon, k)` → (distances_km, indices)
- ruptures                   → `change_points(x, window, threshold)` → indices des ruptures

Cas sans équivalent dans algolib :
- numba : retire simplement les décorateurs @jit/@njit ; le numpy vectorisé sous-jacent
  suffit sans compilation JIT.
- quantile_forest : `sklearn.ensemble.GradientBoostingRegressor(loss="quantile")` fait de
  la régression quantile nativement.
- geopandas : travaille directement sur les colonnes lat/lon du DataFrame avec haversine_km.
"""


from shared.validation.skeleton_contract import ENVIRONMENT_CONTRACT as _ENVIRONMENT_CONTRACT_TRAPS


def repair_skeleton_syntax(issue: QualityIssue) -> Tuple[bool, str]:
    """Répare une SyntaxError dans le skeleton en demandant au LLM de corriger."""
    try:
        from shared.llm.router import chat_with_meta

        conn = _get_conn()
        if not conn:
            return False, "Connexion MySQL indisponible"

        algo_id = issue.entity_id
        if not algo_id:
            return False, "entity_id manquant"

        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("""
                    SELECT a.id, a.name, a.algorithm_key, a.python_skeleton,
                           p.title AS problem_title, dt.name AS type_name
                    FROM algorithms a
                    JOIN problems p ON a.problem_id = p.id
                    JOIN data_types dt ON p.data_type_id = dt.id
                    WHERE a.id = %s
                """, (algo_id,))
                row = cur.fetchone()
        finally:
            conn.close()

        if not row:
            return False, f"algo_id={algo_id} introuvable"

        skel = row["python_skeleton"] or ""
        error_desc = issue.fix_hint or issue.description
        messages = [{"role": "user", "content": (
            _ENVIRONMENT_CONTRACT_TRAPS + "\n"
            f"Voici un skeleton Python pour l'algorithme '{row['name']}' "
            f"(problème : {row['problem_title']}) :\n\n"
            f"```python\n{skel}\n```\n\n"
            f"Erreur de syntaxe détectée : {error_desc}\n\n"
            "Corrige la SyntaxError. Bibliothèques autorisées : numpy, pandas, scipy, "
            "sklearn, statsmodels, matplotlib. Assure-toi que `df['is_anomaly']` est assigné "
            "(valeurs 0/1). Retourne UNIQUEMENT le code Python corrigé, sans balise markdown."
        )}]
        resp = chat_with_meta(messages, temperature=0.2, profile="code")
        new_skeleton = (resp.get("content") or "").strip()
        new_skeleton = new_skeleton.replace("```python", "").replace("```", "").strip()

        if not new_skeleton or len(new_skeleton) < 30:
            return False, "LLM n'a pas retourné de skeleton valide"

        # Valider syntaxe
        import ast
        try:
            ast.parse(new_skeleton)
        except SyntaxError as e:
            return False, f"LLM a retourné un skeleton toujours invalide : {e}"

        valid, valid_detail = _validate_skeleton_execution(new_skeleton, algo_id)
        if not valid:
            return False, f"correctif LLM rejeté après validation : {valid_detail}"

        conn2 = _get_conn()
        try:
            with conn2.cursor() as cur:
                cur.execute("UPDATE algorithms SET python_skeleton=%s WHERE id=%s",
                            (new_skeleton, algo_id))
            conn2.commit()
        finally:
            conn2.close()

        return True, f"Skeleton corrigé et validé ({valid_detail})"

    except (AllProvidersExhausted, RepairUnavailable):
        # Indisponibilité LLM : laisser remonter jusqu'à repair_issue(), qui
        # l'enregistre en 'skipped'. La capturer ici la transformait en échec
        # de réparation et alimentait à tort la soupape anti-acharnement.
        raise
    except Exception as exc:
        return False, f"Erreur repair_skeleton_syntax : {exc}"


def repair_missing_skeleton(issue: QualityIssue) -> Tuple[bool, str]:
    """Régénère un skeleton manquant via LLM (prompt Auto1 S3)."""
    try:
        from automatisation_1.prompts.algorithms_prompt import build_skeleton_messages
        from shared.llm.router import chat_with_meta

        algo_id = issue.entity_id
        if not algo_id:
            return False, "entity_id manquant"

        conn = _get_conn()
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("""
                    SELECT a.id, a.name, a.description, a.algorithm_key,
                           a.category, a.complexity_level,
                           p.title AS problem_title, p.description AS prob_desc,
                           dt.id AS type_id, dt.name AS type_name
                    FROM algorithms a
                    JOIN problems p ON a.problem_id = p.id
                    JOIN data_types dt ON p.data_type_id = dt.id
                    WHERE a.id = %s
                """, (algo_id,))
                row = cur.fetchone()
        finally:
            conn.close()

        if not row:
            return False, f"algo_id={algo_id} introuvable"

        messages = build_skeleton_messages(
            algo_name=row["name"],
            algo_description=row["description"] or "",
            problem_title=row["problem_title"],
            problem_description=row["prob_desc"] or "",
            data_type=row["type_name"],
            category=row["category"] or "",
        )
        resp = chat_with_meta(messages, temperature=0.3, profile="code")
        new_skeleton = (resp.get("content") or "").strip()

        if not new_skeleton or len(new_skeleton) < 30:
            return False, "LLM n'a pas retourné de skeleton"

        import ast
        try:
            ast.parse(new_skeleton)
        except SyntaxError as e:
            return False, f"Skeleton généré invalide : {e}"

        valid, valid_detail = _validate_skeleton_execution(new_skeleton, algo_id)
        if not valid:
            return False, f"correctif LLM rejeté après validation : {valid_detail}"

        conn2 = _get_conn()
        try:
            with conn2.cursor() as cur:
                cur.execute("UPDATE algorithms SET python_skeleton=%s WHERE id=%s",
                            (new_skeleton, algo_id))
            conn2.commit()
        finally:
            conn2.close()

        return True, f"Skeleton généré et validé ({valid_detail})"

    except (AllProvidersExhausted, RepairUnavailable):
        # Indisponibilité LLM : laisser remonter jusqu'à repair_issue(), qui
        # l'enregistre en 'skipped'. La capturer ici la transformait en échec
        # de réparation et alimentait à tort la soupape anti-acharnement.
        raise
    except Exception as exc:
        return False, f"Erreur repair_missing_skeleton : {exc}"


def repair_missing_is_anomaly(issue: QualityIssue) -> Tuple[bool, str]:
    """Demande au LLM d'ajouter l'assignation df['is_anomaly'] manquante."""
    try:
        from shared.llm.router import chat_with_meta

        algo_id = issue.entity_id
        if not algo_id:
            return False, "entity_id manquant"

        conn = _get_conn()
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("SELECT id, name, python_skeleton FROM algorithms WHERE id=%s", (algo_id,))
                row = cur.fetchone()
        finally:
            conn.close()

        if not row or not row["python_skeleton"]:
            return False, "Skeleton introuvable"

        messages = [
            {"role": "user", "content": (
                f"Voici un skeleton Python pour l'algorithme '{row['name']}' :\n\n"
                f"```python\n{row['python_skeleton']}\n```\n\n"
                "Ce skeleton ne définit pas `df['is_anomaly']`. "
                "Modifie-le pour qu'il assigne une colonne `df['is_anomaly']` (valeurs 0/1 booléenne) "
                "en cohérence avec la logique de l'algorithme. "
                "Retourne UNIQUEMENT le code Python corrigé, sans balise markdown."
            )}
        ]
        resp = chat_with_meta(messages, temperature=0.2, profile="code")
        new_skeleton = (resp.get("content") or "").strip()
        new_skeleton = new_skeleton.replace("```python", "").replace("```", "").strip()

        if "is_anomaly" not in new_skeleton:
            return False, "LLM n'a pas ajouté is_anomaly"

        import ast
        try:
            ast.parse(new_skeleton)
        except SyntaxError as e:
            return False, f"Skeleton corrigé invalide : {e}"

        valid, valid_detail = _validate_skeleton_execution(new_skeleton, algo_id)
        if not valid:
            return False, f"correctif LLM rejeté après validation : {valid_detail}"

        conn2 = _get_conn()
        try:
            with conn2.cursor() as cur:
                cur.execute("UPDATE algorithms SET python_skeleton=%s WHERE id=%s",
                            (new_skeleton, algo_id))
            conn2.commit()
        finally:
            conn2.close()

        return True, f"df['is_anomaly'] ajouté et validé ({valid_detail})"

    except (AllProvidersExhausted, RepairUnavailable):
        # Indisponibilité LLM : laisser remonter jusqu'à repair_issue(), qui
        # l'enregistre en 'skipped'. La capturer ici la transformait en échec
        # de réparation et alimentait à tort la soupape anti-acharnement.
        raise
    except Exception as exc:
        return False, f"Erreur repair_missing_is_anomaly : {exc}"


# ── Réparation notebooks ──────────────────────────────────────────────────────

def _find_kernel_killer_algorithms(notebook_id: int) -> Tuple[list, str]:
    """
    Identifie le ou les algorithmes d'un notebook dont le code tue le kernel.

    Exécute chaque skeleton dans le sous-processus isolé du harnais de validation :
    un crash natif y tue le sous-processus (code de retour négatif) au lieu du
    processus appelant, ce qui permet de désigner le coupable au lieu de le deviner.

    Retourne (liste de dicts {id, key, name, detail}, message de diagnostic).
    """
    from shared.validation.skeleton_execution import run_skeleton_once

    conn = _get_conn()
    if not conn:
        return [], "Connexion MySQL indisponible"
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute("""
                SELECT a.id, a.algorithm_key, a.name, a.python_skeleton
                FROM algorithms a
                JOIN problems p ON a.problem_id = p.id
                JOIN notebooks n ON n.problem_id = p.id
                WHERE n.id = %s AND a.python_skeleton IS NOT NULL
            """, (notebook_id,))
            algos = cur.fetchall()
    finally:
        conn.close()

    if not algos:
        return [], "aucun algorithme rattaché à ce notebook"

    killers = []
    for a in algos:
        df = _fetch_sample_df_for_algo(a["id"])
        if df is None or df.empty:
            continue
        ok, detail = run_skeleton_once(a["python_skeleton"], df)
        if not ok and detail.startswith("CRASH NATIF"):
            killers.append({"id": a["id"], "key": a["algorithm_key"],
                            "name": a["name"], "detail": detail})
    return killers, f"{len(algos)} algorithme(s) testé(s), {len(killers)} tueur(s) de kernel"


def repair_failed_notebook(issue: QualityIssue) -> Tuple[bool, str]:
    """
    Remet le notebook en status='generated' pour qu'il soit relancé par S3.

    Sauf en cas de mort du kernel : remettre un statut ne peut PAS corriger un
    crash natif, puisque la cause est dans le code d'un algorithme et que la
    ré-exécution reproduit le crash à l'identique. Cette réparation retournait
    pourtant True sans rien vérifier — sur traces_gps_p11_comparison, cela a donné
    129 détections et 99 « réparations réussies » entre le 2026-07-09 et le
    2026-07-28. Dix-neuf jours de boucle, du budget consommé à chaque cycle, et
    jamais garée : la soupape ne compte que les échecs, et ceux-ci étaient
    comptabilisés en succès.

    On identifie donc d'abord l'algorithme fautif et on le répare ; le notebook
    n'est relancé qu'ensuite, une fois la cause traitée.
    """
    try:
        nb_id = issue.entity_id
        if not nb_id:
            return False, "entity_id manquant"

        if "deadkernel" in (issue.description or "").lower().replace(" ", ""):
            killers, diag = _find_kernel_killer_algorithms(nb_id)
            if not killers:
                # Le crash ne se reproduit pas hors notebook : il peut être dû à
                # l'accumulation mémoire de plusieurs algorithmes dans le même
                # kernel. Relancer a alors un sens.
                log.info("[REPAIR] notebook %s — kernel mort mais aucun algorithme ne "
                         "crashe isolément (%s) ; relance", nb_id, diag)
            else:
                names = ", ".join(k["key"] for k in killers)
                log.warning("[REPAIR] notebook %s — kernel tué par : %s (%s)",
                            nb_id, names, diag)
                repaired = []
                for k in killers:
                    fake = QualityIssue(
                        entity_type="algorithm", entity_id=k["id"], entity_key=k["key"],
                        data_type_id=issue.data_type_id, issue_type="algo_execution_error",
                        severity="high",
                        description=f"Algorithme '{k['name']}' tue le kernel : {k['detail']}",
                        auto_fixable=True, fix_hint=k["detail"],
                    )
                    ok, detail = repair_algo_execution_error(fake)
                    if ok:
                        repaired.append(k["key"])
                if not repaired:
                    return False, (f"kernel tué par {names} — réparation de "
                                   "l'algorithme fautif échouée, relance inutile")
                log.info("[REPAIR] notebook %s — algorithme(s) réparé(s) : %s",
                         nb_id, ", ".join(repaired))

        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE notebooks SET status='generated', error_message=NULL WHERE id=%s",
                    (nb_id,)
                )
            conn.commit()
        finally:
            conn.close()
        return True, f"Notebook {nb_id} remis en status='generated'"
    except (AllProvidersExhausted, RepairUnavailable):
        # Indisponibilité LLM : laisser remonter jusqu'à repair_issue(), qui
        # l'enregistre en 'skipped'. La capturer ici la transformait en échec
        # de réparation et alimentait à tort la soupape anti-acharnement.
        raise
    except Exception as exc:
        return False, f"Erreur repair_failed_notebook : {exc}"


def repair_stale_notebook(issue: QualityIssue) -> Tuple[bool, str]:
    """Remet le notebook en status='generated' pour forcer la reconstruction S2+S3.

    'pending' n'est pas dans l'enum MySQL — on utilise 'generated' (valeur valide
    que S3 lit pour décider quoi exécuter). S2 utilise force=True donc n'a pas
    besoin d'un statut particulier pour reconstruire.
    """
    try:
        nb_id = issue.entity_id
        if not nb_id:
            return False, "entity_id manquant"

        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE notebooks SET status='generated', minio_key=NULL WHERE id=%s",
                    (nb_id,)
                )
            conn.commit()
        finally:
            conn.close()
        return True, f"Notebook {nb_id} marqué generated pour reconstruction S2+S3"
    except (AllProvidersExhausted, RepairUnavailable):
        # Indisponibilité LLM : laisser remonter jusqu'à repair_issue(), qui
        # l'enregistre en 'skipped'. La capturer ici la transformait en échec
        # de réparation et alimentait à tort la soupape anti-acharnement.
        raise
    except Exception as exc:
        return False, f"Erreur repair_stale_notebook : {exc}"


def repair_notebook_minio_missing(issue: QualityIssue) -> Tuple[bool, str]:
    """Efface le minio_key du notebook et remet en 'generated' pour forcer la reconstruction via S2."""
    try:
        nb_id = issue.entity_id
        if not nb_id:
            return False, "entity_id manquant"

        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE notebooks SET minio_key=NULL, status='generated' WHERE id=%s",
                    (nb_id,)
                )
            conn.commit()
        finally:
            conn.close()
        return True, f"minio_key effacé pour notebook {nb_id}"
    except (AllProvidersExhausted, RepairUnavailable):
        # Indisponibilité LLM : laisser remonter jusqu'à repair_issue(), qui
        # l'enregistre en 'skipped'. La capturer ici la transformait en échec
        # de réparation et alimentait à tort la soupape anti-acharnement.
        raise
    except Exception as exc:
        return False, f"Erreur repair_notebook_minio_missing : {exc}"


def repair_dataset_minio_missing(issue: QualityIssue) -> Tuple[bool, str]:
    """Efface le minio_key du dataset pour forcer le re-upload via S1."""
    try:
        ds_id = issue.entity_id
        if not ds_id:
            return False, "entity_id manquant"

        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE datasets SET minio_key=NULL WHERE id=%s",
                    (ds_id,)
                )
            conn.commit()
        finally:
            conn.close()
        return True, f"minio_key effacé pour dataset {ds_id}"
    except (AllProvidersExhausted, RepairUnavailable):
        # Indisponibilité LLM : laisser remonter jusqu'à repair_issue(), qui
        # l'enregistre en 'skipped'. La capturer ici la transformait en échec
        # de réparation et alimentait à tort la soupape anti-acharnement.
        raise
    except Exception as exc:
        return False, f"Erreur repair_dataset_minio_missing : {exc}"


def repair_zero_figures(issue: QualityIssue) -> Tuple[bool, str]:
    """
    Force la reconstruction complète du notebook (S2+S3) pour régénérer les figures.

    Si MinIO n'a pas de figures pour ce notebook, les données sources sont incorrectes
    (mauvais skeleton, colonnes manquantes, exécution silencieuse). On remet le notebook
    en 'pending' avec minio_key=NULL pour que Auto2 S2 le reconstruise depuis les skeletons
    corrigés, puis S3 l'exécute → nouvelles figures dans MinIO → Auto3 recompile le PDF.
    """
    try:
        nb_id = issue.entity_id
        if not nb_id:
            return False, "entity_id manquant"

        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE notebooks
                       SET status='generated', minio_key=NULL, executed_minio_key=NULL,
                           executed_path=NULL, error_message=NULL
                       WHERE id=%s""",
                    (nb_id,)
                )
            conn.commit()
        finally:
            conn.close()
        return True, f"Notebook {nb_id} forcé en reconstruction complète S2+S3 (figures absentes de MinIO)"

    except (AllProvidersExhausted, RepairUnavailable):
        # Indisponibilité LLM : laisser remonter jusqu'à repair_issue(), qui
        # l'enregistre en 'skipped'. La capturer ici la transformait en échec
        # de réparation et alimentait à tort la soupape anti-acharnement.
        raise
    except Exception as exc:
        return False, f"Erreur repair_zero_figures : {exc}"


def repair_report_compile_failed(issue: QualityIssue) -> Tuple[bool, str]:
    """
    Signale un rapport en échec de compilation LaTeX → re-déclenche Auto3.

    Rien à corriger en base ici : la réparation réelle (déterministe pour les
    commandes LaTeX non définies, LLM en repli) vit dans
    automatisation_3/latex_compiler.py et s'exécute pendant la recompilation
    elle-même. Si le rapport échoue encore après le re-run, il réapparaîtra
    au prochain scan comme n'importe quelle autre issue non résolue.
    """
    report_id = issue.entity_id
    if not report_id:
        return False, "entity_id manquant"
    return True, f"Auto3 va recompiler le rapport report_id={report_id} (nouvelle tentative)"


def repair_dataset_regen(issue: QualityIssue) -> Tuple[bool, str]:
    """
    Signale un dataset absent/dégénéré (no_data, no_dataset, dataset_no_anomalies)
    → déclenche Auto1 S4 (génération dataset) puis Auto2/Auto3 en cascade via
    _trigger_pipelines_after_repair. Rien à modifier en base ici.
    """
    if not issue.entity_id:
        return False, "entity_id manquant"
    return True, f"Auto1 S4 va régénérer les données ({issue.issue_type}, id={issue.entity_id})"


def repair_dataset_orphaned(issue: QualityIssue) -> Tuple[bool, str]:
    """
    Supprime une ligne `datasets` orpheline (problem_id NULL) — reliquat de
    l'ancien pipeline monolithique retiré le 2026-07-09.

    Refuse de supprimer si la ligne a été rerattachée à un problème entre le scan
    et la réparation : le scan peut dater de plusieurs minutes et Auto1 S4 tourne
    en parallèle. La condition `problem_id IS NULL` est donc revérifiée dans le
    DELETE lui-même plutôt qu'en amont, pour éviter toute fenêtre de course.
    """
    if not issue.entity_id:
        return False, "entity_id manquant"

    conn = _get_conn()
    if not conn:
        return False, "Connexion MySQL indisponible"
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM datasets WHERE id=%s AND problem_id IS NULL",
                        (issue.entity_id,))
            deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()

    if not deleted:
        return True, f"Dataset #{issue.entity_id} rerattaché entre-temps — suppression annulée"
    return True, f"Dataset orphelin #{issue.entity_id} ('{issue.entity_key}') supprimé"


def repair_duplicate_watch(issue: QualityIssue) -> Tuple[bool, str]:
    """
    Tue les processus `shared.quality --watch` en double. Garde le processus
    courant si c'est lui-même un watch, sinon le plus ancien (PID le plus bas).
    """
    import os
    import signal

    from shared.quality.checks import _find_watch_pids

    pids = _find_watch_pids()
    if len(pids) <= 1:
        return True, "Plus de doublon (déjà résolu)"

    me = os.getpid()
    keep = me if me in pids else min(pids)
    killed, errors = [], []
    for pid in pids:
        if pid == keep:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except OSError as exc:
            errors.append(f"{pid}: {exc}")

    if errors:
        return False, f"Tués: {killed} — échecs: {errors}"
    return True, f"Processus watch en double tués: {killed} (conservé: {keep})"


def repair_no_algorithms(issue: QualityIssue) -> Tuple[bool, str]:
    """
    Signale que ce problème n'a pas d'algorithmes → déclenche Auto1.

    Rien à corriger dans la DB : c'est Auto1 (LLM) qui génère les algorithmes et
    leurs skeletons. On retourne True pour que _trigger_pipelines_after_repair
    lance Auto1 complet pour ce data_type → Auto2 → Auto3.
    """
    prob_id = issue.entity_id
    if not prob_id:
        return False, "entity_id manquant"
    return True, f"Auto1 va générer les algorithmes pour problem_id={prob_id}"


def repair_algo_execution_error(issue: QualityIssue) -> Tuple[bool, str]:
    """
    Corrige un skeleton qui échoue à l'exécution, en utilisant le message d'erreur
    RÉEL capturé (issue.fix_hint) plutôt qu'une simple relance sans changement de code.

    Contrairement à repair_failed_notebook (qui ne fait que remettre le notebook en
    'generated' pour relance), ceci répare le skeleton lui-même — sinon la même erreur
    se reproduirait indéfiniment à chaque scan.
    """
    try:
        algo_id = issue.entity_id
        if not algo_id:
            return False, "entity_id manquant"

        conn = _get_conn()
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("""
                    SELECT a.id, a.name, a.python_skeleton,
                           p.title AS problem_title, dt.name AS type_name
                    FROM algorithms a
                    JOIN problems p ON a.problem_id = p.id
                    JOIN data_types dt ON p.data_type_id = dt.id
                    WHERE a.id = %s
                """, (algo_id,))
                row = cur.fetchone()
        finally:
            conn.close()

        if not row or not row["python_skeleton"]:
            return False, "Skeleton introuvable"

        error_msg = issue.fix_hint or issue.description

        def build_prompt(current_skeleton: str, last_error: Optional[str]) -> str:
            feedback = (
                f"\n\nLa tentative précédente a été rejetée : {last_error}\n"
                "Corrige spécifiquement ce problème sans en réintroduire d'autres.\n"
                if last_error else ""
            )
            return (
                f"Ce skeleton Python pour l'algorithme '{row['name']}' "
                f"(problème : {row['problem_title']}, type de données : {row['type_name']}) "
                f"échoue à l'exécution réelle avec l'erreur suivante :\n\n{error_msg}\n\n"
                f"Voici le code actuel :\n\n```python\n{current_skeleton}\n```\n\n"
                "Corrige le code pour que cette erreur précise ne se reproduise plus, en "
                "conservant l'intention algorithmique d'origine. Bibliothèques autorisées : "
                "numpy, pandas, scipy, sklearn, statsmodels, matplotlib (+ celles déjà "
                "importées si l'erreur ne vient pas d'un import interdit).\n"
                "Si l'erreur vient d'une colonne qui n'existe probablement pas dans les "
                "données réelles (ex: une colonne de référence/timestamp attendue mais "
                "absente du schéma), reconstruis-la raisonnablement à partir des colonnes "
                "disponibles (ex: un timestamp de référence régulier basé sur l'intervalle "
                "d'échantillonnage médian) plutôt que de supposer qu'elle existe.\n"
                "Si l'erreur vient d'un timeout ou d'une boucle coûteuse (ex: un fit de "
                "modèle refait à chaque pas de temps, ou une double boucle sur toutes les "
                "paires de lignes), vectorise le calcul avec numpy/pandas.\n"
                "Assure-toi que `df['is_anomaly']` est bien assigné (valeurs 0/1) avec un "
                "taux de détection réaliste (ni 0%, ni la quasi-totalité des lignes) — "
                "utilise un seuil basé sur un percentile ou un z-score si le seuil d'origine "
                "n'est pas calibré à l'échelle réelle des données."
                f"{feedback}"
                "Retourne UNIQUEMENT le code Python corrigé, sans balise markdown."
            )

        ok, detail = _llm_fix_skeleton_with_retry(algo_id, row["python_skeleton"], build_prompt)
        return ok, (f"{detail} suite à : {error_msg[:80]}" if ok else detail)

    except (AllProvidersExhausted, RepairUnavailable):
        # Indisponibilité LLM : laisser remonter jusqu'à repair_issue(), qui
        # l'enregistre en 'skipped'. La capturer ici la transformait en échec
        # de réparation et alimentait à tort la soupape anti-acharnement.
        raise
    except Exception as exc:
        return False, f"Erreur repair_algo_execution_error : {exc}"


def repair_forbidden_import(issue: QualityIssue) -> Tuple[bool, str]:
    """Demande au LLM de remplacer les imports interdits par des alternatives disponibles."""
    try:
        algo_id = issue.entity_id
        if not algo_id:
            return False, "entity_id manquant"

        conn = _get_conn()
        try:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("SELECT id, name, python_skeleton FROM algorithms WHERE id=%s", (algo_id,))
                row = cur.fetchone()
        finally:
            conn.close()

        if not row or not row["python_skeleton"]:
            return False, "Skeleton introuvable"

        def build_prompt(current_skeleton: str, last_error: Optional[str]) -> str:
            feedback = (
                f"\n\nLa tentative précédente a été rejetée : {last_error}\n"
                "Corrige spécifiquement ce problème sans en réintroduire d'autres.\n"
                if last_error else ""
            )
            return (
                f"Voici un skeleton Python pour l'algorithme '{row['name']}' :\n\n"
                f"```python\n{current_skeleton}\n```\n\n"
                f"Problème : {issue.description}\n\n"
                "Bibliothèques autorisées UNIQUEMENT : numpy, pandas, scipy, sklearn, statsmodels, matplotlib.\n"
                "Remplace tous les imports interdits par des équivalents issus de ces bibliothèques. "
                "Conserve exactement la même logique algorithmique et assure-toi que "
                "`df['is_anomaly']` est bien assigné (valeurs 0/1).\n"
                # Le catalogue algolib est injecté globalement par
                # _llm_fix_skeleton_with_retry — ne pas le dupliquer ici.
                f"{feedback}"
                "Retourne UNIQUEMENT le code Python corrigé, sans balise markdown."
            )

        return _llm_fix_skeleton_with_retry(algo_id, row["python_skeleton"], build_prompt)

    except (AllProvidersExhausted, RepairUnavailable):
        # Indisponibilité LLM : laisser remonter jusqu'à repair_issue(), qui
        # l'enregistre en 'skipped'. La capturer ici la transformait en échec
        # de réparation et alimentait à tort la soupape anti-acharnement.
        raise
    except Exception as exc:
        return False, f"Erreur repair_forbidden_import : {exc}"


# ── Dispatch principal ────────────────────────────────────────────────────────

_REPAIR_DISPATCH = {
    # ── Skeletons LLM ────────────────────────────────────────────────────────
    "skeleton_syntax_error":         repair_skeleton_syntax,
    "missing_skeleton":              repair_missing_skeleton,
    "missing_is_anomaly_assignment": repair_missing_is_anomaly,
    "forbidden_import":              repair_forbidden_import,
    "column_mismatch":               repair_missing_is_anomaly,
    # Échec par-algorithme silencieux dans un notebook de comparaison par ailleurs
    # 'executed' (voir check_algorithm_execution_errors) — corrige le skeleton avec
    # le vrai message d'erreur capturé, pas juste une relance sans changement.
    "algo_execution_error":          repair_algo_execution_error,
    # Même réparation LLM+validation : le fix_hint décrit la miscalibration au lieu
    # d'une trace d'erreur, mais le mécanisme (corriger le skeleton, valider par
    # exécution réelle) est identique.
    "algo_miscalibrated":            repair_algo_execution_error,
    # ── Algorithmes manquants → Auto1 ────────────────────────────────────────
    "no_algorithms":                 repair_no_algorithms,
    # ── Rapports PDF → Auto3 ──────────────────────────────────────────────────
    "compile_failed":                repair_report_compile_failed,
    "report_invalid":                repair_report_compile_failed,
    "report_stale":                  repair_report_compile_failed,
    # ── Notebooks / figures ──────────────────────────────────────────────────
    "execution_failed":              repair_failed_notebook,
    # Exécuté mais 0 résultat extrait / bloqué en 'running' → même remède :
    # remise en 'generated' pour ré-exécution propre par Auto2 S3.
    "no_results":                    repair_failed_notebook,
    "stuck_running":                 repair_failed_notebook,
    "stale_notebook":                repair_stale_notebook,
    "algo_count_mismatch":           repair_stale_notebook,
    "notebook:minio_missing":        repair_notebook_minio_missing,
    # Figures absentes de MinIO → forcer reconstruction complète S2+S3
    "zero_figures":                  repair_zero_figures,
    # ── Datasets absents/dégénérés → Auto1 S4 ────────────────────────────────
    "no_data":                       repair_dataset_regen,
    "no_dataset":                    repair_dataset_regen,
    "dataset_no_anomalies":          repair_dataset_regen,
    "dataset_orphaned":              repair_dataset_orphaned,
    # ── Infra ─────────────────────────────────────────────────────────────────
    "duplicate_watch":               repair_duplicate_watch,
    # ── MinIO par entity_type ────────────────────────────────────────────────
    "minio_file_missing":            None,   # dispatch dans repair_issue() selon entity_type
    # ── Non réparables automatiquement ──────────────────────────────────────
    "missing_required_columns":      None,   # schéma dataset incorrect, nécessite re-génération S1
}


def repair_issue(issue: QualityIssue) -> Tuple[bool, str]:
    """
    Tente de réparer automatiquement une issue qualité.
    Retourne (success, detail_message).
    """
    if not issue.auto_fixable:
        return False, "Issue marquée non auto-fixable"

    handler = _REPAIR_DISPATCH.get(issue.issue_type)

    # Pour minio_file_missing, on dispatch selon l'entity_type
    if issue.issue_type == "minio_file_missing":
        if issue.entity_type == "notebook":
            handler = repair_notebook_minio_missing
        elif issue.entity_type == "dataset":
            handler = repair_dataset_minio_missing
        else:
            return False, f"Pas de handler minio_file_missing pour {issue.entity_type}"

    if handler is None:
        return False, f"Pas de réparation automatique pour issue_type='{issue.issue_type}'"

    try:
        log.info("[REPAIR] %s #%s — %s", issue.entity_type, issue.entity_id, issue.issue_type)
        success, detail = handler(issue)
        level = logging.INFO if success else logging.WARNING
        log.log(level, "[REPAIR] %s → %s | %s", "OK" if success else "FAIL", detail, issue.entity_key)
        return success, detail
    except (AllProvidersExhausted, RepairUnavailable):
        # Indisponibilité LLM : PAS un échec de réparation. On laisse remonter pour
        # que l'appelant l'enregistre en 'skipped' (et mette la boucle en pause si
        # les quotas sont épuisés). Les avaler ici les faisait compter comme des
        # échecs et « garer » à tort des issues jamais réellement tentées.
        raise
    except Exception as exc:
        log.error("[REPAIR] Exception inattendue : %s", exc)
        return False, str(exc)
