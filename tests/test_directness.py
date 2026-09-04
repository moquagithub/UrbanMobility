"""Offline sanity tests for D13 (directness / detour) on synthetic graphs."""

from __future__ import annotations

import networkx as nx
import pytest

from bicyclelane.detectors.directness import detect_directness_deficit

CRS = "EPSG:2154"


def _n(g, name, x, y):
    g.add_node(name, x=x, y=y)


def _e(g, u, v):
    (x1, y1) = g.nodes[u]["x"], g.nodes[u]["y"]
    (x2, y2) = g.nodes[v]["x"], g.nodes[v]["y"]
    g.add_edge(u, v, length=((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5)


def _open_square():
    """Three sides of a 10 m square: a-b-c-d. crow(a,d)=10, cycle path=30."""
    g = nx.Graph()
    _n(g, "a", 0, 0)
    _n(g, "b", 10, 0)
    _n(g, "c", 10, 10)
    _n(g, "d", 0, 10)
    _e(g, "a", "b")
    _e(g, "b", "c")
    _e(g, "c", "d")
    return g


def test_detour_pair_is_flagged_and_ranked_first():
    g = _open_square()
    out = detect_directness_deficit(g, city="Synth", crs=CRS,
                                    d_min=1, d_max=100, min_ratio=1.4)
    assert len(out) >= 1
    top = out.iloc[0]
    assert top["detector"] == "D13"
    # a-d: cycle 30 vs crow 10 -> ratio 3, excess 20 -> the strongest deficit
    assert top["detour_ratio"] == pytest.approx(3.0, abs=1e-6)
    assert top["score"] == pytest.approx(20.0, abs=1e-6)
    assert out.crs == CRS


def test_direct_graph_has_no_deficit():
    g = nx.Graph()
    _n(g, "a", 0, 0)
    _n(g, "b", 50, 0)
    _e(g, "a", "b")  # ratio 1.0
    out = detect_directness_deficit(g, city="Synth", crs=CRS,
                                    d_min=1, d_max=100, min_ratio=1.4)
    assert len(out) == 0


def test_min_ratio_filters_mild_detours():
    g = _open_square()
    # a-c and b-d have ratio ~1.41; only a-d (ratio 3) passes a 2.5 threshold.
    out = detect_directness_deficit(g, city="Synth", crs=CRS,
                                    d_min=1, d_max=100, min_ratio=2.5)
    assert len(out) == 1
    assert out.iloc[0]["detour_ratio"] == pytest.approx(3.0, abs=1e-6)


def test_crow_band_excludes_out_of_range_pairs():
    g = _open_square()
    # Band starting above 10 m excludes the a-d pair (crow 10); the diagonal
    # pairs a-c / b-d (crow ~14.1) remain in band but ratio 1.41 < 2 -> none.
    out = detect_directness_deficit(g, city="Synth", crs=CRS,
                                    d_min=11, d_max=100, min_ratio=2.0)
    assert len(out) == 0


def test_pairs_are_deduplicated():
    g = _open_square()
    out = detect_directness_deficit(g, city="Synth", crs=CRS,
                                    d_min=1, d_max=100, min_ratio=1.0)
    # An unordered endpoint pair must never appear twice.
    pairs = {frozenset((f.coords[0], f.coords[-1])) for f in out.geometry}
    assert len(pairs) == len(out)
    assert len(out) == 6  # C(4,2) distinct pairs among the 4 nodes
