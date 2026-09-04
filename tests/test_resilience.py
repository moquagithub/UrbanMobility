"""Offline sanity tests for D3 (network resilience) on synthetic graphs.

Coordinates are planar metres, tagged EPSG:2154 so geometry lengths make sense.
"""

from __future__ import annotations

import networkx as nx
import pytest

from bicyclelane.detectors.resilience import detect_network_resilience

CRS = "EPSG:2154"


def _n(g, name, x, y):
    g.add_node(name, x=x, y=y)


def _triangle(g, a, b, c, ox, oy):
    """A 2-edge-connected triangle at offset (ox, oy)."""
    _n(g, a, ox, oy)
    _n(g, b, ox + 1, oy)
    _n(g, c, ox + 0.5, oy + 1)
    g.add_edge(a, b)
    g.add_edge(b, c)
    g.add_edge(c, a)


def test_single_cycle_has_no_bridges():
    g = nx.Graph()
    _triangle(g, "a", "b", "c", 0, 0)
    out = detect_network_resilience(g, city="Synth", crs=CRS)
    assert len(out) == 0


def test_bridge_between_two_triangles_is_flagged():
    g = nx.Graph()
    _triangle(g, "a", "b", "c", 0, 0)       # 3 nodes
    _triangle(g, "d", "e", "f", 10, 0)      # 3 nodes
    g.add_edge("c", "d")                     # the only link between them -> bridge
    out = detect_network_resilience(g, city="Synth", crs=CRS)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["detector"] == "D3"
    # removing c-d isolates one triangle -> 3 nodes on the smaller side
    assert row["cut_nodes"] == 3
    assert row["score"] == pytest.approx(3.0)
    assert out.crs == CRS


def test_path_scores_middle_edge_highest():
    # a-b-c-d : every edge is a bridge. Middle edge b-c splits 2|2 (score 2);
    # the end edges split 1|3 (score 1). The middle must rank first.
    g = nx.Graph()
    for i, name in enumerate(["a", "b", "c", "d"]):
        _n(g, name, i, 0)
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    g.add_edge("c", "d")
    out = detect_network_resilience(g, city="Synth", crs=CRS)
    assert len(out) == 3
    assert out.iloc[0]["cut_nodes"] == 2          # ranked first = middle
    assert set(out["cut_nodes"]) == {1, 2}
    assert list(out["rank"]) == [1, 2, 3]


def test_min_cut_nodes_filters_small_splits():
    g = nx.Graph()
    for i, name in enumerate(["a", "b", "c", "d"]):
        _n(g, name, i, 0)
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    g.add_edge("c", "d")
    out = detect_network_resilience(g, city="Synth", crs=CRS, min_cut_nodes=2)
    # only the middle edge (score 2) survives the threshold
    assert len(out) == 1
    assert out.iloc[0]["cut_nodes"] == 2


def test_empty_on_trivial_graph():
    g = nx.Graph()
    _n(g, "a", 0, 0)
    _n(g, "b", 1, 0)
    g.add_edge("a", "b")
    out = detect_network_resilience(g, city="Synth", crs=CRS)
    assert len(out) == 0
