"""Offline sanity tests for D8 (comfort / experience)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from bicyclelane.detectors.comfort import detect_comfort_deficit

CRS = "EPSG:2154"


def _roads(rows):
    """rows: list of (highway, maxspeed, surface, lit, coords)."""
    data = {"highway": [], "maxspeed": [], "surface": [], "lit": [], "name": []}
    geoms = []
    for hw, ms, surf, lit, coords in rows:
        data["highway"].append(hw)
        data["maxspeed"].append(ms)
        data["surface"].append(surf)
        data["lit"].append(lit)
        data["name"].append("Rue X")
        geoms.append(LineString(coords))
    return gpd.GeoDataFrame(data, geometry=geoms, crs=CRS)


def _far_cycle():
    # A cycle line far from the candidate roads so nothing is "already served".
    return gpd.GeoDataFrame(geometry=[LineString([(9000, 9000), (9100, 9000)])], crs=CRS)


def test_fast_road_scores_higher_than_calm_street():
    roads = _roads([
        ("secondary", "70", None, None, [(0, 0), (200, 0)]),      # stressful
        ("residential", "30", None, None, [(0, 100), (200, 100)]),  # calm
    ])
    out = detect_comfort_deficit(roads, _far_cycle(), city="Synth", crs=CRS)
    assert (out["detector"] == "D8").all()
    assert len(out) >= 1
    top = out.iloc[0]
    assert top["highway"] == "secondary"
    # calm residential 30 km/h -> speed_stress 0 -> deficit 0 -> below min_score
    assert "residential" not in set(out["highway"])


def test_bad_surface_beats_asphalt_same_class():
    roads = _roads([
        ("tertiary", "50", "gravel", None, [(0, 0), (200, 0)]),
        ("tertiary", "50", "asphalt", None, [(0, 100), (200, 100)]),
    ])
    out = detect_comfort_deficit(roads, _far_cycle(), city="Synth", crs=CRS)
    # gravel (q=0.4 -> penalty 0.6) must outscore asphalt (q=1.0 -> penalty 0)
    gravel_score = out.loc[out["surface_q"] < 0.5, "score"].iloc[0]
    asphalt_score = out.loc[out["surface_q"] > 0.9, "score"].iloc[0]
    assert gravel_score > asphalt_score


def test_unlit_adds_penalty():
    roads = _roads([
        ("tertiary", "50", "asphalt", "no", [(0, 0), (200, 0)]),
        ("tertiary", "50", "asphalt", "yes", [(0, 100), (200, 100)]),
    ])
    out = detect_comfort_deficit(roads, _far_cycle(), city="Synth", crs=CRS)
    unlit = out[out["lit"] == False]["score"].iloc[0]   # noqa: E712
    lit = out[out["lit"] == True]["score"].iloc[0]       # noqa: E712
    assert unlit > lit


def test_already_served_road_is_excluded():
    roads = _roads([("secondary", "70", None, None, [(0, 0), (200, 0)])])
    # A cycle line running right along the road -> it is already served.
    served = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (200, 0)])], crs=CRS)
    out = detect_comfort_deficit(roads, served, city="Synth", crs=CRS)
    assert len(out) == 0


def test_motorway_is_excluded():
    roads = _roads([("motorway", "130", None, None, [(0, 0), (200, 0)])])
    out = detect_comfort_deficit(roads, _far_cycle(), city="Synth", crs=CRS)
    assert len(out) == 0
