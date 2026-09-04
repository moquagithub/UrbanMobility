"""Offline tests for enriched OSM tag parsing (Foundation v2a, BL-23)."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

from bicyclelane.tags import (
    edge_tag_features,
    parse_lit,
    parse_maxspeed,
    smoothness_quality,
    surface_quality,
)


@pytest.mark.parametrize("value, expected", [
    ("30", 30.0),
    (50, 50.0),
    ("50 km/h", 50.0),
    ("FR:urban", 50.0),
    ("FR:zone30", 30.0),
    ("walk", 6.0),
    ("none", None),
    ("", None),
    ("blah", None),
])
def test_parse_maxspeed_scalar(value, expected):
    assert parse_maxspeed(value) == expected


def test_parse_maxspeed_mph_converted():
    assert parse_maxspeed("30 mph") == pytest.approx(48.28, abs=0.05)


def test_parse_maxspeed_list_takes_strictest():
    # list -> the max numeric (conservative for a comfort penalty)
    assert parse_maxspeed(["30", "50"]) == 50.0
    assert parse_maxspeed(["none", "signals"]) is None


@pytest.mark.parametrize("value, expected", [
    ("yes", True), ("no", False), ("24/7", True), ("automatic", True),
    (["yes"], True), (None, None), ("maybe", None),
])
def test_parse_lit(value, expected):
    assert parse_lit(value) is expected


def test_surface_quality_orders_and_unknown():
    assert surface_quality("asphalt") == pytest.approx(1.0)
    assert surface_quality("gravel") == pytest.approx(0.4)
    assert surface_quality("asphalt") > surface_quality("gravel")
    assert surface_quality("ASPHALT") == pytest.approx(1.0)   # case-insensitive
    assert surface_quality(["asphalt"]) == pytest.approx(1.0)  # list-valued
    assert surface_quality("unobtanium") is None


def test_smoothness_quality():
    assert smoothness_quality("excellent") == pytest.approx(1.0)
    assert smoothness_quality("very_bad") == pytest.approx(0.25)
    assert smoothness_quality("nope") is None


def test_edge_tag_features_parses_and_aligns():
    edges = gpd.GeoDataFrame(
        {
            "maxspeed": ["30", "50 mph", None],
            "lit": ["yes", "no", None],
            "surface": ["asphalt", "gravel", None],
            "smoothness": ["good", None, "bad"],
        },
        geometry=[LineString([(0, 0), (1, 0)])] * 3,
        crs="EPSG:2154",
    )
    feats = edge_tag_features(edges)
    assert list(feats.index) == list(edges.index)
    assert feats["maxspeed_kmh"].iloc[0] == pytest.approx(30.0)
    assert feats["maxspeed_kmh"].iloc[1] == pytest.approx(80.47, abs=0.1)
    assert pd.isna(feats["maxspeed_kmh"].iloc[2])  # missing -> NaN in a float column
    assert feats["lit"].tolist() == [True, False, None]
    assert feats["surface_q"].iloc[0] == pytest.approx(1.0)
    assert feats["smoothness_q"].iloc[2] == pytest.approx(0.4)


def test_edge_tag_features_missing_columns_are_safe():
    # An edge frame with no comfort tag columns at all must not raise.
    edges = gpd.GeoDataFrame(
        {"highway": ["residential", "primary"]},
        geometry=[LineString([(0, 0), (1, 0)])] * 2,
        crs="EPSG:2154",
    )
    feats = edge_tag_features(edges)
    assert feats.shape == (2, 4)
    assert feats["maxspeed_kmh"].isna().all()
