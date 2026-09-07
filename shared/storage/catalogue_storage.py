"""
shared.storage.catalogue_storage — Uploads hiérarchiques vers le bucket `catalogue`.

Structure Minio gérée par ce module :
  catalogue/
    {type_id}/
      metadata.json                              ← type (S1 partiel, S1b complet)
      description.txt                            ← présentation lisible du type (S1/S1b)
      problems/
        {problem_key}/
          metadata.json                          ← problème (S2)
          description.txt                        ← présentation lisible du problème (S2)
          test_results.json                      ← résumé validation algos (S3)
          dataset/
            schema.json                          ← schéma colonnes (S4)
            data.csv                             ← données synthétiques (S4)
            description.txt                      ← présentation lisible du dataset (S4)
          algorithms/
            {algo_key}/
              metadata.json                      ← algo (S3)
              skeleton.py                        ← code Python validé (S3)
              explanation.txt                    ← narrative (S3)

Règle : un échec d'upload est un warning, pas une erreur bloquante.
MySQL reste la source de vérité ; Minio est un cache re-générable.
"""
from __future__ import annotations

import io
import json
import logging
from typing import Dict

from shared.storage.client import BUCKET_CATALOGUE, UploadResult, get_storage

log = logging.getLogger(__name__)

_bucket_ready = False


def _ensure_bucket() -> None:
    global _bucket_ready
    if not _bucket_ready:
        get_storage().ensure_catalogue_bucket()
        _bucket_ready = True


# ── Constructeur de chemin ────────────────────────────────────────────────────

def catalogue_path(type_id: str, *parts: str) -> str:
    """
    Construit une clé Minio sous le répertoire du type.

    catalogue_path("traces_gps", "metadata.json")
        → "traces_gps/metadata.json"
    catalogue_path("traces_gps", "problems", "p1", "algorithms", "alg1", "skeleton.py")
        → "traces_gps/problems/p1/algorithms/alg1/skeleton.py"
    """
    segments = [type_id.strip("/")] + [p.strip("/") for p in parts if p]
    return "/".join(segments)


def problem_dir_prefix(type_id: str, prob_key: str) -> str:
    """Préfixe répertoire d'un problème (valeur stockée dans problems.minio_dir)."""
    return catalogue_path(type_id, "problems", prob_key) + "/"


def algorithm_dir_prefix(type_id: str, prob_key: str, algo_key: str) -> str:
    """Préfixe répertoire d'un algorithme (valeur stockée dans algorithms.minio_dir)."""
    return catalogue_path(type_id, "problems", prob_key, "algorithms", algo_key) + "/"


# ── Primitives internes ───────────────────────────────────────────────────────

def _upload_json(key: str, payload: Dict) -> UploadResult:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return get_storage().upload_bytes(BUCKET_CATALOGUE, key, data,
                                      content_type="application/json")


def _upload_text(key: str, text: str) -> UploadResult:
    return get_storage().upload_bytes(BUCKET_CATALOGUE, key, text.encode("utf-8"),
                                      content_type="text/plain; charset=utf-8")


def _upload_python(key: str, code: str) -> UploadResult:
    return get_storage().upload_bytes(BUCKET_CATALOGUE, key, code.encode("utf-8"),
                                      content_type="text/x-python")


def _upload_csv(key: str, df) -> UploadResult:
    buf = io.BytesIO()
    df.to_csv(buf, index=False, encoding="utf-8")
    return get_storage().upload_bytes(BUCKET_CATALOGUE, key, buf.getvalue(),
                                      content_type="text/csv")


# ── API publique ──────────────────────────────────────────────────────────────

def upload_type_metadata(type_id: str, payload: Dict) -> UploadResult:
    """Upload {type_id}/metadata.json (créé en S1, enrichi en S1b)."""
    _ensure_bucket()
    key = catalogue_path(type_id, "metadata.json")
    result = _upload_json(key, payload)
    if result.success:
        log.debug("[CATALOGUE_STORAGE] ↑ %s (%d bytes)", key, result.size_bytes)
    else:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, result.error)
    return result


