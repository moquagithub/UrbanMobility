"""Elevation & slope (Foundation v2a, BL-23).

Slope is what the relief detector D10 (and, later, a demand-weighted D13) needs.
Elevation comes from a **Digital Elevation Model (DEM)**, which is external data
— so this module keeps it **pluggable and OFF by default**: detectors run
without a DEM, and a real raster is opted in via the ``BICYCLELANE_DEM``
environment variable (same pattern as ``BICYCLELANE_INSEE`` etc.).

An *elevation sampler* is anything with ``sample(coords) -> list[float]`` mapping
metric ``(x, y)`` points to metres of elevation. Tests use :class:`CallableSampler`
over a synthetic surface; production uses :class:`RasterElevationSampler` (needs
the optional ``rasterio`` dependency). :func:`edge_grades` turns per-edge endpoint
elevations into a slope percentage.
"""

from __future__ import annotations

import os
from typing import Callable, Protocol, Sequence

import geopandas as gpd
import pandas as pd


class ElevationSampler(Protocol):
    """Maps metric ``(x, y)`` points to elevation in metres."""

    def sample(self, coords: Sequence[tuple[float, float]]) -> list[float]:
        ...


class CallableSampler:
    """Wrap a plain ``f(x, y) -> elevation`` function as an ElevationSampler."""

    def __init__(self, func: Callable[[float, float], float]):
        self._func = func

    def sample(self, coords: Sequence[tuple[float, float]]) -> list[float]:
        return [float(self._func(x, y)) for (x, y) in coords]


class RasterElevationSampler:
    """Sample a DEM raster file (optional ``rasterio`` dependency).

    Coordinates passed to :meth:`sample` must be in the raster's own CRS. Reads
    the first band; points outside the raster yield the raster nodata sentinel
    (callers should treat non-finite grades as missing).
    """

    def __init__(self, path: str):
        try:
            import rasterio
        except ImportError as exc:  # pragma: no cover - env-specific
            raise ImportError(
                "RasterElevationSampler needs the optional 'rasterio' package "
                "(pip install rasterio)."
            ) from exc
        # Open once to validate the path/format up front (so a bad path fails
        # here and dem_sampler_from_env can fall back to None).
        with rasterio.open(path):
            pass
        self.path = path

    def sample(self, coords: Sequence[tuple[float, float]]) -> list[float]:
        import rasterio

        with rasterio.open(self.path) as ds:
            return [float(v[0]) for v in ds.sample(coords)]


def dem_sampler_from_env(var: str = "BICYCLELANE_DEM") -> ElevationSampler | None:
    """Build a :class:`RasterElevationSampler` from ``$BICYCLELANE_DEM`` or ``None``.

    Returns ``None`` when the variable is unset or the raster cannot be opened,
    so slope-using detectors silently degrade to "no relief signal" — the
    default v1 behaviour.
    """
    path = os.environ.get(var)
    if not path:
        return None
    try:
        return RasterElevationSampler(path)
    except Exception:  # pragma: no cover - env-specific
        return None


def _endpoints(geom) -> tuple[tuple[float, float], tuple[float, float]] | None:
    if geom is None or geom.is_empty:
        return None
    try:
        coords = list(geom.coords)
    except NotImplementedError:  # e.g. MultiLineString
        merged = getattr(geom, "geoms", None)
        if not merged:
            return None
        first = list(list(merged)[0].coords)
        last = list(list(merged)[-1].coords)
        if not first or not last:
            return None
        return (first[0][0], first[0][1]), (last[-1][0], last[-1][1])
    if len(coords) < 2:
        return None
    return (coords[0][0], coords[0][1]), (coords[-1][0], coords[-1][1])


def edge_grades(
    edges: gpd.GeoDataFrame,
    sampler: ElevationSampler,
    *,
    min_run_m: float = 10.0,
) -> pd.Series:
    """Slope percentage per edge = ``100·|Δelevation| / run`` (run ≥ ``min_run_m``).

    Elevation is sampled at each edge's two endpoints (which must be in the same
    CRS the sampler expects). The run is the edge's planar length, floored at
    ``min_run_m`` so very short edges do not blow the grade up. Edges without
    usable geometry get ``NaN``.
    """
    starts: list[tuple[float, float]] = []
    ends: list[tuple[float, float]] = []
    valid: list[int] = []
    runs: list[float] = []
    for pos, (idx, geom) in enumerate(edges.geometry.items()):
        ep = _endpoints(geom)
        if ep is None:
            continue
        starts.append(ep[0])
        ends.append(ep[1])
        valid.append(pos)
        runs.append(max(float(geom.length), min_run_m))

    out = pd.Series([float("nan")] * len(edges), index=edges.index, dtype=float)
    if not valid:
        return out

    z0 = sampler.sample(starts)
    z1 = sampler.sample(ends)
    grades = [100.0 * abs(b - a) / run for a, b, run in zip(z0, z1, runs)]
    for pos, g in zip(valid, grades):
        out.iloc[pos] = g
    return out
