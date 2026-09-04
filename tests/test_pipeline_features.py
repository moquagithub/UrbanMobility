"""Regression test: _features must tolerate all-None numeric columns.

Sparse OSM tags (e.g. no `surface` anywhere in a city) make a detector emit a
numeric attribute column that is entirely None -> object dtype, where the NaN
test `v == v` is True and float(None) used to raise. See the Aix bug report.
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from pipeline import _features  # noqa: E402  (app/ added to path above)


def test_features_handles_all_none_numeric_column():
    gdf = gpd.GeoDataFrame(
        {
            "id": ["D8-0", "D8-1"],
            "score": [0.9, 0.5],
            "explanation": ["a", "b"],
            "surface_q": [None, None],        # all-None -> object dtype
            "maxspeed_kmh": [50.0, None],     # mixed
        },
        geometry=[LineString([(0, 0), (1, 0)]), LineString([(0, 1), (1, 1)])],
        crs="EPSG:2154",
    )
    feats = _features(gdf, "D8", cap=10)  # must not raise
    assert len(feats) == 2
    p0 = feats[0]["properties"]
    # all-None column is omitted; present float is kept and rounded
    assert "surface_q" not in p0
    assert p0["maxspeed_kmh"] == 50.0
    # the row whose maxspeed is None omits it rather than crashing
    assert "maxspeed_kmh" not in feats[1]["properties"]
