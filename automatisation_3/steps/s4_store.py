"""
Étape S4 — Stockage des résultats en MySQL et mise à jour des statuts.

Architecture par problème : chaque item représente un (type × problème).
Le rapport PDF est uploadé vers :
  - bucket catalogue  → {type_id}/problems/{prob_key}/report.pdf
  - MySQL reports     → report_key = "report_{type_id}_{prob_key}", problem_id renseigné
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List

from shared.db.connection import get_connection
from shared.db.repository import ReportRepository

log = logging.getLogger("auto3.s4_store")


def run(compiled_items: List[Dict]) -> Dict[str, int]:
    """
    Sauvegarde les résultats de compilation en MySQL et met à jour processing_status.

    Args:
        compiled_items : résultat de s3_compile.run()

    Retourne {"ok": N, "error": N, "skip": N}
    """
    report_repo = ReportRepository()
    stats       = {"ok": 0, "error": 0, "skip": 0}

    for item in compiled_items:
        dt       = item["data_type"]
        problem  = item.get("problem", {})
        type_id  = dt["id"]
        type_name = dt["name"]
        prob_key  = problem.get("problem_key", f"prob_{problem.get('id', 'x')}")
        prob_id   = problem.get("id")
        prob_title = problem.get("title", prob_key)
        label     = f"{type_name}/{prob_key}"

        success  = item.get("compile_success", False)
        pdf_path = item.get("pdf_path")
        tex_dir  = item.get("tex_dir", "")
        errors   = item.get("compile_errors", "")
        rounds   = item.get("compile_rounds", 0)
        elapsed  = item.get("generation_time_sec", 0.0)
        n_chap   = item.get("n_chapters", 0)
        metrics  = item.get("metrics_summary", {})

        report_key = f"report_{type_id}_{prob_key}"

        n_pages = 0
        if pdf_path:
            n_pages = _count_pdf_pages(pdf_path)

        # Upload PDF vers bucket catalogue (chemin hiérarchique)
        minio_key = ""
        if success and pdf_path:
            try:
                from shared.storage.catalogue_storage import upload_report_pdf
                _pdf_bytes = Path(pdf_path).read_bytes()
                _rc = upload_report_pdf(type_id, prob_key, _pdf_bytes)
                if _rc.success:
                    minio_key = _rc.object_key
                    log.info("[S4] PDF → catalogue Minio : %s", minio_key)
                    try:
                        Path(pdf_path).unlink(missing_ok=True)
                    except Exception:
                        pass
            except Exception as exc:
                log.debug("[S4] Catalogue Minio upload PDF erreur : %s", exc)

        # Fallback : bucket reports classique
        if not minio_key and success and pdf_path:
            try:
                from shared.storage import get_storage
                store = get_storage()
                res = store.upload_report(type_id, Path(pdf_path))
                if res.success:
                    minio_key = res.object_key
                    log.info("[S4] PDF → reports Minio (fallback) : %s", minio_key)
                    try:
                        Path(pdf_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                else:
                    log.warning("[S4] MinIO upload PDF échoué : %s", res.error)
            except Exception as exc:
                log.warning("[S4] MinIO indisponible : %s", exc)

        report_id = report_repo.save_report(
            data_type_id        = type_id,
            report_key          = report_key,
            title               = f"Rapport — {type_name} / {prob_title[:80]}",
            pdf_path            = pdf_path or "",
            tex_dir             = tex_dir,
            n_chapters          = n_chap,
            n_pages             = n_pages,
            compile_success     = success,
            compile_errors      = errors,
            compile_rounds      = rounds,
            generation_time_sec = elapsed,
            metrics_summary     = metrics,
            status              = "compiled" if success else "error",
            minio_key           = minio_key,
            problem_id          = prob_id,
        )

        if report_id:
            log.info("[S4] %s — report#%d sauvegardé (success=%s, pages=%d)",
                     label, report_id, success, n_pages)
        else:
            log.warning("[S4] %s — sauvegarde MySQL échouée", label)

        if success:
            _update_status(type_id, "report_done")
            stats["ok"] += 1
        else:
            stats["error"] += 1

    log.info("[S4] Terminé — ok=%d error=%d skip=%d", stats["ok"], stats["error"], stats["skip"])
    return stats


def _update_status(type_id: str, status: str) -> None:
    conn = get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE data_types SET processing_status=%s, updated_at=NOW() WHERE id=%s",
                (status, type_id),
            )
        conn.commit()
    except Exception as exc:
        log.warning("[S4] update_status erreur : %s", exc)
    finally:
        conn.close()


def _count_pdf_pages(pdf_path: str) -> int:
    try:
        import subprocess
        r = subprocess.run(
            ["pdfinfo", pdf_path],
            capture_output=True, text=True, timeout=10,
        )
        for line in r.stdout.splitlines():
            if "Pages:" in line:
                return int(line.split(":")[1].strip())
    except Exception:
        pass
    return 0