def upload_type_description(type_id: str, text: str) -> UploadResult:
    """Upload {type_id}/description.txt (S1/S1b — présentation lisible du type)."""
    _ensure_bucket()
    key = catalogue_path(type_id, "description.txt")
    result = _upload_text(key, text)
    if not result.success:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, result.error)
    return result


def upload_problem_metadata(type_id: str, prob_key: str, payload: Dict) -> UploadResult:
    """Upload {type_id}/problems/{prob_key}/metadata.json (S2)."""
    _ensure_bucket()
    key = catalogue_path(type_id, "problems", prob_key, "metadata.json")
    result = _upload_json(key, payload)
    if not result.success:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, result.error)
    return result


def upload_problem_description(type_id: str, prob_key: str, text: str) -> UploadResult:
    """Upload {type_id}/problems/{prob_key}/description.txt (S2 — présentation lisible du problème)."""
    _ensure_bucket()
    key = catalogue_path(type_id, "problems", prob_key, "description.txt")
    result = _upload_text(key, text)
    if not result.success:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, result.error)
    return result


def upload_algorithm_metadata(type_id: str, prob_key: str,
                               algo_key: str, payload: Dict) -> UploadResult:
    """Upload {type_id}/problems/{prob_key}/algorithms/{algo_key}/metadata.json (S3)."""
    _ensure_bucket()
    key = catalogue_path(type_id, "problems", prob_key, "algorithms", algo_key, "metadata.json")
    result = _upload_json(key, payload)
    if not result.success:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, result.error)
    return result


def upload_algorithm_skeleton(type_id: str, prob_key: str,
                               algo_key: str, skeleton_code: str) -> UploadResult:
    """Upload {type_id}/problems/{prob_key}/algorithms/{algo_key}/skeleton.py (S3)."""
    _ensure_bucket()
    key = catalogue_path(type_id, "problems", prob_key, "algorithms", algo_key, "skeleton.py")
    result = _upload_python(key, skeleton_code)
    if not result.success:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, result.error)
    return result


def upload_algorithm_explanation(type_id: str, prob_key: str,
                                  algo_key: str, text: str) -> UploadResult:
    """Upload {type_id}/problems/{prob_key}/algorithms/{algo_key}/explanation.txt (S3)."""
    _ensure_bucket()
    key = catalogue_path(type_id, "problems", prob_key, "algorithms", algo_key, "explanation.txt")
    result = _upload_text(key, text)
    if not result.success:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, result.error)
    return result


def upload_test_results(type_id: str, prob_key: str, payload: Dict) -> UploadResult:
    """Upload {type_id}/problems/{prob_key}/test_results.json (S3, fin de problème)."""
    _ensure_bucket()
    key = catalogue_path(type_id, "problems", prob_key, "test_results.json")
    result = _upload_json(key, payload)
    if not result.success:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, result.error)
    return result


def upload_dataset_schema(type_id: str, prob_key: str, payload: Dict) -> UploadResult:
    """Upload {type_id}/problems/{prob_key}/dataset/schema.json (S4)."""
    _ensure_bucket()
    key = catalogue_path(type_id, "problems", prob_key, "dataset", "schema.json")
    result = _upload_json(key, payload)
    if not result.success:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, result.error)
    return result


def upload_dataset_csv(type_id: str, prob_key: str, df) -> UploadResult:
    """Upload {type_id}/problems/{prob_key}/dataset/data.csv (S4)."""
    _ensure_bucket()
    key = catalogue_path(type_id, "problems", prob_key, "dataset", "data.csv")
    result = _upload_csv(key, df)
    if not result.success:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, result.error)
    return result


def upload_dataset_description(type_id: str, prob_key: str, text: str) -> UploadResult:
    """Upload {type_id}/problems/{prob_key}/dataset/description.txt (S4 — présentation lisible du dataset)."""
    _ensure_bucket()
    key = catalogue_path(type_id, "problems", prob_key, "dataset", "description.txt")
    result = _upload_text(key, text)
    if not result.success:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, result.error)
    return result


# ─── Auto2 — Notebooks, figures de comparaison ───────────────────────────────

