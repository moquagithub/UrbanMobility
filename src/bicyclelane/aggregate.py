"""Shared-unit aggregation bridge for composite scoring (BL-21).

The composite engine (:mod:`bicyclelane.composite`) works on a decision matrix
of *units × criteria*, but the detectors emit heterogeneous geometries
(segments for D1/D2/D9/D11, zones for D4/D5). This module maps them onto one
**common spatial unit — a regular grid cell** — so every detector contributes a
comparable per-cell signal, producing the decision matrix the engine consumes.

Each detector's raw ``score`` is min-max normalised to ``[0, 1]``, every feature
is attached to the cell containing its representative point, and a cell's value
for a detector is the **max** normalised score of the features landing in it
(the strongest signal there); a cell with no feature from a detector gets 0.
"""

from __future__ import annotations

from typing import Any, Mapping

import geopandas as gpd
import numpy as np
from shapely.geometry import mapping

from .composite import run_composite, CompositeResult


def _normalise(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    lo, hi = float(values.min()), float(values.max())
    if hi == lo:
        return np.ones_like(values)
    return (values - lo) / (hi - lo)


def aggregate_to_grid(
    per_detector: Mapping[str, gpd.GeoDataFrame | None],
    grid: gpd.GeoDataFrame,
    criteria: list[str],
    *,
    score_col: str = "score",
) -> gpd.GeoDataFrame:
    """Project each detector's normalised score onto the shared ``grid``.

    Returns a copy of ``grid`` with one column per ``criteria`` key holding the
    per-cell normalised value in ``[0, 1]`` (0 where a detector is silent). Each
    feature is assigned to the cell containing its representative point and cells
    take the max over the features that land in them.
    """
    out = grid.copy()
    cell_index = {int(c): i for i, c in enumerate(out["cell_id"])}
    for det in criteria:
        out[det] = 0.0
        gdf = per_detector.get(det)
        if gdf is None or len(gdf) == 0 or score_col not in gdf:
            continue
        feats = gdf.to_crs(out.crs) if gdf.crs != out.crs else gdf.copy()
        feats = feats[~feats.geometry.is_empty & feats.geometry.notna()]
        if len(feats) == 0:
            continue
        norm = _normalise(feats[score_col].to_numpy(dtype=float))
        pts = gpd.GeoDataFrame(
            {"_v": norm}, geometry=feats.geometry.representative_point(), crs=out.crs
        )
        joined = gpd.sjoin(
            pts, out[["cell_id", "geometry"]], predicate="within", how="inner"
        )
        if len(joined) == 0:
            continue
        per_cell = joined.groupby("cell_id")["_v"].max()
        col = out[det].to_numpy()
        for cid, val in per_cell.items():
            pos = cell_index.get(int(cid))
            if pos is not None:
                col[pos] = float(val)
        out[det] = col
    return out


def composite_grid(
    per_detector: Mapping[str, gpd.GeoDataFrame | None],
    grid: gpd.GeoDataFrame,
    criteria: list[str],
    *,
    norm: str = "minmax",
    expert: list[float] | None = None,
    ahp: np.ndarray | None = None,
    top_k: int = 20,
) -> tuple[gpd.GeoDataFrame, CompositeResult]:
    """Aggregate detectors to the grid and score cells with the composite engine.

    Returns ``(cells, result)``: ``cells`` is the grid with the per-detector
    normalised columns plus a ``composite`` column (equal-weights score) and one
    ``contrib_<det>`` column per criterion (that detector's contribution to the
    equal-weights composite); ``result`` is the full :class:`CompositeResult`
    (all weighting methods, comparison, sensitivity) for the compare-methods view.

    Cells are already in the grid's CRS; the per-detector columns are the
    normalised inputs, so the client can recompute the composite live under any
    interactive weights without another server round-trip.
    """
    cells = aggregate_to_grid(per_detector, grid, criteria, score_col="score")
    X = cells[criteria].to_numpy(dtype=float)
    result = run_composite(X, criteria, norm=norm, expert=expert, ahp=ahp, top_k=top_k)

    equal_scores = result.scores["equal"]
    equal_contrib = result.contributions["equal"]
    cells = cells.copy()
    cells["composite"] = equal_scores
    for j, det in enumerate(criteria):
        cells[f"contrib_{det}"] = equal_contrib[:, j]
    return cells, result


def composite_geojson(
    cells: gpd.GeoDataFrame,
    result: CompositeResult,
    criteria: list[str],
    *,
    drop_empty: bool = True,
) -> dict[str, Any]:
    """Serialise composite cells + method comparison for the web payload (WGS84).

    Each cell feature carries ``cell_id``, ``composite`` (equal-weights), the
    per-detector normalised values (keys = ``criteria``, so the client can
    recompute the composite under interactive weights), and one
    ``contrib_<det>`` per criterion. ``drop_empty`` skips cells with no signal
    from any detector. The ``meta`` block reports the weighting methods, their
    pairwise agreement, AHP consistency and weight sensitivity.
    """
    g = cells.to_crs(4326)
    feats: list[dict[str, Any]] = []
    for _, row in g.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        vals = {det: round(float(row[det]), 6) for det in criteria}
        if drop_empty and not any(v > 0 for v in vals.values()):
            continue
        props: dict[str, Any] = {
            "cell_id": int(row["cell_id"]),
            "composite": round(float(row["composite"]), 6),
        }
        props.update(vals)
        for det in criteria:
            props[f"contrib_{det}"] = round(float(row[f"contrib_{det}"]), 6)
        feats.append({"type": "Feature", "geometry": mapping(geom), "properties": props})

    meta = {
        "criteria": list(criteria),
        "weights": {k: [round(float(x), 6) for x in v] for k, v in result.weights.items()},
        "comparison": result.comparison,
        "ahp_consistency_ratio": result.meta.get("ahp_consistency_ratio"),
        "sensitivity": result.meta.get("sensitivity"),
        "norm": result.meta.get("norm"),
    }
    return {"type": "FeatureCollection", "features": feats, "meta": meta}
