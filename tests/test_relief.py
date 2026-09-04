"""Offline sanity tests for D10 (relief & green corridors)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon

from bicyclelane.detectors.relief import detect_green_corridors
from bicyclelane.terrain import CallableSampler

CRS = "EPSG:2154"


def _roads(lines, hw="tertiary"):
    return gpd.GeoDataFrame(
        {"highway": [hw] * len(lines), "name": ["Rue"] * len(lines)},
        geometry=[LineString(c) for c in lines], crs=CRS)


def _far_cycle():
    return gpd.GeoDataFrame(geometry=[LineString([(9000, 9000), (9100, 9000)])], crs=CRS)


def _park(xmin, ymin, xmax, ymax):
    return gpd.GeoDataFrame(geometry=[Polygon(
        [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)])], crs=CRS)


def test_edge_next_to_green_is_kept_and_far_edge_dropped():
    roads = _roads([
        [(0, 5), (200, 5)],     # 5 m from the park edge (y=0..-... below) -> near
        [(0, 500), (200, 500)],  # far from any green
    ])
    park = _park(0, -50, 200, 0)  # park just south of the first road
    out = detect_green_corridors(roads, _far_cycle(), park, city="S", crs=CRS,
                                 buffer_m=80)
    assert (out["detector"] == "D10").all()
    assert len(out) == 1
    assert out.iloc[0]["green_dist_m"] <= 80


def test_no_green_layer_returns_empty():
    roads = _roads([[(0, 0), (200, 0)]])
    out = detect_green_corridors(roads, _far_cycle(), None, city="S", crs=CRS)
    assert len(out) == 0
    empty = gpd.GeoDataFrame(geometry=[], crs=CRS)
    assert len(detect_green_corridors(roads, _far_cycle(), empty, crs=CRS)) == 0


def test_flat_corridor_beats_steep_one_with_dem():
    # Two identical green-adjacent roads; a DEM makes the second one steep.
    roads = _roads([[(0, 5), (200, 5)], [(0, 15), (200, 15)]])
    park = _park(0, -50, 200, 20)  # covers both roads' vicinity
    # Elevation flat along the first road (y=5), steep along the second (y=15):
    sampler = CallableSampler(lambda x, y: 0.0 if y < 10 else 0.10 * x)  # 10% grade
    out = detect_green_corridors(roads, _far_cycle(), park, crs=CRS,
                                 sampler=sampler, buffer_m=80)
    assert len(out) == 2
    # the flat one (grade ~0) must rank above the steep one (grade ~10%)
    top = out.iloc[0]
    assert top["grade_pct"] == pytest.approx(0.0, abs=1e-6)
    steep = out[out["grade_pct"] > 5].iloc[0]
    assert top["score"] > steep["score"]


def test_degrades_without_dem_no_grade_column_needed():
    roads = _roads([[(0, 5), (200, 5)]])
    park = _park(0, -50, 200, 0)
    out = detect_green_corridors(roads, _far_cycle(), park, crs=CRS)  # no sampler
    assert len(out) == 1
    # green proximity only; grade attribute absent is fine
    assert out.iloc[0]["green_dist_m"] <= 80
