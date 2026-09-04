"""D11 - Trip generators / school-route access detector.

Goal
----
Propose streets to equip so that major trip generators -- schools, universities,
hospitals -- have a safe cycle access. A generator whose surroundings have little
cycle provision, weighted by its importance and by the danger of the nearby
roads, yields those streets as proposals (safe routes to school, etc.).

Method (BicycleLane panorama, detector D11)
-------------------------------------------
For each generator ``p`` (weight ``e_p`` by type) with catchment ``buffer(r_acc)``:

* safe-access coverage ``q_p`` estimated in the catchment (skip well-served ones);
* every equippable, not-already-served road edge is a candidate, scored
  ``e_p * (1 - q_p) * highway_weight * (1 + beta*danger) * (0.5 + 0.5*proximity)``
  where ``danger`` flags fast roads (a school beside a fast road is a priority).

Enrolment weighting (Éducation nationale ``e_p``) is a planned refinement; here
``e_p`` is a per-type weight from :mod:`bicyclelane.places`.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd

from ..schema import Opportunity, to_geodataframe
from ._roadprop import (
    DANGER_CLASSES, build_candidate_edges, count_coverage, first_str, highway_weight,
)


def detect_generator_access(
    cycle_gdf: gpd.GeoDataFrame,
    road_gdf: gpd.GeoDataFrame,
    generators_gdf: gpd.GeoDataFrame,
    *,
    r_acc: float = 500.0,
    coverage_threshold: float = 0.6,
    exclude_buffer_m: float = 20.0,
    beta: float = 0.5,
    max_segments: int = 400,
    city: str = "",
    crs: Any = None,
) -> gpd.GeoDataFrame:
    """Detect generator-access proposals (detector D11). Returns ranked LineStrings."""
    work_crs = crs if crs is not None else road_gdf.crs
    if generators_gdf is None or len(generators_gdf) == 0:
        return to_geodataframe([], crs=work_crs)

    roads_full = road_gdf.to_crs(work_crs)
    cyc = cycle_gdf.to_crs(work_crs)
    road_sidx = roads_full.sindex
    cyc_sidx = cyc.sindex

    cand = build_candidate_edges(road_gdf, cycle_gdf, work_crs, exclude_buffer_m=exclude_buffer_m)
    if cand.empty:
        return to_geodataframe([], crs=work_crs)
    cand_sidx = cand.sindex
    geom = cand.geometry.values
    hw = cand["_hw"].to_numpy()
    name = cand["_name"].to_numpy()
    length = cand["_len"].to_numpy()

    gens = generators_gdf.to_crs(work_crs)
    best: dict[int, dict[str, Any]] = {}
    for _, gen in gens.iterrows():
        pt = gen.geometry
        if pt is None or pt.is_empty:
            continue
        e_p = float(gen.get("gen_weight", 1.0) or 1.0)
        gtype = first_str(gen.get("gen_type")) or "generator"
        catch = pt.buffer(r_acc)
        q_p, n_road = count_coverage(catch, cyc_sidx, road_sidx)
        if n_road == 0 or q_p >= coverage_threshold:
            continue
        deficit = 1.0 - q_p

        for pos in cand_sidx.query(catch, predicate="intersects"):
            dist = geom[pos].distance(pt)
            if dist > r_acc:
                continue
            hclass = hw[pos]
            danger = (1.0 + beta) if hclass in DANGER_CLASSES else 1.0
            score = (e_p * deficit * highway_weight(hclass) * danger
                     * (0.5 + 0.5 * (1.0 - dist / r_acc)))
            if score <= 0:
                continue
            prev = best.get(pos)
            if prev is None or score > prev["score"]:
                best[pos] = {
                    "score": score, "geometry": geom[pos], "street": name[pos],
                    "highway": hclass, "generator": gtype, "q_p": q_p,
                    "dist": dist, "danger": danger > 1.0, "length": float(length[pos]),
                }

    items = sorted(best.values(), key=lambda d: d["score"], reverse=True)[:max_segments]
    opportunities: list[Opportunity] = []
    for i, d in enumerate(items):
        opportunities.append(
            Opportunity(
                id=f"D11-{i:05d}", city=city, detector="D11",
                geometry=d["geometry"], score=float(d["score"]),
                attributes={
                    "street": d["street"] or "(unnamed)",
                    "highway": d["highway"] or "(none)",
                    "length_m": round(float(d["length"]), 1),
                    "generator": d["generator"], "gen_dist_m": round(float(d["dist"]), 1),
                    "safe_coverage": round(float(d["q_p"]), 3),
                    "near_fast_road": bool(d["danger"]),
                },
                explanation=(
                    f"Unsafe access to a {d['generator']}: equip "
                    f"{d['street'] or 'this street'} ({d['highway'] or 'road'}, "
                    f"{d['length']:.0f} m), {d['dist']:.0f} m from it "
                    f"(catchment safe coverage {d['q_p']:.0%}"
                    f"{'; near a fast road' if d['danger'] else ''})."
                ),
            )
        )
    for rank, opp in enumerate(opportunities, start=1):
        opp.rank = rank
    return to_geodataframe(opportunities, crs=work_crs)
