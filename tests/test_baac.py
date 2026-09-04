"""Offline tests for the BAAC crash loader (Foundation v2b, BL-28)."""

from __future__ import annotations

import pytest

from bicyclelane.baac import baac_from_env, load_baac, load_baac_dir


def _write_year(d, year, *, lat="43,5290", lon="5,4470"):
    """Write a minimal BAAC triple for one year; A1 is a bike crash, A2 a car."""
    (d / f"caracteristiques-{year}.csv").write_text(
        "Num_Acc;lat;long\n"
        f"A1;{lat};{lon}\n"
        f"A2;43,6000;5,5000\n", encoding="utf-8")
    (d / f"vehicules-{year}.csv").write_text(
        "Num_Acc;catv\nA1;1\nA2;7\n", encoding="utf-8")  # 1=bike, 7=car
    (d / f"usagers-{year}.csv").write_text(
        "Num_Acc;grav\nA1;3\nA2;1\n", encoding="utf-8")  # A1 hospitalised -> 0.6


def test_load_baac_keeps_only_bicycle_crashes(tmp_path):
    _write_year(tmp_path, 2023)
    g = load_baac(
        str(tmp_path / "caracteristiques-2023.csv"),
        str(tmp_path / "vehicules-2023.csv"),
        str(tmp_path / "usagers-2023.csv"),
    )
    assert len(g) == 1                       # only the bicycle accident A1
    assert g.iloc[0]["num_acc"] == "A1"
    assert g.iloc[0]["severity"] == pytest.approx(0.6)  # grav 3 -> hospitalised
    assert g.crs.to_epsg() == 4326
    assert g.iloc[0].geometry.x == pytest.approx(5.447, abs=1e-3)
    assert g.iloc[0].geometry.y == pytest.approx(43.529, abs=1e-3)


def test_integer_scaled_coordinates_are_rescaled(tmp_path):
    # Older BAAC years store lat/long as integers ×10^5 (no decimal separator).
    _write_year(tmp_path, 2016, lat="4352900", lon="544700")
    g = load_baac(
        str(tmp_path / "caracteristiques-2016.csv"),
        str(tmp_path / "vehicules-2016.csv"),
    )
    assert g.iloc[0].geometry.y == pytest.approx(43.529, abs=1e-3)
    assert g.iloc[0].geometry.x == pytest.approx(5.447, abs=1e-3)
    # no usagers file -> default severity
    assert g.iloc[0]["severity"] == pytest.approx(0.5)


def test_load_baac_dir_concatenates_years_and_bbox(tmp_path):
    _write_year(tmp_path, 2022)
    _write_year(tmp_path, 2023)
    g = load_baac_dir(str(tmp_path))
    assert len(g) == 2  # one bike crash per year
    # bbox around Aix keeps them; a far bbox drops them
    near = load_baac_dir(str(tmp_path), bbox=(5.0, 43.0, 6.0, 44.0))
    assert len(near) == 2
    far = load_baac_dir(str(tmp_path), bbox=(2.0, 48.0, 3.0, 49.0))
    assert len(far) == 0


def test_load_baac_dir_tolerates_publisher_naming_variants(tmp_path):
    # Real data.gouv names are inconsistent: "carcteristiques" is a genuine typo
    # in 2021-2022, 2023-2024 use "caract"/"Caract" with underscores and case.
    (tmp_path / "carcteristiques-2021.csv").write_text(
        "Num_Acc;lat;long\nX1;43,52;5,44\n", encoding="utf-8")
    (tmp_path / "vehicules-2021.csv").write_text(
        "Num_Acc;catv\nX1;1\n", encoding="utf-8")
    (tmp_path / "Caract_2024.csv").write_text(
        "Num_Acc;lat;long\nY1;43,53;5,45\n", encoding="utf-8")
    (tmp_path / "Vehicules_2024.csv").write_text(
        "Num_Acc;catv\nY1;1\n", encoding="utf-8")
    g = load_baac_dir(str(tmp_path))
    assert len(g) == 2  # both the typo'd 2021 and the underscore/case 2024 found


def test_caracteristiques_accident_id_column(tmp_path):
    # From 2022 the caracteristiques file renamed Num_Acc -> Accident_Id (while
    # vehicules/usagers kept Num_Acc, with the same id values).
    (tmp_path / "caracteristiques-2022.csv").write_text(
        "Accident_Id;lat;long\nZ1;43,52;5,44\n", encoding="utf-8")
    (tmp_path / "vehicules-2022.csv").write_text(
        "Num_Acc;catv\nZ1;1\n", encoding="utf-8")
    g = load_baac(str(tmp_path / "caracteristiques-2022.csv"),
                  str(tmp_path / "vehicules-2022.csv"))
    assert len(g) == 1 and g.iloc[0]["num_acc"] == "Z1"


def test_baac_from_env(tmp_path, monkeypatch):
    monkeypatch.delenv("BICYCLELANE_BAAC", raising=False)
    assert baac_from_env() is None
    _write_year(tmp_path, 2023)
    monkeypatch.setenv("BICYCLELANE_BAAC", str(tmp_path))
    g = baac_from_env()
    assert g is not None and len(g) == 1
