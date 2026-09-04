"""D5 - Demand/supply mismatch detector.

Goal
----
Surface **where to build new lanes in zones with high demand but low cycling
supply** -- the places where people are likely to want to cycle (many trip
generators / population) but where few or no bicycle lanes exist yet.

Two outputs are provided:

* :func:`detect_demand_supply_mismatch` -> the under-served **zones** (grid
  cells), for context / choropleth.
* :func:`detect_mismatch_segments` -> **actionable proposals**: candidate street
  segments to equip *inside* those zones (a real "build a lane here"), which is
  what the explorer surfaces.

Algorithm (BicycleLane panorama, detector D5)
---------------------------------------------
1. Cover the city boundary with a regular square grid (metric CRS).
2. **Supply** per cell: clipped cycle-lane length / cell area, in km per km².
3. **Demand** per cell: sum of POI weights falling in the cell (see
   :mod:`bicyclelane.demand`). Population (INSEE) can be added here later.
4. Standardise both to z-scores and take the **residual** ``R = z_D - z_S``.
5a. Zones: keep cells with demand > 0 and ``R >= min_residual``.
5b. Segments: inside those zones, take road edges that do **not** already carry
    cycle infrastructure, and rank them by ``R * highway_weight`` -- i.e. the
    most structural streets in the most under-served-with-demand cells first.
"""

from __future__ import annotations

from typing import Any, Optional

import geopandas as gpd
import numpy as np

from ..grid import make_grid, lane_length_per_cell
from ..schema import Opportunity, to_geodataframe

# Preference by road class when proposing a street to equip. Through-roads are
# better cycling-network backbones than service roads; fast roads are excluded.
HIGHWAY_WEIGHTS = {
    "primary": 1.0, "primary_link": 0.9,
    "secondary": 0.9, "secondary_link": 0.8,
    "tertiary": 0.8, "tertiary_link": 0.7,
    "unclassified": 0.55, "residential": 0.5, "living_street": 0.45,
    "road": 0.4, "service": 0.2,
}
HIGHWAY_EXCLUDE = {"motorway", "motorway_link", "trunk", "trunk_link"}


def _zscore(series) -> np.ndarray:
    values = np.asarray(series, dtype=float)
    std = values.std()
    if std == 0:
        return np.zeros_like(values)
    return (values - values.mean()) / std


def _first(value):
    """OSM tags are sometimes lists; return the first *string* token or None.

    Non-string values (notably missing names, which pandas represents as NaN)
    become ``None`` so callers can safely do ``name or "(unnamed)"`` -- NaN is
    truthy in Python and would otherwise leak into the output (and break JSON).
    """
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    return value if isinstance(value, str) else None


def _population_per_cell(
    grid: gpd.GeoDataFrame,
    population_gdf: gpd.GeoDataFrame,
    work_crs: Any,
) -> "np.ndarray":
    """Area-weighted aggregation of ``population`` into each grid cell.

    Both the population cells (INSEE 200 m squares) and the analysis grid cells
    are polygons, so population is apportioned by the *fraction of each
    population cell's area* that falls inside a given grid cell -- e.g. a 200 m
    cell straddling two grid cells contributes its ``population`` split by the
    overlap areas. Returns a per-cell population array aligned to ``grid`` order.
    """
    pop = population_gdf.to_crs(work_crs).copy()
    pop = pop[pop.geometry.notna() & ~pop.geometry.is_empty]
    pop["pop_cell_area"] = pop.geometry.area

    overlay = gpd.overlay(
        pop[["population", "pop_cell_area", "geometry"]],
        grid[["cell_id", "geometry"]],
        how="intersection",
        keep_geom_type=False,
    )
    if overlay.empty:
        return np.zeros(len(grid), dtype=float)

    frac = np.where(
        overlay["pop_cell_area"] > 0,
        overlay.geometry.area / overlay["pop_cell_area"],
        0.0,
    )
    overlay["pop_share"] = overlay["population"].astype(float) * frac
    per_cell = overlay.groupby("cell_id")["pop_share"].sum()
    return grid["cell_id"].map(per_cell).fillna(0.0).to_numpy(dtype=float)


