"""Offline sanity tests for D4 (coverage / density cold-spots) on a synthetic grid.

Coordinates are planar metres (EPSG:2154). A 2500x2500 m boundary splits into a
5x5 grid of 500 m cells. Every cell gets a road (so it counts as *built*) and a
400 m cycle lane -- except:

* the **target** cell (centroid 1250,1250) has a road but *no* cycle lane, so it
  is a near-zero-density island surrounded by well-covered built cells; and
* a **park** corner cell (centroid 250,250) has neither road nor cycle lane, so
  the built mask must drop it entirely.
"""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import LineString, Polygon

from bicyclelane.detectors.coverage import detect_coverage_coldspots

CRS = "EPSG:2154"
CELL = 500.0
N = 5  # 5x5 grid


def _cell_origin(col: int, row: int) -> tuple[float, float]:
    return col * CELL, row * CELL


def _line_in_cell(col: int, row: int) -> LineString:
    """A 400 m horizontal line living well inside cell (col, row)."""
    x0, y0 = _cell_origin(col, row)
    return LineString([(x0 + 50, y0 + 250), (x0 + 450, y0 + 250)])


TARGET = (2, 2)  # centroid (1250, 1250): road but no cycle lane
PARK = (0, 0)    # centroid (250, 250): no road, no cycle -> not built


def _inputs():
    boundary = gpd.GeoDataFrame(
        geometry=[Polygon([(0, 0), (N * CELL, 0), (N * CELL, N * CELL), (0, N * CELL)])],
        crs=CRS,
    )
    cycle_lines, road_lines = [], []
    for col in range(N):
        for row in range(N):
            cell = (col, row)
            if cell != PARK:
                road_lines.append(_line_in_cell(col, row))       # built
            if cell not in (TARGET, PARK):
                cycle_lines.append(_line_in_cell(col, row))       # covered
    cycle = gpd.GeoDataFrame(geometry=cycle_lines, crs=CRS)
    roads = gpd.GeoDataFrame(geometry=road_lines, crs=CRS)
    return cycle, roads, boundary


def _row_at(gdf, x, y, tol=1.0):
    for _, r in gdf.iterrows():
        c = r.geometry.centroid
        if abs(c.x - x) < tol and abs(c.y - y) < tol:
            return r
    return None


def test_low_density_island_is_a_coldspot():
    cycle, roads, boundary = _inputs()
    d4 = detect_coverage_coldspots(cycle, roads, boundary, cell_size=CELL, crs=CRS)

    assert len(d4) >= 1
    # The target cell (near-zero cycle density, surrounded by covered cells) is
    # flagged with a negative Gi* / positive score.
    target = _row_at(d4, 1250, 1250)
    assert target is not None
    assert target["detector"] == "D4"
    assert target["density"] == 0.0
    assert target["gi_star"] < 0
    assert target["score"] > 0


def test_built_mask_excludes_cells_without_roads():
    cycle, roads, boundary = _inputs()
    d4 = detect_coverage_coldspots(cycle, roads, boundary, cell_size=CELL, crs=CRS)
    # The park corner has zero cycle density but no roads -> never returned.
    assert _row_at(d4, 250, 250) is None
    # Every returned zone has road length above the built threshold.
    assert (d4["road_len_m"] >= 50.0).all()


def test_output_schema_and_ranking():
    cycle, roads, boundary = _inputs()
    d4 = detect_coverage_coldspots(cycle, roads, boundary, cell_size=CELL, crs=CRS)

    assert (d4["detector"] == "D4").all()
    assert d4.geometry.apply(lambda g: g.geom_type == "Polygon").all()
    scores = list(d4["score"])
    assert scores == sorted(scores, reverse=True)
    assert list(d4["rank"]) == list(range(1, len(d4) + 1))
    assert (d4["score"] > 0).all()


def test_degenerate_inputs_return_empty():
    # A single built cell: no meaningful neighbourhood -> empty output.
    boundary = gpd.GeoDataFrame(
        geometry=[Polygon([(0, 0), (400, 0), (400, 400), (0, 400)])], crs=CRS)
    cycle = gpd.GeoDataFrame(geometry=[LineString([(50, 200), (350, 200)])], crs=CRS)
    roads = gpd.GeoDataFrame(geometry=[LineString([(50, 100), (350, 100)])], crs=CRS)
    d4 = detect_coverage_coldspots(cycle, roads, boundary, cell_size=CELL, crs=CRS)
    assert d4.empty
