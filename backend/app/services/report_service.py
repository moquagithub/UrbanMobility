"""
services/report_service.py
===========================
Logique de la page Streamlit `page_download` (app_eda.py), portée en
service pur : génère les rapports LaTeX + figures PNG (via `build_latex()` /
`build_latex_summary()`, eda_analyse.py — non modifiés) et les compresse en
ZIP, prêts à être renvoyés en téléchargement par les routers.

Substituts légers pour `anonymizer` / `error_logger`
-----------------------------------------------------
`build_latex()` / `build_latex_summary()` ne lisent que des attributs de
données pures sur ces deux paramètres (`anonymizer.value_log`,
`error_logger.error_count`, `error_logger.errors` — jamais de méthode
appelée). On leur substitue donc un `types.SimpleNamespace` reconstruit à
partir du snapshot persistant (`value_log`, `error_snapshot`) plutôt que de
conserver les objets `PIIAnonymizer` / `ErrorLogger` vivants — cohérent
avec le choix de persistance de `dataset_store.py` (objets non picklables
de façon fiable).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Any, Dict

from app.services import eda_service

# ── Imports de la logique métier existante (non modifiée) ────────────────────
from eda_analyse import build_latex, build_latex_summary, _auto_detect_pipeline  # noqa: E402


def _fake_anonymizer(record: Dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(value_log=record.get("value_log") or {})


def _fake_error_logger(record: Dict[str, Any]) -> SimpleNamespace:
    snapshot = record.get("error_snapshot") or {"error_count": 0, "errors": []}
    return SimpleNamespace(error_count=snapshot.get("error_count", 0), errors=snapshot.get("errors", []))


def _zip_directory(work_dir: str) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in os.listdir(work_dir):
            fpath = os.path.join(work_dir, fname)
            if os.path.isfile(fpath):
                zf.write(fpath, fname)
    buf.seek(0)
    return buf.read()


def generate_detailed_report(dataset_id: str) -> Dict[str, Any]:
    """
    Équivalent du 'Rapport 1 — Analyse Détaillée' de page_download : génère
    rapport_eda.tex + figures PNG (manquants, distributions, boxplots,
    corrélations, importance) et retourne le ZIP correspondant.
    """
    record = eda_service.get_dataset(dataset_id)
    df, meta, imp_df = record["df"], record["meta"], record["imp_df"]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    work_dir = tempfile.mkdtemp(prefix=f"rapport_eda_{ts}_")
    try:
        tex_path = os.path.join(work_dir, "rapport_eda.tex")
        build_latex(
            csv_path=record["filename"],
            df=df,
            meta=meta,
            imp_df=imp_df,
            output_path=tex_path,
            error_logger=_fake_error_logger(record),
            anonymizer=_fake_anonymizer(record),
        )
        zip_bytes = _zip_directory(work_dir)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return {"filename": f"rapport_eda_{ts}.zip", "content": zip_bytes}


def generate_synthesis_report(before_dataset_id: str, after_dataset_id: str) -> Dict[str, Any]:
    """
    Équivalent du 'Rapport 2 — Synthèse Comparative (Avant/Après)' de
    page_download. Contrairement à l'app Streamlit (qui exige de ré-uploader
    le CSV transformé), les deux versions sont déjà disponibles via leurs
    dataset_id respectifs (voir recommendations_service.apply_recommendations,
    qui crée le dataset "après" sans jamais modifier le dataset "avant").
    """
    before = eda_service.get_dataset(before_dataset_id)
    after = eda_service.get_dataset(after_dataset_id)

    pipeline = _auto_detect_pipeline(before["df"], after["df"], before["meta"], after["meta"])

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    work_dir = tempfile.mkdtemp(prefix=f"rapport_synthese_{ts}_")
    try:
        tex_path = os.path.join(work_dir, "rapport_synthese.tex")
        build_latex_summary(
            csv_path_before=before["filename"],
            csv_path_after=after["filename"],
            df_before=before["df"],
            df_after=after["df"],
            meta_before=before["meta"],
            meta_after=after["meta"],
            imp_before=before["imp_df"],
            imp_after=after["imp_df"],
            pipeline_steps=pipeline,
            output_path=tex_path,
            error_logger=_fake_error_logger(before),
        )
        zip_bytes = _zip_directory(work_dir)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return {"filename": f"rapport_synthese_{ts}.zip", "content": zip_bytes}
