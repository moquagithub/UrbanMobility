"""Offline tests for expert-panel weight elicitation (BL-22)."""

from __future__ import annotations

import numpy as np
import pytest

from bicyclelane.experts import (
    aggregate_ahp_panel,
    aggregate_direct,
    bwm_weights,
)

CRIT = ["D1", "D5", "D9"]


def test_direct_uniform_gives_equal_weights():
    w = aggregate_direct([{"D1": 1, "D5": 1, "D9": 1}], CRIT)
    np.testing.assert_allclose(w, [1 / 3, 1 / 3, 1 / 3])


def test_direct_aggregates_and_sums_to_one():
    panel = [{"D1": 1, "D5": 3, "D9": 1}, {"D1": 1, "D5": 2, "D9": 2}]
    w = aggregate_direct(panel, CRIT, method="mean")
    assert w.sum() == pytest.approx(1.0)
    assert w[1] > w[0]  # D5 rated highest on average


def test_direct_scale_invariant_across_experts():
    # One expert uses a 0-1 scale, another 0-100 -> normalisation removes the bias
    a = aggregate_direct([{"D1": 0.2, "D5": 0.6, "D9": 0.2}], CRIT)
    b = aggregate_direct([{"D1": 20, "D5": 60, "D9": 20}], CRIT)
    np.testing.assert_allclose(a, b)


def test_ahp_panel_recovers_consistent_group():
    pri = np.array([0.2, 0.5, 0.3])
    A = pri[:, None] / pri[None, :]      # a consistent matrix
    w, cr = aggregate_ahp_panel([A, A])  # two identical experts
    np.testing.assert_allclose(w, pri, atol=1e-8)
    assert cr == pytest.approx(0.0, abs=1e-8)


def test_bwm_recovers_simple_weights():
    # Best = D9, worst = D1; D9 is 2x D5 and 6x D1; D5 is 3x D1, D9 is 6x D1.
    w = bwm_weights(
        best="D9", worst="D1",
        best_to_others={"D9": 1, "D5": 2, "D1": 6},
        others_to_worst={"D9": 6, "D5": 3, "D1": 1},
        criteria=CRIT)
    assert w.sum() == pytest.approx(1.0)
    # ordering must be D9 > D5 > D1
    d = dict(zip(CRIT, w))
    assert d["D9"] > d["D5"] > d["D1"]
    # consistent case -> weights ~ (6:3:1)/10 for D9:D5:D1... i.e. D1≈0.1
    assert d["D1"] == pytest.approx(0.1, abs=0.03)


def test_bwm_weights_feed_composite():
    from bicyclelane.composite import composite_scores, normalise
    w = bwm_weights("D9", "D1", {"D9": 1, "D5": 2, "D1": 4},
                    {"D9": 4, "D5": 2, "D1": 1}, CRIT)
    X = normalise(np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]), "minmax")
    scores, _ = composite_scores(X, w)
    assert scores[1] > scores[0]  # the D9-heavy unit wins under D9-favouring weights
