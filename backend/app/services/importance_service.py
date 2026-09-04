"""
services/importance_service.py
===============================
Logique de la page Streamlit `page_importance` (app_eda.py), portée en
service pur. Le score composite lui-même (`imp_df`) est calculé une seule
fois à l'upload par `compute_importance()` (eda_analyse.py, non modifié) et
mis en cache dans le record du dataset — ce service ne fait que le
formater et générer l'interprétation textuelle via `describe_importance()`
(également non modifié).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from app.services import eda_service
from app.services.json_safe import safe_float

# ── Import de la logique métier existante (non modifiée) ─────────────────────
from eda_analyse import describe_importance  # noqa: E402


def _strip_html(text: str) -> str:
    """describe_importance() retourne des paragraphes avec des balises <b> HTML simples
    (pensées pour du rendu Streamlit `unsafe_allow_html`) — on les convertit en
    Markdown pour un rendu neutre côté frontend, comme le faisait déjà app_eda.py."""
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text


def get_importance(dataset_id: str, top_n: Optional[int] = None) -> Dict[str, Any]:
    record   = eda_service.get_dataset(dataset_id)
    imp_df   = record["imp_df"]
    meta     = record["meta"]
    reverse  = {v: k for k, v in meta["mapping"].items()}

    n_total = len(imp_df)
    n = min(top_n, n_total) if top_n else n_total
    data = imp_df.head(n)

    lignes = [
        {
            "nom_anonyme":       idx,
            "nom_original":      reverse.get(idx, idx),
            "rang":              int(row["rang"]),
            "score_importance":  safe_float(row["score_importance"]) or 0.0,
            "completude_pct":    safe_float(row["completude_pct"]) or 0.0,
            "variabilite_norm":  safe_float(row["variabilite_norm"]) or 0.0,
            "correlation_max":   safe_float(row["correlation_max"]) or 0.0,
            "unicite_norm":      safe_float(row["unicite_norm"]) or 0.0,
        }
        for idx, row in data.iterrows()
    ]

    interpretation = [_strip_html(p) for p in describe_importance(imp_df, meta["mapping"])]

    return {
        "dataset_id": dataset_id,
        "top_n": n,
        "lignes": lignes,
        "interpretation": interpretation,
    }
