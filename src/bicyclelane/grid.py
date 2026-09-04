"""Regular square grid over a boundary + clipped lane length per cell.

This supports the BL-5 style density product: cover a city boundary with a
regular square grid (in metric CRS), then measure how many metres of cycle lane
fall inside each cell. The result is a GeoDataFrame of cells carrying a
``lane_length_m`` column, ready for choropleth mapping.
"""

from __future__ import annotations

from typing import Optional

import geopandas as gpd
import numpy as np
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from .crs import project_gdf


def make_grid(
    boundary: gpd.GeoDataFrame | BaseGeometry,
    cell_size: float,
    *,
    crs: Optional[str] = None,
) -> gpd.GeoDataFrame:
    """Build a regular square grid covering ``boundary``.

    Parameters
    ----------
    boundary:
        The area to tile, as a GeoDataFrame or a single shapely geometry. If a
        GeoDataFrame in a geographic CRS is passed it is reprojected to a metric
        CRS first (so ``cell_size`` is in metres).
    cell_size:
        Side length of each square cell, in the units of the working CRS
        (metres when metric).
    crs:
        Optional explicit metric CRS to project into. If ``None`` and the input
        is geographic, the local UTM zone is estimated.

    Returns
    -------
    geopandas.GeoDataFrame
        Grid cells (only those intersecting the boundary) with a ``cell_id``
        column, in the metric CRS.
    """
    if isinstance(boundary, gpd.GeoDataFrame):
        gdf = boundary
        if gdf.crs is not None and not gdf.crs.is_projected:
            gdf = project_gdf(gdf, crs)
        work_crs = gdf.crs
        area = gdf.union_all()
    else:
        area = boundary
        work_crs = crs

    minx, miny, maxx, maxy = area.bounds
    xs = np.arange(minx, maxx + cell_size, cell_size)
    ys = np.arange(miny, maxy + cell_size, cell_size)

    cells = []
    for x0 in xs[:-1]:
        for y0 in ys[:-1]:
            cell = Polygon(
                [
                    (x0, y0),
                    (x0 + cell_size, y0),
                    (x0 + cell_size, y0 + cell_size),
                    (x0, y0 + cell_size),
                ]
            )
            if cell.intersects(area):
                cells.append(cell)

    grid = gpd.GeoDataFrame(
        {"cell_id": range(len(cells))}, geometry=cells, crs=work_crs
    )
    return grid


def lane_length_per_cell(
    grid: gpd.GeoDataFrame,
    lanes: gpd.GeoDataFrame,
    *,
    length_col: str = "lane_length_m",
) -> gpd.GeoDataFrame:
    """Compute clipped cycle-lane length inside each grid cell.

    Each lane geometry is intersected with each intersecting cell and the length
    of the clipped piece is summed per cell.

    Parameters
    ----------
    grid:
        Grid cells (metric CRS), e.g. from :func:`make_grid`.
    lanes:
        Cycle-lane geometries (LineStrings / MultiLineStrings) in the *same*
        metric CRS as ``grid``.
    length_col:
        Name of the output column holding the per-cell metres.

    Returns
    -------
    geopandas.GeoDataFrame
        A copy of ``grid`` with the ``length_col`` column added.
    """
    if grid.crs != lanes.crs:
        lanes = lanes.to_crs(grid.crs)

    out = grid.copy()
    out[length_col] = 0.0

    # Spatial index over lanes for efficiency.
    sindex = lanes.sindex
    for idx, cell in out.geometry.items():
        candidate_pos = list(sindex.intersection(cell.bounds))
        if not candidate_pos:
            continue
        candidates = lanes.iloc[candidate_pos]
        total = 0.0
        for geom in candidates.geometry:
            if geom is None or geom.is_empty:
                continue
            clipped = geom.intersection(cell)
            if not clipped.is_empty:
                total += clipped.length
        out.at[idx, length_col] = total

    return out
