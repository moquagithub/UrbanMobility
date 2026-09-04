"""Composite scoring engine (BL-20).

The v1 detectors (D1, D2, D4, D5, D9, D11) each produce their own ranked output.
This module aggregates them into a single **composite priority score** over a
shared set of candidate units, and does so under *several* weighting methods so
their rankings can be compared rather than committing to one (cross-cutting
problem T6, multi-criteria weighting).

The engine is deliberately geometry-agnostic: it operates on a **decision
matrix** ``X`` of shape ``(n_units, n_criteria)`` whose columns are per-detector
scores. Mapping the heterogeneous detector outputs (segments, zones, corridors)
onto a common spatial unit is the caller's job (web integration, BL-21); keeping
the numeric core pure makes it fully unit-testable offline.

Pipeline:

1. :func:`normalise` — put criteria on a comparable scale (min-max / z-score /
   rank / quantile).
2. a weight vector from one of :func:`equal_weights`, :func:`expert_weights`,
   :func:`ahp_weights`; or the non-aggregated :func:`pareto_front`.
3. :func:`composite_scores` — weighted sum plus the per-criterion
   **contribution** matrix ``w_j · x_ij`` (explainability: how much each
   detector drove a unit's score).
4. comparison helpers — :func:`rank_correlation`, :func:`topk_jaccard`,
   :func:`weight_sensitivity` — to report how the methods agree.

:func:`run_composite` ties it together and returns a :class:`CompositeResult`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np
from scipy import stats

NormMethod = str  # "minmax" | "zscore" | "rank" | "quantile"


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #
def normalise(X: np.ndarray, method: NormMethod = "minmax") -> np.ndarray:
    """Normalise each column of ``X`` independently (higher = better).

    * ``minmax``   → linearly to ``[0, 1]`` (a constant column becomes all 1s).
    * ``zscore``   → zero mean, unit std (a constant column becomes all 0s).
    * ``rank``     → average ranks rescaled to ``[0, 1]``; robust to outliers.
    * ``quantile`` → empirical CDF in ``[0, 1]`` (ties share their mean rank).
    """
    X = np.asarray(X, dtype=float)
    if X.ndim != 2:
        raise ValueError("X must be a 2-D (n_units, n_criteria) matrix")
    out = np.empty_like(X)
    for j in range(X.shape[1]):
        col = X[:, j]
        out[:, j] = _normalise_col(col, method)
    return out


def _normalise_col(col: np.ndarray, method: NormMethod) -> np.ndarray:
    n = col.size
    if n == 0:
        return col.copy()
    if method == "minmax":
        lo, hi = float(np.min(col)), float(np.max(col))
        if hi == lo:
            return np.ones_like(col)
        return (col - lo) / (hi - lo)
    if method == "zscore":
        mu, sd = float(np.mean(col)), float(np.std(col))
        if sd == 0.0:
            return np.zeros_like(col)
        return (col - mu) / sd
    if method in ("rank", "quantile"):
        ranks = stats.rankdata(col, method="average")  # 1..n, ties averaged
        if n == 1:
            return np.ones_like(col)
        return (ranks - 1.0) / (n - 1.0)
    raise ValueError(f"unknown normalisation method: {method!r}")


# --------------------------------------------------------------------------- #
# Weighting methods
# --------------------------------------------------------------------------- #
def equal_weights(n_criteria: int) -> np.ndarray:
    """Uniform weights summing to 1 — the baseline."""
    if n_criteria <= 0:
        raise ValueError("n_criteria must be positive")
    return np.full(n_criteria, 1.0 / n_criteria)


def expert_weights(raw: Sequence[float]) -> np.ndarray:
    """Normalise an expert's raw importance vector to sum to 1.

    Accepts any non-negative numbers (e.g. 0–1 sliders, 1–5 Likert). Rejects an
    all-zero vector, which carries no information.
    """
    w = np.asarray(raw, dtype=float)
    if np.any(w < 0):
        raise ValueError("expert weights must be non-negative")
    total = float(w.sum())
    if total == 0.0:
        raise ValueError("expert weights sum to zero")
    return w / total


def ahp_weights(pairwise: np.ndarray) -> tuple[np.ndarray, float]:
    """Saaty AHP weights from a pairwise-comparison matrix.

    ``pairwise[i, j]`` is how many times more important criterion *i* is than
    *j* (so ``pairwise[j, i] == 1 / pairwise[i, j]`` and the diagonal is 1).
    Returns ``(weights, consistency_ratio)``: the principal-eigenvector weights
    (summing to 1) and the consistency ratio CR. A CR ≤ 0.10 is conventionally
    acceptable; above that the expert judgements are too inconsistent to trust.
    """
    A = np.asarray(pairwise, dtype=float)
    n = A.shape[0]
    if A.shape != (n, n):
        raise ValueError("pairwise matrix must be square")
    eigvals, eigvecs = np.linalg.eig(A)
    k = int(np.argmax(eigvals.real))
    w = np.abs(eigvecs[:, k].real)
    w = w / w.sum()
    lambda_max = float(eigvals[k].real)
    if n <= 2:
        cr = 0.0  # a 1x1/2x2 reciprocal matrix is always perfectly consistent
    else:
        ci = (lambda_max - n) / (n - 1)
        ri = _RANDOM_INDEX.get(n, 1.49)  # Saaty's RI table; 1.49 for large n
        cr = ci / ri if ri > 0 else 0.0
    return w, cr


# Saaty's Random Index (average CI of random reciprocal matrices) by order n.
_RANDOM_INDEX: dict[int, float] = {
    1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24,
    7: 1.32, 8: 1.41, 9: 1.45, 10: 1.49,
}


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def composite_scores(
    Xn: np.ndarray, weights: Sequence[float]
) -> tuple[np.ndarray, np.ndarray]:
    """Weighted-sum composite and its per-criterion contribution matrix.

    ``Xn`` is an already-normalised matrix. Returns ``(scores, contributions)``
    where ``scores[i] = Σ_j w_j · Xn[i, j]`` and
    ``contributions[i, j] = w_j · Xn[i, j]`` (rows sum to ``scores``) — the
    explainability breakdown of how much each detector drove unit *i*.
    """
    Xn = np.asarray(Xn, dtype=float)
    w = np.asarray(weights, dtype=float)
    if w.shape[0] != Xn.shape[1]:
        raise ValueError("weights length must match number of criteria")
    contributions = Xn * w[np.newaxis, :]
    scores = contributions.sum(axis=1)
    return scores, contributions


def pareto_front(X: np.ndarray) -> np.ndarray:
    """Boolean mask of the non-dominated rows (all criteria maximised).

    A unit is dominated when another unit is ``>=`` on every criterion and
    strictly ``>`` on at least one. The survivors are the Pareto-efficient set —
    a weight-free way to surface "no method can rank these away" opportunities.
    """
    X = np.asarray(X, dtype=float)
    n = X.shape[0]
    nondominated = np.ones(n, dtype=bool)
    for i in range(n):
        if not nondominated[i]:
            continue
        # rows that dominate i: >= everywhere and > somewhere
        ge = np.all(X >= X[i], axis=1)
        gt = np.any(X > X[i], axis=1)
        if np.any(ge & gt):
            nondominated[i] = False
    return nondominated


def pareto_layers(X: np.ndarray) -> np.ndarray:
    """Non-dominated-sorting rank per row (0 = first Pareto front, 1 = next …).

    Peels successive Pareto fronts, giving an ordinal ranking usable when a
    single non-dominated set is too coarse.
    """
    X = np.asarray(X, dtype=float)
    n = X.shape[0]
    layers = np.full(n, -1, dtype=int)
    remaining = np.ones(n, dtype=bool)
    current = 0
    while remaining.any():
        idx = np.where(remaining)[0]
        front_mask = pareto_front(X[idx])
        layers[idx[front_mask]] = current
        remaining[idx[front_mask]] = False
        current += 1
    return layers


# --------------------------------------------------------------------------- #
# Comparison between methods
# --------------------------------------------------------------------------- #
def rank_correlation(a: Sequence[float], b: Sequence[float]) -> dict[str, float]:
    """Spearman ρ and Kendall τ between two score vectors (higher = agree)."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.size < 2:
        return {"spearman": 1.0, "kendall": 1.0}
    rho = stats.spearmanr(a, b).statistic
    tau = stats.kendalltau(a, b).statistic
    return {
        "spearman": float(rho) if rho == rho else 0.0,
        "kendall": float(tau) if tau == tau else 0.0,
    }


