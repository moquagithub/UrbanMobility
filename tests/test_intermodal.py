"""Offline sanity tests for D9 (intermodality / station access)."""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import LineString, Point

from bicyclelane.detectors.intermodal import detect_intermodal_access

CRS = "EPSG:2154"


def _far_cycle():
    return gpd.GeoDataFrame(geometry=[LineString([(5000, 5000), (5100, 5000)])], crs=CRS)


def test_proposes_streets_near_underserved_hub():
    hubs = gpd.GeoDataFrame({"kind": ["station"]}, geometry=[Point(500, 500)], crs=CRS)
    roads = gpd.GeoDataFrame(
        {"highway": ["residential", "residential"], "name": ["Rue A", "Rue Far"]},
        geometry=[LineString([(400, 500), (600, 500)]),       # ~near the hub
                  LineString([(3000, 3000), (3200, 3000)])],  # outside r_acc
        crs=CRS,
    )
    d9 = detect_intermodal_access(_far_cycle(), roads, hubs, r_acc=800, city="Synth", crs=CRS)

    assert len(d9) >= 1
    assert (d9["detector"] == "D9").all()
    streets = set(d9["street"])
    assert "Rue A" in streets
    assert "Rue Far" not in streets           # beyond the catchment
    assert d9.iloc[0].geometry.geom_type == "LineString"


def test_fast_roads_are_excluded():
    hubs = gpd.GeoDataFrame({"kind": ["station"]}, geometry=[Point(500, 500)], crs=CRS)
    roads = gpd.GeoDataFrame(
        {"highway": ["motorway"], "name": ["A51"]},
        geometry=[LineString([(400, 500), (600, 500)])], crs=CRS,
    )
    d9 = detect_intermodal_access(_far_cycle(), roads, hubs, r_acc=800, crs=CRS)
    assert d9.empty


def test_well_served_hub_yields_nothing():
    hubs = gpd.GeoDataFrame({"kind": ["station"]}, geometry=[Point(500, 500)], crs=CRS)
    road_geom = LineString([(400, 500), (600, 500)])
    roads = gpd.GeoDataFrame({"highway": ["residential"], "name": ["Rue A"]},
                             geometry=[road_geom], crs=CRS)
    # Cycle lane covering the same street -> catchment coverage high -> skip hub.
    cycle = gpd.GeoDataFrame(geometry=[road_geom], crs=CRS)
    d9 = detect_intermodal_access(cycle, roads, hubs, r_acc=800,
                                  coverage_threshold=0.5, crs=CRS)
    assert d9.empty
