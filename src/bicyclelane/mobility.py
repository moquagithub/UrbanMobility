"""Origin–Destination flows for the modal-shift detector D14 (Foundation v2b).

D14 looks for **corridors carrying many short car trips** that could shift to
cycling. That needs an OD matrix — where people drive from/to. This module keeps
the detector decoupled from any single source by working on a generic **OD
desire-line** layer (a straight line per origin→destination pair with a car-trip
``flow``), and provides:

* :func:`load_od_csv` — a generic OD CSV (``o_lon, o_lat, d_lon, d_lat, flow``),
  kept to short bikeable distances.
* :func:`load_mobpro` — build that layer from INSEE **MOBPRO** (home–work
  flows, commune granularity) joined to commune centroids: car mode only
  (``TRANS = 5``), weighted by ``IPONDI``.
* :func:`od_from_env` — opt in via ``BICYCLELANE_OD`` (a generic OD CSV).

All OFF by default: no file → D14 is empty.
"""

from __future__ import annotations

import math
import os
from typing import Iterable, Optional

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

# MOBPRO transport mode = car/van (5). 3 = bicycle, 6 = public transport, etc.
CAR_TRANS = {"5"}


def _haversine_km(lon1, lat1, lon2, lat2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _pick(columns, candidates):
    low = {str(c).strip().lower(): c for c in columns}
    for c in candidates:
        if c in low:
            return low[c]
    return None


def _build_lines(records, *, min_km, max_km, bbox) -> gpd.GeoDataFrame:
    """records: iterable of (o_lon, o_lat, d_lon, d_lat, flow). Keep bikeable,
    non-degenerate, in-bbox desire lines."""
    geoms, flows, kms = [], [], []
    for olon, olat, dlon, dlat, flow in records:
        if None in (olon, olat, dlon, dlat) or not flow or flow <= 0:
            continue
        km = _haversine_km(olon, olat, dlon, dlat)
        if km < min_km or km > max_km:
            continue
        if bbox is not None:
            w, s, e, n = bbox
            # keep if either endpoint is inside the area of interest
            in_o = w <= olon <= e and s <= olat <= n
            in_d = w <= dlon <= e and s <= dlat <= n
            if not (in_o or in_d):
                continue
        geoms.append(LineString([(olon, olat), (dlon, dlat)]))
        flows.append(float(flow))
        kms.append(round(km, 2))
    return gpd.GeoDataFrame({"flow": flows, "km": kms}, geometry=geoms, crs="EPSG:4326")


def load_od_csv(
    path: str, *, min_km: float = 0.3, max_km: float = 5.0, bbox: Optional[tuple] = None,
) -> gpd.GeoDataFrame:
    """Load a generic OD CSV (``o_lon, o_lat, d_lon, d_lat, flow``) as desire
    lines (WGS84), kept to ``[min_km, max_km]`` straight-line distance."""
    df = pd.read_csv(path, sep=None, engine="python", dtype=str)
    c = {k: _pick(df.columns, v) for k, v in {
        "olon": ("o_lon", "olon", "origin_lon", "x_o"),
        "olat": ("o_lat", "olat", "origin_lat", "y_o"),
        "dlon": ("d_lon", "dlon", "dest_lon", "x_d"),
        "dlat": ("d_lat", "dlat", "dest_lat", "y_d"),
        "flow": ("flow", "trips", "count", "ipondi", "nb"),
    }.items()}
    if any(v is None for v in c.values()):
        raise ValueError(f"{os.path.basename(path)}: need columns o_lon,o_lat,"
                         f"d_lon,d_lat,flow (case-insensitive).")
    num = {k: pd.to_numeric(df[c[k]], errors="coerce") for k in c}
    records = zip(num["olon"], num["olat"], num["dlon"], num["dlat"], num["flow"])
    return _build_lines(
        [(a, b, cc, d, e) for a, b, cc, d, e in records],
        min_km=min_km, max_km=max_km, bbox=bbox)


def load_mobpro(
    flows_path: str,
    centroids: dict[str, tuple[float, float]] | str,
    *,
    car_trans: Iterable[str] = CAR_TRANS,
    min_km: float = 0.3,
    max_km: float = 5.0,
    bbox: Optional[tuple] = None,
) -> gpd.GeoDataFrame:
    """Build OD desire lines from INSEE MOBPRO home–work flows.

    ``flows_path`` is the MOBPRO CSV (columns ``COMMUNE`` residence, ``DCLT``
    work commune, ``TRANS`` mode, ``IPONDI`` weight). ``centroids`` maps a
    commune code to ``(lon, lat)`` — either a dict or a CSV path with columns
    ``code, lon, lat``. Keeps car trips (``TRANS`` in ``car_trans``), aggregates
    ``IPONDI`` per (residence, work), and draws a line between the two centroids.
    """
    if isinstance(centroids, str):
        cdf = pd.read_csv(centroids, sep=None, engine="python", dtype=str)
        code = _pick(cdf.columns, ("code", "codgeo", "insee", "com", "depcom"))
        lon = _pick(cdf.columns, ("lon", "longitude", "x"))
        lat = _pick(cdf.columns, ("lat", "latitude", "y"))
        centroids = {str(r[code]): (float(r[lon]), float(r[lat]))
                     for _, r in cdf.iterrows()
                     if r[code] is not None and r[lon] and r[lat]}

    df = pd.read_csv(flows_path, sep=None, engine="python", dtype=str)
    o_c = _pick(df.columns, ("commune", "codgeo", "comm"))
    d_c = _pick(df.columns, ("dclt", "dclt_code", "trav"))
    t_c = _pick(df.columns, ("trans",))
    w_c = _pick(df.columns, ("ipondi", "nbflux", "flow", "nb"))
    if o_c is None or d_c is None or w_c is None:
        raise ValueError(f"{os.path.basename(flows_path)}: need COMMUNE, DCLT "
                         f"and IPONDI columns.")
    car = set(car_trans)
    agg: dict[tuple, float] = {}
    for _, r in df.iterrows():
        if t_c is not None and str(r[t_c]).strip() not in car:
            continue
        try:
            w = float(r[w_c])
        except (TypeError, ValueError):
            continue
        key = (str(r[o_c]), str(r[d_c]))
        agg[key] = agg.get(key, 0.0) + w

    records = []
    for (o, d), flow in agg.items():
        if o == d or o not in centroids or d not in centroids:
            continue
        olon, olat = centroids[o]
        dlon, dlat = centroids[d]
        records.append((olon, olat, dlon, dlat, flow))
    return _build_lines(records, min_km=min_km, max_km=max_km, bbox=bbox)


def route_od(od_gdf: gpd.GeoDataFrame, road_graph) -> gpd.GeoDataFrame:
    """Snap each OD desire line to the road network and replace it with the
    shortest on-road path, so the corridor follows real streets.

    Commune-to-commune straight desire lines cut across the landscape and rarely
    align with the road network; routing each pair (nearest road node → shortest
    path → the path's edges) makes the corridor land on actual streets. An
    endpoint outside the analysed graph snaps to the nearest boundary road, which
    correctly yields an in-city corridor toward that external destination.
    Returns desire lines carrying the same ``flow``, in the graph's CRS.
    """
    import networkx as nx
    import numpy as np
    from scipy.spatial import cKDTree

    G = road_graph
    crs = G.graph.get("crs")
    nodes = list(G.nodes)
    if not nodes:
        return gpd.GeoDataFrame({"flow": []}, geometry=[], crs=crs)
    xy = np.array([[G.nodes[n]["x"], G.nodes[n]["y"]] for n in nodes], dtype=float)
    tree = cKDTree(xy)
    Gu = nx.Graph(G)  # undirected, simple — enough for shortest-path corridors

    od = od_gdf.to_crs(crs) if od_gdf.crs != crs else od_gdf
    geoms, flows = [], []
    dijkstra = {}
    for geom, flow in zip(od.geometry, od["flow"]):
        if geom is None or geom.is_empty:
            continue
        (ox, oy), (dx, dy) = geom.coords[0], geom.coords[-1]
        n0 = nodes[int(tree.query([ox, oy])[1])]
        n1 = nodes[int(tree.query([dx, dy])[1])]
        if n0 == n1:
            continue
        try:
            path = nx.shortest_path(Gu, n0, n1, weight="length")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
        pts = [(G.nodes[n]["x"], G.nodes[n]["y"]) for n in path]
        if len(pts) >= 2:
            geoms.append(LineString(pts))
            flows.append(float(flow))
    return gpd.GeoDataFrame({"flow": flows}, geometry=geoms, crs=crs)


def od_from_env(var: str = "BICYCLELANE_OD", *, bbox: Optional[tuple] = None):
    """Load OD desire lines from ``$BICYCLELANE_OD`` (a generic OD CSV) or
    ``None`` when unset/unreadable — so D14 degrades to empty."""
    path = os.environ.get(var)
    if not path or not os.path.isfile(path):
        return None
    try:
        g = load_od_csv(path, bbox=bbox)
        return g if len(g) > 0 else None
    except Exception:  # pragma: no cover - defensive
        return None