def demand_supply_grid(
    cycle_gdf: gpd.GeoDataFrame,
    pois_gdf: gpd.GeoDataFrame,
    boundary: gpd.GeoDataFrame,
    *,
    cell_size: float = 500.0,
    crs: Any = None,
    population_gdf: Optional[gpd.GeoDataFrame] = None,
    pop_weight: float = 1.0,
) -> gpd.GeoDataFrame:
    """Build the grid carrying per-cell ``supply``, ``demand`` and ``residual``.

    Demand is ``D_i = pop_weight*Pop_i + Σ_k w_k·n_{k,i}`` where the POI term is
    the sum of POI weights in the cell and ``Pop_i`` is the (optional)
    area-weighted INSEE population aggregated into the cell. When
    ``population_gdf`` is ``None`` the population term is absent and the demand
    surface is byte-for-byte identical to the POI-only behaviour.
    """
    grid = make_grid(boundary, cell_size, crs=crs)
    work_crs = grid.crs
    if cycle_gdf.crs != work_crs:
        cycle_gdf = cycle_gdf.to_crs(work_crs)

    grid = lane_length_per_cell(grid, cycle_gdf)
    grid["area_km2"] = grid.geometry.area / 1e6
    grid["supply"] = (grid["lane_length_m"] / 1000.0) / grid["area_km2"].replace(0, np.nan)
    grid["supply"] = grid["supply"].fillna(0.0)

    pois = pois_gdf.to_crs(work_crs).copy()
    pois["geometry"] = pois.geometry.representative_point()
    joined = gpd.sjoin(pois, grid[["cell_id", "geometry"]], predicate="within", how="inner")
    demand = joined.groupby("cell_id")["poi_weight"].sum()
    grid["demand"] = grid["cell_id"].map(demand).fillna(0.0)

    if population_gdf is not None:
        grid["population"] = _population_per_cell(grid, population_gdf, work_crs)
        grid["demand"] = grid["demand"] + pop_weight * grid["population"]

    grid["zD"] = _zscore(grid["demand"])
    grid["zS"] = _zscore(grid["supply"])
    grid["residual"] = grid["zD"] - grid["zS"]
    return grid


def detect_demand_supply_mismatch(
    cycle_gdf: gpd.GeoDataFrame,
    pois_gdf: gpd.GeoDataFrame,
    boundary: gpd.GeoDataFrame,
    *,
    cell_size: float = 500.0,
    min_residual: float = 0.0,
    city: str = "",
    crs: Any = None,
    population_gdf: Optional[gpd.GeoDataFrame] = None,
    pop_weight: float = 1.0,
) -> gpd.GeoDataFrame:
    """Detect demand/supply mismatch **zones** (grid cells)."""
    grid = demand_supply_grid(
        cycle_gdf, pois_gdf, boundary, cell_size=cell_size, crs=crs,
        population_gdf=population_gdf, pop_weight=pop_weight,
    )
    cand = grid[(grid["demand"] > 0) & (grid["residual"] >= min_residual)].copy()
    cand = cand.sort_values("residual", ascending=False)

    opportunities: list[Opportunity] = []
    for seq, (_, row) in enumerate(cand.iterrows()):
        opportunities.append(
            Opportunity(
                id=f"D5z-{seq:04d}",
                city=city,
                detector="D5",
                geometry=row.geometry,
                score=float(row["residual"]),
                attributes={
                    "cell_id": int(row["cell_id"]),
                    "demand": round(float(row["demand"]), 3),
                    "supply": round(float(row["supply"]), 4),
                    "residual": round(float(row["residual"]), 4),
                    "area_km2": round(float(row["area_km2"]), 4),
                },
                explanation=(
                    f"Under-served vs demand: demand z={row['zD']:.2f}, "
                    f"supply z={row['zS']:.2f}, residual={row['residual']:.2f} "
                    f"({int(cell_size)} m cell, demand weight {row['demand']:.0f})."
                ),
            )
        )
    for rank, opp in enumerate(opportunities, start=1):
        opp.rank = rank
    return to_geodataframe(opportunities, crs=grid.crs)


