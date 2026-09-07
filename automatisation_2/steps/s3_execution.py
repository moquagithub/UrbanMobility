"""
Étape S3 — Exécution des notebooks Jupyter.

Utilise nbconvert.preprocessors.ExecutePreprocessor pour exécuter chaque notebook
dans un kernel Python isolé, capture les erreurs, le temps d'exécution et les
figures générées.

Stratégie de robustesse :
- Timeout par notebook : 300 secondes
- En cas d'erreur de cellule : notebook marqué 'error' mais on continue
- Les figures .png dans le répertoire de figures sont collectées après exécution
- Les métriques JSON sont extraites du stdout de la dernière cellule
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

import nbformat
from nbconvert.preprocessors import ExecutePreprocessor, CellExecutionError

from shared.db.repository import CatalogueRepository, NotebookRepository

log = logging.getLogger("auto2.s3_execution")

# Timeout PAR CELLULE appliqué par nbclient, donc par algorithme. Partagé avec la
# validation par exécution : si les deux divergent, la validation rejette des
# skeletons que ce module exécute très bien (ou l'inverse).
from shared.validation.skeleton_contract import EXECUTION_TIMEOUT_SEC as TIMEOUT_SEC

MAX_STDOUT_CHARS  = 3000


def run(
    type_ids: Optional[List[str]] = None,
    run_id: Optional[int] = None,
    max_notebooks: Optional[int] = None,
) -> Dict[str, int]:
    """
    Exécute tous les notebooks générés (status='generated').

    Args:
        type_ids      : restreindre à certains types de données
        run_id        : ID de la run auto1 associée (pour notebook_results)
        max_notebooks : limite le nombre de notebooks à exécuter (test)

    Retourne {"ok": N, "failed": N, "timeout": N}
    """
    cat_repo      = CatalogueRepository()
    notebook_repo = NotebookRepository()

    types = cat_repo.list_data_types()
    if type_ids:
        types = [t for t in types if t["id"] in type_ids]

    stats       = {"ok": 0, "failed": 0, "timeout": 0}
    total_done  = 0

    for dt in types:
        type_id   = dt["id"]
        type_name = dt["name"]

        pending = notebook_repo.list_pending_execution(type_id)
        if not pending:
            log.info("[S3] %s — aucun notebook en attente", type_name)
            continue

        log.info("[S3] %s — %d notebook(s) à exécuter", type_name, len(pending))

        for nb_row in pending:
            if max_notebooks and total_done >= max_notebooks:
                break

            nb_id   = nb_row["id"]
            nb_key  = nb_row.get("notebook_key", "")
            nb_path = nb_row.get("notebook_path", "")
            algo_id = nb_row.get("algorithm_id")
            prob_id = nb_row.get("problem_id")

            if not nb_path or not Path(nb_path).exists():
                # Tenter téléchargement depuis MinIO
                minio_key_nb = nb_row.get("minio_key", "")
                if minio_key_nb:
                    try:
                        from shared.storage import get_storage
                        _store = get_storage()
                        Path(nb_path).parent.mkdir(parents=True, exist_ok=True)
                        _store.download_notebook(minio_key_nb, Path(nb_path))
                        log.info("[S3] Notebook téléchargé depuis MinIO : %s", minio_key_nb)
                    except Exception as _de:
                        log.warning("[S3] Download MinIO notebook échoué : %s", _de)

            if not nb_path or not Path(nb_path).exists():
                log.warning("[S3] Fichier introuvable : %s", nb_path)
                notebook_repo.update_execution(
                    nb_id, "failed",
                    error_message="notebook_path introuvable",
                )
                stats["failed"] += 1
                total_done += 1
                continue

            log.info("[S3] Exécution : %s", nb_key)
            t0 = time.perf_counter()

            exec_ok, error_msg, stdout_all, nb_executed = _execute_notebook(nb_path)
            elapsed = round(time.perf_counter() - t0, 2)

            # Détecter timeout — 'timeout' n'est pas dans l'enum MySQL, on utilise 'failed'
            if error_msg and "timeout" in error_msg.lower():
                status = "failed"
                stats["timeout"] += 1
            elif exec_ok:
                status = "executed"
                stats["ok"] += 1
            else:
                status = "failed"
                stats["failed"] += 1

            # Extraire métriques JSON du stdout
            metrics = _extract_metrics_json(stdout_all)

            # Collecter les figures générées
            figures = _collect_figures(nb_path)

            # ── Validation qualité de l'output ────────────────────────────
            quality = _validate_execution_quality(
                status, exec_ok, metrics, figures, is_comparison=not bool(algo_id),
            )
            if quality["issues"]:
                for issue in quality["issues"]:
                    log.warning("[S3] QUALITÉ %s — %s", nb_key, issue)
            if quality["regen_skeleton"] and algo_id:
                notebook_repo.flag_skeleton_regen(algo_id, reason="; ".join(quality["issues"]))
                log.warning("[S3] %s → squelette flaggé pour régénération", nb_key)

            # Sauvegarder le notebook exécuté (avec outputs)
            executed_path = None
            executed_minio_key = None
            if nb_executed and exec_ok:
                executed_path = _save_executed(nb_path, nb_executed)
                # Upload notebook exécuté vers MinIO
                try:
                    from shared.storage import get_storage
                    store = get_storage()
                    res = store.upload_executed_notebook(
                        nb_row.get("data_type_id", ""), nb_key, Path(executed_path)
                    )
                    if res.success:
                        executed_minio_key = res.object_key
                except Exception as exc:
                    log.debug("[S3] MinIO upload executed échoué : %s", exc)

            # Upload figures vers MinIO + enregistrement dans table figures
            figure_minio_keys: List[str] = []
            if figures:
                try:
                    from shared.storage import get_storage
                    from shared.db.repository.figure_repo import FigureRepository
                    store    = get_storage()
                    fig_repo = FigureRepository()
                    type_id  = nb_row.get("data_type_id", "")
                    items = [
                        ("figures", f"{type_id}/{Path(f).name}", Path(f))
                        for f in figures if Path(f).exists()
                    ]
                    if items:
                        batch_results = store.batch_upload(items)
                        for res, (_, mkey, local_p) in zip(batch_results, items):
                            if res.success:
                                figure_minio_keys.append(res.object_key)
                                # Persister la clé MinIO dans la table figures
                                fig_key = f"{type_id}_{local_p.stem}"
                                fig_repo.save_figure(
                                    data_type_id=type_id,
                                    figure_key=fig_key,
                                    problem_id=prob_id,
                                    figure_type="algo_result",
                                    title=local_p.stem,
                                    file_path=str(local_p),
                                    minio_key=res.object_key,
                                )
                except Exception as exc:
                    log.debug("[S3] MinIO upload figures échoué : %s", exc)

            # Nettoyage fichiers locaux après upload MinIO réussi
            if executed_minio_key and executed_path:
                try:
                    Path(executed_path).unlink(missing_ok=True)
                except Exception:
                    pass
            if figure_minio_keys and figures:
                for _fig in figures:
                    try:
                        Path(_fig).unlink(missing_ok=True)
                    except Exception:
                        pass
            # Supprimer notebook original (déjà dans MinIO depuis S2)
            if nb_row.get("minio_key") and nb_path and Path(nb_path).exists():
                try:
                    Path(nb_path).unlink(missing_ok=True)
                except Exception:
                    pass

            # Mise à jour statut notebook
            notebook_repo.update_execution(
                notebook_id=nb_id,
                status=status,
                execution_time_sec=elapsed,
                error_message=error_msg[:500] if error_msg else None,
                executed_path=executed_path,
                executed_minio_key=executed_minio_key,
            )

            # Figures à stocker dans notebook_results :
            # priorité aux clés MinIO (persistantes) plutôt qu'aux chemins locaux (effacés)
            persisted_figures = figure_minio_keys if figure_minio_keys else figures

            # Sauvegarde résultat détaillé
            if algo_id:
                # Notebook individuel (ancien modèle) : 1 résultat
                notebook_repo.save_result(
                    run_id=run_id or None,
                    notebook_id=nb_id,
                    algorithm_id=algo_id or 0,
                    notebook_num=total_done + 1,
                    notebook_path=nb_path,
                    status=status,
                    execution_time_sec=elapsed,
                    metrics_json=metrics,
                    final_metrics_json=metrics,
                    output_figures=persisted_figures,
                    stdout_summary=stdout_all[:MAX_STDOUT_CHARS],
                    cell_errors_count=0 if exec_ok else 1,
                    figure_path=persisted_figures[0] if persisted_figures else "",
                )
            else:
                # Notebook de comparaison (nouveau modèle) : N résultats, 1 par algo
                algo_results = _extract_algo_results(stdout_all)
                for ar in algo_results:
                    ar_algo_id = ar.get("algo_id")
                    if not ar_algo_id:
                        continue
                    notebook_repo.save_result(
                        run_id=run_id or None,
                        notebook_id=nb_id,
                        algorithm_id=ar_algo_id,
                        notebook_num=total_done + 1,
                        notebook_path=nb_path,
                        status=status,
                        execution_time_sec=ar.get("exec_time_sec") or elapsed,
                        metrics_json=ar,
                        final_metrics_json=ar,
                        output_figures=persisted_figures,
                        stdout_summary=stdout_all[:MAX_STDOUT_CHARS],
                        cell_errors_count=0 if exec_ok else 1,
                        figure_path=persisted_figures[0] if persisted_figures else "",
                    )
                if algo_results:
                    log.info("[S3] %s — %d résultats algo extraits du notebook comparaison",
                             nb_key, len(algo_results))

            log.info(
                "[S3] %s — %s en %.1fs | figures=%d | métriques=%s | qualité=%s",
                nb_key, status, elapsed, len(figures),
                list(metrics.keys())[:4],
                "OK" if not quality["issues"] else f"{len(quality['issues'])} problème(s)",
            )
            total_done += 1

    log.info(
        "[S3] Terminé — ok=%d error=%d timeout=%d",
        stats["ok"], stats["failed"], stats["timeout"],
    )
    return stats


def _execute_notebook(
    nb_path: str,
) -> tuple[bool, Optional[str], str, Optional[nbformat.NotebookNode]]:
    """
    Exécute un notebook avec ExecutePreprocessor.

    Retourne (ok, error_message, stdout_all, nb_node).
    """
    with open(nb_path, encoding="utf-8") as f:
        nb = nbformat.read(f, as_version=4)

    ep = ExecutePreprocessor(
        timeout=TIMEOUT_SEC,
        kernel_name="python3",
        allow_errors=False,
    )

    stdout_parts: List[str] = []
    error_msg: Optional[str] = None
    ok = True

    try:
        ep.preprocess(nb, {"metadata": {"path": str(Path(".").resolve())}})
    except CellExecutionError as exc:
        summary = f"{exc.ename}: {exc.evalue}"
        error_msg = f"{summary}\n\n{exc}"[:800]
        ok = False
        log.warning("[S3] CellExecutionError : %s", summary)
    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"[:800]
        ok = False
        log.warning("[S3] Erreur exécution : %s", error_msg[:200])

    # Extraire stdout de toutes les cellules
    for cell in nb.cells:
        if cell.cell_type != "code":
            continue
        for output in cell.get("outputs", []):
            if output.get("output_type") == "stream" and output.get("name") == "stdout":
                stdout_parts.append(output.get("text", ""))

    return ok, error_msg, "\n".join(stdout_parts), nb


def _save_executed(original_path: str, nb: nbformat.NotebookNode) -> str:
    """Sauvegarde le notebook après exécution (avec outputs) dans un sous-dossier executed/."""
    p = Path(original_path)
    executed_dir = p.parent / "executed"
    executed_dir.mkdir(parents=True, exist_ok=True)
    executed_path = executed_dir / p.name
    with open(executed_path, "w", encoding="utf-8") as f:
        nbformat.write(nb, f)
    return str(executed_path)


def _extract_metrics_json(stdout: str) -> Dict:
    """
    Cherche la ligne 'Métriques JSON : {...}' dans le stdout et la parse.
    Fallback : recherche n'importe quelle ligne JSON valide avec f1/precision/recall.
    """
    pattern = r"M[eé]triques\s+JSON\s*:\s*(\{.*?\})"
    m = re.search(pattern, stdout, re.IGNORECASE | re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass

    # Fallback : dernière ligne JSON
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and len(obj) > 0:
                    return obj
            except Exception:
                pass

    return {}


def _extract_algo_results(stdout: str) -> List[Dict]:
    """
    Extrait les métriques par algorithme d'un notebook de comparaison.

    Cherche les lignes "ALGO_RESULT: {...}" dans le stdout.
    """
    results = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("ALGO_RESULT:"):
            payload = line[len("ALGO_RESULT:"):].strip()
            try:
                obj = json.loads(payload)
                if isinstance(obj, dict) and obj.get("algo_id"):
                    results.append(obj)
            except Exception:
                pass
    return results


def _collect_figures(nb_path: str) -> List[str]:
    """Collecte uniquement les figures produites par CE notebook (FIGURES_DIR + NOTEBOOK_ID)."""
    try:
        with open(nb_path, encoding="utf-8") as f:
            nb = nbformat.read(f, as_version=4)

        # Cell 1 = paramètres : contient FIGURES_DIR et NOTEBOOK_ID
        params_src = nb.cells[1].source if len(nb.cells) > 1 else ""

        m_dir = re.search(r"FIGURES_DIR\s*=\s*r?['\"]([^'\"]+)['\"]", params_src)
        m_id  = re.search(r"NOTEBOOK_ID\s*=\s*(\d+)", params_src)

        if m_dir and m_id:
            figures_dir = Path(m_dir.group(1))
            nb_id       = int(m_id.group(1))
            prefix      = f"nb{nb_id:03d}_"
            found = sorted(figures_dir.glob(f"{prefix}*.png"))
            return [str(p) for p in found]  # vide si le notebook a échoué

    except Exception as exc:
        log.debug("[S3] _collect_figures erreur : %s", exc)

    return []


def _validate_execution_quality(
    status: str,
    exec_ok: bool,
    metrics: Dict,
    figures: List[str],
    is_comparison: bool = False,
) -> Dict:
    """
    Valide la qualité de l'output d'un notebook exécuté.

    Détecte les cas où le notebook a "réussi" techniquement mais produit
    un résultat incohérent :
      - Aucune anomalie détectée (F1=0 ET recall=0) → skeleton doit être révisé
      - Aucune figure produite malgré une exécution réussie → problème de chemin
      - Métriques absentes → la cellule de reporting n'a pas fonctionné

    Les notebooks de comparaison (is_comparison=True) rapportent leurs métriques via des
    lignes "ALGO_RESULT: {...}" par algorithme (voir _extract_algo_results), pas via le
    bloc "Métriques JSON : {...}" que cherche _extract_metrics_json — donc `metrics` y est
    toujours vide par construction et ne doit pas être interprété comme une anomalie.

    Retourne {"issues": [...str], "regen_skeleton": bool}
    """
    issues: List[str] = []
    regen_skeleton = False

    if not exec_ok or status != "executed":
        return {"issues": issues, "regen_skeleton": False}

    if not is_comparison:
        # Vérifier les métriques de détection (notebooks individuels uniquement)
        f1 = metrics.get("f1")
        recall = metrics.get("recall")

        if f1 is not None and recall is not None:
            if float(f1) == 0.0 and float(recall) == 0.0:
                issues.append(
                    "Détection nulle : F1=0 et Recall=0 — l'algorithme ne détecte aucune anomalie. "
                    "Le squelette Python doit assigner df['is_anomaly']=True pour les anomalies détectées."
                )
                regen_skeleton = True
        elif not metrics:
            issues.append(
                "Métriques absentes du stdout — la cellule de reporting JSON n'a pas été exécutée "
                "ou n'a pas trouvé df['is_anomaly']."
            )

    # Vérifier les figures
    if not figures:
        issues.append(
            "Aucune figure produite malgré une exécution réussie — "
            "vérifier FIGURES_DIR et le code de sauvegarde matplotlib dans le notebook."
        )

    return {"issues": issues, "regen_skeleton": regen_skeleton}
