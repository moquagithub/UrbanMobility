"""OSM extraction of point features used by the access detectors.

* :func:`get_stations` -> public-transport hubs for D9 (intermodality).
* :func:`get_generators` -> major trip generators for D11 (schools, universities,
  hospitals), each carrying a category weight.

Both cache to disk and return WGS84 point GeoDataFrames (polygon features are
reduced to a representative point). Kept separate from :mod:`bicyclelane.demand`
so the two concerns evolve independently.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import pickle
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np

_CACHE_ROOT = Path(__file__).resolve().parent.parent.parent / ".cache"

# Note: amenity=bus_station is deliberately excluded — on some Overpass servers
# that single tag can take ~90 s to return for a whole city while adding little
# (railway + public_transport stations cover the structural hubs).
STATION_TAGS = {
    "railway": ["station", "halt", "tram_stop"],
    "public_transport": ["station"],
}

# Ridership proxy: weight a hub by its type (a main railway station generates far
# more cycling trips than a tram stop). Real ridership (SNCF Open Data / GTFS
# service level) can override this via load_station_ridership + a nearest match.
STATION_WEIGHTS = {"station": 3.0, "halt": 2.0, "tram_stop": 1.5}

GENERATOR_TAGS = {"amenity": ["school", "university", "college", "kindergarten", "hospital"]}
GENERATOR_WEIGHTS = {
    "university": 3.0, "school": 3.0, "college": 3.0,
    "hospital": 2.0, "kindergarten": 1.5,
}


def _bbox_suffix(bbox: Optional[tuple] = None) -> str:
    """Cache-key suffix for a bbox: empty when None (so place-only keys stay
    byte-identical to the pre-bbox behaviour and existing caches remain valid),
    else ``|w|s|e|n`` with each coord rounded to 5 decimals."""
    if bbox is None:
        return ""
    return "|" + "|".join(f"{round(float(c), 5)}" for c in bbox)


def _cache_path(kind: str, place: str, date: str, bbox: Optional[tuple] = None) -> Path:
    d = _CACHE_ROOT / kind
    d.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(
        f"{kind}|{place}|{date}{_bbox_suffix(bbox)}".encode()
    ).hexdigest()[:16]
    return d / f"{key}.gpickle"


def _points(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    out = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    out["geometry"] = out.geometry.representative_point()
    return out


def _hub_kind(row) -> str:
    rw = row.get("railway")
    if isinstance(rw, (list, tuple)):
        rw = rw[0] if rw else None
    if isinstance(rw, str):
        return rw
    pt = row.get("public_transport")
    if isinstance(pt, (list, tuple)):
        pt = pt[0] if pt else None
    return "station" if isinstance(pt, str) else "station"


def _postprocess_stations(feats: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reduce fetched hub features to the WGS84 point frame returned by
    :func:`get_stations` (shared by the place and bbox fetch paths)."""
    feats = _points(feats)
    feats["kind"] = [_hub_kind(r) for _, r in feats.iterrows()]
    feats["hub_weight"] = [STATION_WEIGHTS.get(k, 2.0) for k in feats["kind"]]
    out = feats[["geometry", "kind", "hub_weight"]].reset_index(drop=True)
    out = out.set_crs(feats.crs or "EPSG:4326", allow_override=True)
    return out


def get_stations(
    place: str,
    *,
    bbox: Optional[tuple] = None,
    date: Optional[str] = None,
    use_cache: bool = True,
) -> gpd.GeoDataFrame:
    """Public-transport hubs for ``place`` (WGS84 points).

    Columns ``kind`` (station/halt/tram_stop) and ``hub_weight`` — a ridership
    proxy by type (see STATION_WEIGHTS), usable as ``f_h`` in D9.

    ``bbox`` is an optional area of interest ``(west, south, east, north)`` in
    EPSG:4326 (lon/lat): when ``None`` (default) the whole ``place`` is fetched
    via ``features_from_place``; when given, only the bbox is fetched via
    ``features_from_bbox`` (same station tags), and it is folded into the cache
    key so it caches separately from the place query. With ``bbox`` set,
    ``place`` may be a plain label.
    """
    import osmnx as ox

    date = date or _dt.date.today().isoformat()
    path = _cache_path("stations", place, date, bbox=bbox)
    if use_cache and path.exists():
        with path.open("rb") as fh:
            return pickle.load(fh)

    if bbox is None:
        feats = ox.features_from_place(place, tags=STATION_TAGS)
    else:
        feats = ox.features_from_bbox(bbox, tags=STATION_TAGS)
    out = _postprocess_stations(feats)
    with path.open("wb") as fh:
        pickle.dump(out, fh)
    return out


