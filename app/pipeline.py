"""City analysis pipeline for the web MVP.

Runs the available detectors for a city, normalises each detector's score to
[0, 1] (so criteria are comparable when the user weights them), and returns a
compact JSON-serialisable payload: detector metadata, the existing cycle
network (base layer) and the opportunities as GeoJSON in EPSG:4326.

Results are cached in-process per (place, cap) so slider/weight exploration on
the client never re-runs the extraction.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import osmnx as ox
from shapely.geometry import box, mapping

from bicyclelane.crs import project_graph
from bicyclelane.grid import make_grid
from bicyclelane.aggregate import composite_grid, composite_geojson
from bicyclelane.osm import get_cycle_graph
from bicyclelane.roads import get_road_graph
from bicyclelane.demand import get_pois
from bicyclelane.places import (
    get_stations, get_generators, get_green, get_tourism,
    load_station_ridership, load_school_enrolment, apply_nearest_value,
)
from bicyclelane.terrain import dem_sampler_from_env
from bicyclelane.baac import baac_from_env
from bicyclelane.mobility import od_from_env
from bicyclelane.detectors.missing_links import detect_missing_links
from bicyclelane.detectors.continuity import detect_continuity_gaps
from bicyclelane.detectors.resilience import detect_network_resilience
from bicyclelane.detectors.directness import detect_directness_deficit
from bicyclelane.detectors.comfort import detect_comfort_deficit
from bicyclelane.detectors.relief import detect_green_corridors
from bicyclelane.detectors.tourism import detect_tourism_corridors
from bicyclelane.detectors.safety import detect_safety_hotspots
from bicyclelane.detectors.equity import detect_equity_gaps
from bicyclelane.detectors.modalshift import detect_modal_shift
from bicyclelane.detectors.coverage import detect_coverage_coldspots
from bicyclelane.detectors.mismatch import (
    detect_demand_supply_mismatch,
    detect_mismatch_segments,
)
from bicyclelane.detectors.intermodal import detect_intermodal_access
from bicyclelane.detectors.generators import detect_generator_access

# Detector registry: key -> display metadata.
DETECTORS = [
    {"key": "D1", "label": "Missing links", "color": "#c2410c", "geometry": "line"},
    {"key": "D2", "label": "Route continuity", "color": "#0f766e", "geometry": "line"},
    {"key": "D3", "label": "Network resilience", "color": "#0891b2", "geometry": "line"},
    {"key": "D4", "label": "Coverage / density", "color": "#16a34a", "geometry": "zone"},
    {"key": "D5", "label": "Demand–supply mismatch", "color": "#7c3aed", "geometry": "zone"},
    {"key": "D6", "label": "Accessibility & equity", "color": "#155e75", "geometry": "zone"},
    {"key": "D7", "label": "Safety / crashes", "color": "#e11d48", "geometry": "line"},
    {"key": "D8", "label": "Comfort / experience", "color": "#db2777", "geometry": "line"},
    {"key": "D10", "label": "Relief & green corridors", "color": "#4d7c0f", "geometry": "line"},
    {"key": "D9", "label": "Intermodality / stations", "color": "#0369a1", "geometry": "line"},
    {"key": "D11", "label": "Generators / schools", "color": "#b91c1c", "geometry": "line"},
    {"key": "D12", "label": "Tourism & leisure", "color": "#a21caf", "geometry": "line"},
    {"key": "D13", "label": "Directness / detour", "color": "#a16207", "geometry": "line"},
    {"key": "D14", "label": "Modal shift (short car trips)", "color": "#4338ca", "geometry": "line"},
]

_CACHE: dict[tuple[str, int], dict[str, Any]] = {}

# Relative weight of INSEE population vs summed POI weights in the D5 demand
# surface. Only used when population is enabled (see _load_population); tune once
# a real INSEE file is wired in.
POP_WEIGHT = 0.05
_POP_CACHE: dict[Any, Any] = {}
_DEP_CACHE: dict[Any, Any] = {}


def _to_3035_bbox(wgs_bbox):
    """Convert a (west, south, east, north) WGS84 bbox to EPSG:3035 (the INSEE
    grid CRS), so the loader can prune the national file to the city cheaply."""
    if wgs_bbox is None:
        return None
    try:
        return tuple(gpd.GeoSeries([box(*wgs_bbox)], crs="EPSG:4326")
                     .to_crs("EPSG:3035").total_bounds)
    except Exception:
        return None


def _load_insee(work_crs, wgs_bbox, kind, cache):
    """Shared loader for the optional INSEE Filosofi file (``BICYCLELANE_INSEE``).

    ``kind`` is ``"population"`` (D5) or ``"deprivation"`` (D6). The city bbox is
    pushed down as an EPSG:3035 filter so only the city's 200 m cells are parsed
    from the (national, ~2.5 M-row) file. Returns ``None`` when unset/unreadable.
    """
    import os

    path = os.environ.get("BICYCLELANE_INSEE")
    if not path:
        return None
    key = (path, tuple(round(v, 2) for v in wgs_bbox) if wgs_bbox else None)
    if key not in cache:
        try:
            from bicyclelane import demand
            loader = (demand.load_insee_population if kind == "population"
                      else demand.load_insee_deprivation)
            gdf = loader(path, bbox=_to_3035_bbox(wgs_bbox))
            cache[key] = gdf.to_crs(work_crs) if gdf is not None else None
        except Exception:
            cache[key] = None
    return cache[key]


def _load_population(work_crs, wgs_bbox=None):
    """Optional INSEE gridded population (D5), enabled via ``BICYCLELANE_INSEE``."""
    return _load_insee(work_crs, wgs_bbox, "population", _POP_CACHE)


def _load_deprivation(work_crs, wgs_bbox=None):
    """Optional INSEE deprivation / poverty share (D6), same file as population."""
    return _load_insee(work_crs, wgs_bbox, "deprivation", _DEP_CACHE)


def _apply_optional_weight(gdf, env_var, loader, value_col, out_col):
    """Override ``gdf[out_col]`` from a real dataset (nearest match) when the
    ``env_var`` points at a local file (e.g. SNCF ridership / EN enrolment).
    Off by default -> the type-based proxy weights are kept."""
    import os

    path = os.environ.get(env_var)
    if not path:
        return gdf
    try:
        ref = loader(path)
        return apply_nearest_value(gdf, ref, value_col, out_col)
    except Exception:
        return gdf


def _normalise(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [1.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def _features(gdf, detector_key: str, cap: int) -> list[dict[str, Any]]:
    if gdf is None or len(gdf) == 0:
        return []
    g = gdf.sort_values("score", ascending=False).head(cap).to_crs(4326)
    norms = _normalise([float(s) for s in g["score"]])
    feats: list[dict[str, Any]] = []
    for (_, row), norm in zip(g.iterrows(), norms):
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        props = {
            "id": row.get("id"),
            "detector": detector_key,
            "score": round(float(row["score"]), 6),
            "score_norm": round(float(norm), 6),
            "explanation": row.get("explanation", ""),
        }
        numeric = ("score_nodes", "gap_m", "road_len_m", "gap_len_m",
                   "continuity_index", "demand", "supply", "residual",
                   "area_km2", "length_m", "cell_id",
                   "hub_dist_m", "hub_weight", "coverage", "gen_dist_m", "safe_coverage",
                   "density", "gi_star", "cut_nodes", "component_nodes",
                   "detour_ratio", "cycle_dist_m", "crow_m",
                   "comfort_deficit", "speed_stress", "surface_q", "maxspeed_kmh",
                   "green_dist_m", "grade_pct", "attractor_pull", "n_attractors",
                   "crash_severity", "n_crashes", "deprivation", "shift_flow")
        text = ("street", "highway", "hub", "generator")
        for extra in numeric:
            val = row[extra] if extra in row else None
            # Skip missing values: None (an all-None column stays object dtype,
            # so the NaN test alone is not enough) and NaN (val != val).
            if val is not None and val == val:
                props[extra] = round(float(val), 4)
        for extra in text:
            if extra in row and isinstance(row[extra], str):
                props[extra] = row[extra]
        feats.append({"type": "Feature", "geometry": mapping(geom), "properties": props})
    return feats


def _zone_segments(seg_gdf, *, cap_per_zone: int = 30) -> dict[str, list[dict[str, Any]]]:
    """Group proposed lane segments by their zone ``cell_id`` (GeoJSON, WGS84)."""
    if seg_gdf is None or len(seg_gdf) == 0:
        return {}
    g = seg_gdf.sort_values("score", ascending=False).to_crs(4326)
    out: dict[str, list[dict[str, Any]]] = {}
    for _, row in g.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        cid = str(int(row["cell_id"]))
        bucket = out.setdefault(cid, [])
        if len(bucket) >= cap_per_zone:
            continue
        bucket.append({
            "type": "Feature",
            "geometry": mapping(geom),
            "properties": {
                "id": row.get("id"),
                "street": row.get("street", "(unnamed)"),
                "highway": row.get("highway", ""),
                "length_m": round(float(row["length_m"]), 1),
                "score": round(float(row["score"]), 4),
                "explanation": row.get("explanation", ""),
            },
        })
    return out


def _edges_gdf(graph) -> gpd.GeoDataFrame:
    """Edge GeoDataFrame of a graph, or an empty one if it has no edges.

    A small bbox (or a city with essentially no OSM cycleways, e.g. Marrakech)
    can yield an edgeless cycle graph; ``ox.graph_to_gdfs(nodes=False)`` raises on
    that, so we guard it — the detectors handle an empty supply layer fine.
    """
    if graph.number_of_edges() == 0:
        return gpd.GeoDataFrame(geometry=[], crs=graph.graph.get("crs"))
    return ox.graph_to_gdfs(graph, nodes=False)


def run_city(
    place: str, *, bbox: tuple | None = None, cap: int = 250, use_cache: bool = True
) -> dict[str, Any]:
    """Analyse ``place`` (or, when ``bbox`` is given, only that area of interest)
    and return the explorer payload (cached). ``bbox`` = (west, south, east,
    north) in EPSG:4326."""
    key = (place, bbox, cap)
    if use_cache and key in _CACHE:
        return _CACHE[key]

    cycle_wgs = get_cycle_graph(place, bbox=bbox)
    road_wgs = get_road_graph(place, bbox=bbox)
    cycle_g = project_graph(cycle_wgs)
    road_g = project_graph(road_wgs)
    cycle_gdf_proj = _edges_gdf(cycle_g)
    road_gdf_proj = _edges_gdf(road_g)

    # Boundary for the grid detectors: the drawn bbox, or the geocoded place.
    if bbox is not None:
        boundary = gpd.GeoDataFrame(
            geometry=[box(*bbox)], crs="EPSG:4326").to_crs(cycle_gdf_proj.crs)
    else:
        boundary = ox.geocode_to_gdf(place).to_crs(cycle_gdf_proj.crs)

    # City extent in WGS84 (west, south, east, north): the drawn bbox or the
    # geocoded place. Used to prune the national INSEE/BAAC datasets to the city.
    wgs_aoi = bbox if bbox is not None else tuple(boundary.to_crs(4326).total_bounds)

    d1 = detect_missing_links(cycle_g, road_g, city=place)
    try:
        d2 = detect_continuity_gaps(cycle_gdf_proj, road_gdf_proj, city=place)
    except Exception:  # pragma: no cover - keep the app working if D2 fails
        d2 = None
    try:
        d3 = detect_network_resilience(cycle_g, city=place, crs=cycle_gdf_proj.crs)
    except Exception:  # pragma: no cover - keep the app working if D3 fails
        d3 = None
    try:
        d13 = detect_directness_deficit(cycle_g, city=place, crs=cycle_gdf_proj.crs)
    except Exception:  # pragma: no cover - keep the app working if D13 fails
        d13 = None
    try:
        d8 = detect_comfort_deficit(
            road_gdf_proj, cycle_gdf_proj, city=place, crs=cycle_gdf_proj.crs)
    except Exception:  # pragma: no cover - keep the app working if D8 fails
        d8 = None
    try:
        green = get_green(place, bbox=bbox).to_crs(cycle_gdf_proj.crs)
        d10 = detect_green_corridors(
            road_gdf_proj, cycle_gdf_proj, green, city=place,
            crs=cycle_gdf_proj.crs, sampler=dem_sampler_from_env())
    except Exception:  # pragma: no cover - keep the app working if D10 fails
        d10 = None
    try:
        tourism = get_tourism(place, bbox=bbox).to_crs(cycle_gdf_proj.crs)
        d12 = detect_tourism_corridors(
            road_gdf_proj, cycle_gdf_proj, tourism, city=place, crs=cycle_gdf_proj.crs)
    except Exception:  # pragma: no cover - keep the app working if D12 fails
        d12 = None
    try:
        # BAAC crashes are opt-in (BICYCLELANE_BAAC); bound the load to the city
        # extent so a whole-country dataset is not scanned. None -> D7 is empty.
        crashes = baac_from_env(bbox=wgs_aoi)
        d7 = detect_safety_hotspots(
            road_gdf_proj, cycle_gdf_proj, crashes, city=place, crs=cycle_gdf_proj.crs)
    except Exception:  # pragma: no cover - keep the app working if D7 fails
        d7 = None
    try:
        # OD flows are opt-in (BICYCLELANE_OD, a generic OD CSV). None -> D14 empty.
        d14 = detect_modal_shift(
            road_gdf_proj, cycle_gdf_proj, od_from_env(bbox=wgs_aoi),
            city=place, crs=cycle_gdf_proj.crs, road_graph=road_g)
    except Exception:  # pragma: no cover - keep the app working if D14 fails
        d14 = None
    d5_segments = None
    population = _load_population(cycle_gdf_proj.crs, wgs_aoi)
    try:
        pois = get_pois(place, bbox=bbox).to_crs(cycle_gdf_proj.crs)
        d5 = detect_demand_supply_mismatch(
            cycle_gdf_proj, pois, boundary, cell_size=500.0, city=place,
            crs=cycle_gdf_proj.crs, population_gdf=population, pop_weight=POP_WEIGHT,
        )
        d5_segments = detect_mismatch_segments(
            cycle_gdf_proj, road_gdf_proj, pois, boundary,
            cell_size=500.0, min_residual=0.0, max_segments=4000, city=place,
            crs=cycle_gdf_proj.crs, population_gdf=population, pop_weight=POP_WEIGHT,
        )
    except Exception:  # pragma: no cover - keep the app working if D5 fails
        d5 = None

    try:
        hubs = get_stations(place, bbox=bbox)
        hubs = _apply_optional_weight(
            hubs, "BICYCLELANE_RIDERSHIP", load_station_ridership, "ridership", "hub_weight")
        d9 = detect_intermodal_access(
            cycle_gdf_proj, road_gdf_proj, hubs.to_crs(cycle_gdf_proj.crs),
            city=place, crs=cycle_gdf_proj.crs)
    except Exception:  # pragma: no cover
        d9 = None
    try:
        gens = get_generators(place, bbox=bbox)
        gens = _apply_optional_weight(
            gens, "BICYCLELANE_ENROLMENT", load_school_enrolment, "enrolment", "gen_weight")
        d11 = detect_generator_access(
            cycle_gdf_proj, road_gdf_proj, gens.to_crs(cycle_gdf_proj.crs),
            city=place, crs=cycle_gdf_proj.crs)
    except Exception:  # pragma: no cover
        d11 = None

    try:
        d4 = detect_coverage_coldspots(
            cycle_gdf_proj, road_gdf_proj, boundary,
            cell_size=500.0, city=place, crs=cycle_gdf_proj.crs)
    except Exception:  # pragma: no cover
        d4 = None
    try:
        # D6 reuses the same BICYCLELANE_INSEE Filosofi file as D5's population.
        d6 = detect_equity_gaps(
            cycle_gdf_proj, _load_deprivation(cycle_gdf_proj.crs, wgs_aoi), boundary,
            cell_size=500.0, city=place, crs=cycle_gdf_proj.crs)
    except Exception:  # pragma: no cover - keep the app working if D6 fails
        d6 = None

    per_detector = {"D1": d1, "D2": d2, "D3": d3, "D4": d4, "D5": d5, "D6": d6,
                    "D7": d7, "D8": d8, "D9": d9, "D10": d10, "D11": d11,
                    "D12": d12, "D13": d13, "D14": d14}
    counts = {k: (0 if v is None else int(len(v))) for k, v in per_detector.items()}

    # Composite priority layer (BL-20/BL-21): aggregate every detector onto a
    # shared 500 m grid and score cells under several weighting methods. Guarded
    # so the per-detector explorer keeps working even if aggregation fails.
    composite = {"type": "FeatureCollection", "features": [], "meta": {}}
    try:
        criteria = [det["key"] for det in DETECTORS]
        grid = make_grid(boundary, cell_size=500.0, crs=cycle_gdf_proj.crs)
        cells, comp_result = composite_grid(per_detector, grid, criteria)
        composite = composite_geojson(cells, comp_result, criteria)
    except Exception:  # pragma: no cover - keep the app working if composite fails
        pass

    feats: list[dict[str, Any]] = []
    for det in DETECTORS:
        feats.extend(_features(per_detector.get(det["key"]), det["key"], cap))

    # Existing cycle network as a light base layer (WGS84).
    base = _edges_gdf(cycle_wgs)
    network = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": mapping(geom), "properties": {}}
            for geom in base.geometry if geom is not None and not geom.is_empty
        ],
    }

    detectors_meta = [
        {**det, "count": counts.get(det["key"], 0),
         "shown": min(cap, counts.get(det["key"], 0))}
        for det in DETECTORS
    ]

    payload = {
        "city": place,
        "bbox": list(bbox) if bbox is not None else None,
        "cap": cap,
        "detectors": detectors_meta,
        "network": network,
        "features": {"type": "FeatureCollection", "features": feats},
        "composite": composite,
        "zone_segments": _zone_segments(d5_segments),
    }
    _CACHE[key] = payload
    return payload
