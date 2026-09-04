"""D6 - Accessibility & equity detector (BL-30).

Goal
----
Prioritise lanes in **disadvantaged, under-served** zones: areas that are both
socially deprived and poorly covered by cycle infrastructure. A zone-level,
demand-side signal. Deprivation comes from INSEE Filosofi (poverty share per
household, :func:`bicyclelane.demand.load_insee_deprivation`, opt-in via
``BICYCLELANE_INSEE`` — the same file D5 uses for population).

Score
-----
Over a regular grid: aggregate deprivation (area-weighted mean) and cycle-lane
density per cell, standardise both, and rank cells by the **residual**
``R = z(deprivation) − z(supply)`` — high need, low supply — keeping only
deprived cells (``deprivation > 0`` and ``R ≥ min_residual``). MAUP / edge
effects (T4) apply, as for all grid detectors.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import numpy as np

from ..grid import make_grid, lane_length_per_cell
from ..schema import Opportunity, to_geodataframe


def _zscore(values: np.ndarray) -> np.ndarray:
    v = np.asarray(values, dtype=float)
    sd = v.std()
    if sd == 0:
        return np.zeros_like(v)
    return (v - v.mean()) / sd


def _area_weighted_mean(grid, values_gdf, value_col: str) -> dict[int, float]:
    """Area-weighted mean of ``value_col`` over each grid cell (intensive var)."""
    inter = gpd.overlay(
        grid[["cell_id", "geometry"]], values_gdf[[value_col, "geometry"]],
        how="intersection", keep_geom_type=True)
    if inter.empty:
        return {}
    inter["_a"] = inter.geometry.area
    inter["_wv"] = inter["_a"] * inter[value_col].astype(float)
    agg = inter.groupby("cell_id").agg(_wv=("_wv", "sum"), _a=("_a", "sum"))
    return {int(cid): (r["_wv"] / r["_a"]) if r["_a"] > 0 else 0.0
            for cid, r in agg.iterrows()}


def detect_equity_gaps(
    cycle_gdf: gpd.GeoDataFrame,
    deprivation_gdf: gpd.GeoDataFrame | None,
    boundary: gpd.GeoDataFrame,
    *,
    city: str = "",
    crs: Any = None,
    cell_size: float = 500.0,
    min_residual: float = 0.0,
    max_results: int = 500,
) -> gpd.GeoDataFrame:
    """Rank under-served disadvantaged zones (``detector == "D6"``).

    ``deprivation_gdf`` carries a ``deprivation`` column in ``[0, 1]`` (see
    :func:`bicyclelane.demand.load_insee_deprivation`). Returns grid-cell zones
    with ``score`` = residual, ``deprivation`` and ``supply``. Empty when no
    deprivation layer is supplied.
    """
    work_crs = crs if crs is not None else cycle_gdf.crs
    if deprivation_gdf is None or len(deprivation_gdf) == 0:
        return to_geodataframe([], crs=crs)

    grid = make_grid(boundary, cell_size, crs=work_crs)
    if grid.empty:
        return to_geodataframe([], crs=crs)
    grid = lane_length_per_cell(grid, cycle_gdf.to_crs(work_crs) if
                                cycle_gdf.crs != work_crs else cycle_gdf)

    dep = deprivation_gdf.to_crs(work_crs) if deprivation_gdf.crs != work_crs \
        else deprivation_gdf
    dep = dep[dep.geometry.notna() & ~dep.geometry.is_empty]
    # Clip to the grid extent so a national deprivation layer does not blow up
    # the overlay (the pipeline already prunes, but keep the detector robust).
    minx, miny, maxx, maxy = grid.total_bounds
    dep = dep.cx[minx:maxx, miny:maxy]
    means = _area_weighted_mean(grid, dep, "deprivation")
    grid["deprivation"] = grid["cell_id"].map(means).fillna(0.0)

    area_km2 = (cell_size / 1000.0) ** 2
    grid["supply"] = grid["lane_length_m"] / 1000.0 / area_km2  # km per km²
    grid["zD"] = _zscore(grid["deprivation"].to_numpy())
    grid["zS"] = _zscore(grid["supply"].to_numpy())
    grid["residual"] = grid["zD"] - grid["zS"]

    cand = grid[(grid["deprivation"] > 0) & (grid["residual"] >= min_residual)]
    cand = cand.sort_values("residual", ascending=False).head(max_results)

    opportunities: list[Opportunity] = []
    for seq, (_, row) in enumerate(cand.iterrows()):
        opportunities.append(Opportunity(
            id=f"D6-{seq}",
            city=city,
            detector="D6",
            geometry=row.geometry,
            score=float(row["residual"]),
            rank=seq + 1,
            attributes={
                "deprivation": round(float(row["deprivation"]), 3),
                "supply": round(float(row["supply"]), 3),
                "residual": round(float(row["residual"]), 4),
                "cell_id": int(row["cell_id"]),
            },
            explanation=(
                f"Under-served disadvantaged zone: poverty share "
                f"{row['deprivation']:.0%}, cycle supply z={row['zS']:.2f}, "
                f"residual={row['residual']:.2f}. Equity priority for a new lane."
            ),
        ))
    return to_geodataframe(opportunities, crs=crs)
