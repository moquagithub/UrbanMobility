"""Demand proxy: weighted points of interest (POIs) from OpenStreetMap.

For the v1 MVP the demand signal is built from **OSM POIs** (trip generators)
weighted by category -- schools, stations, health, shops, offices, food. This
is fetchable on the fly for any city (like the network extraction) and needs no
national dataset.

A stronger proxy -- INSEE Filosofi 200 m gridded **population** -- can be added
as an OPTIONAL extra demand input via :func:`load_insee_population`, which loads
a *local* INSEE file into 200 m ``population`` cells. ``mismatch`` then folds
``Pop_i`` into the per-cell demand surface when a population layer is supplied.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import pickle
import re
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache" / "pois"

# OSM tags fetched as candidate trip generators.
POI_TAGS = {
    "amenity": True,
    "shop": True,
    "office": True,
    "railway": ["station", "halt", "tram_stop"],
    "public_transport": ["station"],
}

# Category weights (relative importance as cycling-trip generators).
AMENITY_WEIGHTS = {
    "school": 3.0, "university": 3.0, "college": 3.0, "kindergarten": 2.0,
    "hospital": 2.0, "clinic": 2.0, "doctors": 1.0, "pharmacy": 1.0,
    "restaurant": 1.0, "cafe": 1.0, "bar": 1.0, "fast_food": 1.0,
    "bus_station": 3.0, "marketplace": 2.0, "library": 2.0,
}
SHOP_WEIGHT = 1.0
OFFICE_WEIGHT = 2.0
STATION_WEIGHT = 3.0


def _bbox_suffix(bbox: Optional[tuple] = None) -> str:
    """Cache-key suffix for a bbox: empty when None (so place-only keys stay
    byte-identical to the pre-bbox behaviour and existing caches remain valid),
    else ``|w|s|e|n`` with each coord rounded to 5 decimals."""
    if bbox is None:
        return ""
    return "|" + "|".join(f"{round(float(c), 5)}" for c in bbox)


def _cache_key(place: str, date: str, bbox: Optional[tuple] = None) -> str:
    return hashlib.sha1(
        f"pois|{place}|{date}{_bbox_suffix(bbox)}".encode()
    ).hexdigest()[:16]


def _postprocess_pois(feats: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Weight and trim fetched features into the ``(geometry, poi_weight)`` frame
    returned by :func:`get_pois` (shared by the place and bbox fetch paths)."""
    feats = feats[feats.geometry.notna() & ~feats.geometry.is_empty].copy()
    feats["poi_weight"] = [_poi_weight(row) for _, row in feats.iterrows()]
    out = feats.loc[feats["poi_weight"] > 0, ["geometry", "poi_weight"]].reset_index(drop=True)
    out = out.set_crs(feats.crs or "EPSG:4326", allow_override=True)
    return out


def _poi_weight(row) -> float:
    """Weight a single POI as the max over its category signals (0 = ignore)."""
    weights = [0.0]
    amenity = row.get("amenity")
    if isinstance(amenity, str):
        weights.append(AMENITY_WEIGHTS.get(amenity, 0.0))
    if isinstance(row.get("shop"), str):
        weights.append(SHOP_WEIGHT)
    if isinstance(row.get("office"), str):
        weights.append(OFFICE_WEIGHT)
    if isinstance(row.get("railway"), str) or isinstance(row.get("public_transport"), str):
        weights.append(STATION_WEIGHT)
    return max(weights)


