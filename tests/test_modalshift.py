"""Offline sanity tests for D14 (modal shift)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from bicyclelane.detectors.modalshift import detect_modal_shift

CRS = "EPSG:2154"


def _roads(lines, hw="secondary"):
    return gpd.GeoDataFrame(
        {"highway": [hw] * len(lines), "name": ["Rue"] * len(lines)},
        geometry=[LineString(c) for c in lines], crs=CRS)


def _far_cycle():
    return gpd.GeoDataFrame(geometry=[LineString([(9000, 9000), (9100, 9000)])], crs=CRS)


def _od(lines_flows):
    return gpd.GeoDataFrame(
        {"flow": [f for _, f in lines_flows]},
        geometry=[LineString(c) for c, _ in lines_flows], crs=CRS)


def test_corridor_with_car_flow_is_scored():
    roads = _roads([[(0, 0), (200, 0)], [(0, 500), (200, 500)]])
    od = _od([([(10, 2), (190, 2)], 200.0)])  # a desire line along road 0
    out = detect_modal_shift(roads, _far_cycle(), od, city="S", crs=CRS, buffer_m=50)
    assert (out["detector"] == "D14").all()
    assert len(out) == 1
    assert out.iloc[0]["shift_flow"] == pytest.approx(200.0)


def test_road_without_flow_is_excluded():
    roads = _roads([[(0, 0), (200, 0)], [(0, 500), (200, 500)]])
    od = _od([([(10, 2), (190, 2)], 200.0)])  # only near road 0
    out = detect_modal_shift(roads, _far_cycle(), od, crs=CRS, buffer_m=50)
    streets = set(out["street"]) if len(out) else set()
    assert len(out) == 1  # road 1 (no flow nearby) is not proposed


def test_no_od_layer_returns_empty():
    roads = _roads([[(0, 0), (200, 0)]])
    assert len(detect_modal_shift(roads, _far_cycle(), None, crs=CRS)) == 0
    empty = gpd.GeoDataFrame({"flow": []}, geometry=[], crs=CRS)
    assert len(detect_modal_shift(roads, _far_cycle(), empty, crs=CRS)) == 0


def test_flows_accumulate_and_min_score_filters():
    roads = _roads([[(0, 0), (200, 0)]])
    od = _od([([(10, 2), (100, 2)], 1.0), ([(100, 2), (190, 2)], 1.0)])
    # two low-flow lines over the same edge -> flow 2 * highway_weight(0.9)=1.8
    out = detect_modal_shift(roads, _far_cycle(), od, crs=CRS, buffer_m=50,
                             min_score=1.5)
    assert len(out) == 1
    assert out.iloc[0]["shift_flow"] == pytest.approx(2.0)
