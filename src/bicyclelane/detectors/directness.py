"""D13 - Directness / detour detector (BL-27).

Goal
----
Find **desire lines** where cycling is far less direct than a straight route —
node pairs whose on-network cycle distance is a large multiple of their
crow-flight distance. A new, more direct link there would cut the detour. A
supply-side signal computed on the cycle graph alone (straight-line reference,
no road graph or demand data).

Algorithm
---------
1. Reduce the cycle graph to a simple undirected weighted graph (edge
   ``length`` in metres; falls back to the crow distance of its endpoints).
2. From a deterministic, capped sample of source nodes, run a bounded
   single-source Dijkstra.
3. For each reachable target within a crow-distance band ``[d_min, d_max]``
   (dedup each unordered pair once), compute the **detour ratio**
   ``r = cycle_dist / crow``. Keep pairs with ``r >= min_ratio``.
4. Score by the **excess distance** ``cycle_dist − crow`` (metres a direct link
   would save); the opportunity geometry is the straight desire line.

Coordinates must be in a metric CRS (node ``x`` / ``y`` in metres); reproject
with :func:`bicyclelane.crs.project_graph` first.
"""

from __future__ import annotations

from math import hypot
from typing import Any, Hashable

import networkx as nx
from geopandas import GeoDataFrame
from shapely.geometry import LineString

from ..schema import Opportunity, to_geodataframe


def _undirected_weighted(graph: nx.Graph) -> nx.Graph:
    """Simple undirected graph with a metric ``length`` on every edge."""
    G = nx.Graph()
    for n, data in graph.nodes(data=True):
        G.add_node(n, x=data.get("x"), y=data.get("y"))
    for u, v, data in graph.edges(data=True):
        if u == v:
            continue
        length = data.get("length")
        if length is None:
            length = hypot(
                G.nodes[u]["x"] - G.nodes[v]["x"], G.nodes[u]["y"] - G.nodes[v]["y"]
            )
        if G.has_edge(u, v):
            if length < G[u][v]["length"]:
                G[u][v]["length"] = length
        else:
            G.add_edge(u, v, length=length)
    return G


def _sample_sources(nodes: list[Hashable], max_sources: int) -> list[Hashable]:
    """Deterministic, evenly-strided subset of ``nodes`` (no randomness)."""
    if len(nodes) <= max_sources:
        return nodes
    stride = len(nodes) / max_sources
    return [nodes[int(i * stride)] for i in range(max_sources)]


def detect_directness_deficit(
    cycle_graph: nx.Graph,
    *,
    city: str = "",
    crs: Any = None,
    d_min: float = 300.0,
    d_max: float = 3000.0,
    min_ratio: float = 1.4,
    max_sources: int = 200,
    max_results: int = 500,
) -> GeoDataFrame:
    """Rank directness deficits (high cycle-detour desire lines) as opportunities.

    Returns a GeoDataFrame (``detector == "D13"``) of straight desire lines with
    ``score`` = excess distance (m), plus ``detour_ratio`` / ``cycle_dist_m`` /
    ``crow_m`` attributes. Empty when no pair in the crow band exceeds
    ``min_ratio``.
    """
    G = _undirected_weighted(cycle_graph)
    nodes = sorted(G.nodes(), key=lambda n: str(n))
    sources = _sample_sources(nodes, max_sources)
    cutoff = d_max * 5.0  # bound the search; catches ratios up to ~5 at the band edge

    seen: set[frozenset] = set()
    opportunities: list[Opportunity] = []
    seq = 0
    for s in sources:
        lengths = nx.single_source_dijkstra_path_length(
            G, s, cutoff=cutoff, weight="length")
        sx, sy = G.nodes[s]["x"], G.nodes[s]["y"]
        for t, dist in lengths.items():
            if t == s:
                continue
            key = frozenset((s, t))
            if key in seen:
                continue
            tx, ty = G.nodes[t]["x"], G.nodes[t]["y"]
            crow = hypot(sx - tx, sy - ty)
            if crow < d_min or crow > d_max:
                continue
            seen.add(key)
            ratio = dist / crow if crow > 0 else 0.0
            if ratio < min_ratio:
                continue
            excess = dist - crow
            opportunities.append(Opportunity(
                id=f"D13-{seq}",
                city=city,
                detector="D13",
                geometry=LineString([(sx, sy), (tx, ty)]),
                score=float(excess),
                attributes={
                    "detour_ratio": round(float(ratio), 3),
                    "cycle_dist_m": round(float(dist), 1),
                    "crow_m": round(float(crow), 1),
                },
                explanation=(
                    f"Directness deficit: the cycle route is {ratio:.1f}× the "
                    f"straight distance ({dist:.0f} m vs {crow:.0f} m). A more "
                    f"direct link would save about {excess:.0f} m."
                ),
            ))
            seq += 1

    opportunities.sort(key=lambda o: o.score, reverse=True)
    opportunities = opportunities[:max_results]
    for rank, opp in enumerate(opportunities, start=1):
        opp.rank = rank
    return to_geodataframe(opportunities, crs=crs)
