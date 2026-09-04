"""Offline tests for elevation & slope (Foundation v2a, BL-23)."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString

from bicyclelane.terrain import (
    CallableSampler,
    dem_sampler_from_env,
    edge_grades,
)

CRS = "EPSG:2154"


def _edges(lines):
    return gpd.GeoDataFrame(geometry=[LineString(pts) for pts in lines], crs=CRS)


def test_grade_on_a_tilted_plane():
    # Elevation rises 0.05 m per metre east (a 5% east-facing slope).
    sampler = CallableSampler(lambda x, y: 0.05 * x)
    edges = _edges([[(0, 0), (100, 0)],   # 100 m run east -> 5 m rise -> 5%
                    [(0, 0), (0, 100)]])  # 100 m run north -> flat -> 0%
    g = edge_grades(edges, sampler)
    assert g.iloc[0] == pytest.approx(5.0)
    assert g.iloc[1] == pytest.approx(0.0)


def test_flat_terrain_is_zero_grade():
    sampler = CallableSampler(lambda x, y: 42.0)
    g = edge_grades(_edges([[(0, 0), (100, 50)]]), sampler)
    assert g.iloc[0] == pytest.approx(0.0)


def test_min_run_floors_short_edges():
    sampler = CallableSampler(lambda x, y: 0.05 * x)
    edges = _edges([[(0, 0), (1, 0)]])  # 1 m run, rise 0.05 m
    # with the default 10 m floor: 100*0.05/10 = 0.5%  (not 5% from the raw 1 m)
    g = edge_grades(edges, sampler, min_run_m=10.0)
    assert g.iloc[0] == pytest.approx(0.5)
    g_raw = edge_grades(edges, sampler, min_run_m=1.0)
    assert g_raw.iloc[0] == pytest.approx(5.0)


def test_empty_geometry_is_nan():
    edges = _edges([[(0, 0), (10, 0)]])
    edges.loc[0, "geometry"] = None
    g = edge_grades(edges, CallableSampler(lambda x, y: x))
    assert np.isnan(g.iloc[0])


def test_dem_sampler_from_env_absent(monkeypatch):
    monkeypatch.delenv("BICYCLELANE_DEM", raising=False)
    assert dem_sampler_from_env() is None
    monkeypatch.setenv("BICYCLELANE_DEM", "/no/such/file.tif")
    assert dem_sampler_from_env() is None  # unreadable -> None, not a crash


def test_raster_elevation_sampler_reads_a_geotiff(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin
    from bicyclelane.terrain import RasterElevationSampler

    # 10x10 raster, 1 m pixels, origin at (0, 10) top-left; elevation = x-coord.
    data = np.tile(np.arange(10, dtype="float32"), (10, 1))  # value == column == x
    path = tmp_path / "dem.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=10, width=10, count=1,
        dtype="float32", crs=CRS, transform=from_origin(0, 10, 1, 1),
    ) as ds:
        ds.write(data, 1)

    sampler = RasterElevationSampler(str(path))
    # pixel centre at x=2.5 -> column 2 -> value 2; x=7.5 -> value 7
    zs = sampler.sample([(2.5, 5.0), (7.5, 5.0)])
    assert zs[0] == pytest.approx(2.0)
    assert zs[1] == pytest.approx(7.0)
