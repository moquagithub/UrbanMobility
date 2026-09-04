"""Offline sanity tests for D11 (trip generators / school access)."""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import LineString, Point

from bicyclelane.detectors.generators import detect_generator_access

CRS = "EPSG:2154"


def _far_cycle():
    return gpd.GeoDataFrame(geometry=[LineString([(5000, 5000), (5100, 5000)])], crs=CRS)


def _school():
    return gpd.GeoDataFrame(
        {"gen_type": ["school"], "gen_weight": [3.0]},
        geometry=[Point(500, 500)], crs=CRS)


def test_proposes_access_streets_near_generator():
    roads = gpd.GeoDataFrame(
        {"highway": ["residential", "residential"], "name": ["Rue A", "Rue Far"]},
        geometry=[LineString([(400, 500), (600, 500)]),
                  LineString([(3000, 3000), (3200, 3000)])],
        crs=CRS,
    )
    d11 = detect_generator_access(_far_cycle(), roads, _school(), r_acc=500, city="Synth", crs=CRS)

    assert len(d11) >= 1
    assert (d11["detector"] == "D11").all()
    row = d11.iloc[0]
    assert row.geometry.geom_type == "LineString"
    assert row["street"] == "Rue A"
    assert row["generator"] == "school"
    assert "Rue Far" not in set(d11["street"])


def test_fast_road_is_flagged_as_danger_and_boosts_score():
    # Same geometry, two runs: one residential, one primary (danger). The primary
    # one must score higher (danger multiplier) and be flagged near_fast_road.
    roads = gpd.GeoDataFrame(
        {"highway": ["primary"], "name": ["Avenue Rapide"]},
        geometry=[LineString([(400, 500), (600, 500)])], crs=CRS,
    )
    d11 = detect_generator_access(_far_cycle(), roads, _school(), r_acc=500, crs=CRS)
    assert len(d11) == 1
    assert bool(d11.iloc[0]["near_fast_road"]) is True


def test_motorway_excluded_and_empty_generators():
    roads = gpd.GeoDataFrame(
        {"highway": ["motorway"], "name": ["A51"]},
        geometry=[LineString([(400, 500), (600, 500)])], crs=CRS,
    )
    assert detect_generator_access(_far_cycle(), roads, _school(), r_acc=500, crs=CRS).empty
    empty = gpd.GeoDataFrame({"gen_type": [], "gen_weight": []},
                             geometry=[], crs=CRS)
    assert detect_generator_access(_far_cycle(), roads, empty, r_acc=500, crs=CRS).empty
