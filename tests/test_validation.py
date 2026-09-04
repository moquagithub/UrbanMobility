"""Offline tests for the AMP-plan backtest (BL-22)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from bicyclelane.validation import _category, backtest, plan_positives

CRS = "EPSG:2154"


def _plan():
    return gpd.GeoDataFrame(
        {"statut": ["A réaliser: 2019-2023", "Réalisé dans le plan vélo: 2019-2023",
                    "Réalisé avant le plan vélo"],
         "category": ["planned_new", "realized_in_plan", "pre_existing"]},
        geometry=[LineString([(0, 0), (100, 0)]),
                  LineString([(0, 100), (100, 100)]),
                  LineString([(0, 900), (100, 900)])], crs=CRS)


def test_category_mapping():
    assert _category("A réaliser: 2024-2030") == "planned_new"
    assert _category("En travaux") == "planned_new"
    assert _category("Réalisé dans le plan vélo 2024") == "realized_in_plan"
    assert _category("Réalisé 2025") == "realized_in_plan"
    assert _category("Réalisé avant le plan vélo") == "pre_existing"


def test_plan_positives_excludes_pre_existing():
    pos = plan_positives(_plan())
    assert len(pos) == 2  # planned_new + realized_in_plan, not pre_existing


def test_backtest_perfect_and_partial():
    pos = plan_positives(_plan())  # two positives at y=0 and y=100
    # Ranked opportunities: #1 on the first positive (hit), #2 far away (miss),
    # #3 on the second positive (hit).
    opp = gpd.GeoDataFrame(geometry=[
        LineString([(10, 2), (90, 2)]),      # near y=0 positive -> hit
        LineString([(10, 500), (90, 500)]),  # nowhere -> miss
        LineString([(10, 102), (90, 102)]),  # near y=100 positive -> hit
    ], crs=CRS)
    m = backtest(opp, pos, match_tol_m=10, ks=(1, 3))
    assert m["n_positives"] == 2 and m["n_opportunities"] == 3
    assert m["n_hits"] == 2
    assert m["precision_at"][1] == pytest.approx(1.0)   # rank 1 is a hit
    assert m["precision_at"][3] == pytest.approx(2 / 3, abs=1e-3)
    assert m["recall_at"][3] == pytest.approx(1.0)      # both positives covered
    assert m["average_precision"] > 0.5


def test_backtest_empty_inputs():
    pos = plan_positives(_plan())
    empty = gpd.GeoDataFrame(geometry=[], crs=CRS)
    assert backtest(empty, pos)["average_precision"] == 0.0
    assert backtest(pos, empty)["n_positives"] == 0
