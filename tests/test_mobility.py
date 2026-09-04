"""Offline tests for the OD / MOBPRO loaders (Foundation v2b, BL-28)."""

from __future__ import annotations

import pytest

from bicyclelane.mobility import load_mobpro, load_od_csv, od_from_env


def test_load_od_csv_keeps_short_lines(tmp_path):
    csv = tmp_path / "od.csv"
    csv.write_text(
        "o_lon,o_lat,d_lon,d_lat,flow\n"
        "5.44,43.53,5.45,43.54,200\n"   # ~1.3 km -> kept
        "5.44,43.53,6.00,43.90,500\n",  # ~55 km -> dropped
        encoding="utf-8")
    g = load_od_csv(str(csv), max_km=5.0)
    assert len(g) == 1
    assert g.iloc[0]["flow"] == pytest.approx(200)
    assert g.crs.to_epsg() == 4326


def test_load_od_csv_bbox_keeps_local(tmp_path):
    csv = tmp_path / "od.csv"
    csv.write_text("o_lon,o_lat,d_lon,d_lat,flow\n5.44,43.53,5.45,43.54,200\n",
                   encoding="utf-8")
    assert len(load_od_csv(str(csv), bbox=(5.3, 43.4, 5.6, 43.7))) == 1
    assert len(load_od_csv(str(csv), bbox=(2.0, 48.0, 3.0, 49.0))) == 0


def test_load_mobpro_car_only_and_aggregates(tmp_path):
    flows = tmp_path / "mobpro.csv"
    flows.write_text(
        "COMMUNE,DCLT,TRANS,IPONDI\n"
        "13001,13002,5,100\n"   # car -> kept
        "13001,13002,3,50\n"    # bike -> excluded
        "13001,13001,5,30\n",   # same commune -> excluded
        encoding="utf-8")
    centroids = {"13001": (5.44, 43.53), "13002": (5.46, 43.54)}  # ~2 km apart
    g = load_mobpro(str(flows), centroids, max_km=5.0)
    assert len(g) == 1
    assert g.iloc[0]["flow"] == pytest.approx(100)


def test_od_from_env(tmp_path, monkeypatch):
    monkeypatch.delenv("BICYCLELANE_OD", raising=False)
    assert od_from_env() is None
    csv = tmp_path / "od.csv"
    csv.write_text("o_lon,o_lat,d_lon,d_lat,flow\n5.44,43.53,5.45,43.54,200\n",
                   encoding="utf-8")
    monkeypatch.setenv("BICYCLELANE_OD", str(csv))
    assert len(od_from_env()) == 1


def test_route_od_follows_graph():
    """route_od snaps a straight desire line onto the road network path."""
    import networkx as nx
    import geopandas as gpd
    from shapely.geometry import LineString
    from bicyclelane.mobility import route_od

    G = nx.Graph(); G.graph["crs"] = "EPSG:2154"
    for n, (x, y) in {"a": (0, 0), "b": (100, 0), "c": (200, 0), "d": (100, 80)}.items():
        G.add_node(n, x=x, y=y)
    for u, v in [("a", "b"), ("b", "c"), ("b", "d")]:
        (x1, y1), (x2, y2) = (G.nodes[u]["x"], G.nodes[u]["y"]), (G.nodes[v]["x"], G.nodes[v]["y"])
        G.add_edge(u, v, length=((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5)

    od = gpd.GeoDataFrame({"flow": [10.0]},
                          geometry=[LineString([(5, 3), (195, 3)])], crs="EPSG:2154")
    routed = route_od(od, G)
    assert len(routed) == 1
    coords = list(routed.geometry.iloc[0].coords)
    assert coords[0] == (0, 0) and coords[-1] == (200, 0)   # snapped a -> c
    assert (100, 0) in coords                                # via b, not the straight line
    assert routed.iloc[0]["flow"] == 10.0
