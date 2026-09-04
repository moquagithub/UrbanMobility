"""Offline smoke test for the Folium mapping/export layer."""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import LineString

from bicyclelane.mapping import render_opportunities


def test_render_writes_a_standalone_html_map(tmp_path):
    # Existing cycle network (WGS84 lon/lat).
    network = gpd.GeoDataFrame(
        geometry=[LineString([(5.44, 43.52), (5.45, 43.53)])],
        crs="EPSG:4326",
    )
    # One opportunity layer in a *projected* CRS (must be reprojected internally).
    opps = gpd.GeoDataFrame(
        {"id": ["D1-0001"], "detector": ["D1"], "score": [1.23], "rank": [1],
         "explanation": ["synthetic connector"]},
        geometry=[LineString([(5.451, 43.531), (5.452, 43.532)])],
        crs="EPSG:4326",
    ).to_crs(3857)

    out = tmp_path / "map.html"
    result = render_opportunities(network, {"D1 missing links": opps}, out)

    assert result == out
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert len(html) > 1000
    assert "leaflet" in html.lower()          # a real Leaflet map was written
    assert "D1 missing links (1)" in html     # layer name + auto count


def test_render_handles_empty_layer(tmp_path):
    network = gpd.GeoDataFrame(
        geometry=[LineString([(5.44, 43.52), (5.45, 43.53)])], crs="EPSG:4326")
    empty = gpd.GeoDataFrame(
        {"id": []}, geometry=[], crs="EPSG:4326")

    out = tmp_path / "empty.html"
    render_opportunities(network, {"D2 continuity gaps": empty}, out)
    assert out.exists()
