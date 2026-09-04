"""D12 - Tourism & leisure corridors detector (BL-32).

Goal
----
Rank streets that pass near **tourism / leisure attractors** (museums, viewpoints,
monuments, parks…) yet have no cycle lane — corridors that would connect visitors
to points of interest. The attractors come from OSM
(:func:`bicyclelane.places.get_tourism`), so no external data is needed.

Score
-----
For each equippable road edge (not fast, not already served) the **attractor
pull** is the sum of nearby attractor weights within ``buffer_m``; the score is
``attractor_pull · highway_weight``.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd

from ..schema import Opportunity, to_geodataframe
from ._roadprop import build_candidate_edges, highway_weight


def detect_tourism_corridors(
    road_gdf: gpd.GeoDataFrame,
    cycle_gdf: gpd.GeoDataFrame,
    tourism_gdf: gpd.GeoDataFrame | None,
    *,
    city: str = "",
    crs: Any = None,
    buffer_m: float = 150.0,
    min_score: float = 0.3,
    max_results: int = 500,
) -> gpd.GeoDataFrame:
    """Rank tourism-corridor lane opportunities (``detector == "D12"``).

    ``tourism_gdf`` are weighted attractor points (see
    :func:`bicyclelane.places.get_tourism`, column ``tour_weight``). Returns
    proposed road edges with ``score`` = attractor_pull · highway_weight,
    ``attractor_pull`` and ``n_attractors``. Empty when there are no attractors
    or nothing clears ``min_score``.
    """
    work_crs = crs if crs is not None else road_gdf.crs
    cand = build_candidate_edges(road_gdf, cycle_gdf, work_crs)
    if (cand is None or cand.empty or tourism_gdf is None
            or len(tourism_gdf) == 0 or "tour_weight" not in tourism_gdf):
        return to_geodataframe([], crs=crs)

    tpts = tourism_gdf.to_crs(work_crs) if tourism_gdf.crs != work_crs else tourism_gdf
    tpts = tpts[tpts.geometry.notna() & ~tpts.geometry.is_empty]
    if tpts.empty:
        return to_geodataframe([], crs=crs)
    weights = tpts["tour_weight"].to_numpy(dtype=float)
    tsindex = tpts.sindex

    opportunities: list[Opportunity] = []
    seq = 0
    for i in range(len(cand)):
        row = cand.iloc[i]
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        hits = tsindex.query(geom.buffer(buffer_m), predicate="intersects")
        if len(hits) == 0:
            continue
        pull = float(weights[hits].sum())
        hw = row.get("_hw")
        score = pull * highway_weight(hw)
        if score < min_score:
            continue
        opportunities.append(Opportunity(
            id=f"D12-{seq}",
            city=city,
            detector="D12",
            geometry=geom,
            score=float(score),
            attributes={
                "attractor_pull": round(pull, 2),
                "n_attractors": int(len(hits)),
                "highway": hw,
                "street": row.get("_name") or "(unnamed)",
                "length_m": round(float(row.get("_len", geom.length)), 1),
            },
            explanation=(
                f"Tourism corridor: {int(len(hits))} nearby attractor(s) "
                f"(pull {round(pull, 1)}) with no cycle lane — a route worth equipping."
            ),
        ))
        seq += 1

    opportunities.sort(key=lambda o: o.score, reverse=True)
    opportunities = opportunities[:max_results]
    for rank, opp in enumerate(opportunities, start=1):
        opp.rank = rank
    return to_geodataframe(opportunities, crs=crs)
