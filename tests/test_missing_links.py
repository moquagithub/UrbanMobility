"""Offline sanity tests for D1 (missing links) on hand-built synthetic graphs.

All coordinates are in a planar metric CRS (metres); we tag the output as
EPSG:2154 purely so lengths are interpreted as metres.
"""

from __future__ import annotations

import networkx as nx
import pytest

from bicyclelane.detectors.missing_links import detect_missing_links

CRS = "EPSG:2154"


def _add_node(g: nx.Graph, n, x, y) -> None:
    g.add_node(n, x=x, y=y)


def _add_edge(g: nx.Graph, u, v) -> None:
    (x1, y1) = g.nodes[u]["x"], g.nodes[u]["y"]
    (x2, y2) = g.nodes[v]["x"], g.nodes[v]["y"]
    g.add_edge(u, v, length=((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5)


def _two_components_with_road():
    """Two disconnected cycle lines with a 100 m gap, joined by a road."""
    cycle = nx.Graph()
    # Component A: (0,0)-(100,0), terminals a0 and a1.
    _add_node(cycle, "a0", 0, 0)
    _add_node(cycle, "a1", 100, 0)
    _add_edge(cycle, "a0", "a1")
    # Component B: (200,0)-(300,0), terminals b0 and b1.
    _add_node(cycle, "b0", 200, 0)
    _add_node(cycle, "b1", 300, 0)
    _add_edge(cycle, "b0", "b1")

    # Road running straight along y=0 with nodes at the two inner terminals.
    road = nx.Graph()
    _add_node(road, "r0", 100, 0)
    _add_node(road, "r1", 200, 0)
    _add_edge(road, "r0", "r1")  # length 100
    return cycle, road


def test_returns_connectors_between_distinct_components():
    cycle, road = _two_components_with_road()
    gdf = detect_missing_links(cycle, road, eps=300, kappa=1.5, city="Synth", crs=CRS)

    assert len(gdf) >= 1
    # Every connector must join two DISTINCT components.
    assert (gdf["comp_u"] != gdf["comp_v"]).all()
    assert (gdf["detector"] == "D1").all()
    assert gdf.crs == CRS


def test_road_feasibility_constraint_holds():
    cycle, road = _two_components_with_road()
    kappa = 1.5
    gdf = detect_missing_links(cycle, road, eps=300, kappa=kappa, crs=CRS)
    # L_r <= kappa * g for every returned connector.
    assert (gdf["road_len_m"] <= kappa * gdf["gap_m"] + 1e-6).all()


def test_ranking_is_by_score_descending():
    cycle, road = _two_components_with_road()
    gdf = detect_missing_links(cycle, road, eps=300, kappa=1.5, crs=CRS)
    scores = list(gdf["score"])
    assert scores == sorted(scores, reverse=True)
    assert list(gdf["rank"]) == list(range(1, len(gdf) + 1))


def test_infeasible_road_detour_is_rejected():
    """If the only road route is a long detour, kappa filters it out."""
    cycle, _ = _two_components_with_road()
    # Road detour: 100->(150,400)->200 is far longer than 1.5 * 100 m gap.
    road = nx.Graph()
    _add_node(road, "r0", 100, 0)
    _add_node(road, "rmid", 150, 400)
    _add_node(road, "r1", 200, 0)
    _add_edge(road, "r0", "rmid")
    _add_edge(road, "rmid", "r1")

    gdf = detect_missing_links(cycle, road, eps=300, kappa=1.5, crs=CRS)
    # Every road route is a long detour (> kappa * gap), so nothing is feasible.
    assert gdf.empty


def test_same_component_pairs_are_never_connectors():
    cycle, road = _two_components_with_road()
    gdf = detect_missing_links(cycle, road, eps=1000, kappa=5.0, crs=CRS)
    # a0/a1 are the same component and must never appear as a pair.
    for _, row in gdf.iterrows():
        assert not ({row["u"], row["v"]} <= {"a0", "a1"})
        assert not ({row["u"], row["v"]} <= {"b0", "b1"})


def test_score_uses_smaller_component_length():
    """Make component B longer than A; score numerator must be A's length."""
    cycle = nx.Graph()
    _add_node(cycle, "a0", 0, 0)
    _add_node(cycle, "a1", 100, 0)  # A length = 100
    _add_edge(cycle, "a0", "a1")
    _add_node(cycle, "b0", 200, 0)
    _add_node(cycle, "b1", 700, 0)  # B length = 500
    _add_edge(cycle, "b0", "b1")

    road = nx.Graph()
    _add_node(road, "r0", 100, 0)
    _add_node(road, "r1", 200, 0)
    _add_edge(road, "r0", "r1")  # 100 m

    gdf = detect_missing_links(cycle, road, eps=300, kappa=2.0, crs=CRS)
    inner = gdf[(gdf["u"].isin(["a1"])) & (gdf["v"].isin(["b0"]))]
    assert not inner.empty
    row = inner.iloc[0]
    # min(100, 500) / L_r ; L_r == 100 -> score == 1.0
    assert row["road_len_m"] == pytest.approx(100.0)
    assert row["score"] == pytest.approx(1.0)


def test_node_count_score_reported_alongside_length_score():
    """S1_nodes = min(|C_i|, |C_j|) / L_r is reported for comparison."""
    cycle, road = _two_components_with_road()
    gdf = detect_missing_links(cycle, road, eps=300, kappa=1.5, crs=CRS)

    for col in ("score_nodes", "comp_u_nodes", "comp_v_nodes"):
        assert col in gdf.columns
    row = gdf.iloc[0]
    # Each synthetic component has exactly 2 nodes; L_r == 100 m.
    assert row["comp_u_nodes"] == 2
    assert row["comp_v_nodes"] == 2
    assert row["score_nodes"] == pytest.approx(2.0 / 100.0)
    # Length-based score stays the primary ranking key.
    assert row["score"] == pytest.approx(1.0)
