"""D10 - Relief & green corridors detector (BL-26).

Goal
----
Surface attractive **corridors for new lanes along green space** — streets next
to parks, woods or water — preferring **low-gradient** routes. Green adjacency is
the core signal (this is a *green-corridor* detector); slope, when a DEM is
plugged in (:mod:`bicyclelane.terrain`, ``BICYCLELANE_DEM``), modulates it so
flat green corridors rank above steep ones. Without a DEM it degrades gracefully
to pure green adjacency.

Score
-----
For each equippable road edge (not fast, not already served) within
``buffer_m`` of a green/water feature:

    score = green_proximity · flat_factor · highway_weight

* ``green_proximity`` = ``1 − dist/buffer_m`` (nearer green = higher).
* ``flat_factor`` — with a DEM, ``clamp(1 − grade/max_grade, 0.2, 1)`` (steep is
  penalised but not zeroed); ``1.0`` when no DEM is available.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd

from ..schema import Opportunity, to_geodataframe
from ..terrain import ElevationSampler, edge_grades
from ._roadprop import build_candidate_edges, highway_weight


def detect_green_corridors(
    road_gdf: gpd.GeoDataFrame,
    cycle_gdf: gpd.GeoDataFrame,
    green_gdf: gpd.GeoDataFrame | None,
    *,
    city: str = "",
    crs: Any = None,
    sampler: ElevationSampler | None = None,
    buffer_m: float = 80.0,
    max_grade: float = 6.0,
    min_score: float = 0.15,
    max_results: int = 500,
) -> gpd.GeoDataFrame:
    """Rank green-corridor lane opportunities (``detector == "D10"``).

    ``green_gdf`` are green/water geometries (see
    :func:`bicyclelane.places.get_green`); ``sampler`` is an optional elevation
    sampler for the low-gradient preference. Returns proposed road edges with
    ``score``, ``green_dist_m`` and (when a DEM is given) ``grade_pct``. Empty
    when there is no green layer or nothing lies within ``buffer_m``.
    """
    work_crs = crs if crs is not None else road_gdf.crs
    cand = build_candidate_edges(road_gdf, cycle_gdf, work_crs)
    if cand is None or cand.empty or green_gdf is None or len(green_gdf) == 0:
        return to_geodataframe([], crs=crs)

    green = green_gdf.to_crs(work_crs) if green_gdf.crs != work_crs else green_gdf
    green = green[green.geometry.notna() & ~green.geometry.is_empty]
    if green.empty:
        return to_geodataframe([], crs=crs)

    # Nearest green feature within buffer_m; edges with none are dropped.
    near = gpd.sjoin_nearest(
        cand, green[["geometry"]], how="inner",
        max_distance=buffer_m, distance_col="green_dist_m",
    )
    if near.empty:
        return to_geodataframe([], crs=crs)
    # sjoin_nearest can emit ties -> keep the closest green per edge.
    near = near.sort_values("green_dist_m")
    near = near[~near.index.duplicated(keep="first")].sort_index()

    grades = edge_grades(near, sampler) if sampler is not None else None

    opportunities: list[Opportunity] = []
    seq = 0
    for pos in range(len(near)):
        row = near.iloc[pos]
        dist = float(row["green_dist_m"])
        proximity = max(0.0, 1.0 - dist / buffer_m)
        grade = None
        flat_factor = 1.0
        if grades is not None:
            gval = float(grades.iloc[pos])
            if gval == gval:  # not NaN
                grade = gval
                flat_factor = max(0.2, min(1.0, 1.0 - gval / max_grade))
        hw = row.get("_hw")
        score = proximity * flat_factor * highway_weight(hw)
        if score < min_score:
            continue

        attrs: dict[str, Any] = {
            "green_dist_m": round(dist, 1),
            "highway": hw,
            "street": row.get("_name") or "(unnamed)",
            "length_m": round(float(row.get("_len", row.geometry.length)), 1),
        }
        bits = [f"{round(proximity, 2)} green proximity"]
        if grade is not None:
            attrs["grade_pct"] = round(grade, 1)
            bits.append(f"{round(grade, 1)}% grade")
        opportunities.append(Opportunity(
            id=f"D10-{seq}",
            city=city,
            detector="D10",
            geometry=row.geometry,
            score=float(score),
            attributes=attrs,
            explanation=(
                f"Green-corridor opportunity ({', '.join(bits)}) with no cycle "
                f"lane — an attractive route to equip."
            ),
        ))
        seq += 1

    opportunities.sort(key=lambda o: o.score, reverse=True)
    opportunities = opportunities[:max_results]
    for rank, opp in enumerate(opportunities, start=1):
        opp.rank = rank
    return to_geodataframe(opportunities, crs=crs)