def _generator_weight(row) -> float:
    amenity = row.get("amenity")
    if isinstance(amenity, (list, tuple)):
        amenity = amenity[0] if amenity else None
    return GENERATOR_WEIGHTS.get(amenity, 0.0) if isinstance(amenity, str) else 0.0


def _generator_type(row) -> str:
    amenity = row.get("amenity")
    if isinstance(amenity, (list, tuple)):
        amenity = amenity[0] if amenity else None
    return amenity if isinstance(amenity, str) else "generator"


def _postprocess_generators(feats: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reduce fetched generator features to the WGS84 point frame returned by
    :func:`get_generators` (shared by the place and bbox fetch paths)."""
    feats = _points(feats)
    feats["gen_type"] = [_generator_type(r) for _, r in feats.iterrows()]
    feats["gen_weight"] = [_generator_weight(r) for _, r in feats.iterrows()]
    out = feats.loc[feats["gen_weight"] > 0, ["geometry", "gen_type", "gen_weight"]].reset_index(drop=True)
    out = out.set_crs(feats.crs or "EPSG:4326", allow_override=True)
    return out


def get_generators(
    place: str,
    *,
    bbox: Optional[tuple] = None,
    date: Optional[str] = None,
    use_cache: bool = True,
) -> gpd.GeoDataFrame:
    """Trip generators for ``place`` (WGS84 points) with ``gen_type`` / ``gen_weight``.

    ``bbox`` is an optional area of interest ``(west, south, east, north)`` in
    EPSG:4326 (lon/lat): when ``None`` (default) the whole ``place`` is fetched
    via ``features_from_place``; when given, only the bbox is fetched via
    ``features_from_bbox`` (same generator tags), and it is folded into the cache
    key so it caches separately from the place query. With ``bbox`` set,
    ``place`` may be a plain label.
    """
    import osmnx as ox

    date = date or _dt.date.today().isoformat()
    path = _cache_path("generators", place, date, bbox=bbox)
    if use_cache and path.exists():
        with path.open("rb") as fh:
            return pickle.load(fh)

    if bbox is None:
        feats = ox.features_from_place(place, tags=GENERATOR_TAGS)
    else:
        feats = ox.features_from_bbox(bbox, tags=GENERATOR_TAGS)
    out = _postprocess_generators(feats)
    with path.open("wb") as fh:
        pickle.dump(out, fh)
    return out


# Green / natural features used by D10 (relief & green corridors): parks and
# greenery, plus water for waterside routes. Areas and waterway lines are kept.
GREEN_TAGS = {
    "leisure": ["park", "garden", "nature_reserve", "recreation_ground"],
    "landuse": ["forest", "grass", "meadow", "village_green", "recreation_ground"],
    "natural": ["wood", "water", "grassland", "scrub", "heath"],
    "waterway": ["river", "canal", "stream"],
}


def _postprocess_green(feats: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Reduce fetched green features to a geometry-only WGS84 frame (areas and
    waterway lines; points are dropped as they carry no corridor extent)."""
    if feats is None or len(feats) == 0:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    g = feats[feats.geometry.notna() & ~feats.geometry.is_empty]
    g = g[g.geometry.geom_type.isin(
        ["Polygon", "MultiPolygon", "LineString", "MultiLineString"])]
    out = gpd.GeoDataFrame(geometry=g.geometry.reset_index(drop=True))
    return out.set_crs(feats.crs or "EPSG:4326", allow_override=True)


def get_green(
    place: str,
    *,
    bbox: Optional[tuple] = None,
    date: Optional[str] = None,
    use_cache: bool = True,
) -> gpd.GeoDataFrame:
    """Green / water features for ``place`` (WGS84 geometries, areas + waterways).

    Used by D10 to find corridors *along green space*. ``bbox`` behaves as in
    :func:`get_stations` / :func:`get_generators`.
    """
    import osmnx as ox

    date = date or _dt.date.today().isoformat()
    path = _cache_path("green", place, date, bbox=bbox)
    if use_cache and path.exists():
        with path.open("rb") as fh:
            return pickle.load(fh)

    if bbox is None:
        feats = ox.features_from_place(place, tags=GREEN_TAGS)
    else:
        feats = ox.features_from_bbox(bbox, tags=GREEN_TAGS)
    out = _postprocess_green(feats)
    with path.open("wb") as fh:
        pickle.dump(out, fh)
    return out


# Tourism / leisure attractors for D12, weighted by drawing power.
TOURISM_TAGS = {
    "tourism": ["attraction", "museum", "viewpoint", "gallery", "artwork",
                "theme_park", "zoo", "aquarium", "hotel", "guest_house", "hostel"],
    "historic": ["monument", "memorial", "castle", "ruins", "archaeological_site"],
}
TOURISM_WEIGHTS = {
    "attraction": 3.0, "theme_park": 3.0, "zoo": 3.0, "aquarium": 3.0,
    "museum": 3.0, "castle": 2.5, "viewpoint": 2.0, "gallery": 2.0,
    "monument": 2.0, "memorial": 1.5, "ruins": 2.0, "archaeological_site": 2.0,
    "artwork": 1.0, "hotel": 1.0, "guest_house": 1.0, "hostel": 1.0,
}


def _tourism_kind(row) -> Optional[str]:
    for key in ("tourism", "historic"):
        v = row.get(key)
        if isinstance(v, (list, tuple)):
            v = v[0] if v else None
        if isinstance(v, str) and v in TOURISM_WEIGHTS:
            return v
    return None


def _postprocess_tourism(feats: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if feats is None or len(feats) == 0:
        return gpd.GeoDataFrame(
            {"tour_type": [], "tour_weight": []}, geometry=[], crs="EPSG:4326")
    feats = _points(feats)
    feats["tour_type"] = [_tourism_kind(r) for _, r in feats.iterrows()]
    feats["tour_weight"] = [TOURISM_WEIGHTS.get(k, 0.0) for k in feats["tour_type"]]
    out = feats.loc[feats["tour_weight"] > 0,
                    ["geometry", "tour_type", "tour_weight"]].reset_index(drop=True)
    return out.set_crs(feats.crs or "EPSG:4326", allow_override=True)


def get_tourism(
    place: str,
    *,
    bbox: Optional[tuple] = None,
    date: Optional[str] = None,
    use_cache: bool = True,
) -> gpd.GeoDataFrame:
    """Tourism/leisure attractors for ``place`` (WGS84 points).

    Columns ``tour_type`` and ``tour_weight`` (drawing power, see
    TOURISM_WEIGHTS), used by D12. ``bbox`` behaves as in :func:`get_stations`.
    """
    import osmnx as ox

    date = date or _dt.date.today().isoformat()
    path = _cache_path("tourism", place, date, bbox=bbox)
    if use_cache and path.exists():
        with path.open("rb") as fh:
            return pickle.load(fh)

    if bbox is None:
        feats = ox.features_from_place(place, tags=TOURISM_TAGS)
    else:
        feats = ox.features_from_bbox(bbox, tags=TOURISM_TAGS)
    out = _postprocess_tourism(feats)
    with path.open("wb") as fh:
        pickle.dump(out, fh)
    return out


# --------------------------------------------------------------------------- #
# Optional real weighting data: station ridership (D9) and school enrolment
# (D11). Both are country-specific (France: SNCF Open Data, Éducation nationale)
# and are matched to the OSM hubs/generators by nearest neighbour. Enabled via
# the pipeline (BICYCLELANE_RIDERSHIP / BICYCLELANE_ENROLMENT env vars); off by
# default, in which case the type-based proxy weights are used.
# --------------------------------------------------------------------------- #

def _pick_column(columns, candidates):
    low = {str(c).lower(): c for c in columns}
    for cand in candidates:
        if cand in low:
            return low[cand]
    for c in columns:
        cl = str(c).lower()
        if any(cand in cl for cand in candidates):
            return c
    return None


def _read_points_with_value(path, value_candidates) -> gpd.GeoDataFrame:
    """Read a local CSV/GeoFile of points carrying a numeric value column.

    Returns a WGS84 GeoDataFrame with a single ``value`` column (representative
    points). CSVs must carry lon/lat columns; geo files carry their geometry.
    """
    import pandas as pd

    p = str(path).lower()
    if p.endswith((".gpkg", ".shp", ".geojson", ".json")):
        gdf = gpd.read_file(path)
    else:
        df = pd.read_csv(path, sep=None, engine="python")
        lon = _pick_column(df.columns, ("lon", "longitude", "x", "lng"))
        lat = _pick_column(df.columns, ("lat", "latitude", "y"))
        if lon is None or lat is None:
            raise ValueError("CSV needs longitude/latitude columns")
        gdf = gpd.GeoDataFrame(
            df, geometry=gpd.points_from_xy(df[lon], df[lat]), crs="EPSG:4326")

    valcol = _pick_column(gdf.columns, value_candidates)
    if valcol is None:
        raise ValueError(f"no value column found among {value_candidates}")
    import pandas as pd
    out = gpd.GeoDataFrame(
        {"value": pd.to_numeric(gdf[valcol], errors="coerce")},
        geometry=gdf.geometry, crs=gdf.crs or "EPSG:4326")
    out = out[out["value"].notna() & out.geometry.notna() & ~out.geometry.is_empty]
    out = out.to_crs("EPSG:4326").reset_index(drop=True)
    out["geometry"] = out.geometry.representative_point()
    return out


def load_station_ridership(path) -> gpd.GeoDataFrame:
    """Station ridership points (WGS84, column ``ridership``) from a local file.

    Source (France): SNCF Open Data, "Fréquentation en gares" (annual passenger
    counts), with station coordinates. Matched to OSM hubs by nearest neighbour.
    """
    g = _read_points_with_value(path, ("frequentation", "ridership", "voyageurs", "total"))
    return g.rename(columns={"value": "ridership"})


def load_school_enrolment(path) -> gpd.GeoDataFrame:
    """School enrolment points (WGS84, column ``enrolment``) from a local file.

    Source (France): Éducation nationale annuaire / effectifs, with coordinates.
    Matched to OSM generators by nearest neighbour.
    """
    g = _read_points_with_value(path, ("effectif", "enrolment", "enrollment", "eleves", "nb_eleves"))
    return g.rename(columns={"value": "enrolment"})


def apply_nearest_value(
    target: gpd.GeoDataFrame,
    ref: gpd.GeoDataFrame,
    value_col: str,
    out_col: str,
    *,
    max_dist_m: float = 400.0,
) -> gpd.GeoDataFrame:
    """Set ``target[out_col]`` from the nearest ``ref[value_col]`` within max_dist_m.

    Targets without a match keep their existing ``out_col`` (the type proxy).
    """
    if target is None or len(target) == 0 or ref is None or len(ref) == 0:
        return target
    from scipy.spatial import cKDTree

    crs_m = target.estimate_utm_crs()
    t = target.to_crs(crs_m)
    r = ref.to_crs(crs_m)
    rxy = np.array([(g.x, g.y) for g in r.geometry], dtype=float)
    txy = np.array([(g.x, g.y) for g in t.geometry], dtype=float)
    dist, idx = cKDTree(rxy).query(txy, k=1)

    out = target.copy().reset_index(drop=True)
    vals = (out[out_col].astype(float).to_numpy()
            if out_col in out.columns else np.full(len(out), np.nan))
    rvals = r[value_col].to_numpy()
    for i in range(len(out)):
        if dist[i] <= max_dist_m:
            vals[i] = float(rvals[int(idx[i])])
    out[out_col] = vals
    return out