def get_pois(
    place: str,
    *,
    bbox: Optional[tuple] = None,
    date: Optional[str] = None,
    cache_dir: Path | str = DEFAULT_CACHE_DIR,
    use_cache: bool = True,
) -> gpd.GeoDataFrame:
    """Download (or load) weighted POIs for ``place`` (WGS84).

    Parameters
    ----------
    place:
        A geocodable place name. When ``bbox`` is given ``place`` may be a plain
        label (used only in the cache key).
    bbox:
        Optional area of interest ``(west, south, east, north)`` in EPSG:4326
        (lon/lat). When ``None`` (default) the whole ``place`` is fetched via
        ``features_from_place``; when given, only the bbox is fetched via
        ``features_from_bbox`` (same POI tags). Folded into the cache key so it
        caches separately from the place query.

    Returns
    -------
    geopandas.GeoDataFrame
        Columns ``geometry`` (as fetched: points/polygons) and ``poi_weight``
        (> 0), in EPSG:4326. Reproject before spatial work.
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
        feats = ox.features_from_place(place, tags=POI_TAGS)
    else:
        feats = ox.features_from_bbox(bbox, tags=POI_TAGS)
    out = _postprocess_pois(feats)

    with cache_path.open("wb") as fh:
        pickle.dump(out, fh)
    return out


# INSEE gridded-population support -------------------------------------------

# INSEE 200 m grid-id encoding of the LAEA (EPSG:3035) south-west corner, e.g.
# ``CRS3035RES200mN2039400E3527200`` -> N (northing) = 2039400, E (easting) =
# 3527200 metres. The cell is the 200 m square whose SW corner is (E, N).
INSEE_CRS = "EPSG:3035"
INSEE_CELL_SIZE = 200.0
_INSEE_ID_RE = re.compile(r"N(?P<n>\d+)E(?P<e>\d+)")

# Candidate column names (case-insensitive) for the grid id and population.
_IDCAR_COLS = ("idcar_200m", "idcar", "idinspire", "id_car", "id")
_POP_COLS = ("ind", "pop", "population", "ind_c", "men")


def _parse_insee_id(idcar: str) -> Optional[tuple[float, float]]:
    """Return the (easting, northing) SW corner in EPSG:3035 for an INSEE id."""
    if not isinstance(idcar, str):
        return None
    m = _INSEE_ID_RE.search(idcar)
    if not m:
        return None
    return float(m.group("e")), float(m.group("n"))


def _pick_column(columns, candidates) -> Optional[str]:
    """Case-insensitive lookup of the first candidate present in ``columns``."""
    lower = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    return None


def load_insee_population(
    path: Path | str,
    *,
    bbox: Optional[tuple[float, float, float, float]] = None,
) -> gpd.GeoDataFrame:
    """Load INSEE Filosofi 200 m gridded population from a **local** file.

    This is the ``Pop_i`` term of the D5 demand surface
    ``D_i = pop_weight*Pop_i + Σ_k w_k·n_{k,i}``. It reads a *provided local
    path* only; it never downloads the national dataset (which is large).

    Where to get the data
    ---------------------
    INSEE "Données carroyées – 200 m" (Filosofi / *revenus, pauvreté et niveau
    de vie – données carroyées*), distributed as a GeoPackage/shapefile of 200 m
    cells (EPSG:3035, LAEA Europe) and/or a CSV keyed by the INSEE grid id.
    Download it manually from insee.fr and pass the local file here.

    Accepted formats
    ----------------
    * **GeoPackage** (``.gpkg``) or **shapefile** (``.shp``): read via
      :func:`geopandas.read_file`; geometry is taken as-is. Expected to carry a
      population column (``ind`` / ``Ind`` — number of individuals).
    * **CSV** (``.csv``): must carry a grid-id column (``idcar`` / ``Idcar_200m``
      / ``idInspire``) that encodes the EPSG:3035 SW corner, e.g.
      ``CRS3035RES200mN2039400E3527200``, and a population column (``ind`` /
      ``Ind``). Each 200 m square cell is reconstructed from the id.

    Parameters
    ----------
    path:
        Local path to the INSEE file.
    bbox:
        Optional ``(minx, miny, maxx, maxy)`` filter. Interpreted in the file's
        own CRS (EPSG:3035 for the reconstructed CSV / native geo files) — or in
        EPSG:4326 if its values look like lon/lat degrees — and applied before
        reprojection to prune the (large) national grid to a city.

    Returns
    -------
    geopandas.GeoDataFrame
        200 m cell polygons with a single numeric ``population`` column,
        reprojected to **EPSG:4326** (WGS84) for consistency with the rest of
        the demand pipeline (``get_pois`` also returns WGS84).
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".csv":
        gdf = _load_insee_csv(path, bbox=bbox)
    else:
        gdf = _load_insee_geofile(path)

    if bbox is not None:
        gdf = _filter_bbox(gdf, bbox)

    if gdf.crs is None:
        gdf = gdf.set_crs(INSEE_CRS, allow_override=True)
    if gdf.crs.to_string() != "EPSG:4326":
        gdf = gdf.to_crs("EPSG:4326")
    return gdf[["geometry", "population"]].reset_index(drop=True)


