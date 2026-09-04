"""Offline tests for the Opportunity schema and GeoDataFrame round-trip."""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import LineString

from bicyclelane.schema import CORE_COLUMNS, Opportunity, to_geodataframe

CRS = "EPSG:2154"


def _sample_opportunities() -> list[Opportunity]:
    return [
        Opportunity(
            id="D1-0000",
            city="Testville",
            detector="D1",
            geometry=LineString([(0, 0), (10, 0)]),
            score=2.5,
            rank=1,
            attributes={"gap_m": 100.0, "road_len_m": 120.0},
            explanation="first",
        ),
        Opportunity(
            id="D1-0001",
            city="Testville",
            detector="D1",
            geometry=LineString([(0, 0), (0, 10)]),
            score=1.0,
            rank=2,
            attributes={"gap_m": 50.0, "road_len_m": 60.0},
            explanation="second",
        ),
    ]


def test_to_geodataframe_preserves_core_fields():
    gdf = to_geodataframe(_sample_opportunities(), crs=CRS)

    assert isinstance(gdf, gpd.GeoDataFrame)
    assert len(gdf) == 2
    assert gdf.crs == CRS
    for col in CORE_COLUMNS:
        assert col in gdf.columns
    # geometry is last, core columns come first and in order.
    assert list(gdf.columns[: len(CORE_COLUMNS)]) == CORE_COLUMNS
    assert gdf.columns[-1] == "geometry"


def test_attributes_are_spread_into_columns():
    gdf = to_geodataframe(_sample_opportunities(), crs=CRS)
    assert "gap_m" in gdf.columns
    assert "road_len_m" in gdf.columns
    assert gdf.loc[0, "gap_m"] == 100.0
    assert gdf.loc[0, "id"] == "D1-0000"
    assert gdf.loc[1, "detector"] == "D1"


def test_round_trip_values_match_source():
    opps = _sample_opportunities()
    gdf = to_geodataframe(opps, crs=CRS)
    for i, opp in enumerate(opps):
        assert gdf.loc[i, "id"] == opp.id
        assert gdf.loc[i, "score"] == opp.score
        assert gdf.loc[i, "rank"] == opp.rank
        assert gdf.loc[i, "explanation"] == opp.explanation
        assert gdf.loc[i, "geometry"].equals(opp.geometry)


def test_empty_input_yields_empty_frame_with_columns():
    gdf = to_geodataframe([], crs=CRS)
    assert isinstance(gdf, gpd.GeoDataFrame)
    assert len(gdf) == 0
    for col in CORE_COLUMNS:
        assert col in gdf.columns
