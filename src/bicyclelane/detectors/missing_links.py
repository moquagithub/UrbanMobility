"""D1 - Missing links detector.

Goal
----
Find short, road-feasible connectors that would join two *distinct* connected
components of the cycle network, closing a gap that is small "as the crow flies"
yet currently forces a long detour (or is simply impassable) by bike.

Algorithm (as specified in the BicycleLane panorama, detector D1)
----------------------------------------------------------------
1. Compute the connected components ``{C_1, C_2, ...}`` of the (undirected)
   cycle graph.
2. Collect the *terminal* nodes: nodes of degree 1 (dead-ends of the cycle
   network) -- these are where a component can naturally be extended.
3. For every candidate pair of terminals ``(u, v)`` with
   ``comp(u) != comp(v)`` and euclidean gap ``g = ||u - v|| <= eps``, test
   *road feasibility*: the shortest path length along the road network,
   ``L_r``, must satisfy ``L_r <= kappa * g`` (the on-road route is not a huge
   detour relative to the straight gap).
4. Score each feasible connector. Two variants of the "importance of the
   smaller joined component" are computed side by side so they can be compared:

       S1        = min(len(C_i),   len(C_j))   / L_r      (length-based, metres)
       S1_nodes  = min(|C_i|,      |C_j|)      / L_r      (node-count-based)

   where ``len(C)`` is the total cycle length (metres) of the component and
   ``|C|`` is its number of nodes. Bigger networks joined by a shorter road
   link score higher under both.
5. Rank all feasible connectors by the length-based ``S1`` descending
   (``score``); ``score_nodes`` is reported alongside for comparison.

Notes
-----
* All coordinates are assumed to be in a **metric CRS** (node attributes
  ``x``/``y`` in metres). Reproject graphs with :func:`bicyclelane.crs.project_graph`
  (or build synthetic graphs directly in a planar CRS) before calling.
* ``len(C)`` is dimensionally metres, so ``S1`` is dimensionless; ``S1_nodes``
  has units of 1/metre. The two are not directly comparable in magnitude -- the
  point is to compare the *rankings* they induce.
"""

from __future__ import annotations

import math
from typing import Any, Hashable

import geopandas as gpd
import networkx as nx
import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import LineString

from ..schema import Opportunity, to_geodataframe


def _node_xy(graph: nx.Graph, node: Hashable) -> tuple[float, float]:
    """Return the ``(x, y)`` metric coordinates of a graph node."""
    data = graph.nodes[node]
    try:
        return float(data["x"]), float(data["y"])
    except KeyError as exc:  # pragma: no cover - defensive
        raise KeyError(
            f"Node {node!r} is missing 'x'/'y' coordinate attributes; "
            "reproject the graph to a metric CRS first."
        ) from exc


def _euclidean(p: tuple[float, float], q: tuple[float, float]) -> float:
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _as_undirected(graph: nx.Graph) -> nx.Graph:
    """Collapse a (possibly multi/directed) graph to a simple undirected graph,
    preserving node attributes and a per-edge ``length`` (metres)."""
    simple = nx.Graph()
    simple.add_nodes_from(graph.nodes(data=True))
    for u, v, data in graph.edges(data=True):
        if u == v:
            continue
        length = data.get("length")
        if length is None:
            length = _euclidean(_node_xy(graph, u), _node_xy(graph, v))
        if simple.has_edge(u, v):
            # Keep the shortest parallel edge.
            if length < simple[u][v]["length"]:
                simple[u][v]["length"] = float(length)
        else:
            simple.add_edge(u, v, length=float(length))
    return simple


def _component_length(graph: nx.Graph, nodes: set[Hashable]) -> float:
    """Total cycle length (metres) of the sub-network induced by ``nodes``."""
    total = 0.0
    for u, v, data in graph.subgraph(nodes).edges(data=True):
        total += float(data.get("length", 0.0))
    return total


def _nearest_road_node(
    road: nx.Graph, point: tuple[float, float]
) -> Hashable | None:
    """Return the road node closest (euclidean) to ``point``."""
    best_node = None
    best_dist = math.inf
    for node in road.nodes:
        d = _euclidean(point, _node_xy(road, node))
        if d < best_dist:
            best_dist = d
            best_node = node
    return best_node


