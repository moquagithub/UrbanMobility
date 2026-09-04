"""D7 - Safety / crashes detector (BL-29).

Goal
----
Rank streets with a concentration of **cyclist crashes** and no protective cycle
lane — a new lane there would most reduce risk. Uses the BAAC crash points
(:mod:`bicyclelane.baac`, external open data, opt-in via ``BICYCLELANE_BAAC``);
when no crash layer is available the detector yields nothing.

Score
-----
Each crash is map-matched to the nearest equippable road edge (not fast, not
already served) within ``match_tol_m``. An edge's score is the sum of matched
crash **severity** weights times its ``highway_weight``; already-lane-covered
roads are excluded upstream by ``build_candidate_edges``.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd

from ..schema import Opportunity, to_geodataframe
from ._roadprop import build_candidate_edges, highway_weight


def detect_safety_hotspots(
    road_gdf: gpd.GeoDataFrame,
    cycle_gdf: gpd.GeoDataFrame,
    crashes_gdf: gpd.GeoDataFrame | None,
    *,
    city: str = "",
    crs: Any = None,
    match_tol_m: float = 25.0,
    min_score: float = 0.3,
    max_results: int = 500,
) -> gpd.GeoDataFrame:
    """Rank crash-concentration lane opportunities (``detector == "D7"``).

    ``crashes_gdf`` are bicycle-crash points with a ``severity`` weight (see
    :func:`bicyclelane.baac.load_baac`). Returns proposed road edges with
    ``score`` = summed severity · highway_weight, ``crash_severity`` and
    ``n_crashes``. Empty when there is no crash layer or nothing matches.
    """
    work_crs = crs if crs is not None else road_gdf.crs
    cand = build_candidate_edges(road_gdf, cycle_gdf, work_crs)
    if cand is None or cand.empty or crashes_gdf is None or len(crashes_gdf) == 0:
        return to_geodataframe([], crs=crs)

    crashes = crashes_gdf.to_crs(work_crs) if crashes_gdf.crs != work_crs else crashes_gdf
    crashes = crashes[crashes.geometry.notna() & ~crashes.geometry.is_empty]
    if crashes.empty:
        return to_geodataframe([], crs=crs)
    if "severity" not in crashes:
        crashes = crashes.assign(severity=0.5)

    cand = cand.reset_index(drop=True)
    # Match each crash to its nearest candidate edge within tolerance.
    matched = gpd.sjoin_nearest(
        crashes[["severity", "geometry"]], cand[["geometry"]],
        how="inner", max_distance=match_tol_m, distance_col="_d")
    if matched.empty:
        return to_geodataframe([], crs=crs)

    agg = matched.groupby("index_right").agg(
        crash_severity=("severity", "sum"), n_crashes=("severity", "size"))

    opportunities: list[Opportunity] = []
    seq = 0
    for edge_pos, row in agg.iterrows():
        cedge = cand.iloc[int(edge_pos)]
        hw = cedge.get("_hw")
        pull = float(row["crash_severity"])
        score = pull * highway_weight(hw)
        if score < min_score:
            continue
        opportunities.append(Opportunity(
            id=f"D7-{seq}",
            city=city,
            detector="D7",
            geometry=cedge.geometry,
            score=float(score),
            attributes={
                "crash_severity": round(pull, 2),
                "n_crashes": int(row["n_crashes"]),
                "highway": hw,
                "street": cedge.get("_name") or "(unnamed)",
                "length_m": round(float(cedge.get("_len", cedge.geometry.length)), 1),
            },
            explanation=(
                f"{int(row['n_crashes'])} cyclist crash(es) here (severity "
                f"{round(pull, 1)}) with no protective lane — a lane would cut risk."
            ),
        ))
        seq += 1

    opportunities.sort(key=lambda o: o.score, reverse=True)
    opportunities = opportunities[:max_results]
    for rank, opp in enumerate(opportunities, start=1):
        opp.rank = rank
    return to_geodataframe(opportunities, crs=crs)
