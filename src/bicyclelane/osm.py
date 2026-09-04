"""Extraction of the CYCLE network for a place, with an on-disk cache.

This is a thin wrapper around OSMnx. It is intentionally *not* exercised by the
offline test-suite (it needs live Overpass access), but it is written to be
runnable and is used by :mod:`bicyclelane.demo`.

Cache
-----
Downloaded graphs are pickled to a cache directory, keyed by ``place`` and the
extraction ``date`` (default: today). This makes repeated runs cheap and keeps
results reproducible for a given day.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import pickle
from pathlib import Path
from typing import Optional

import networkx as nx

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache" / "cycle"

# OSM filter selecting infrastructure usable/dedicated to cycling. Kept broad so
# the cycle graph reflects real cycle provision (dedicated lanes, tracks, and
# ways tagged as bicycle-designated).
CYCLE_CUSTOM_FILTER = (
    '["highway"~"cycleway"]'
    '["area"!~"yes"]'
)


def _bbox_suffix(bbox: Optional[tuple] = None) -> str:
    """Cache-key suffix for a bbox: empty when None (so place-only keys stay
    byte-identical to the pre-bbox behaviour and existing caches remain valid),
    else ``|w|s|e|n`` with each coord rounded to 5 decimals."""
    if bbox is None:
        return ""
    return "|" + "|".join(f"{round(float(c), 5)}" for c in bbox)


def _cache_key(place: str, date: str, extra: str = "", bbox: Optional[tuple] = None) -> str:
    raw = f"cycle|{place}|{date}|{extra}{_bbox_suffix(bbox)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def get_cycle_graph(
    place: str,
    *,
    bbox: Optional[tuple] = None,
    date: Optional[str] = None,
    cache_dir: Path | str = DEFAULT_CACHE_DIR,
    use_cache: bool = True,
) -> nx.MultiDiGraph:
    """Download (or load from cache) the cycle network for ``place``.

    Parameters
    ----------
    place:
        A geocodable place name, e.g. ``"Aix-en-Provence, France"``. When
        ``bbox`` is given ``place`` may be a plain label (used only in the cache
        key), since the extraction is driven by the bbox.
    bbox:
        Optional area of interest ``(west, south, east, north)`` in EPSG:4326
        (lon/lat). When ``None`` (default) the whole ``place`` is fetched via
        ``graph_from_place``; when given, only the bbox is fetched via
        ``graph_from_bbox`` (same cycle filter). The bbox is folded into the
        cache key so it caches separately from the place query.
    date:
        ISO date string used in the cache key. Defaults to today's date.
    cache_dir:
        Directory where pickled graphs are stored.
    use_cache:
        When ``True`` (default) a cached graph is reused if present.

    Returns
    -------
    networkx.MultiDiGraph
        The cycle graph in WGS84 (unprojected). Reproject with
        :func:`bicyclelane.crs.project_graph` before measuring.
    """
    import osmnx as ox

    date = date or _dt.date.today().isoformat()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{_cache_key(place, date, bbox=bbox)}.gpickle"

    if use_cache and cache_path.exists():
        with cache_path.open("rb") as fh:
            return pickle.load(fh)

    if bbox is None:
        graph = ox.graph_from_place(
            place,
            custom_filter=CYCLE_CUSTOM_FILTER,
            retain_all=True,
            simplify=True,
        )
    else:
        graph = ox.graph_from_bbox(
            bbox,
            custom_filter=CYCLE_CUSTOM_FILTER,
            retain_all=True,
            simplify=True,
        )
    with cache_path.open("wb") as fh:
        pickle.dump(graph, fh)
    return graph
