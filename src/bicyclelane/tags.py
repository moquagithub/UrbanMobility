"""Enriched OSM edge-tag parsing (Foundation v2a, BL-23).

The comfort detector D8 needs per-edge signals that live in raw OSM tags but are
messy: ``maxspeed`` mixes numbers, ``"30 mph"`` and country codes; ``lit`` is a
free-text yes/no-ish field; ``surface`` / ``smoothness`` are categorical. This
module turns them into clean numeric features:

* ``maxspeed_kmh`` — float km/h (mph converted, implicit country codes resolved)
* ``lit`` — nullable bool
* ``surface_q`` / ``smoothness_q`` — cyclability quality in ``[0, 1]`` (1 = best)

Missing/unknown values become ``None`` (never a silent wrong default), so a
detector can decide how to treat absence. No new data source — these tags are
already fetched with the network; this is purely parsing.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import pandas as pd

# Implicit maxspeed for OSM country/zone codes (km/h), keyed lowercase (values
# are matched case-insensitively). Extend as needed.
COUNTRY_MAXSPEED: dict[str, float] = {
    "fr:urban": 50.0, "fr:rural": 80.0, "fr:motorway": 130.0,
    "fr:zone30": 30.0, "fr:living_street": 20.0, "fr:walk": 6.0,
    ":urban": 50.0, ":rural": 90.0, ":living_street": 20.0, ":walk": 6.0,
}

# Cyclability comfort by surface / smoothness category (1 = smooth/best).
SURFACE_QUALITY: dict[str, float] = {
    "asphalt": 1.0, "concrete": 0.95, "paved": 0.9, "chipseal": 0.9,
    "paving_stones": 0.8, "concrete:plates": 0.75, "wood": 0.55,
    "compacted": 0.65, "fine_gravel": 0.6, "sett": 0.55, "cobblestone": 0.4,
    "unpaved": 0.4, "gravel": 0.4, "ground": 0.35, "dirt": 0.3,
    "grass": 0.25, "pebblestone": 0.3, "sand": 0.15, "mud": 0.1,
}
SMOOTHNESS_QUALITY: dict[str, float] = {
    "excellent": 1.0, "good": 0.85, "intermediate": 0.6, "bad": 0.4,
    "very_bad": 0.25, "horrible": 0.15, "very_horrible": 0.05, "impassable": 0.0,
}

_LIT_TRUE = {"yes", "24/7", "automatic", "sunset-sunrise", "dusk-dawn", "limited"}
_LIT_FALSE = {"no", "disused"}


def _first_token(value: Any) -> Any:
    """First element of a list-valued OSM tag (OSM often stores multi-values)."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def parse_maxspeed(value: Any) -> float | None:
    """Parse an OSM ``maxspeed`` value to km/h (``None`` if unknown / no limit).

    Handles numbers, ``"30"``, ``"30 mph"``, implicit country codes
    (``"FR:urban"``) and list values (the strictest / max numeric is returned,
    the conservative choice for a comfort penalty)."""
    if isinstance(value, (list, tuple)):
        parsed = [parse_maxspeed(v) for v in value]
        parsed = [p for p in parsed if p is not None]
        return max(parsed) if parsed else None
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        return None
    s = value.strip().lower()
    if not s or s in {"none", "signals", "variable", "unknown"}:
        return None
    if s in COUNTRY_MAXSPEED:
        return COUNTRY_MAXSPEED[s]
    if s == "walk":
        return 6.0
    if s.endswith("mph"):
        num = s[:-3].strip()
        try:
            return float(num) * 1.609344
        except ValueError:
            return None
    if s.endswith("km/h") or s.endswith("kmh"):
        s = s.replace("km/h", "").replace("kmh", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_lit(value: Any) -> bool | None:
    """Parse an OSM ``lit`` value to a bool (``None`` if absent / unrecognised)."""
    value = _first_token(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip().lower()
    if s in _LIT_TRUE:
        return True
    if s in _LIT_FALSE:
        return False
    return None


def surface_quality(value: Any) -> float | None:
    """Map an OSM ``surface`` category to cyclability quality in ``[0, 1]``."""
    tok = _first_token(value)
    if not isinstance(tok, str):
        return None
    return SURFACE_QUALITY.get(tok.strip().lower())


def smoothness_quality(value: Any) -> float | None:
    """Map an OSM ``smoothness`` category to cyclability quality in ``[0, 1]``."""
    tok = _first_token(value)
    if not isinstance(tok, str):
        return None
    return SMOOTHNESS_QUALITY.get(tok.strip().lower())


def edge_tag_features(edges: gpd.GeoDataFrame) -> pd.DataFrame:
    """Per-edge comfort features parsed from raw OSM tag columns.

    Returns a DataFrame aligned to ``edges.index`` with columns
    ``maxspeed_kmh`` (float|NA), ``lit`` (bool|NA), ``surface_q`` /
    ``smoothness_q`` (float|NA). Tag columns absent from ``edges`` are treated as
    all-missing, so this is safe on any osmnx edge frame.
    """
    def col(name: str) -> pd.Series:
        if name in edges.columns:
            return edges[name]
        return pd.Series([None] * len(edges), index=edges.index)

    return pd.DataFrame(
        {
            "maxspeed_kmh": [parse_maxspeed(v) for v in col("maxspeed")],
            "lit": [parse_lit(v) for v in col("lit")],
            "surface_q": [surface_quality(v) for v in col("surface")],
            "smoothness_q": [smoothness_quality(v) for v in col("smoothness")],
        },
        index=edges.index,
    )
