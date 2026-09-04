r"""D4 - Coverage / density cold-spot detector (BL-13).

Goal
----
Surface **built city zones that are statistically under-served by cycle
infrastructure** -- a pure *supply-side* signal (unlike D5, which contrasts
supply against demand). D4 answers: "which built-up areas have a robustly
below-average density of cycle lanes compared to their surroundings?".

Algorithm (BicycleLane panorama, detector D4)
---------------------------------------------
1. Cover the city boundary with a regular square grid (metric CRS).
2. **Cycle density** per cell: clipped cycle-lane length / cell area, in km per
   km².
3. **Built mask**: measure road length per cell and keep only cells carrying at
   least ``built_road_len_m`` metres of road. This restricts the analysis to
   built-up land and avoids flagging parks / rivers / rural cells as
   opportunities -- with no extra OSM fetch (roads are already available).
4. **Getis-Ord Gi\*** cold-spot statistic over the *built* cells, using the
   regular grid's own adjacency (the 8 surrounding cells + self, found via a
   KD-tree on cell centroids with radius ~1.5*cell_size). Binary weights.
   A strongly **negative** Gi\* marks a robust low-density (cold) cluster.
5. Opportunity **score = −Gi\*** (higher = colder = more under-served). Keep the
   below-average cells (Gi\* < 0, i.e. score > 0), ranked by score descending.
   Each geometry is the grid cell polygon (a zone).
"""

from __future__ import annotations

from typing import Any

import numpy as np
from geopandas import GeoDataFrame
from scipy.spatial import cKDTree

from ..grid import make_grid, lane_length_per_cell
from ..schema import Opportunity, to_geodataframe


def _gi_star(x: np.ndarray, neighbours: list[np.ndarray]) -> np.ndarray:
    r"""Getis-Ord Gi\* z-scores with binary weights over a fixed adjacency.

    ``x`` is the per-(built)-cell value (density); ``neighbours[i]`` lists the
    positional indices ``j`` (including ``i`` itself) that are weighted 1 for
    cell ``i``. Returns an array of Gi\* z-scores aligned to ``x``; a fully
    degenerate surface (n<=1 or zero variance) yields all-zeros.
    """
    n = x.size
    out = np.zeros(n, dtype=float)
    if n <= 1:
        return out

    x_bar = x.mean()
    s = np.sqrt((x ** 2).mean() - x_bar ** 2)  # population std of x
    if not np.isfinite(s) or s == 0:
        return out

    for i in range(n):
        idx = neighbours[i]
        w_sum = float(idx.size)            # Σ_j w_ij  (binary weights)
        w_sq_sum = float(idx.size)         # Σ_j w_ij² (binary => w == w²)
        num = x[idx].sum() - x_bar * w_sum
        denom_inner = (n * w_sq_sum - w_sum ** 2) / (n - 1)
        if denom_inner <= 0:
            continue
        denom = s * np.sqrt(denom_inner)
        if denom == 0:
            continue
        out[i] = num / denom
    return out


def detect_coverage_coldspots(
    cycle_gdf: GeoDataFrame,
    road_gdf: GeoDataFrame,
    boundary,
    *,
    cell_size: float = 500.0,
    built_road_len_m: float = 50.0,
    city: str = "",
    crs: Any = None,
) -> GeoDataFrame:
    """Detect coverage / density **cold-spots** (under-served built zones)."""
    grid = make_grid(boundary, cell_size, crs=crs)
    work_crs = grid.crs

    # Cycle-lane density (km per km²) and road length per cell.
    grid = lane_length_per_cell(grid, cycle_gdf, length_col="lane_length_m")
    grid = lane_length_per_cell(grid, road_gdf, length_col="road_len_m")
    grid["area_km2"] = grid.geometry.area / 1e6
    grid["density"] = (grid["lane_length_m"] / 1000.0) / grid["area_km2"].replace(
        0, np.nan
    )
    grid["density"] = grid["density"].fillna(0.0)

    # Built mask: only cells with enough road are candidate zones.
    built = grid[grid["road_len_m"] >= built_road_len_m].reset_index(drop=True)
    if len(built) <= 1:
        return to_geodataframe([], crs=work_crs)

    # Grid adjacency: 8-neighbours + self via KD-tree on cell centroids.
    cents = built.geometry.centroid
    coords = np.column_stack([cents.x.to_numpy(), cents.y.to_numpy()])
    tree = cKDTree(coords)
    radius = 1.5 * cell_size
    neighbours = [np.asarray(js, dtype=int) for js in tree.query_ball_tree(tree, radius)]

    x = built["density"].to_numpy(dtype=float)
    gi = _gi_star(x, neighbours)
    built["gi_star"] = gi
    built["score"] = -gi  # colder (more negative Gi*) => higher priority

    # Keep robustly below-average cells and rank coldest first.
    cand = built[built["score"] > 0].copy()
    cand = cand.sort_values("score", ascending=False)

    opportunities: list[Opportunity] = []
    for seq, (_, row) in enumerate(cand.iterrows()):
        opportunities.append(
            Opportunity(
                id=f"D4-{seq:04d}",
                city=city,
                detector="D4",
                geometry=row.geometry,
                score=float(row["score"]),
                attributes={
                    "cell_id": int(row["cell_id"]),
                    "density": round(float(row["density"]), 4),
                    "road_len_m": round(float(row["road_len_m"]), 1),
                    "gi_star": round(float(row["gi_star"]), 4),
                    "score": round(float(row["score"]), 4),
                    "area_km2": round(float(row["area_km2"]), 4),
                },
                explanation=(
                    f"Under-served built zone: cycle density "
                    f"{row['density']:.2f} km/km² sits in a cold cluster "
                    f"(Gi*={row['gi_star']:.2f}) among nearby built cells "
                    f"({int(cell_size)} m cell, {row['road_len_m']:.0f} m of road)."
                ),
            )
        )
    for rank, opp in enumerate(opportunities, start=1):
        opp.rank = rank
    return to_geodataframe(opportunities, crs=work_crs)