def upload_notebook(type_id: str, prob_key: str, nb_bytes: bytes) -> UploadResult:
    """Upload {type_id}/problems/{prob_key}/notebook.ipynb (Auto2 S2)."""
    _ensure_bucket()
    key = catalogue_path(type_id, "problems", prob_key, "notebook.ipynb")
    try:
        from shared.storage.client import BUCKET_CATALOGUE, get_storage
        store = get_storage()
        store._mc.put_object(
            BUCKET_CATALOGUE, key,
            io.BytesIO(nb_bytes), len(nb_bytes),
            content_type="application/json",
        )
        return UploadResult(success=True, object_key=key)
    except Exception as exc:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, exc)
        return UploadResult(success=False, error=str(exc))


def upload_algorithm_notebook_standalone(type_id: str, prob_key: str,
                                          algo_key: str, nb_bytes: bytes) -> UploadResult:
    """Upload {type_id}/problems/{prob_key}/algorithms/{algo_key}/notebook.ipynb"""
    _ensure_bucket()
    key = catalogue_path(type_id, "problems", prob_key, "algorithms", algo_key, "notebook.ipynb")
    try:
        from shared.storage.client import BUCKET_CATALOGUE, get_storage
        store = get_storage()
        store._mc.put_object(BUCKET_CATALOGUE, key, io.BytesIO(nb_bytes), len(nb_bytes),
                             content_type="application/json")
        return UploadResult(success=True, object_key=key)
    except Exception as exc:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, exc)
        return UploadResult(success=False, error=str(exc))


def upload_algorithm_concept_latex(type_id: str, prob_key: str,
                                    algo_key: str, tex_content: str) -> UploadResult:
    """Upload {type_id}/problems/{prob_key}/algorithms/{algo_key}/concept.tex"""
    _ensure_bucket()
    key = catalogue_path(type_id, "problems", prob_key, "algorithms", algo_key, "concept.tex")
    result = _upload_text(key, tex_content)
    if not result.success:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, result.error)
    return result


def upload_comparison_figure_named(type_id: str, prob_key: str,
                                    suffix: str, png_bytes: bytes) -> UploadResult:
    """Upload {type_id}/problems/{prob_key}/comparison_{suffix}.png (S5 — 2 figures séparées)."""
    _ensure_bucket()
    key = catalogue_path(type_id, "problems", prob_key, f"comparison_{suffix}.png")
    try:
        from shared.storage.client import BUCKET_CATALOGUE, get_storage
        store = get_storage()
        store._mc.put_object(BUCKET_CATALOGUE, key, io.BytesIO(png_bytes), len(png_bytes),
                             content_type="image/png")
        return UploadResult(success=True, object_key=key)
    except Exception as exc:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, exc)
        return UploadResult(success=False, error=str(exc))


def upload_comparison_figure(type_id: str, prob_key: str, png_bytes: bytes) -> UploadResult:
    """Upload {type_id}/problems/{prob_key}/comparison.png (Auto2 S5)."""
    _ensure_bucket()
    key = catalogue_path(type_id, "problems", prob_key, "comparison.png")
    try:
        from shared.storage.client import BUCKET_CATALOGUE, get_storage
        store = get_storage()
        store._mc.put_object(
            BUCKET_CATALOGUE, key,
            io.BytesIO(png_bytes), len(png_bytes),
            content_type="image/png",
        )
        return UploadResult(success=True, object_key=key)
    except Exception as exc:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, exc)
        return UploadResult(success=False, error=str(exc))


def upload_report_pdf(type_id: str, prob_key: str, pdf_bytes: bytes) -> UploadResult:
    """Upload {type_id}/problems/{prob_key}/report.pdf (Auto3)."""
    _ensure_bucket()
    key = catalogue_path(type_id, "problems", prob_key, "report.pdf")
    try:
        from shared.storage.client import BUCKET_CATALOGUE, get_storage
        store = get_storage()
        store._mc.put_object(
            BUCKET_CATALOGUE, key,
            io.BytesIO(pdf_bytes), len(pdf_bytes),
            content_type="application/pdf",
        )
        return UploadResult(success=True, object_key=key)
    except Exception as exc:
        log.warning("[CATALOGUE_STORAGE] ✗ %s : %s", key, exc)
        return UploadResult(success=False, error=str(exc))