def topk_jaccard(a: Sequence[float], b: Sequence[float], k: int) -> float:
    """Jaccard overlap of the top-``k`` units under two score vectors.

    1.0 = identical top-k sets, 0.0 = disjoint. Ties at the cutoff are resolved
    by taking the ``k`` highest indices deterministically.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    k = min(k, a.size)
    if k <= 0:
        return 1.0
    top_a = set(np.argsort(-a, kind="stable")[:k].tolist())
    top_b = set(np.argsort(-b, kind="stable")[:k].tolist())
    inter = len(top_a & top_b)
    union = len(top_a | top_b)
    return inter / union if union else 1.0


def weight_sensitivity(
    Xn: np.ndarray,
    weights: Sequence[float],
    *,
    k: int,
    perturbations: int = 100,
    scale: float = 0.1,
    seed: int = 0,
) -> dict[str, float]:
    """How stable the top-``k`` is under random weight perturbations.

    Adds zero-mean noise (``scale`` × relative) to the weights ``perturbations``
    times, re-ranks, and reports the mean/min top-``k`` Jaccard against the
    unperturbed ranking. Near 1.0 → the ranking is robust to how exactly the
    weights are set; low → the top-k is fragile and the weighting choice matters.
    """
    Xn = np.asarray(Xn, dtype=float)
    w = np.asarray(weights, dtype=float)
    base, _ = composite_scores(Xn, w)
    rng = np.random.default_rng(seed)
    jaccs = []
    for _ in range(perturbations):
        noise = rng.normal(0.0, scale, size=w.shape)
        wp = np.clip(w * (1.0 + noise), 0.0, None)
        if wp.sum() == 0.0:
            continue
        wp = wp / wp.sum()
        sp, _ = composite_scores(Xn, wp)
        jaccs.append(topk_jaccard(base, sp, k))
    if not jaccs:
        return {"mean_jaccard": 1.0, "min_jaccard": 1.0}
    return {"mean_jaccard": float(np.mean(jaccs)), "min_jaccard": float(np.min(jaccs))}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
@dataclass
class CompositeResult:
    """Bundle returned by :func:`run_composite`.

    * ``criteria`` — criterion (detector) names, matching the columns.
    * ``normalised`` — the normalised decision matrix.
    * ``weights`` — ``method -> weight vector`` for each aggregating method.
    * ``scores`` — ``method -> composite score per unit``.
    * ``contributions`` — ``method -> (n_units, n_criteria)`` breakdown.
    * ``pareto_mask`` — non-dominated units (weight-free).
    * ``comparison`` — pairwise rank agreement between methods.
    * ``meta`` — extras (AHP consistency ratio, sensitivity, …).
    """

    criteria: list[str]
    normalised: np.ndarray
    weights: dict[str, np.ndarray]
    scores: dict[str, np.ndarray]
    contributions: dict[str, np.ndarray]
    pareto_mask: np.ndarray
    comparison: dict[str, dict[str, float]]
    meta: dict[str, object] = field(default_factory=dict)


def run_composite(
    X: np.ndarray,
    criteria: Sequence[str],
    *,
    norm: NormMethod = "minmax",
    expert: Sequence[float] | None = None,
    ahp: np.ndarray | None = None,
    top_k: int = 20,
) -> CompositeResult:
    """Score ``X`` under every available weighting method and compare them.

    Always computes ``equal`` weights; adds ``expert`` and/or ``ahp`` when their
    inputs are supplied. Reports pairwise Spearman/Kendall/top-k agreement so
    the team can see where the methods diverge before choosing one.
    """
    X = np.asarray(X, dtype=float)
    n_criteria = X.shape[1]
    if len(criteria) != n_criteria:
        raise ValueError("criteria length must match number of columns")

    Xn = normalise(X, norm)

    weights: dict[str, np.ndarray] = {"equal": equal_weights(n_criteria)}
    meta: dict[str, object] = {"norm": norm}
    if expert is not None:
        weights["expert"] = expert_weights(expert)
    if ahp is not None:
        w_ahp, cr = ahp_weights(ahp)
        weights["ahp"] = w_ahp
        meta["ahp_consistency_ratio"] = cr

    scores: dict[str, np.ndarray] = {}
    contributions: dict[str, np.ndarray] = {}
    for name, w in weights.items():
        s, c = composite_scores(Xn, w)
        scores[name] = s
        contributions[name] = c

    pmask = pareto_front(Xn)

    comparison: dict[str, dict[str, float]] = {}
    names = list(scores)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            corr = rank_correlation(scores[a], scores[b])
            corr["topk_jaccard"] = topk_jaccard(scores[a], scores[b], top_k)
            comparison[f"{a}|{b}"] = corr

    meta["sensitivity"] = {
        name: weight_sensitivity(Xn, w, k=top_k) for name, w in weights.items()
    }

    return CompositeResult(
        criteria=list(criteria),
        normalised=Xn,
        weights=weights,
        scores=scores,
        contributions=contributions,
        pareto_mask=pmask,
        comparison=comparison,
        meta=meta,
    )


def decision_matrix(
    per_detector_norm: Mapping[str, Mapping[str, float]],
    criteria: Sequence[str],
) -> tuple[np.ndarray, list[str]]:
    """Assemble a decision matrix from per-detector, per-unit normalised scores.

    ``per_detector_norm`` maps a detector key to ``{unit_id: score}``. Units are
    the union of all ids seen; a detector that says nothing about a unit
    contributes 0 there (no signal). Returns ``(X, unit_ids)`` with ``X`` shaped
    ``(n_units, len(criteria))`` and columns ordered like ``criteria``.

    This is the bridge from the pooled per-detector outputs to the engine once a
    shared spatial unit exists; the web integration (BL-21) provides the mapping.
    """
    unit_ids: list[str] = []
    seen: set[str] = set()
    for det in criteria:
        for uid in per_detector_norm.get(det, {}):
            if uid not in seen:
                seen.add(uid)
                unit_ids.append(uid)
    X = np.zeros((len(unit_ids), len(criteria)), dtype=float)
    index = {uid: i for i, uid in enumerate(unit_ids)}
    for j, det in enumerate(criteria):
        for uid, val in per_detector_norm.get(det, {}).items():
            X[index[uid], j] = float(val)
    return X, unit_ids