# Household counts in the same Filosofi 200 m file, for the D6 deprivation signal.
_MEN_COLS = ("men", "nb_men", "menages")
_POOR_COLS = ("men_pauv", "menpauv", "men_pauvre", "menpauvre")


def load_insee_deprivation(
    path: Path | str,
    *,
    bbox: Optional[tuple[float, float, float, float]] = None,
) -> gpd.GeoDataFrame:
    """Load a **deprivation** signal from the same INSEE Filosofi 200 m file
    used for population (D5). Deprivation = poverty share of households
    ``Men_pauv / Men`` in ``[0, 1]`` (higher = more disadvantaged), the input
    D6 needs. Same formats/CRS as :func:`load_insee_population`; returns
    ``[geometry, deprivation]`` in EPSG:4326.
    """
    path = Path(path)

    def _rate(men, poor) -> float:
        m, p = float(men), float(poor)
        return max(0.0, min(1.0, p / m)) if m > 0 else 0.0

    if path.suffix.lower() == ".csv":
        df, m = _read_insee_frame(
            path, {"id": _IDCAR_COLS, "men": _MEN_COLS, "poor": _POOR_COLS})
        if m["id"] is None or m["men"] is None or m["poor"] is None:
            raise ValueError(
                f"{path.name}: need an INSEE id column plus household columns "
                f"{_MEN_COLS} and {_POOR_COLS} (case-insensitive).")
        men = pd.to_numeric(df[m["men"]], errors="coerce").fillna(0.0)
        poor = pd.to_numeric(df[m["poor"]], errors="coerce").fillna(0.0)
        keep_id = _prefilter_ids(bbox)
        geoms, deps = [], []
        for idcar, mm, pp in zip(df[m["id"]], men, poor):
            corner = _parse_insee_id(idcar)
            if corner is None:
                continue
            e, n = corner
            if keep_id is not None and not keep_id(e, n):
                continue
            geoms.append(box(e, n, e + INSEE_CELL_SIZE, n + INSEE_CELL_SIZE))
            deps.append(_rate(mm, pp))
        gdf = gpd.GeoDataFrame({"deprivation": deps}, geometry=geoms, crs=INSEE_CRS)
    else:
        gdf = gpd.read_file(path)
        men_col = _pick_column(gdf.columns, _MEN_COLS)
        poor_col = _pick_column(gdf.columns, _POOR_COLS)
        if men_col is None or poor_col is None:
            raise ValueError(
                f"{path.name}: need household columns {_MEN_COLS} and "
                f"{_POOR_COLS} (case-insensitive).")
        men = pd.to_numeric(gdf[men_col], errors="coerce").fillna(0.0)
        poor = pd.to_numeric(gdf[poor_col], errors="coerce").fillna(0.0)
        gdf = gdf.copy()
        gdf["deprivation"] = [_rate(m, p) for m, p in zip(men, poor)]
        gdf = gdf[["geometry", "deprivation"]]

    if bbox is not None:
        gdf = _filter_bbox(gdf, bbox)
    if gdf.crs is None:
        gdf = gdf.set_crs(INSEE_CRS, allow_override=True)
    if gdf.crs.to_string() != "EPSG:4326":
        gdf = gdf.to_crs("EPSG:4326")
    return gdf[["geometry", "deprivation"]].reset_index(drop=True)


def _load_insee_geofile(path: Path) -> gpd.GeoDataFrame:
    """Read a GeoPackage/shapefile of 200 m cells into a ``population`` frame."""
    gdf = gpd.read_file(path)
    pop_col = _pick_column(gdf.columns, _POP_COLS)
    if pop_col is None:
        raise ValueError(
            f"No population column found in {path.name}; expected one of "
            f"{_POP_COLS} (case-insensitive)."
        )
    out = gdf.copy()
    out["population"] = pd.to_numeric(out[pop_col], errors="coerce").fillna(0.0)
    return out[["geometry", "population"]]


