"""BAAC crash-data loader (Foundation v2b, BL-28).

Reads the French national road-crash database (BAAC / ONISR, "Bases de données
annuelles des accidents corporels de la circulation", data.gouv.fr, Licence
Ouverte) and returns the **bicycle-involved** crashes as geolocated points,
weighted by injury severity — the input D7 needs.

The BAAC ships one CSV per year for each of four tables joined on ``Num_Acc``:
``caracteristiques`` (date + ``lat``/``long``), ``vehicules`` (``catv`` vehicle
category — a bicycle is ``1``), ``usagers`` (``grav`` severity), ``lieux``. The
format drifts year to year (separator ``;`` or ``,``; UTF-8 or Latin-1; ``lat``/
``long`` with a comma decimal, and older years scaled ×10^5), so the reader
sniffs and normalises. Geocoding is only reliable from 2019 on — pool several
recent years for a meaningful hotspot signal.

Everything here is OFF by default: enabled via ``BICYCLELANE_BAAC`` (a directory
of BAAC CSVs) through :func:`baac_from_env`.
"""

from __future__ import annotations

import os
import re
from typing import Iterable, Optional

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

# Vehicle categories counted as a bicycle (BAAC ``catv``). 1 = bicyclette; the
# electrically-assisted bike shares code 1, e-scooters have separate codes.
BIKE_CATV = {"1"}

# Injury-severity weight from BAAC ``grav`` (1 unharmed, 2 killed, 3 hospitalised,
# 4 slightly injured). A killed cyclist marks the most dangerous spot.
GRAV_WEIGHT = {"1": 0.1, "2": 1.0, "3": 0.6, "4": 0.3}


def _read_csv_any(path: str) -> pd.DataFrame:
    """Read a BAAC CSV as all-strings, sniffing separator and encoding."""
    for enc in ("utf-8", "latin-1"):
        try:
            return pd.read_csv(path, sep=None, engine="python", dtype=str,
                               encoding=enc, on_bad_lines="skip")
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    return pd.read_csv(path, sep=";", dtype=str, encoding="latin-1",
                       on_bad_lines="skip")


def _find_col(df: pd.DataFrame, *names: str) -> Optional[str]:
    low = {str(c).strip().lower(): c for c in df.columns}
    for n in names:
        if n in low:
            return low[n]
    return None


def _to_coord(value: object) -> Optional[float]:
    """Parse a BAAC ``lat``/``long`` string to decimal degrees (or ``None``)."""
    if value is None:
        return None
    s = str(value).strip().replace(",", ".")
    if not s or s.lower() in {"nan", "na"}:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    if v == 0.0:
        return None
    # Older years store coordinates as integers scaled by 10^5 (no decimal).
    if abs(v) > 180.0:
        v = v / 1e5
    return v


