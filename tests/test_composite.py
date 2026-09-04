"""Offline tests for the composite scoring engine (BL-20)."""

from __future__ import annotations

import numpy as np
import pytest

from bicyclelane.composite import (
    ahp_weights,
    composite_scores,
    decision_matrix,
    equal_weights,
    expert_weights,
    normalise,
    pareto_front,
    pareto_layers,
    rank_correlation,
    run_composite,
    topk_jaccard,
    weight_sensitivity,
)


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
def test_minmax_maps_to_unit_interval():
    X = np.array([[0.0, 10.0], [5.0, 20.0], [10.0, 30.0]])
    Xn = normalise(X, "minmax")
    assert Xn.min() == pytest.approx(0.0)
    assert Xn.max() == pytest.approx(1.0)
    np.testing.assert_allclose(Xn[:, 0], [0.0, 0.5, 1.0])


def test_minmax_constant_column_is_all_ones():
    X = np.array([[3.0], [3.0], [3.0]])
    np.testing.assert_allclose(normalise(X, "minmax")[:, 0], [1.0, 1.0, 1.0])


def test_zscore_zero_mean_unit_std():
    X = np.array([[1.0], [2.0], [3.0], [4.0]])
    col = normalise(X, "zscore")[:, 0]
    assert col.mean() == pytest.approx(0.0, abs=1e-12)
    assert col.std() == pytest.approx(1.0)


def test_rank_normalisation_is_monotone_in_unit_interval():
    X = np.array([[10.0], [30.0], [20.0]])
    col = normalise(X, "rank")[:, 0]
    # 10 -> lowest (0), 30 -> highest (1), 20 -> middle (0.5)
    np.testing.assert_allclose(col, [0.0, 1.0, 0.5])


# --------------------------------------------------------------------------- #
# Weighting
# --------------------------------------------------------------------------- #
def test_equal_weights_sum_to_one():
    w = equal_weights(4)
    assert w.sum() == pytest.approx(1.0)
    assert np.allclose(w, 0.25)


def test_expert_weights_normalised():
    w = expert_weights([1, 2, 1])
    assert w.sum() == pytest.approx(1.0)
    np.testing.assert_allclose(w, [0.25, 0.5, 0.25])


def test_expert_weights_reject_all_zero():
    with pytest.raises(ValueError):
        expert_weights([0, 0, 0])


def test_ahp_recovers_weights_of_consistent_matrix():
    # Build a perfectly consistent matrix from a known priority vector.
    priorities = np.array([0.5, 0.3, 0.2])
    A = priorities[:, None] / priorities[None, :]
    w, cr = ahp_weights(A)
    np.testing.assert_allclose(w, priorities, atol=1e-8)
    assert cr == pytest.approx(0.0, abs=1e-8)  # consistent -> CR ~ 0


def test_ahp_inconsistent_matrix_has_positive_cr():
    A = np.array([[1.0, 3.0, 9.0], [1 / 3, 1.0, 0.2], [1 / 9, 5.0, 1.0]])
    _, cr = ahp_weights(A)
    assert cr > 0.1  # deliberately inconsistent judgements


# --------------------------------------------------------------------------- #
# Aggregation & explainability
# --------------------------------------------------------------------------- #
def test_contributions_sum_to_scores():
    Xn = np.array([[1.0, 0.0], [0.5, 0.5], [0.0, 1.0]])
    w = np.array([0.7, 0.3])
    scores, contrib = composite_scores(Xn, w)
    np.testing.assert_allclose(contrib.sum(axis=1), scores)
    np.testing.assert_allclose(scores, [0.7, 0.5, 0.3])
    # contribution of criterion 0 to unit 0 is exactly w0 * x00
    assert contrib[0, 0] == pytest.approx(0.7)


def test_composite_scores_weight_length_mismatch():
    with pytest.raises(ValueError):
        composite_scores(np.zeros((3, 2)), [1.0])


# --------------------------------------------------------------------------- #
# Pareto
# --------------------------------------------------------------------------- #
def test_pareto_front_drops_dominated_point():
    # C is dominated by B (B >= C on both, strictly > on both). A and B are on
    # the front (each best on one criterion).
    X = np.array([[1.0, 0.0], [0.6, 0.6], [0.5, 0.5], [0.0, 1.0]])
    mask = pareto_front(X)
    assert mask.tolist() == [True, True, False, True]


def test_pareto_layers_peel_in_order():
    X = np.array([[1.0, 1.0], [0.5, 0.5], [0.0, 0.0]])
    layers = pareto_layers(X)
    assert layers.tolist() == [0, 1, 2]


# --------------------------------------------------------------------------- #
# Comparison helpers
# --------------------------------------------------------------------------- #
def test_rank_correlation_identical_and_reversed():
    a = [1.0, 2.0, 3.0, 4.0]
    assert rank_correlation(a, a)["spearman"] == pytest.approx(1.0)
    assert rank_correlation(a, list(reversed(a)))["spearman"] == pytest.approx(-1.0)


def test_topk_jaccard_bounds():
    a = np.array([0.9, 0.8, 0.1, 0.2])
    assert topk_jaccard(a, a, 2) == pytest.approx(1.0)
    b = np.array([0.1, 0.2, 0.9, 0.8])  # top-2 disjoint from a's
    assert topk_jaccard(a, b, 2) == pytest.approx(0.0)


def test_weight_sensitivity_stable_when_one_criterion_dominates():
    # Unit 0 is best on both criteria -> top-1 is robust to weight perturbation.
    Xn = np.array([[1.0, 1.0], [0.2, 0.1], [0.0, 0.0]])
    s = weight_sensitivity(Xn, [0.5, 0.5], k=1, perturbations=50)
    assert s["mean_jaccard"] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def test_run_composite_end_to_end():
    X = np.array([[10.0, 0.0], [5.0, 5.0], [0.0, 10.0], [2.0, 1.0]])
    criteria = ["D1", "D5"]
    priorities = np.array([0.6, 0.4])
    A = priorities[:, None] / priorities[None, :]
    res = run_composite(X, criteria, norm="minmax", expert=[3, 1], ahp=A, top_k=2)

    assert set(res.scores) == {"equal", "expert", "ahp"}
    for name in res.scores:
        # contributions decompose the scores for every method
        np.testing.assert_allclose(
            res.contributions[name].sum(axis=1), res.scores[name])
    assert res.meta["ahp_consistency_ratio"] == pytest.approx(0.0, abs=1e-8)
    # ahp weights ~ the priorities we encoded
    np.testing.assert_allclose(res.weights["ahp"], priorities, atol=1e-8)
    assert "equal|expert" in res.comparison
    assert res.pareto_mask.dtype == bool and res.pareto_mask.shape == (4,)


def test_run_composite_criteria_length_checked():
    with pytest.raises(ValueError):
        run_composite(np.zeros((2, 2)), ["only-one"])


def test_decision_matrix_union_and_zero_fill():
    per = {"D1": {"u1": 0.9, "u2": 0.4}, "D5": {"u2": 0.7, "u3": 1.0}}
    X, units = decision_matrix(per, ["D1", "D5"])
    assert units == ["u1", "u2", "u3"]
    # u1 has no D5 signal -> 0; u3 has no D1 signal -> 0
    exp = np.array([[0.9, 0.0], [0.4, 0.7], [0.0, 1.0]])
    np.testing.assert_allclose(X, exp)
