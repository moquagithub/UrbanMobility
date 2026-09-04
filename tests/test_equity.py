"""Offline sanity tests for D6 (accessibility & equity)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon, box

from bicyclelane.detectors.equity import detect_equity_gaps

CRS = "EPSG:2154"


def _boundary():
    return gpd.GeoDataFrame(geometry=[box(0, 0, 1000, 1000)], crs=CRS)


def _deprivation():
    # Left half deprived (0.40 poverty share), right half well-off (0.05).
    return gpd.GeoDataFrame(
        {"deprivation": [0.40, 0.05]},
        geometry=[box(0, 0, 500, 1000), box(500, 0, 1000, 1000)], crs=CRS)


def test_deprived_unserved_zone_ranks_first():
    # Cycle lane only on the right (well-off) side -> left is deprived & unserved.
    cycle = gpd.GeoDataFrame(geometry=[LineString([(750, 50), (750, 950)])], crs=CRS)
    out = detect_equity_gaps(cycle, _deprivation(), _boundary(),
                             city="S", crs=CRS, cell_size=500)
    assert (out["detector"] == "D6").all()
    assert len(out) >= 1
    top = out.iloc[0]
    assert top["deprivation"] == pytest.approx(0.40)      # the deprived side
    assert top.geometry.centroid.x < 500                  # left half
    assert top["residual"] > 0


def test_no_deprivation_layer_returns_empty():
    cycle = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (100, 0)])], crs=CRS)
    assert len(detect_equity_gaps(cycle, None, _boundary(), crs=CRS)) == 0
    empty = gpd.GeoDataFrame({"deprivation": []}, geometry=[], crs=CRS)
    assert len(detect_equity_gaps(cycle, empty, _boundary(), crs=CRS)) == 0


def test_min_residual_filters_well_served_zones():
    # A very high threshold keeps nothing.
    cycle = gpd.GeoDataFrame(geometry=[LineString([(750, 50), (750, 950)])], crs=CRS)
    out = detect_equity_gaps(cycle, _deprivation(), _boundary(),
                             city="S", crs=CRS, cell_size=500, min_residual=99.0)
    assert len(out) == 0