def detect_missing_links(
    cycle_graph: nx.Graph,
    road_graph: nx.Graph,
    *,
    eps: float = 300.0,
    kappa: float = 1.5,
    city: str = "",
    crs: Any = None,
) -> gpd.GeoDataFrame:
    """Detect missing-link opportunities (detector D1).

    Parameters
    ----------
    cycle_graph:
        The cycle network as a NetworkX graph in a **metric CRS** (node
        attributes ``x``/``y`` in metres; edges may carry a ``length``).
    road_graph:
        The full road network (same metric CRS) used to test feasibility of a
        connector and to measure the on-road length ``L_r``.
    eps:
        Maximum euclidean gap ``g`` (metres) between two terminals for them to be
        considered a candidate pair.
    kappa:
        Detour tolerance: a connector is feasible iff ``L_r <= kappa * g``.
    city:
        City / place name recorded on each opportunity.
    crs:
        CRS assigned to the output GeoDataFrame (should match the graphs' metric
        CRS). If ``None``, the ``crs`` graph attribute of ``cycle_graph`` is used
        when present.

    Returns
    -------
    geopandas.GeoDataFrame
        Ranked opportunities (highest ``score`` first). Each row's geometry is
        the straight connector line between the two terminals; attributes carry
        ``gap_m`` (g), ``road_len_m`` (L_r), the two component sizes, and the
        endpoint node ids.
    """
    cycle = _as_undirected(cycle_graph)
    road = _as_undirected(road_graph)

    if crs is None:
        crs = cycle_graph.graph.get("crs") if hasattr(cycle_graph, "graph") else None

    # 1. Connected components + a node->component-id map.
    components = list(nx.connected_components(cycle))
    comp_id: dict[Hashable, int] = {}
    comp_len: dict[int, float] = {}
    comp_nodes: dict[int, int] = {}
    for cid, nodes in enumerate(components):
        comp_len[cid] = _component_length(cycle, nodes)
        comp_nodes[cid] = len(nodes)
        for n in nodes:
            comp_id[n] = cid

    # 2. Terminal (degree-1) nodes.
    terminals = [n for n in cycle.nodes if cycle.degree(n) == 1]
    if not terminals:
        return to_geodataframe([], crs=crs)
    term_xy = np.asarray([_node_xy(cycle, t) for t in terminals], dtype=float)

    # Pre-snap each terminal to its nearest road node via a KD-tree over road
    # node coordinates -- O(|T| log|V_road|) instead of a per-terminal linear
    # scan of every road node, which is what makes large cities tractable.
    road_nodes = list(road.nodes)
    snap: dict[Hashable, Hashable | None] = {}
    snap_xy: dict[Hashable, tuple[float, float]] = {}
    if road_nodes:
        road_xy = np.asarray([_node_xy(road, n) for n in road_nodes], dtype=float)
        _, nn = cKDTree(road_xy).query(term_xy, k=1)
        for i, t in enumerate(terminals):
            k = int(nn[i])
            snap[t] = road_nodes[k]
            snap_xy[t] = (float(road_xy[k, 0]), float(road_xy[k, 1]))
    else:
        snap = {t: None for t in terminals}

    # 3. Candidate terminal pairs within eps found with a KD-tree (avoids the
    # O(|T|^2) all-pairs loop).
    candidate_pairs = cKDTree(term_xy).query_pairs(eps)

    # Bounded, cached single-source Dijkstra from each snapped road node: explore
    # only within kappa*eps metres, so routing stays local even on huge graphs.
    cutoff = kappa * eps
    dijkstra_cache: dict[Hashable, dict[Hashable, float]] = {}

    def _road_len(ru: Hashable, rv: Hashable) -> float | None:
        if ru not in dijkstra_cache:
            try:
                dijkstra_cache[ru] = nx.single_source_dijkstra_path_length(
                    road, ru, cutoff=cutoff, weight="length")
            except nx.NodeNotFound:
                dijkstra_cache[ru] = {}
        return dijkstra_cache[ru].get(rv)

    opportunities: list[Opportunity] = []
    seq = 0
    for i, j in candidate_pairs:
        u, v = terminals[i], terminals[j]
        # 3a. Must belong to distinct components.
        if comp_id[u] == comp_id[v]:
            continue

        pu = (float(term_xy[i, 0]), float(term_xy[i, 1]))
        pv = (float(term_xy[j, 0]), float(term_xy[j, 1]))
        g = _euclidean(pu, pv)
        if g == 0.0:
            continue

        # 3c. Road feasibility: shortest on-road path between snapped nodes.
        ru, rv = snap[u], snap[v]
        if ru is None or rv is None:
            continue
        road_dist = _road_len(ru, rv)
        if road_dist is None:
            continue
        # Account for the snap offsets so L_r reflects terminal-to-terminal cost.
        l_r = road_dist + _euclidean(pu, snap_xy[u]) + _euclidean(pv, snap_xy[v])
        if l_r <= 0 or l_r > kappa * g:
            continue

        # 4. Score -- two variants side by side (length-based and node-count).
        ci, cj = comp_id[u], comp_id[v]
        score = min(comp_len[ci], comp_len[cj]) / l_r
        score_nodes = min(comp_nodes[ci], comp_nodes[cj]) / l_r

        opportunities.append(
            Opportunity(
                id=f"D1-{seq:04d}",
                city=city,
                detector="D1",
                geometry=LineString([pu, pv]),
                score=float(score),
                attributes={
                    "u": u,
                    "v": v,
                    "gap_m": round(g, 3),
                    "road_len_m": round(l_r, 3),
                    "comp_u": ci,
                    "comp_v": cj,
                    "comp_u_len_m": round(comp_len[ci], 3),
                    "comp_v_len_m": round(comp_len[cj], 3),
                    "comp_u_nodes": comp_nodes[ci],
                    "comp_v_nodes": comp_nodes[cj],
                    "score_nodes": round(float(score_nodes), 6),
                },
                explanation=(
                    f"Missing link: terminals {u} and {v} are {g:.0f} m apart "
                    f"(distinct cycle components), joinable by {l_r:.0f} m of road "
                    f"(<= {kappa:g} x gap). S1={score:.4f} (length), "
                    f"S1_nodes={score_nodes:.4f} (node count)."
                ),
            )
        )
        seq += 1

    # 5. Rank by score descending; assign 1-based rank.
    opportunities.sort(key=lambda o: o.score, reverse=True)
    for rank, opp in enumerate(opportunities, start=1):
        opp.rank = rank

    return to_geodataframe(opportunities, crs=crs)
