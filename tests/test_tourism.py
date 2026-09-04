"""Offline sanity tests for D12 (tourism & leisure corridors)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from bicyclelane.detectors.tourism import detect_tourism_corridors

CRS = "EPSG:2154"


def _roads(lines, hw="tertiary"):
    return gpd.GeoDataFrame(
        {"highway": [hw] * len(lines), "name": ["Rue"] * len(lines)},
        geometry=[LineString(c) for c in lines], crs=CRS)


def _far_cycle():
    return gpd.GeoDataFrame(geometry=[LineString([(9000, 9000), (9100, 9000)])], crs=CRS)


def _tourism(points):
    """points: list of (x, y, weight)."""
    return gpd.GeoDataFrame(
        {"tour_type": ["attraction"] * len(points),
         "tour_weight": [w for *_, w in points]},
        geometry=[Point(x, y) for x, y, _ in points], crs=CRS)


def test_edge_near_attractors_kept_far_dropped():
    roads = _roads([[(0, 0), (200, 0)], [(0, 500), (200, 500)]])
    tour = _tourism([(100, 10, 3.0)])  # next to the first road only
    out = detect_tourism_corridors(roads, _far_cycle(), tour, city="S", crs=CRS,
                                   buffer_m=150)
    assert (out["detector"] == "D12").all()
    assert len(out) == 1
    assert out.iloc[0]["n_attractors"] == 1


def test_more_pull_scores_higher():
    roads = _roads([[(0, 0), (200, 0)], [(0, 300), (200, 300)]])
    tour = _tourism([
        (100, 10, 3.0), (120, 10, 3.0),   # two strong attractors near road 0
        (100, 310, 1.0),                   # one weak attractor near road 1
    ])
    out = detect_tourism_corridors(roads, _far_cycle(), tour, city="S", crs=CRS,
                                   buffer_m=150)
    assert len(out) == 2
    top = out.iloc[0]
    assert top["attractor_pull"] == pytest.approx(6.0)  # 3+3 wins over 1


def test_no_tourism_layer_returns_empty():
    roads = _roads([[(0, 0), (200, 0)]])
    assert len(detect_tourism_corridors(roads, _far_cycle(), None, crs=CRS)) == 0
    empty = gpd.GeoDataFrame({"tour_weight": []}, geometry=[], crs=CRS)
    assert len(detect_tourism_corridors(roads, _far_cycle(), empty, crs=CRS)) == 0


def test_already_served_excluded():
    roads = _roads([[(0, 0), (200, 0)]])
    served = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (200, 0)])], crs=CRS)
    tour = _tourism([(100, 10, 3.0)])
    out = detect_tourism_corridors(roads, served, tour, crs=CRS)
    assert len(out) == 0
