"""Offline sanity tests for D2 (route continuity) on synthetic corridors.

Geometry is in a planar metric CRS (metres), tagged EPSG:2154.
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from bicyclelane.detectors.continuity import (
    analyze_corridor,
    corridors_from_roads,
    detect_continuity_gaps,
)

CRS = "EPSG:2154"
TOL = 1e-6


def _corridor_setup():
    """A straight 1000 m 'Main St' corridor (built from two named road edges)
    with cycle coverage on [0, 300] and [600, 1000] -> one 300 m gap."""
    road = gpd.GeoDataFrame(
        {"name": ["Main St", "Main St"]},
        geometry=[
            LineString([(0, 0), (500, 0)]),
            LineString([(500, 0), (1000, 0)]),
        ],
        crs=CRS,
    )
    cycle = gpd.GeoDataFrame(
        {"kind": ["lane", "lane"]},
        geometry=[
            LineString([(0, 0), (300, 0)]),
            LineString([(600, 0), (1000, 0)]),
        ],
        crs=CRS,
    )
    return cycle, road


def test_corridor_merges_named_edges():
    _, road = _corridor_setup()
    corridors = corridors_from_roads(road)
    assert len(corridors) == 1
    name, centreline = corridors[0]
    assert name == "Main St"
    assert centreline.length == pytest.approx(1000.0)


def test_mass_balance_covered_plus_gaps_equals_length():
    cycle, road = _corridor_setup()
    _, centreline = corridors_from_roads(road)[0]
    report = analyze_corridor(centreline, cycle, buffer_m=15)

    total_gap = sum(g["length"] for g in report["gaps"])
    assert report["covered_len"] + total_gap == pytest.approx(
        report["corridor_len"], abs=1e-4
    )


def test_continuity_index_matches_expected():
    cycle, road = _corridor_setup()
    _, centreline = corridors_from_roads(road)[0]
    report = analyze_corridor(centreline, cycle, buffer_m=15)
    # covered = 300 + 400 = 700 over 1000 -> 0.7
    assert report["covered_len"] == pytest.approx(700.0, abs=1e-4)
    assert report["continuity_index"] == pytest.approx(0.7, abs=1e-4)


def test_returned_gaps_within_bounds():
    cycle, road = _corridor_setup()
    gdf = detect_continuity_gaps(
        cycle, road, d_min=30, d_max=500, buffer_m=15, city="Synth", crs=CRS
    )
    assert len(gdf) == 1
    assert (gdf["gap_len_m"] >= 30 - TOL).all()
    assert (gdf["gap_len_m"] <= 500 + TOL).all()
    assert (gdf["detector"] == "D2").all()
    assert gdf.crs == CRS


def test_score_formula():
    cycle, road = _corridor_setup()
    gdf = detect_continuity_gaps(cycle, road, d_min=30, d_max=500, buffer_m=15, crs=CRS)
    row = gdf.iloc[0]
    # S2 = (L_before + L_after) / |I| = (300 + 400) / 300
    assert row["gap_len_m"] == pytest.approx(300.0, abs=1e-4)
    assert row["len_before_m"] == pytest.approx(300.0, abs=1e-4)
    assert row["len_after_m"] == pytest.approx(400.0, abs=1e-4)
    assert row["score"] == pytest.approx(700.0 / 300.0, abs=1e-4)


def test_gap_outside_bounds_is_filtered():
    """Tighten d_max below the gap length -> nothing returned."""
    cycle, road = _corridor_setup()
    gdf = detect_continuity_gaps(cycle, road, d_min=30, d_max=200, buffer_m=15, crs=CRS)
    assert gdf.empty


def test_ranking_descending_with_two_gaps():
    """A corridor with two gaps of different flanking coverage ranks correctly."""
    road = gpd.GeoDataFrame(
        {"name": ["Rue A"]},
        geometry=[LineString([(0, 0), (1000, 0)])],
        crs=CRS,
    )
    # Coverage: [0,100], [200,250], [400,900]. Gaps: [100,200]=100 (before 100,
    # after 50), [250,400]=150 (before 50, after 500), trailing [900,1000]=100.
    cycle = gpd.GeoDataFrame(
        geometry=[
            LineString([(0, 0), (100, 0)]),
            LineString([(200, 0), (250, 0)]),
            LineString([(400, 0), (900, 0)]),
        ],
        crs=CRS,
    )
    gdf = detect_continuity_gaps(cycle, road, d_min=30, d_max=500, buffer_m=15, crs=CRS)
    scores = list(gdf["score"])
    assert scores == sorted(scores, reverse=True)
    assert list(gdf["rank"]) == list(range(1, len(gdf) + 1))
    # every returned gap within [30, 500]
    assert (gdf["gap_len_m"].between(30 - TOL, 500 + TOL)).all()


def test_uncovered_corridors_and_end_gaps_are_dropped():
    """Tightening: a street with no cycle provision is not a continuity gap, and
    a trailing uncovered end (one flank = 0) is not either."""
    road = gpd.GeoDataFrame(
        {"name": ["Rue Y", "Rue Z"]},
        geometry=[LineString([(0, 0), (1000, 0)]),
                  LineString([(0, 100), (1000, 100)])],
        crs=CRS,
    )
    # Rue Y: no coverage -> skipped. Rue Z: coverage only [0,300] -> the only
    # uncovered stretch is a trailing end (after = 0) -> dropped.
    cycle = gpd.GeoDataFrame(geometry=[LineString([(0, 100), (300, 100)])], crs=CRS)
    gdf = detect_continuity_gaps(cycle, road, d_min=30, d_max=1000, buffer_m=15, crs=CRS)
    assert gdf.empty


def test_tiny_endpoint_gap_is_snapped_into_one_corridor():
    """Two same-name edges with a 0.5 m endpoint gap should merge into a single
    ~1000 m corridor with default snap_tol_m, whereas without snapping (tol=0)
    they stay as two separate runs."""
    road = gpd.GeoDataFrame(
        {"name": ["Main St", "Main St"]},
        geometry=[
            LineString([(0, 0), (500, 0)]),
            LineString([(500.5, 0), (1000, 0)]),
        ],
        crs=CRS,
    )
    # Without snapping the 0.5 m gap is not stitched -> two runs.
    unsnapped = corridors_from_roads(road, snap_tol_m=0.0)
    assert len(unsnapped) == 2

    # With the default tolerance the gap is stitched -> one ~1000 m centreline.
    corridors = corridors_from_roads(road)
    assert len(corridors) == 1
    name, centreline = corridors[0]
    assert name == "Main St"
    assert centreline.length == pytest.approx(1000.0, abs=1e-6)


def test_large_gap_is_not_bridged():
    """A same-name street genuinely split by a big gap (e.g. a river) must still
    yield two corridors -- the small tolerance must not bridge it."""
    road = gpd.GeoDataFrame(
        {"name": ["River Rd", "River Rd"]},
        geometry=[
            LineString([(0, 0), (500, 0)]),
            LineString([(600, 0), (1000, 0)]),  # 100 m gap
        ],
        crs=CRS,
    )
    corridors = corridors_from_roads(road)
    assert len(corridors) == 2


def test_interior_gap_with_tiny_flank_is_dropped():
    """An interior gap flanked by only a 10 m cycle sliver is below min_flank_m."""
    road = gpd.GeoDataFrame(
        {"name": ["Rue W"]}, geometry=[LineString([(0, 0), (1000, 0)])], crs=CRS)
    cycle = gpd.GeoDataFrame(
        geometry=[LineString([(0, 0), (300, 0)]), LineString([(400, 0), (410, 0)])],
        crs=CRS,
    )
    # Gap [300,400]=100 m has flanks 300 m and 10 m; 10 < min_flank_m (20) -> dropped.
    gdf = detect_continuity_gaps(
        cycle, road, d_min=30, d_max=500, min_flank_m=20, buffer_m=15, crs=CRS)
    assert gdf.empty
