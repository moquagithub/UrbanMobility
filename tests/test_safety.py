"""Offline sanity tests for D7 (safety / crashes)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from bicyclelane.detectors.safety import detect_safety_hotspots

CRS = "EPSG:2154"


def _roads(lines, hw="secondary"):
    return gpd.GeoDataFrame(
        {"highway": [hw] * len(lines), "name": ["Rue"] * len(lines)},
        geometry=[LineString(c) for c in lines], crs=CRS)


def _far_cycle():
    return gpd.GeoDataFrame(geometry=[LineString([(9000, 9000), (9100, 9000)])], crs=CRS)


def _crashes(points):
    return gpd.GeoDataFrame(
        {"severity": [s for *_, s in points]},
        geometry=[Point(x, y) for x, y, _ in points], crs=CRS)


def test_crashes_on_a_street_make_a_hotspot():
    roads = _roads([[(0, 0), (200, 0)], [(0, 500), (200, 500)]])
    crashes = _crashes([(50, 3, 1.0), (150, 4, 0.6)])  # both near road 0
    out = detect_safety_hotspots(roads, _far_cycle(), crashes, city="S", crs=CRS,
                                 match_tol_m=25)
    assert (out["detector"] == "D7").all()
    assert len(out) == 1
    assert out.iloc[0]["n_crashes"] == 2
    assert out.iloc[0]["crash_severity"] == pytest.approx(1.6)


def test_crash_beyond_tolerance_is_unmatched():
    roads = _roads([[(0, 0), (200, 0)]])
    crashes = _crashes([(100, 100, 1.0)])  # 100 m away -> beyond 25 m tol
    out = detect_safety_hotspots(roads, _far_cycle(), crashes, crs=CRS, match_tol_m=25)
    assert len(out) == 0


def test_no_crash_layer_returns_empty():
    roads = _roads([[(0, 0), (200, 0)]])
    assert len(detect_safety_hotspots(roads, _far_cycle(), None, crs=CRS)) == 0
    empty = gpd.GeoDataFrame({"severity": []}, geometry=[], crs=CRS)
    assert len(detect_safety_hotspots(roads, _far_cycle(), empty, crs=CRS)) == 0


def test_more_severe_street_scores_higher():
    roads = _roads([[(0, 0), (200, 0)], [(0, 300), (200, 300)]])
    crashes = _crashes([(100, 3, 1.0), (100, 303, 0.3)])  # road 0 severe, road 1 light
    out = detect_safety_hotspots(roads, _far_cycle(), crashes, crs=CRS,
                                 match_tol_m=25, min_score=0.0)
    assert len(out) == 2
    assert out.iloc[0]["crash_severity"] == pytest.approx(1.0)  # severe ranks first
