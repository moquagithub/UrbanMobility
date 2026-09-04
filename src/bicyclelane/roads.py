"""Extraction of the full ROAD (drive) network for a place, with a cache.

The road network provides the *candidate support* for new bicycle lanes: D1
checks that a proposed connector is physically feasible along existing roads,
and D2 groups road edges into corridors. Like :mod:`bicyclelane.osm`, this is a
thin, cached OSMnx wrapper not exercised by the offline tests.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import pickle
from pathlib import Path
from typing import Optional

import networkx as nx

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache" / "roads"


def _bbox_suffix(bbox: Optional[tuple] = None) -> str:
    """Cache-key suffix for a bbox: empty when None (so place-only keys stay
    byte-identical to the pre-bbox behaviour and existing caches remain valid),
    else ``|w|s|e|n`` with each coord rounded to 5 decimals."""
    if bbox is None:
        return ""
    return "|" + "|".join(f"{round(float(c), 5)}" for c in bbox)


def _cache_key(place: str, date: str, network_type: str, bbox: Optional[tuple] = None) -> str:
    raw = f"roads|{place}|{date}|{network_type}{_bbox_suffix(bbox)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def get_road_graph(
    place: str,
    *,
    bbox: Optional[tuple] = None,
    network_type: str = "drive",
    date: Optional[str] = None,
    cache_dir: Path | str = DEFAULT_CACHE_DIR,
    use_cache: bool = True,
) -> nx.MultiDiGraph:
    """Download (or load from cache) the road network for ``place``.

    Parameters
    ----------
    place:
        A geocodable place name. When ``bbox`` is given ``place`` may be a plain
        label (used only in the cache key).
    bbox:
        Optional area of interest ``(west, south, east, north)`` in EPSG:4326
        (lon/lat). When ``None`` (default) the whole ``place`` is fetched via
        ``graph_from_place``; when given, only the bbox is fetched via
        ``graph_from_bbox`` (same ``network_type``). The bbox is folded into the
        cache key so it caches separately from the place query.
    network_type:
        OSMnx network type; ``"drive"`` by default (the drivable road network).
    date:
        ISO date used in the cache key. Defaults to today.
    cache_dir:
        Directory for pickled graphs.
    use_cache:
        Reuse a cached graph when present.

    Returns
    -------
    networkx.MultiDiGraph
        The road graph in WGS84 (unprojected).
    """
    import osmnx as ox

    date = date or _dt.date.today().isoformat()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{_cache_key(place, date, network_type, bbox=bbox)}.gpickle"

    if use_cache and cache_path.exists():
        with cache_path.open("rb") as fh:
            return pickle.load(fh)

    if bbox is None:
        graph = ox.graph_from_place(
            place,
            network_type=network_type,
            retain_all=True,
            simplify=True,
        )
    else:
        graph = ox.graph_from_bbox(
            bbox,
            network_type=network_type,
            retain_all=True,
            simplify=True,
        )
    with cache_path.open("wb") as fh:
        pickle.dump(graph, fh)
    return graph