def _read_insee_frame(path: Path, role_candidates: dict) -> tuple:
    """Fast CSV read of only the needed INSEE columns.

    Sniffs the separator from the header, uses the C engine and ``usecols`` (so
    the national 200 m file's 30+ columns are not all parsed), and maps each
    *role* to the matching column name. Returns ``(df, {role: col})``.
    """
    with open(path, encoding="latin-1") as fh:
        header = fh.readline().rstrip("\n")
    sep = max([",", ";", "\t"], key=header.count)
    file_cols = [c.strip().strip('"') for c in header.split(sep)]
    mapping = {role: _pick_column(file_cols, cands)
               for role, cands in role_candidates.items()}
    usecols = [c for c in mapping.values() if c]
    df = pd.read_csv(path, sep=sep, dtype=str, usecols=usecols, encoding="latin-1")
    return df, mapping


def _prefilter_ids(bbox):
    """Return a fast ``(e, n) -> bool`` keep-test for a 200 m cell corner, or
    ``None``. Only applied when ``bbox`` is clearly in metric EPSG:3035 (values
    > 180); padded by one cell so edge cells that intersect are not dropped (the
    exact filter still runs afterwards)."""
    if bbox is None or not all(abs(v) > 180.0 for v in bbox):
        return None
    x0, y0, x1, y1 = bbox
    x0 -= INSEE_CELL_SIZE
    y0 -= INSEE_CELL_SIZE
    return lambda e, n: (x0 <= e <= x1) and (y0 <= n <= y1)


def _load_insee_csv(path: Path, bbox=None) -> gpd.GeoDataFrame:
    """Read an INSEE CSV, reconstructing 200 m cells from the grid id."""
    df, m = _read_insee_frame(path, {"id": _IDCAR_COLS, "pop": _POP_COLS})
    if m["id"] is None:
        raise ValueError(
            f"No INSEE grid-id column found in {path.name}; expected one of "
            f"{_IDCAR_COLS} (case-insensitive)."
        )
    if m["pop"] is None:
        raise ValueError(
            f"No population column found in {path.name}; expected one of "
            f"{_POP_COLS} (case-insensitive)."
        )

    population = pd.to_numeric(df[m["pop"]], errors="coerce").fillna(0.0)
    keep_id = _prefilter_ids(bbox)
    geoms = []
    keep = []
    pops = []
    for idcar, pop in zip(df[m["id"]], population):
        corner = _parse_insee_id(idcar)
        if corner is None:
            continue
        e, n = corner
        if keep_id is not None and not keep_id(e, n):
            continue
        geoms.append(box(e, n, e + INSEE_CELL_SIZE, n + INSEE_CELL_SIZE))
        pops.append(float(pop))
        keep.append(True)

    return gpd.GeoDataFrame(
        {"population": pops}, geometry=geoms, crs=INSEE_CRS
    )


def _filter_bbox(
    gdf: gpd.GeoDataFrame, bbox: tuple[float, float, float, float]
) -> gpd.GeoDataFrame:
    """Spatially pre-filter ``gdf`` by ``bbox`` (file CRS, or WGS84 degrees)."""
    minx, miny, maxx, maxy = bbox
    # If the bbox looks like lon/lat degrees, express it in the gdf CRS.
    looks_geographic = (
        abs(minx) <= 180 and abs(maxx) <= 180
        and abs(miny) <= 90 and abs(maxy) <= 90
    )
    if looks_geographic and gdf.crs is not None and gdf.crs.is_projected:
        clip = gpd.GeoDataFrame(
            geometry=[box(minx, miny, maxx, maxy)], crs="EPSG:4326"
        ).to_crs(gdf.crs)
        clip_geom = clip.geometry.iloc[0]
    else:
        clip_geom = box(minx, miny, maxx, maxy)
    return gdf[gdf.geometry.intersects(clip_geom)].copy()
