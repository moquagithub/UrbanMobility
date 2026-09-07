"""
Validation d'un skeleton Python d'algorithme par EXÉCUTION RÉELLE (pas juste
syntaxe/AST), dans un sous-processus isolé avec timeout.

Extrait de shared/quality/repair.py pour être réutilisable à deux endroits :
  - réactivement, par repair.py, quand un skeleton déjà en base échoue à
    l'exécution d'un notebook Auto2 (repair.py fournit le df via une requête DB)
  - proactivement, par automatisation_1/steps/s4_datasets.py, juste après la
    génération du dataset — le df est déjà en mémoire, pas besoin de requête DB.
"""
from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd


def run_skeleton_once(skeleton: str, df: pd.DataFrame,
                      timeout_sec: Optional[int] = None) -> Tuple[bool, str]:
    """Une seule exécution du skeleton dans un sous-processus isolé. Voir
    validate_skeleton_execution pour pourquoi ceci est appelé plusieurs fois."""
    import subprocess
    import sys
    import tempfile
    import textwrap
    import pickle
    import os

    from shared.validation.skeleton_contract import (
        EXECUTION_TIMEOUT_SEC, resolve_entry_point,
    )

    # Même résolution que automatisation_2/notebook_builder : valider sous une règle
    # plus stricte que la production rejetterait du code qui s'exécute très bien.
    entry = resolve_entry_point(skeleton)
    if timeout_sec is None:
        timeout_sec = EXECUTION_TIMEOUT_SEC

    with tempfile.TemporaryDirectory() as tmpdir:
        df_path = os.path.join(tmpdir, "df.pkl")
        skel_path = os.path.join(tmpdir, "skeleton.py")
        with open(df_path, "wb") as f:
            pickle.dump(df, f)
        with open(skel_path, "w", encoding="utf-8") as f:
            f.write(skeleton)

        # Racine du projet (parent de shared/). Les notebooks générés s'exécutent avec
        # ce répertoire comme cwd et peuvent donc importer `shared.algolib` ; le
        # sous-processus de validation, lui, démarre depuis un dossier temporaire où
        # `shared` est introuvable. Sans cette injection, la validation rejetterait en
        # « ModuleNotFoundError: No module named 'shared' » des skeletons que le
        # notebook exécute parfaitement — encore une divergence validation/production.
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))

        harness = textwrap.dedent(f"""
            import pickle, sys, warnings
            sys.path.insert(0, {project_root!r})
            import pandas as _pd
            # Sous le Copy-on-Write de pandas 3, l'affectation chainee
            # (df["is_anomaly"][mask] = 1) ne modifie RIEN et ne leve AUCUNE erreur :
            # l'algorithme s'execute proprement et retourne 0 anomalie. On ne peut
            # donc pas distinguer ce bug d'un simple seuil mal calibre, et le
            # diagnostic renvoye au LLM l'envoie corriger le mauvais probleme.
            # On promeut le ChainedAssignmentError en erreur pour le rendre visible.
            warnings.simplefilter("error", _pd.errors.ChainedAssignmentError)
            with open({df_path!r}, "rb") as f:
                df = pickle.load(f)
            ns = {{}}
            with open({skel_path!r}, encoding="utf-8") as f:
                exec(f.read(), ns)
            if not callable(ns.get({entry!r})):
                defined = sorted(k for k, v in ns.items()
                                 if callable(v) and not k.startswith("_"))
                print("VALIDATION_FAIL: le code ne definit aucune fonction d'entree "
                      "appelable prenant le DataFrame en argument. Nomme-la `run(df)`. "
                      "Fonctions definies : " + (str(defined) or "aucune"))
                sys.exit(1)
            # `run` peut exister mais réclamer des arguments que l'appelant ne
            # fournira jamais (observé : ref_layer, G, transition_matrix,
            # tunnel_topology). Sans ce controle, l'erreur remontait en
            # « run() missing 1 required positional argument » — un TypeError brut
            # qui ne dit pas au modele que la signature elle-meme est le probleme.
            import inspect as _inspect
            _extra = [
                p.name for p in list(
                    _inspect.signature(ns[{entry!r}]).parameters.values())[1:]
                if p.default is _inspect.Parameter.empty
                and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
            ]
            if _extra:
                print("VALIDATION_FAIL: `run` reclame des arguments supplementaires "
                      "sans valeur par defaut : " + str(_extra) + ". L'appelant n'appelle "
                      "que run(df). Construis ces structures A L'INTERIEUR de run() a "
                      "partir de df, ou donne-leur une valeur par defaut.")
                sys.exit(1)
            try:
                out = ns[{entry!r}](df.copy())
            except _pd.errors.ChainedAssignmentError:
                print("VALIDATION_FAIL: affectation chainee detectee "
                      '(ex. df["is_anomaly"][mask] = 1). Sous pandas 3 cette ecriture '
                      "est PERDUE silencieusement : la colonne reste inchangee et "
                      "l'algorithme ne detecte rien. Ecris en une seule indexation : "
                      'df.loc[mask, "is_anomaly"] = 1')
                sys.exit(1)
            if not hasattr(out, "columns"):
                print("VALIDATION_FAIL: `run(df)` doit retourner un DataFrame pandas, "
                      "or il a retourne un " + type(out).__name__)
                sys.exit(1)
            if "is_anomaly" not in out.columns:
                print("VALIDATION_FAIL: pas de colonne is_anomaly")
                sys.exit(1)
            n = len(out)
            n_anom = int(out["is_anomaly"].astype(bool).sum())
            print(f"VALIDATION_OK:{{n_anom}}:{{n}}")
        """)
        harness_path = os.path.join(tmpdir, "harness.py")
        with open(harness_path, "w", encoding="utf-8") as f:
            f.write(harness)

        try:
            proc = subprocess.run(
                [sys.executable, harness_path],
                capture_output=True, text=True, timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return False, f"timeout ({timeout_sec}s) lors de la validation par exécution"

        if proc.returncode != 0:
            # Un VALIDATION_FAIL est un diagnostic que NOUS écrivons, en clair et
            # actionnable ; le renvoyer tel quel plutôt que noyé dans un
            # « échec exécution (code=1) : <fin de traceback> ». Ce feedback est
            # réinjecté dans le prompt LLM à la tentative suivante : une trace
            # pointant vers notre harness.py (ex. « KeyError: 'run' ») désigne un
            # fichier que le modèle n'a jamais écrit et ne lui dit pas quoi corriger.
            out_txt = (proc.stdout or "").strip()
            for line in reversed(out_txt.splitlines()):
                if line.startswith("VALIDATION_FAIL:"):
                    return False, line[len("VALIDATION_FAIL:"):].strip()
            tail = (proc.stderr or out_txt or "").strip()[-300:]
            if proc.returncode < 0:
                # Code négatif = le processus a été tué par un signal : crash natif
                # dans une extension C (SIGSEGV=-11, SIGABRT=-6). Ce n'est PAS une
                # exception Python et ça ne se rattrape pas : dans un notebook, ça
                # tue le kernel entier et emporte les résultats de tous les autres
                # algorithmes (cas traces_gps_p11_comparison). Le message était vide
                # — stderr l'est aussi lors d'un crash natif — et ne disait donc rien
                # au modèle sur ce qu'il devait corriger.
                import signal as _signal
                try:
                    signame = _signal.Signals(-proc.returncode).name
                except Exception:
                    signame = f"signal {-proc.returncode}"
                return False, (
                    f"CRASH NATIF ({signame}) : le processus a été tué net, sans "
                    "exception Python rattrapable. Une opération bas niveau corrompt "
                    "la mémoire — typiquement une routine scipy/numpy appelée avec des "
                    "dimensions incohérentes, un tableau non contigu ou en lecture "
                    "seule, une récursion sans borne, ou une allocation démesurée. "
                    "Dans un notebook ce crash tue le kernel ENTIER et fait perdre les "
                    "résultats de tous les autres algorithmes. Réécris le calcul avec "
                    "des opérations numpy vectorisées simples, en vérifiant les formes "
                    "avant chaque produit matriciel et en copiant les tableaux "
                    "(.to_numpy(copy=True)) avant toute routine qui écrit en place. "
                    f"{tail}"
                ).strip()
            return False, f"échec exécution (code={proc.returncode}) : {tail}"

        out = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if not out.startswith("VALIDATION_OK:"):
            return False, f"sortie inattendue : {out[:200]}"

        try:
            n_anom, n = (int(x) for x in out.split(":")[1:3])
        except Exception:
            return True, "exécution réussie (comptage anomalies non parsable)"

        if n_anom == 0:
            return False, f"exécution réussie mais 0 anomalie détectée sur {n} lignes (seuil probablement mal calibré)"
        if n_anom > 0.5 * n:
            return False, f"exécution réussie mais {n_anom}/{n} lignes détectées comme anomalies (seuil probablement mal calibré)"

        return True, f"validé : {n_anom}/{n} anomalies détectées"


def validate_skeleton_execution(
    skeleton: str, df: pd.DataFrame, timeout_sec: Optional[int] = None, n_runs: int = 2,
) -> Tuple[bool, str]:
    """
    Valide un skeleton par EXÉCUTION RÉELLE (pas juste une vérification de syntaxe),
    dans un sous-processus isolé avec timeout — un crash natif (segfault, memory
    corruption d'une lib C comme scipy) ou une boucle infinie ne doit jamais faire
    tomber le processus appelant.

    Exécute n_runs fois (défaut 2) : une corruption mémoire native (ex: scipy SLSQP
    avec contraintes) est souvent NON-DÉTERMINISTE — observé en pratique un même
    skeleton produisant "free(): invalid next size" puis, sur une autre exécution,
    "malloc.c:4376 assertion failed" ou aucune erreur du tout. Une seule passe ne
    suffit pas à détecter fiablement ce genre de bug ; toutes les passes doivent
    réussir pour que le skeleton soit accepté.

    Retourne (success, detail). success=False si le skeleton lève une exception,
    crashe le processus (sur N'IMPORTE laquelle des exécutions), dépasse le timeout,
    ou ne produit pas de colonne 'is_anomaly' exploitable.
    """
    import time

    from shared.validation.skeleton_contract import EXECUTION_TIMEOUT_SEC

    if df is None or df.empty:
        return True, "validation exécution ignorée (pas de dataset disponible)"

    if timeout_sec is None:
        timeout_sec = EXECUTION_TIMEOUT_SEC

    last_detail = ""
    for i in range(n_runs):
        # Les passes suivantes ne servent qu'à débusquer les crashs natifs
        # NON-DÉTERMINISTES, qui surviennent vite : inutile de leur réaccorder le
        # budget complet. On les borne au double du temps réellement mis par la
        # première passe (plancher 30 s), sinon un skeleton lent mais valide
        # immobiliserait la boucle de réparation pendant n_runs × 300 s.
        budget = timeout_sec if i == 0 else max(30, min(timeout_sec, int(elapsed * 2) + 5))
        started = time.monotonic()
        ok, detail = run_skeleton_once(skeleton, df, budget)
        elapsed = time.monotonic() - started
        if not ok:
            return False, f"{detail} (essai {i + 1}/{n_runs})"
        last_detail = detail

    return True, last_detail
