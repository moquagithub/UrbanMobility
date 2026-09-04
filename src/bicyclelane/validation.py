"""Backtest validation against the AMP cycling plan (BL-22).

Compares a detector's (or the composite's) ranked opportunities to the
**Métropole Aix-Marseille-Provence cycling plan** — the ground truth. The plan
geometries come from the *Observatoire Plan Vélo* (Collectif Vélos en Ville,
observatoire.velosenville.org), which digitised the June-2019 metropolitan plan;
reuse requires attribution.

A plan segment is a **positive** target when it is part of the plan's *new*
network (``A réaliser`` / ``En travaux`` / ``Réalisé dans le plan``); segments
that already existed before the plan are excluded. A ranked opportunity is a
*hit* when it lies within ``match_tol_m`` of a positive plan segment. From the
ranked hit/miss sequence we report Precision@k, Recall@k (distinct plan coverage)
and a mean-average-precision proxy.
"""

from __future__ import annotations

from typing import Any, Iterable

import geopandas as gpd

# A metric CRS good for mainland France (Lambert-93), for buffers/distances.
METRIC_CRS = "EPSG:2154"


def _category(statut: object) -> str:
    s = str(statut or "")
    if s.startswith("A réaliser") or s == "En travaux":
        return "planned_new"
    if s.startswith("Réalisé dans le plan"):
        return "realized_in_plan"
    if s.startswith("Réalisé avant"):
        return "pre_existing"
    if s.startswith("Réalisé"):
        return "realized_in_plan"  # "Réalisé 2024/2025" = realised within the plan
    return "other"


def load_amp_plan(path: str) -> gpd.GeoDataFrame:
    """Load the AMP plan GeoJSON with a ``category`` column (WGS84).

    Categories: ``planned_new`` / ``realized_in_plan`` / ``pre_existing`` /
    ``other`` derived from the ``statut`` field.
    """
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326", allow_override=True)
    statut = gdf["statut"] if "statut" in gdf.columns else [None] * len(gdf)
    gdf = gdf.copy()
    gdf["category"] = [_category(s) for s in statut]
    return gdf


def plan_positives(plan: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """The plan's *new* network used as backtest ground truth (positives)."""
    return plan[plan["category"].isin(("planned_new", "realized_in_plan"))]


def backtest(
    opportunities: gpd.GeoDataFrame,
    positives: gpd.GeoDataFrame,
    *,
    match_tol_m: float = 30.0,
    ks: Iterable[int] = (10, 25, 50, 100),
) -> dict[str, Any]:
    """Score a ranked opportunity list against the plan positives.

    ``opportunities`` must be ordered best-first (row order = rank). Returns
    ``precision_at``/``recall_at`` per k, ``average_precision`` (proxy, over the
    reachable positives), and counts. A hit = opportunity within ``match_tol_m``
    of a positive plan segment.
    """
    opp = opportunities.to_crs(METRIC_CRS) if len(opportunities) else opportunities
    pos = positives.to_crs(METRIC_CRS) if len(positives) else positives
    n_opp, n_pos = len(opp), len(pos)
    if n_opp == 0 or n_pos == 0:
        return {"precision_at": {}, "recall_at": {}, "average_precision": 0.0,
                "n_opportunities": n_opp, "n_positives": n_pos, "n_hits": 0}

    pos_geoms = list(pos.geometry)
    pos_sindex = pos.sindex
    pos_buffer = pos.geometry.buffer(match_tol_m).union_all()

    hit_flags: list[bool] = []
    matched_pos: set[int] = set()
    for geom in opp.geometry:
        if geom is None or geom.is_empty:
            hit_flags.append(False)
            continue
        hit = geom.distance(pos_buffer) == 0.0  # within the buffered plan
        hit_flags.append(bool(hit))
        if hit:
            for j in pos_sindex.query(geom.buffer(match_tol_m), predicate="intersects"):
                matched_pos.add(int(j))

    # Precision@k / Recall@k (recall = distinct positives covered by top-k).
    precision_at, recall_at = {}, {}
    for k in ks:
        kk = min(int(k), n_opp)
        if kk == 0:
            continue
        top_hits = sum(hit_flags[:kk])
        covered = set()
        for geom in opp.geometry.iloc[:kk]:
            if geom is None or geom.is_empty:
                continue
            for j in pos_sindex.query(geom.buffer(match_tol_m), predicate="intersects"):
                covered.add(int(j))
        precision_at[int(k)] = round(top_hits / kk, 4)
        recall_at[int(k)] = round(len(covered) / n_pos, 4)

    # Average precision (proxy): mean of running precision at each hit rank,
    # normalised by the reachable positives.
    hits = 0
    ap = 0.0
    for i, flag in enumerate(hit_flags, start=1):
        if flag:
            hits += 1
            ap += hits / i
    ap = ap / max(1, min(n_pos, n_opp))

    return {
        "precision_at": precision_at,
        "recall_at": recall_at,
        "average_precision": round(ap, 4),
        "n_opportunities": n_opp,
        "n_positives": n_pos,
        "n_hits": sum(hit_flags),
    }
