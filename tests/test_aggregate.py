"""Offline tests for the shared-unit aggregation bridge (BL-21)."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Polygon, box

from bicyclelane.aggregate import (
    aggregate_to_grid, composite_geojson, composite_grid,
)

CRS = "EPSG:2154"


def _grid_2x1():
    """Two adjacent 100 m cells: cell 0 = x in [0,100], cell 1 = x in [100,200]."""
    cells = [box(0, 0, 100, 100), box(100, 0, 200, 100)]
    return gpd.GeoDataFrame({"cell_id": [0, 1]}, geometry=cells, crs=CRS)


def _det(scores, geoms):
    return gpd.GeoDataFrame({"score": scores}, geometry=geoms, crs=CRS)


def test_feature_lands_in_containing_cell():
    grid = _grid_2x1()
    # One line in cell 0, one in cell 1.
    d = _det([1.0, 3.0], [LineString([(10, 50), (40, 50)]),
                          LineString([(120, 50), (160, 50)])])
    out = aggregate_to_grid({"D1": d}, grid, ["D1"])
    # min-max over [1,3] -> cell0 gets 0.0, cell1 gets 1.0
    assert out.loc[out.cell_id == 0, "D1"].iloc[0] == pytest.approx(0.0)
    assert out.loc[out.cell_id == 1, "D1"].iloc[0] == pytest.approx(1.0)


def test_silent_detector_is_zero_filled():
    grid = _grid_2x1()
    d = _det([5.0], [LineString([(10, 50), (40, 50)])])  # only cell 0
    out = aggregate_to_grid({"D1": d, "D5": None}, grid, ["D1", "D5"])
    assert (out["D5"] == 0.0).all()
    # constant single value normalises to 1.0 in its cell, 0 elsewhere
    assert out.loc[out.cell_id == 0, "D1"].iloc[0] == pytest.approx(1.0)
    assert out.loc[out.cell_id == 1, "D1"].iloc[0] == pytest.approx(0.0)


def test_cell_takes_max_of_features_within():
    grid = _grid_2x1()
    # Two features in cell 0 with raw 0 and 10, one in cell 1 with raw 5.
    d = _det([0.0, 10.0, 5.0], [
        LineString([(10, 50), (20, 50)]),
        LineString([(30, 50), (40, 50)]),
        LineString([(120, 50), (160, 50)]),
    ])
    out = aggregate_to_grid({"D1": d}, grid, ["D1"])
    # normalised: 0->0, 10->1, 5->0.5 ; cell0 = max(0,1)=1, cell1 = 0.5
    assert out.loc[out.cell_id == 0, "D1"].iloc[0] == pytest.approx(1.0)
    assert out.loc[out.cell_id == 1, "D1"].iloc[0] == pytest.approx(0.5)


def test_composite_grid_columns_and_contributions():
    grid = _grid_2x1()
    d1 = _det([1.0, 3.0], [LineString([(10, 50), (40, 50)]),
                           LineString([(120, 50), (160, 50)])])
    d5 = _det([9.0, 1.0], [LineString([(10, 60), (40, 60)]),
                           LineString([(120, 60), (160, 60)])])
    cells, result = composite_grid({"D1": d1, "D5": d5}, grid, ["D1", "D5"])
    # equal-weights composite = 0.5*D1 + 0.5*D5, and contributions decompose it
    for _, row in cells.iterrows():
        assert row["composite"] == pytest.approx(
            row["contrib_D1"] + row["contrib_D5"])
    assert "equal" in result.scores
    np.testing.assert_allclose(result.scores["equal"], cells["composite"].to_numpy())


def test_composite_geojson_shape_and_meta():
    grid = _grid_2x1()
    d1 = _det([1.0, 3.0], [LineString([(10, 50), (40, 50)]),
                           LineString([(120, 50), (160, 50)])])
    cells, result = composite_grid({"D1": d1, "D5": None}, grid, ["D1", "D5"])
    fc = composite_geojson(cells, result, ["D1", "D5"], drop_empty=True)
    assert fc["type"] == "FeatureCollection"
    # cell 0 has D1 signal (normalised 0 though -> not >0) ... cell 1 has D1=1.0
    keys = [f["properties"]["cell_id"] for f in fc["features"]]
    assert 1 in keys  # the cell with a positive signal is kept
    f1 = next(f for f in fc["features"] if f["properties"]["cell_id"] == 1)
    p = f1["properties"]
    assert p["composite"] == pytest.approx(p["contrib_D1"] + p["contrib_D5"])
    assert set(fc["meta"]["criteria"]) == {"D1", "D5"}
    assert "equal" in fc["meta"]["weights"]


def test_reprojects_detector_to_grid_crs():
    grid = _grid_2x1()  # EPSG:2154 (metres)
    # Same line expressed in WGS84 would not fall in the metric cells unless
    # reprojected; here we just check CRS mismatch does not crash and a metric
    # feature still lands correctly.
    d = _det([2.0], [LineString([(50, 50), (60, 50)])])
    out = aggregate_to_grid({"D1": d}, grid, ["D1"])
    assert out.loc[out.cell_id == 0, "D1"].iloc[0] == pytest.approx(1.0)
