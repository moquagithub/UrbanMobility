"""D8 - Comfort / experience detector (BL-25).

Goal
----
Rank streets where cycling is currently **uncomfortable or stressful** — high
motor-traffic speed, poor surface, no lighting — and which are not yet served by
a cycle lane, so a dedicated lane would most improve the experience. Supply-side:
it reads OSM tags already fetched with the network (via
:func:`bicyclelane.tags.edge_tag_features`), no external data.

Score
-----
For each equippable road edge (not a fast road, not already served — see
:func:`bicyclelane.detectors._roadprop.build_candidate_edges`) a comfort deficit
in ``[0, 1]`` is combined from available tag signals:

    deficit = 0.60·speed_stress + 0.25·surface_penalty + 0.15·light_penalty

* ``speed_stress`` — from ``maxspeed`` (30 km/h calm → 70+ stressful); when the
  tag is missing it falls back to a per-highway-class stress proxy (arterials
  stressful, residential calm), so the detector still works with sparse tags.
* ``surface_penalty`` — ``1 − surface_q`` when a surface tag is present, else 0.
* ``light_penalty`` — 1 when explicitly unlit, else 0.

The opportunity score is ``deficit · highway_weight`` (structural streets make
better lane backbones). Tag completeness (T1) is a known limitation.
"""

from __future__ import annotations

from math import isnan
from typing import Any

import geopandas as gpd

from ..schema import Opportunity, to_geodataframe
from ..tags import edge_tag_features
from ._roadprop import build_candidate_edges, highway_weight

# Traffic-stress proxy by highway class, used when ``maxspeed`` is absent.
STRESS_BY_HW: dict[str, float] = {
    "primary": 0.9, "primary_link": 0.85, "secondary": 0.7, "secondary_link": 0.65,
    "tertiary": 0.5, "tertiary_link": 0.45, "unclassified": 0.35,
    "residential": 0.2, "living_street": 0.05, "road": 0.4, "service": 0.1,
}

_W_SPEED, _W_SURFACE, _W_LIGHT = 0.60, 0.25, 0.15


def _present(value: Any) -> bool:
    return value is not None and not (isinstance(value, float) and isnan(value))


def _speed_stress(maxspeed_kmh: Any, hw: Any) -> float:
    if _present(maxspeed_kmh):
        return max(0.0, min(1.0, (float(maxspeed_kmh) - 30.0) / 40.0))
    return STRESS_BY_HW.get(hw, 0.3)


def detect_comfort_deficit(
    road_gdf: gpd.GeoDataFrame,
    cycle_gdf: gpd.GeoDataFrame,
    *,
    city: str = "",
    crs: Any = None,
    min_score: float = 0.15,
    max_results: int = 500,
) -> gpd.GeoDataFrame:
    """Rank equippable streets by comfort deficit (``detector == "D8"``).

    Returns proposed lane segments (road edges) with ``score`` = deficit ·
    highway_weight and ``comfort_deficit`` / ``speed_stress`` / ``surface_q`` /
    ``lit`` / ``maxspeed_kmh`` attributes. Empty when nothing clears ``min_score``.
    """
    work_crs = crs if crs is not None else road_gdf.crs
    cand = build_candidate_edges(road_gdf, cycle_gdf, work_crs)
    if cand is None or cand.empty:
        return to_geodataframe([], crs=crs)

    feats = edge_tag_features(cand)
    # Column-wise Python lists: a row view (feats.iloc[i]) would coerce the mixed
    # dtypes to a common type and turn bool `lit` into a value where `is False`
    # fails — so read each feature column as native Python values.
    ms_list = feats["maxspeed_kmh"].tolist()
    lit_list = feats["lit"].tolist()
    surf_list = feats["surface_q"].tolist()

    opportunities: list[Opportunity] = []
    seq = 0
    for i in range(len(cand)):
        row = cand.iloc[i]
        hw = row.get("_hw")
        ms, lit, sq = ms_list[i], lit_list[i], surf_list[i]
        ss = _speed_stress(ms, hw)
        surf_pen = (1.0 - float(sq)) if _present(sq) else 0.0
        light_pen = 1.0 if lit is False else 0.0
        deficit = _W_SPEED * ss + _W_SURFACE * surf_pen + _W_LIGHT * light_pen
        score = deficit * highway_weight(hw)
        if score < min_score:
            continue

        maxspeed = ms if _present(ms) else None
        surface_q = round(float(sq), 3) if _present(sq) else None
        bits = [f"traffic-stress {ss:.2f}"]
        if surface_q is not None:
            bits.append(f"surface quality {surface_q:.2f}")
        if lit is False:
            bits.append("unlit")
        opportunities.append(Opportunity(
            id=f"D8-{seq}",
            city=city,
            detector="D8",
            geometry=row.geometry,
            score=float(score),
            attributes={
                "comfort_deficit": round(float(deficit), 3),
                "speed_stress": round(float(ss), 3),
                "surface_q": surface_q,
                "lit": lit,
                "maxspeed_kmh": round(float(maxspeed), 1) if maxspeed is not None else None,
                "highway": hw,
                "street": row.get("_name") or "(unnamed)",
                "length_m": round(float(row.get("_len", row.geometry.length)), 1),
            },
            explanation=(
                f"Uncomfortable to cycle ({', '.join(bits)}) with no dedicated "
                f"lane — a protected lane here would improve the experience."
            ),
        ))
        seq += 1

    opportunities.sort(key=lambda o: o.score, reverse=True)
    opportunities = opportunities[:max_results]
    for rank, opp in enumerate(opportunities, start=1):
        opp.rank = rank
    return to_geodataframe(opportunities, crs=crs)
