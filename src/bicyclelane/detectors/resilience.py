"""D3 - Network resilience detector (BL-24).

Goal
----
Surface **fragile links** of the *existing cycle network* — edges whose failure
would cut part of the network off with no alternative route. These are the
places where adding a **parallel / redundant** lane most improves resilience.
A pure *supply-side* signal: it needs only the cycle graph, no demand data.

Algorithm
---------
1. Reduce the cycle graph to a simple undirected graph.
2. Find its **bridges** (edges whose removal disconnects a component); a bridge
   is exactly a link with no alternative cycle route around it.
3. Score each bridge by how much it carries structurally: the number of nodes
   on the **smaller side** it would isolate if it failed (higher = more
   critical). This is computed in ``O(V + E)`` via the *bridge tree* (contract
   each 2-edge-connected component to a node; the bridges form a forest, and a
   forest edge's smaller side is a subtree node-sum).

Each opportunity geometry is the fragile bridge edge; the explanation frames it
as "reinforce here". Coordinates are assumed to be in a metric CRS (node ``x`` /
``y`` in metres); reproject with :func:`bicyclelane.crs.project_graph` first.
"""

from __future__ import annotations

from typing import Any, Hashable

import networkx as nx
from geopandas import GeoDataFrame
from shapely.geometry import LineString

from ..schema import Opportunity, to_geodataframe


def _undirected_simple(graph: nx.Graph) -> nx.Graph:
    """Collapse a (possibly directed / multi) graph to a simple undirected one,
    carrying node ``x``/``y`` and a representative edge ``geometry``/``length``."""
    G = nx.Graph()
    for n, data in graph.nodes(data=True):
        G.add_node(n, x=data.get("x"), y=data.get("y"))
    for u, v, data in graph.edges(data=True):
        if u == v or G.has_edge(u, v):
            continue
        G.add_edge(u, v, geometry=data.get("geometry"), length=data.get("length"))
    return G


def _edge_geometry(G: nx.Graph, u: Hashable, v: Hashable) -> LineString:
    geom = G[u][v].get("geometry")
    if geom is not None:
        return geom
    return LineString(
        [(G.nodes[u]["x"], G.nodes[u]["y"]), (G.nodes[v]["x"], G.nodes[v]["y"])]
    )


def _smaller_sides(bridges: list[tuple], comp_id: dict, comp_size: dict, total: int
                   ) -> dict[frozenset, int]:
    """For each bridge (as a 2ecc-tree edge), the smaller-side original-node count.

    Builds the bridge tree (nodes = 2-edge-connected component ids, edges =
    bridges) — a tree per graph component — and returns, keyed by the frozenset
    of the two component ids a bridge joins, ``min(subtree, total - subtree)``.
    """
    T = nx.Graph()
    T.add_nodes_from(comp_size)
    for u, v in bridges:
        T.add_edge(comp_id[u], comp_id[v])

    root = next(iter(comp_size))
    parent: dict[Any, Any] = {root: None}
    order: list[Any] = []
    stack = [root]
    seen = {root}
    while stack:
        x = stack.pop()
        order.append(x)
        for y in T.neighbors(x):
            if y not in seen:
                seen.add(y)
                parent[y] = x
                stack.append(y)

    subtree = dict(comp_size)
    for x in reversed(order):
        if parent[x] is not None:
            subtree[parent[x]] += subtree[x]

    sides: dict[frozenset, int] = {}
    for x in comp_size:
        if parent[x] is not None:
            s = subtree[x]
            sides[frozenset((parent[x], x))] = min(s, total - s)
    return sides


def detect_network_resilience(
    cycle_graph: nx.Graph,
    *,
    city: str = "",
    crs: Any = None,
    min_cut_nodes: int = 1,
    max_results: int = 500,
) -> GeoDataFrame:
    """Rank fragile (bridge) links of the cycle network by their isolated-node count.

    Returns a GeoDataFrame of opportunities (``detector == "D3"``) with the
    bridge geometry, ``score`` = smaller-side node count, and ``cut_nodes`` /
    ``component_nodes`` attributes. Empty when the network has no bridges (fully
    2-edge-connected) or is too small.
    """
    G = _undirected_simple(cycle_graph)

    opportunities: list[Opportunity] = []
    seq = 0
    for comp_nodes in nx.connected_components(G):
        if len(comp_nodes) < 3:
            continue
        Gc = G.subgraph(comp_nodes).copy()
        bridges = list(nx.bridges(Gc))
        if not bridges:
            continue

        Hc = Gc.copy()
        Hc.remove_edges_from(bridges)
        comp_id: dict[Hashable, int] = {}
        comp_size: dict[int, int] = {}
        for cid, cc in enumerate(nx.connected_components(Hc)):
            for n in cc:
                comp_id[n] = cid
            comp_size[cid] = len(cc)

        total = len(comp_nodes)
        sides = _smaller_sides(bridges, comp_id, comp_size, total)

        for u, v in bridges:
            key = frozenset((comp_id[u], comp_id[v]))
            sc = sides.get(key)
            if sc is None or sc < min_cut_nodes:
                continue
            opportunities.append(Opportunity(
                id=f"D3-{seq}",
                city=city,
                detector="D3",
                geometry=_edge_geometry(Gc, u, v),
                score=float(sc),
                attributes={"cut_nodes": int(sc), "component_nodes": int(total)},
                explanation=(
                    f"Fragile link: if it fails, {int(sc)} node(s) of the cycle "
                    "network are cut off with no alternative route. A parallel or "
                    "redundant lane here improves resilience."
                ),
            ))
            seq += 1

    opportunities.sort(key=lambda o: o.score, reverse=True)
    opportunities = opportunities[:max_results]
    for rank, opp in enumerate(opportunities, start=1):
        opp.rank = rank
    return to_geodataframe(opportunities, crs=crs)
