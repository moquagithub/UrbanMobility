"""Offline sanity tests for D5 (demand/supply mismatch) on a synthetic grid.

Coordinates are planar metres (EPSG:2154). A 1000x1000 m boundary splits into
four 500 m cells. Supply (a cycle lane) sits in the bottom-left cell; demand
(POIs) sits in the top-right cell -- so the top-right cell is the mismatch.
"""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon

from bicyclelane.detectors.mismatch import (
    detect_demand_supply_mismatch,
    detect_mismatch_segments,
)

CRS = "EPSG:2154"


def _inputs():
    boundary = gpd.GeoDataFrame(
        geometry=[Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)])], crs=CRS)
    # Supply: a 400 m lane in the bottom-left cell.
    cycle = gpd.GeoDataFrame(
        geometry=[LineString([(50, 50), (450, 50)])], crs=CRS)
    # Demand: two weighted POIs in the top-right cell.
    pois = gpd.GeoDataFrame(
        {"poi_weight": [3.0, 3.0]},
        geometry=[Point(700, 700), Point(800, 800)], crs=CRS)
    return cycle, pois, boundary


def test_mismatch_flags_high_demand_low_supply_cell():
    cycle, pois, boundary = _inputs()
    d5 = detect_demand_supply_mismatch(cycle, pois, boundary, cell_size=500, crs=CRS)

    assert len(d5) >= 1
    top = d5.iloc[0]
    assert top["detector"] == "D5"
    assert top.geometry.geom_type == "Polygon"
    # The winning cell carries demand but essentially no supply.
    assert top["demand"] > 0
    assert top["supply"] == 0
    assert top["residual"] > 0
    # Its centroid is in the top-right quadrant.
    assert top.geometry.centroid.x > 500 and top.geometry.centroid.y > 500


def test_only_cells_with_demand_are_returned():
    cycle, pois, boundary = _inputs()
    d5 = detect_demand_supply_mismatch(cycle, pois, boundary, cell_size=500, crs=CRS)
    # Cells with no demand (incl. the supplied bottom-left cell) are excluded.
    assert (d5["demand"] > 0).all()


def test_ranking_is_by_residual_descending():
    cycle, pois, boundary = _inputs()
    d5 = detect_demand_supply_mismatch(cycle, pois, boundary, cell_size=500, crs=CRS)
    scores = list(d5["score"])
    assert scores == sorted(scores, reverse=True)
    assert list(d5["rank"]) == list(range(1, len(d5) + 1))


def test_segments_proposes_streets_inside_the_mismatch_zone():
    cycle, pois, boundary = _inputs()
    # Road A sits in the high-demand top-right cell; road B in the bottom-left
    # (supplied, no-demand) cell -> only A should be proposed.
    roads = gpd.GeoDataFrame(
        {"highway": ["residential", "residential"], "name": ["Rue A", "Rue B"]},
        geometry=[LineString([(600, 600), (900, 600)]),
                  LineString([(100, 100), (400, 100)])],
        crs=CRS,
    )
    seg = detect_mismatch_segments(cycle, roads, pois, boundary, cell_size=500, crs=CRS)

    assert len(seg) == 1
    row = seg.iloc[0]
    assert row["detector"] == "D5"
    assert row.geometry.geom_type == "LineString"
    assert row["street"] == "Rue A"            # the proposed lane is a real street
    assert row["highway"] == "residential"
    assert row.geometry.centroid.x > 500       # inside the top-right mismatch cell


def test_segments_excludes_fast_roads():
    cycle, pois, boundary = _inputs()
    roads = gpd.GeoDataFrame(
        {"highway": ["motorway"], "name": ["A51"]},
        geometry=[LineString([(600, 600), (900, 600)])], crs=CRS,
    )
    seg = detect_mismatch_segments(cycle, roads, pois, boundary, cell_size=500, crs=CRS)
    assert seg.empty                            # motorways are never proposed