def load_baac(
    caracteristiques: str,
    vehicules: str,
    usagers: Optional[str] = None,
    *,
    bike_catv: Iterable[str] = BIKE_CATV,
    bbox: Optional[tuple] = None,
) -> gpd.GeoDataFrame:
    """Load one year's BAAC files into bicycle-crash points (WGS84).

    Returns a GeoDataFrame with a ``severity`` weight per crash (from ``usagers``
    ``grav`` when provided, else a flat 0.5). ``bbox`` = (west, south, east,
    north) in EPSG:4326 filters to an area. Only accidents whose ``vehicules``
    include a bicycle ``catv`` are kept.
    """
    bike_catv = set(bike_catv)
    veh = _read_csv_any(vehicules)
    v_acc, v_catv = _find_col(veh, "num_acc", "accident_id"), _find_col(veh, "catv")
    if v_acc is None or v_catv is None:
        return gpd.GeoDataFrame({"severity": []}, geometry=[], crs="EPSG:4326")
    bike_ids = set(veh.loc[veh[v_catv].astype(str).str.strip().isin(bike_catv), v_acc])

    severity: dict[str, float] = {}
    if usagers:
        usr = _read_csv_any(usagers)
        u_acc, u_grav = _find_col(usr, "num_acc", "accident_id"), _find_col(usr, "grav")
        if u_acc is not None and u_grav is not None:
            for acc, grav in zip(usr[u_acc], usr[u_grav]):
                w = GRAV_WEIGHT.get(str(grav).strip())
                if w is not None:
                    severity[acc] = max(severity.get(acc, 0.0), w)

    car = _read_csv_any(caracteristiques)
    c_acc = _find_col(car, "num_acc", "accident_id")  # 2022 renamed it Accident_Id
    c_lat, c_lon = _find_col(car, "lat"), _find_col(car, "long", "lon", "lng")
    if c_acc is None or c_lat is None or c_lon is None:
        return gpd.GeoDataFrame({"severity": []}, geometry=[], crs="EPSG:4326")

    rows = []
    for acc, lat, lon in zip(car[c_acc], car[c_lat], car[c_lon]):
        if acc not in bike_ids:
            continue
        y, x = _to_coord(lat), _to_coord(lon)
        if y is None or x is None:
            continue
        rows.append((acc, x, y, severity.get(acc, 0.5)))

    if not rows:
        return gpd.GeoDataFrame({"severity": []}, geometry=[], crs="EPSG:4326")

    out = gpd.GeoDataFrame(
        {"num_acc": [r[0] for r in rows], "severity": [r[3] for r in rows]},
        geometry=[Point(r[1], r[2]) for r in rows], crs="EPSG:4326")
    if bbox is not None:
        w, s, e, n = bbox
        out = out.cx[w:e, s:n]
    return out.reset_index(drop=True)


def _year_of(path: str) -> Optional[str]:
    m = re.search(r"(20\d{2})", os.path.basename(path))
    return m.group(1) if m else None


def load_baac_dir(
    directory: str, *, years: Optional[Iterable[str]] = None, bbox: Optional[tuple] = None,
) -> gpd.GeoDataFrame:
    """Load and concatenate every year found in a directory of BAAC CSVs.

    Files are matched by name (``*caracteristiques*`` / ``*vehicules*`` /
    ``*usagers*``) and paired by the 4-digit year in the filename. ``years``
    optionally restricts which years to load.
    """
    want = None if years is None else set(years)

    def by_year(*tokens: str) -> dict[str, str]:
        """Map year -> first CSV whose lowercased name contains any token.

        Token-matching (not globbing) tolerates the publisher's inconsistent
        names: caracteristiques / carcteristiques (a real typo in 2021-2022) /
        caract, and Vehicules_2024 / vehicules-2019, across cases.
        """
        out: dict[str, str] = {}
        for name in sorted(os.listdir(directory)):
            n = name.lower()
            if not n.endswith(".csv") or not any(tok in n for tok in tokens):
                continue
            y = _year_of(name)
            if y and (want is None or y in want) and y not in out:
                out[y] = os.path.join(directory, name)
        return out

    cars = by_year("aract", "carct")   # caracteristiques / carcteristiques / caract
    vehs = by_year("hicul")            # vehicules
    usrs = by_year("sager")            # usagers

    frames = []
    for y, car in sorted(cars.items()):
        veh = vehs.get(y)
        if veh is None:
            continue
        frames.append(load_baac(car, veh, usrs.get(y), bbox=bbox))
    frames = [f for f in frames if len(f) > 0]
    if not frames:
        return gpd.GeoDataFrame({"severity": []}, geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")


def baac_from_env(var: str = "BICYCLELANE_BAAC", *, bbox: Optional[tuple] = None):
    """Load BAAC bicycle crashes from ``$BICYCLELANE_BAAC`` (a dir), or ``None``.

    Off by default: unset or unreadable → ``None``, so D7 silently produces no
    output rather than failing.
    """
    path = os.environ.get(var)
    if not path or not os.path.isdir(path):
        return None
    try:
        g = load_baac_dir(path, bbox=bbox)
        return g if len(g) > 0 else None
    except Exception:  # pragma: no cover - defensive
        return None
