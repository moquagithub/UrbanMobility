"""Shared helpers for detectors that propose road segments to equip.

Used by the access-oriented detectors (D9 intermodality, D11 generators): given
an area with a cycling deficit, propose the equippable road edges inside it that
are not already served by cycle infrastructure.

Performance note: the expensive work (which edges are equippable and not already
served) is done **once** in :func:`build_candidate_edges`; the per-hub /
per-generator loop then only queries a spatial index and measures point
distances. Catchment coverage is estimated by an spatial-index **count** ratio,
which avoids re-clipping thousands of geometries per feature.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import pandas as pd

# Preference by road class (structural streets make better lane backbones);
# fast roads are excluded from proposals.
HIGHWAY_WEIGHTS = {
    "primary": 1.0, "primary_link": 0.9,
    "secondary": 0.9, "secondary_link": 0.8,
    "tertiary": 0.8, "tertiary_link": 0.7,
    "unclassified": 0.55, "residential": 0.5, "living_street": 0.45,
    "road": 0.4, "service": 0.2,
}
HIGHWAY_EXCLUDE = {"motorway", "motorway_link", "trunk", "trunk_link"}
DANGER_CLASSES = {"primary", "primary_link", "secondary", "secondary_link"}


def first_str(value):
    """Return the first string token of a (possibly list-valued) OSM tag, else None."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    return value if isinstance(value, str) else None


def highway_weight(hclass) -> float:
    return HIGHWAY_WEIGHTS.get(hclass, 0.3)


def _col(gdf: gpd.GeoDataFrame, name: str) -> pd.Series:
    if name in gdf.columns:
        return gdf[name]
    return pd.Series([None] * len(gdf), index=gdf.index)


def build_candidate_edges(
    road_gdf: gpd.GeoDataFrame,
    cycle_gdf: gpd.GeoDataFrame,
    work_crs: Any,
    *,
    exclude_buffer_m: float = 20.0,
) -> gpd.GeoDataFrame:
    """Equippable road edges NOT already served by cycle infrastructure.

    Columns added: ``_hw`` (highway class), ``_name`` (street), ``_len`` (m).
    Fast roads (motorway/trunk) are dropped; an edge more than half covered by
    the buffered cycle network is dropped. Computed once per analysis.
    """
    roads = road_gdf.to_crs(work_crs).reset_index(drop=True)
    roads = roads.assign(
        _hw=[first_str(v) for v in _col(roads, "highway")],
        _name=[first_str(v) for v in _col(roads, "name")],
    )
    roads["_len"] = roads.geometry.length
    equip = (~roads["_hw"].isin(HIGHWAY_EXCLUDE)) & roads.geometry.notna() & (roads["_len"] > 0)
    cand = roads[equip].reset_index(drop=True)
    if cand.empty:
        return cand

    served = cycle_gdf.to_crs(work_crs).buffer(exclude_buffer_m)
    if len(served) == 0:
        return cand

    ssindex = served.sindex
    keep = []
    for geom, length in zip(cand.geometry, cand["_len"]):
        hits = ssindex.query(geom, predicate="intersects")
        if len(hits) == 0:
            keep.append(True)
            continue
        near = served.iloc[hits].union_all()
        keep.append(geom.intersection(near).length <= 0.5 * length)
    return cand[pd.Series(keep, index=cand.index)].reset_index(drop=True)


def count_coverage(catch, cyc_sindex, road_sindex) -> tuple[float, int]:
    """Estimate cycle coverage in ``catch`` as the ratio of cycle to road
    features intersecting it (via spatial index counts). Returns (ratio, n_roads)."""
    n_road = len(road_sindex.query(catch, predicate="intersects"))
    if n_road == 0:
        return 1.0, 0
    n_cyc = len(cyc_sindex.query(catch, predicate="intersects"))
    return n_cyc / n_road, n_road
