"""
services/quiz_service.py
=========================
Logique de la page Streamlit `page_quiz` (app_eda.py), portée en service
pur. `QuizProcessor` (quiz_processor.py, legacy, non modifié) est exécuté
une seule fois par dataset (`run_all()` est coûteux : détection de types,
parsing multi-réponses, décodage JSON, vérifications de cohérence) puis le
`QuizReport` résultant est mis en cache dans le record persistant du
dataset — équivalent du cache `st.session_state.quiz_report` de l'app
Streamlit, mais qui survit aux requêtes/redémarrages puisqu'il est
sérialisé avec le reste du dataset (voir dataset_store.py : QuizReport ne
contient que des DataFrame/dict/list, donc entièrement picklable).
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from app.services import dataset_store, eda_service

# ── Import de la logique métier existante (non modifiée) ─────────────────────
from quiz_processor import QuizProcessor  # noqa: E402

MAX_JSON_PREVIEW_ROWS = 20  # même valeur que page_quiz (decoded.head(20))


def _df_to_records(df: pd.DataFrame, limit: int = None) -> List[Dict[str, Any]]:
    """Convertit un DataFrame en liste de dicts JSON-safe (NaN -> None)."""
    if df is None or df.empty:
        return []
    out = df.head(limit) if limit else df
    out = out.astype(object).where(pd.notnull(out), None)
    return out.reset_index().to_dict(orient="records")


def _series_to_records(series: pd.Series) -> List[Dict[str, Any]]:
    """Convertit une Series de fréquences (index=modalité) en liste [{modalite, frequence}]."""
    if series is None or len(series) == 0:
        return []
    return [{"modalite": str(k), "frequence": int(v)} for k, v in series.items()]


def _get_or_compute_report(dataset_id: str):
    record = eda_service.get_dataset(dataset_id)
    report = record.get("quiz_report")
    if report is not None:
        return report

    df = record["df"]
    anon_map = record["anon_map"]
    qp = QuizProcessor(df, anon_map, verbose=False)
    report = qp.run_all()

    dataset_store.update(dataset_id, quiz_report=report)
    return report


def get_quiz_report(dataset_id: str) -> Dict[str, Any]:
    """Équivalent complet de page_quiz : KPIs + les 5 onglets (taux de réponse,
    multi-réponses, JSON, ordinales, incohérences), en une seule réponse —
    le frontend (DQE-9) pourra répartir ces données dans des onglets/tabs."""
    report = _get_or_compute_report(dataset_id)

    return {
        "dataset_id": dataset_id,
        "n_multi": report.n_multi,
        "n_json": report.n_json,
        "n_ordinal": report.n_ordinal,
        "n_freetext": report.n_freetext,
        "n_filter": report.n_filter,
        "n_incoherences": len(report.consistency_issues),
        "response_rates": _df_to_records(report.response_rates),
        "multi_freq": {col: _series_to_records(freq) for col, freq in report.multi_freq.items()},
        "multi_coocc": {col: _df_to_records(coocc) for col, coocc in report.multi_coocc.items()},
        "json_decoded_preview": {
            col: _df_to_records(decoded, limit=MAX_JSON_PREVIEW_ROWS)
            for col, decoded in report.json_decoded.items()
        },
        "ordinal_stats": dict(report.ordinal_stats),
        "consistency_issues": list(report.consistency_issues),
    }
