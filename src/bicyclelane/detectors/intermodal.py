"""D9 - Intermodality / station access detector.

Goal
----
Propose streets to equip so that public-transport hubs (stations, tram/metro
stops, bus stations) are reachable by bike -- the "last kilometre" to transit.
A hub whose catchment has little cycle provision, and which sits among
equippable streets, yields those streets as proposals.

Method (BicycleLane panorama, detector D9)
------------------------------------------
For each hub ``h`` with catchment ``N(h) = buffer(r_acc)``:

* cycle coverage ``c_h`` estimated in ``N(h)`` (skip well-served hubs);
* every equippable, not-already-served road edge in ``N(h)`` is a candidate,
  scored ``(1 - c_h) * highway_weight * (0.5 + 0.5*proximity)`` where proximity
  falls with distance to the hub. Each street keeps its best score across hubs.

Ridership weighting (SNCF/GTFS ``f_h``) is a planned refinement; here every hub
counts equally.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd

from ..schema import Opportunity, to_geodataframe
from ._roadprop import build_candidate_edges, count_coverage, first_str, highway_weight


def detect_intermodal_access(
    cycle_gdf: gpd.GeoDataFrame,
    road_gdf: gpd.GeoDataFrame,
    hubs_gdf: gpd.GeoDataFrame,
    *,
    r_acc: float = 800.0,
    coverage_threshold: float = 0.5,
    exclude_buffer_m: float = 20.0,
    max_segments: int = 400,
    city: str = "",
    crs: Any = None,
) -> gpd.GeoDataFrame:
    """Detect station-access proposals (detector D9). Returns ranked LineStrings."""
    work_crs = crs if crs is not None else road_gdf.crs
    if hubs_gdf is None or len(hubs_gdf) == 0:
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

    hubs = hubs_gdf.to_crs(work_crs)
    best: dict[int, dict[str, Any]] = {}
    for _, hub in hubs.iterrows():
        pt = hub.geometry
        if pt is None or pt.is_empty:
            continue
        catch = pt.buffer(r_acc)
        c_h, n_road = count_coverage(catch, cyc_sidx, road_sidx)
        if n_road == 0 or c_h >= coverage_threshold:
            continue
        deficit = 1.0 - c_h
        hub_kind = first_str(hub.get("kind")) or "station"
        f_h = float(hub.get("hub_weight", 1.0) or 1.0)   # ridership proxy (S9 = f_h·(1−c_h)…)

        for pos in cand_sidx.query(catch, predicate="intersects"):
            dist = geom[pos].distance(pt)
            if dist > r_acc:
                continue
            score = f_h * deficit * highway_weight(hw[pos]) * (0.5 + 0.5 * (1.0 - dist / r_acc))
            if score <= 0:
                continue
            prev = best.get(pos)
            if prev is None or score > prev["score"]:
                best[pos] = {
                    "score": score, "geometry": geom[pos], "street": name[pos],
                    "highway": hw[pos], "hub": hub_kind, "hub_weight": f_h,
                    "dist": dist, "coverage": c_h, "length": float(length[pos]),
                }

    items = sorted(best.values(), key=lambda d: d["score"], reverse=True)[:max_segments]
    opportunities: list[Opportunity] = []
    for i, d in enumerate(items):
        opportunities.append(
            Opportunity(
                id=f"D9-{i:05d}", city=city, detector="D9",
                geometry=d["geometry"], score=float(d["score"]),
                attributes={
                    "street": d["street"] or "(unnamed)",
                    "highway": d["highway"] or "(none)",
                    "length_m": round(float(d["length"]), 1),
                    "hub": d["hub"], "hub_weight": round(float(d["hub_weight"]), 2),
                    "hub_dist_m": round(float(d["dist"]), 1),
                    "coverage": round(float(d["coverage"]), 3),
                },
                explanation=(
                    f"Access to a {d['hub']} lacking cycle provision: equip "
                    f"{d['street'] or 'this street'} ({d['highway'] or 'road'}, "
                    f"{d['length']:.0f} m), {d['dist']:.0f} m from the hub "
                    f"(catchment cycle coverage {d['coverage']:.0%})."
                ),
            )
        )
    for rank, opp in enumerate(opportunities, start=1):
        opp.rank = rank
    return to_geodataframe(opportunities, crs=work_crs)