def detect_mismatch_segments(
    cycle_gdf: gpd.GeoDataFrame,
    road_gdf: gpd.GeoDataFrame,
    pois_gdf: gpd.GeoDataFrame,
    boundary: gpd.GeoDataFrame,
    *,
    cell_size: float = 500.0,
    min_residual: float = 0.5,
    exclude_buffer_m: float = 20.0,
    max_segments: int = 400,
    city: str = "",
    crs: Any = None,
    population_gdf: Optional[gpd.GeoDataFrame] = None,
    pop_weight: float = 1.0,
) -> gpd.GeoDataFrame:
    """Propose **candidate street segments to equip** inside the mismatch zones.

    A candidate is a road edge whose centroid falls in an under-served
    high-demand cell (``demand > 0`` and ``residual >= min_residual``), that is
    not already covered by cycle infrastructure, and whose road class is
    equippable (fast roads excluded). Score = ``residual * highway_weight``.

    Each returned geometry is the road segment itself -- the proposed lane.
    """
    grid = demand_supply_grid(
        cycle_gdf, pois_gdf, boundary, cell_size=cell_size, crs=crs,
        population_gdf=population_gdf, pop_weight=pop_weight,
    )
    work_crs = grid.crs
    zones = grid[(grid["demand"] > 0) & (grid["residual"] >= min_residual)]
    if zones.empty:
        return to_geodataframe([], crs=work_crs)

    roads = road_gdf.to_crs(work_crs).reset_index(drop=True)
    cyc = cycle_gdf.to_crs(work_crs)
    served = cyc.buffer(exclude_buffer_m).union_all()

    # Attach the residual/demand of the containing under-served cell to each edge.
    reps = roads.copy()
    reps["geometry"] = reps.geometry.representative_point()
    tagged = gpd.sjoin(
        reps, zones[["cell_id", "residual", "demand", "geometry"]],
        predicate="within", how="inner",
    )

    opportunities: list[Opportunity] = []
    for idx, trow in tagged.iterrows():
        edge = roads.geometry.iloc[idx]
        if edge is None or edge.is_empty:
            continue
        hclass = _first(roads.iloc[idx].get("highway"))
        if hclass in HIGHWAY_EXCLUDE:
            continue
        hw = HIGHWAY_WEIGHTS.get(hclass, 0.3)
        # Skip edges already (mostly) served by cycle infrastructure.
        try:
            overlap = edge.intersection(served).length
        except Exception:
            overlap = 0.0
        if edge.length > 0 and overlap > 0.5 * edge.length:
            continue

        residual = float(trow["residual"])
        score = residual * hw
        if score <= 0:
            continue
        name = _first(roads.iloc[idx].get("name"))
        opportunities.append(
            Opportunity(
                id=f"D5-{idx:05d}",
                city=city,
                detector="D5",
                geometry=edge,
                score=score,
                attributes={
                    "street": name or "(unnamed)",
                    "highway": hclass or "(none)",
                    "length_m": round(float(edge.length), 1),
                    "cell_id": int(trow["cell_id"]),
                    "demand": round(float(trow["demand"]), 3),
                    "residual": round(residual, 4),
                    "highway_weight": hw,
                },
                explanation=(
                    f"Proposed lane on {name or 'this street'} "
                    f"({hclass or 'road'}, {edge.length:.0f} m) — inside an "
                    f"under-served high-demand zone (residual {residual:.2f}, "
                    f"demand weight {trow['demand']:.0f})."
                ),
            )
        )

    opportunities.sort(key=lambda o: o.score, reverse=True)
    opportunities = opportunities[:max_segments]
    for rank, opp in enumerate(opportunities, start=1):
        opp.rank = rank
    return to_geodataframe(opportunities, crs=work_crs)
