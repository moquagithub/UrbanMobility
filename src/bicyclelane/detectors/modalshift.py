"""D14 - Modal shift detector (BL-31).

Goal
----
Rank **corridors carrying many short car trips** (typically < 5 km) that could
shift to cycling if a lane existed. Uses an OD desire-line layer with a car-trip
``flow`` (:mod:`bicyclelane.mobility`, opt-in via ``BICYCLELANE_OD``); empty when
no OD layer is available.

Score
-----
Each OD desire line's ``flow`` is deposited on the equippable road edges (not
fast, not already served) that fall within ``buffer_m`` of it; an edge's score
is the accumulated shiftable flow times its ``highway_weight``.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd

from ..schema import Opportunity, to_geodataframe
from ._roadprop import build_candidate_edges, highway_weight


def detect_modal_shift(
    road_gdf: gpd.GeoDataFrame,
    cycle_gdf: gpd.GeoDataFrame,
    od_gdf: gpd.GeoDataFrame | None,
    *,
    city: str = "",
    crs: Any = None,
    road_graph=None,
    buffer_m: float = 100.0,
    min_score: float = 0.5,
    max_results: int = 500,
) -> gpd.GeoDataFrame:
    """Rank modal-shift corridors (``detector == "D14"``).

    ``od_gdf`` are OD desire lines with a ``flow`` column (see
    :func:`bicyclelane.mobility.load_od_csv`). When ``road_graph`` is given the
    desire lines are first **routed on the road network** (see
    :func:`bicyclelane.mobility.route_od`) so coarse commune-to-commune lines
    follow real streets; ``buffer_m`` then narrows to a street-width match.
    Returns proposed road edges with ``score`` = accumulated shiftable flow ·
    highway_weight and ``shift_flow``. Empty when there is no OD layer or nothing
    clears ``min_score``.
    """
    work_crs = crs if crs is not None else road_gdf.crs
    cand = build_candidate_edges(road_gdf, cycle_gdf, work_crs)
    if (cand is None or cand.empty or od_gdf is None or len(od_gdf) == 0
            or "flow" not in od_gdf):
        return to_geodataframe([], crs=crs)

    if road_graph is not None:
        from ..mobility import route_od
        od_gdf = route_od(od_gdf, road_graph)
        buffer_m = min(buffer_m, 30.0)  # routed corridors follow streets closely
        if od_gdf is None or len(od_gdf) == 0:
            return to_geodataframe([], crs=crs)

    od = od_gdf.to_crs(work_crs) if od_gdf.crs != work_crs else od_gdf
    od = od[od.geometry.notna() & ~od.geometry.is_empty]
    if od.empty:
        return to_geodataframe([], crs=crs)

    cand = cand.reset_index(drop=True)
    # Buffer each desire line and attach its flow to the candidate edges it covers.
    corridors = od.copy()
    corridors["geometry"] = od.buffer(buffer_m)
    joined = gpd.sjoin(cand[["geometry"]], corridors[["flow", "geometry"]],
                       predicate="intersects", how="inner")
    if joined.empty:
        return to_geodataframe([], crs=crs)
    flow_by_edge = joined.groupby(joined.index)["flow"].sum()

    opportunities: list[Opportunity] = []
    seq = 0
    for edge_pos, flow in flow_by_edge.items():
        row = cand.iloc[int(edge_pos)]
        hw = row.get("_hw")
        score = float(flow) * highway_weight(hw)
        if score < min_score:
            continue
        opportunities.append(Opportunity(
            id=f"D14-{seq}",
            city=city,
            detector="D14",
            geometry=row.geometry,
            score=float(score),
            attributes={
                "shift_flow": round(float(flow), 1),
                "highway": hw,
                "street": row.get("_name") or "(unnamed)",
                "length_m": round(float(row.get("_len", row.geometry.length)), 1),
            },
            explanation=(
                f"Modal-shift corridor: ~{round(float(flow))} short car trips "
                f"pass here with no cycle lane — strong candidate to equip."
            ),
        ))
        seq += 1

    opportunities.sort(key=lambda o: o.score, reverse=True)
    opportunities = opportunities[:max_results]
    for rank, opp in enumerate(opportunities, start=1):
        opp.rank = rank
    return to_geodataframe(opportunities, crs=crs)
