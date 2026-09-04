"""Offline tests for optional INSEE gridded population in the D5 demand surface.

All synthetic -- no network and no real INSEE file. Coordinates are planar
metres (EPSG:3035, the INSEE grid CRS) so lengths/areas are metric. A
1000x1000 m boundary splits into four 500 m cells; POIs sit in the top-right
cell. We then check that:

* ``population_gdf=None`` reproduces the POI-only behaviour byte-for-byte;
* adding population concentrated in a cell raises that cell's demand/residual.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point, Polygon, box

from bicyclelane.demand import load_insee_population
from bicyclelane.detectors.mismatch import (
    demand_supply_grid,
    detect_demand_supply_mismatch,
)

# EPSG:3035 (LAEA Europe) -- metric, and the native INSEE grid CRS.
CRS = "EPSG:3035"
# A base offset placing the synthetic scene on realistic EPSG:3035 coordinates.
X0, Y0 = 3_527_000.0, 2_039_000.0


def _inputs():
    boundary = gpd.GeoDataFrame(
        geometry=[Polygon([
            (X0, Y0), (X0 + 1000, Y0), (X0 + 1000, Y0 + 1000), (X0, Y0 + 1000),
        ])],
        crs=CRS,
    )
    cycle = gpd.GeoDataFrame(
        geometry=[LineString([(X0 + 50, Y0 + 50), (X0 + 450, Y0 + 50)])], crs=CRS)
    pois = gpd.GeoDataFrame(
        {"poi_weight": [3.0, 3.0]},
        geometry=[Point(X0 + 700, Y0 + 700), Point(X0 + 800, Y0 + 800)],
        crs=CRS,
    )
    return cycle, pois, boundary


def _pop_cells_in(minx, miny, maxx, maxy, *, population, cell=200.0):
    """Tile a rectangle with 200 m population cells each carrying ``population``."""
    geoms = []
    x = minx
    while x < maxx - 1e-6:
        y = miny
        while y < maxy - 1e-6:
            geoms.append(box(x, y, x + cell, y + cell))
            y += cell
        x += cell
    return gpd.GeoDataFrame(
        {"population": [float(population)] * len(geoms)}, geometry=geoms, crs=CRS)


# --- (a) population_gdf=None preserves current behaviour ---------------------


def test_none_population_is_identical_to_poi_only():
    cycle, pois, boundary = _inputs()
    base = demand_supply_grid(cycle, pois, boundary, cell_size=500, crs=CRS)
    with_none = demand_supply_grid(
        cycle, pois, boundary, cell_size=500, crs=CRS, population_gdf=None)

    for col in ("demand", "supply", "zD", "zS", "residual"):
        assert np.array_equal(base[col].to_numpy(), with_none[col].to_numpy())
    # No population column is introduced in the None path.
    assert "population" not in with_none.columns


def test_none_population_zone_output_identical():
    cycle, pois, boundary = _inputs()
    zones_base = detect_demand_supply_mismatch(
        cycle, pois, boundary, cell_size=500, crs=CRS)
    zones_none = detect_demand_supply_mismatch(
        cycle, pois, boundary, cell_size=500, crs=CRS, population_gdf=None)

    assert list(zones_base["score"]) == list(zones_none["score"])
    assert list(zones_base["id"]) == list(zones_none["id"])
    assert len(zones_base) == len(zones_none)


# --- (b) concentrated population raises that cell's demand/residual ----------


def test_population_raises_demand_in_its_cell():
    cycle, pois, boundary = _inputs()
    # Concentrate population in the bottom-left cell (which has POI demand 0 and
    # supply from the cycle lane) -- lots of people, no demand from POIs yet.
    # 2x2 = 4 population cells fully inside the 500 m cell (no overhang).
    pop = _pop_cells_in(X0, Y0, X0 + 400, Y0 + 400, population=100.0)

    base = demand_supply_grid(cycle, pois, boundary, cell_size=500, crs=CRS)
    with_pop = demand_supply_grid(
        cycle, pois, boundary, cell_size=500, crs=CRS,
        population_gdf=pop, pop_weight=1.0)

    # Identify the bottom-left cell by centroid.
    def _bl_index(grid):
        cx = grid.geometry.centroid.x
        cy = grid.geometry.centroid.y
        return grid.index[(cx < X0 + 500) & (cy < Y0 + 500)][0]

    bl = _bl_index(with_pop)
    # Population injected -> its demand jumps from 0 to ~ area-weighted people.
    assert base.loc[bl, "demand"] == 0.0
    assert with_pop.loc[bl, "demand"] > 0.0
    # 4 cells * 100 people, all fully inside the 500 m cell -> area-weight 1.
    assert with_pop.loc[bl, "population"] == 400.0
    assert with_pop.loc[bl, "residual"] > base.loc[bl, "residual"]


def test_population_makes_cell_a_mismatch_zone():
    cycle, pois, boundary = _inputs()
    # Heavy population in the top-LEFT cell, which otherwise has no POI demand
    # and no supply -> it should surface as a mismatch zone once population is on.
    pop = _pop_cells_in(X0, Y0 + 500, X0 + 500, Y0 + 1000, population=100.0)

    zones_base = detect_demand_supply_mismatch(
        cycle, pois, boundary, cell_size=500, crs=CRS, min_residual=0.0)
    zones_pop = detect_demand_supply_mismatch(
        cycle, pois, boundary, cell_size=500, crs=CRS, min_residual=0.0,
        population_gdf=pop, pop_weight=1.0)

    def _has_top_left(zones):
        for _, row in zones.iterrows():
            c = row.geometry.centroid
            if c.x < X0 + 500 and c.y > Y0 + 500:
                return True
        return False

    # Without population the top-left cell has demand 0 -> excluded.
    assert not _has_top_left(zones_base)
    # With population it becomes an under-served high-demand zone.
    assert _has_top_left(zones_pop)


# --- loader smoke tests (CSV grid-id reconstruction + geo file) -------------


def test_load_insee_population_csv_reconstructs_cells(tmp_path):
    # Two INSEE 200 m grid ids on the EPSG:3035 grid.
    df = pd.DataFrame({
        "idcar_200m": [
            "CRS3035RES200mN2039400E3527200",
            "CRS3035RES200mN2039600E3527200",
        ],
        "ind": [12, 7],
    })
    csv = tmp_path / "insee.csv"
    df.to_csv(csv, index=False)

    gdf = load_insee_population(csv)
    assert list(gdf.columns) == ["geometry", "population"]
    assert gdf.crs.to_string() == "EPSG:4326"
    assert len(gdf) == 2
    assert sorted(gdf["population"].tolist()) == [7.0, 12.0]

    # Each reconstructed cell is a 200 m square in EPSG:3035 -> ~0.04 ha area.
    metric = gdf.to_crs("EPSG:3035")
    for area in metric.geometry.area:
        assert abs(area - 200.0 * 200.0) < 1.0


def test_load_insee_population_geofile_roundtrip(tmp_path):
    cells = _pop_cells_in(X0, Y0, X0 + 400, Y0 + 400, population=5.0)
    cells = cells.rename(columns={"population": "Ind"})  # INSEE-style column name
    gpkg = tmp_path / "insee.gpkg"
    cells.to_file(gpkg, driver="GPKG")

    gdf = load_insee_population(gpkg)
    assert list(gdf.columns) == ["geometry", "population"]
    assert gdf.crs.to_string() == "EPSG:4326"
    assert (gdf["population"] == 5.0).all()


def test_load_insee_population_bbox_filter(tmp_path):
    # A 2x2 block of population cells; bbox keeps only the SW one.
    df = pd.DataFrame({
        "idcar": [
            "CRS3035RES200mN2039400E3527200",
            "CRS3035RES200mN2039400E3527400",
            "CRS3035RES200mN2039600E3527200",
            "CRS3035RES200mN2039600E3527400",
        ],
        "ind": [1, 2, 3, 4],
    })
    csv = tmp_path / "insee.csv"
    df.to_csv(csv, index=False)

    # bbox in EPSG:3035 selecting only the SW cell (population 1).
    gdf = load_insee_population(
        csv, bbox=(3_527_200, 2_039_400, 3_527_350, 2_039_550))
    assert len(gdf) == 1
    assert gdf["population"].iloc[0] == 1.0


def test_load_insee_deprivation_csv_poverty_share(tmp_path):
    """load_insee_deprivation computes Men_pauv/Men from the same Filosofi file."""
    from bicyclelane.demand import load_insee_deprivation

    csv = tmp_path / "filo200.csv"
    csv.write_text(
        "idcar_200m,Ind,Men,Men_pauv\n"
        "CRS3035RES200mN2039400E3527200,80,40,10\n"   # 10/40 = 0.25
        "CRS3035RES200mN2039600E3527200,60,20,10\n",  # 10/20 = 0.50
        encoding="utf-8")
    gdf = load_insee_deprivation(csv)
    assert list(gdf.columns) == ["geometry", "deprivation"]
    assert gdf.crs.to_epsg() == 4326
    deps = sorted(round(float(d), 2) for d in gdf["deprivation"])
    assert deps == [0.25, 0.50]


def test_load_insee_deprivation_bbox_prefilter(tmp_path):
    """A metric (EPSG:3035) bbox prunes the national grid to the city cells."""
    from bicyclelane.demand import load_insee_deprivation

    csv = tmp_path / "filo.csv"
    csv.write_text(
        "idcar_200m,Ind,Men,Men_pauv\n"
        "CRS3035RES200mN2039400E3527200,80,40,10\n"   # kept by the bbox below
        "CRS3035RES200mN2999999E3999999,80,40,20\n",  # far away -> pruned
        encoding="utf-8")
    g = load_insee_deprivation(csv, bbox=(3527000, 2039300, 3527500, 2039500))
    assert len(g) == 1
    assert round(float(g["deprivation"].iloc[0]), 2) == 0.25
