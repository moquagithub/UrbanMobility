"""Offline tests for hub/generator weighting (D9 f_h, ridership/enrolment hooks)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from bicyclelane.detectors.intermodal import detect_intermodal_access
from bicyclelane.places import (
    apply_nearest_value, load_station_ridership, load_school_enrolment,
)

CRS = "EPSG:2154"


def _far_cycle():
    return gpd.GeoDataFrame(geometry=[LineString([(5000, 5000), (5100, 5000)])], crs=CRS)


def _roads():
    return gpd.GeoDataFrame(
        {"highway": ["residential"], "name": ["Rue A"]},
        geometry=[LineString([(400, 500), (600, 500)])], crs=CRS)


def _hub(weight):
    return gpd.GeoDataFrame(
        {"kind": ["station"], "hub_weight": [weight]},
        geometry=[Point(500, 500)], crs=CRS)


def test_d9_score_scales_with_hub_weight():
    lo = detect_intermodal_access(_far_cycle(), _roads(), _hub(1.0), r_acc=800, crs=CRS)
    hi = detect_intermodal_access(_far_cycle(), _roads(), _hub(4.0), r_acc=800, crs=CRS)
    assert len(lo) == 1 and len(hi) == 1
    # f_h enters the score linearly: 4x the weight -> 4x the score.
    assert hi.iloc[0]["score"] == pytest.approx(4.0 * lo.iloc[0]["score"])
    assert hi.iloc[0]["hub_weight"] == pytest.approx(4.0)


def test_apply_nearest_value_overrides_matched_only():
    target = gpd.GeoDataFrame(
        {"hub_weight": [2.0, 2.0]},
        geometry=[Point(5.4400, 43.5200), Point(5.6000, 43.7000)], crs="EPSG:4326")
    ref = gpd.GeoDataFrame(
        {"ridership": [12345.0]},
        geometry=[Point(5.4401, 43.5201)], crs="EPSG:4326")  # ~13 m from target 0
    out = apply_nearest_value(target, ref, "ridership", "hub_weight", max_dist_m=500)
    assert out["hub_weight"].iloc[0] == pytest.approx(12345.0)  # matched -> real value
    assert out["hub_weight"].iloc[1] == pytest.approx(2.0)      # too far -> keeps proxy


def test_ridership_and_enrolment_loaders(tmp_path):
    r = tmp_path / "ridership.csv"
    r.write_text("nom,longitude,latitude,frequentation\nGareA,5.44,43.52,1000000\n")
    g = load_station_ridership(str(r))
    assert "ridership" in g.columns and len(g) == 1
    assert g["ridership"].iloc[0] == pytest.approx(1_000_000)

    e = tmp_path / "enrol.csv"
    e.write_text("name,lon,lat,effectif\nEcoleB,5.45,43.53,650\n")
    g2 = load_school_enrolment(str(e))
    assert "enrolment" in g2.columns and g2["enrolment"].iloc[0] == pytest.approx(650)
