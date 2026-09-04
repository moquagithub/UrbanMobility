"""Expert-panel weight elicitation for the composite (BL-22).

Turns a panel of experts' judgements into a single weight vector for the
composite scoring engine (feed the result to
:func:`bicyclelane.composite.run_composite` as ``expert=``). Three elicitation
modes, all returning non-negative weights that sum to 1, aligned to a given
``criteria`` order:

* **direct** — each expert gives an importance score per detector (any scale:
  0–1 sliders, 1–5 Likert, budget points). Each expert's vector is normalised,
  then aggregated across the panel (arithmetic or geometric mean).
* **AHP panel** — each expert fills a pairwise-comparison matrix; the group
  matrix is the element-wise **geometric mean** of the experts' matrices (the
  standard AHP group aggregation), then the principal-eigenvector weights and a
  group consistency ratio (via :func:`bicyclelane.composite.ahp_weights`).
* **Best–Worst Method (BWM)** — a lighter elicitation: each expert names the
  best and worst criterion and rates the others against them (two short
  vectors instead of a full matrix). Solved as a small linear program.

Alternatives to *stated* expert weights (discussed in BL-22): a **data-driven**
weighting learned from the AMP plan (revealed preference), and having experts
judge sampled **opportunities** rather than criteria (the validation panel).
"""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np

from .composite import ahp_weights


def _as_vector(judgement: Mapping[str, float], criteria: Sequence[str]) -> np.ndarray:
    v = np.array([float(judgement.get(c, 0.0)) for c in criteria], dtype=float)
    v = np.clip(v, 0.0, None)
    s = v.sum()
    return v / s if s > 0 else v


def aggregate_direct(
    judgements: Sequence[Mapping[str, float]],
    criteria: Sequence[str],
    *,
    method: str = "geomean",
) -> np.ndarray:
    """Aggregate per-expert importance scores into panel weights (sum 1).

    Each expert vector is normalised first (so scales/points don't bias the
    panel), then combined by the arithmetic (``mean``) or geometric
    (``geomean``, the default — robust to a single extreme expert) mean.
    """
    if not judgements:
        raise ValueError("no expert judgements provided")
    mats = np.array([_as_vector(j, criteria) for j in judgements])  # experts × criteria
    if method == "mean":
        w = mats.mean(axis=0)
    elif method == "geomean":
        # geometric mean over experts; a tiny floor avoids zeros collapsing it
        w = np.exp(np.log(mats + 1e-12).mean(axis=0))
    else:
        raise ValueError(f"unknown method: {method!r}")
    s = w.sum()
    return w / s if s > 0 else np.full(len(criteria), 1.0 / len(criteria))


def aggregate_ahp_panel(matrices: Sequence[np.ndarray]) -> tuple[np.ndarray, float]:
    """Group-AHP weights from several experts' pairwise matrices.

    The group matrix is the element-wise geometric mean of the experts'
    reciprocal matrices (the standard aggregation of individual judgements);
    returns ``(weights, group_consistency_ratio)``.
    """
    if not matrices:
        raise ValueError("no pairwise matrices provided")
    stack = np.array([np.asarray(m, dtype=float) for m in matrices])
    n = stack.shape[1]
    if stack.shape[1] != stack.shape[2]:
        raise ValueError("pairwise matrices must be square")
    group = np.exp(np.log(stack).mean(axis=0))  # element-wise geometric mean
    return ahp_weights(group)


def bwm_weights(
    best: str,
    worst: str,
    best_to_others: Mapping[str, float],
    others_to_worst: Mapping[str, float],
    criteria: Sequence[str],
) -> np.ndarray:
    """Best–Worst Method weights (Rezaei) via a linear program.

    ``best`` / ``worst`` are the most / least important criteria. ``best_to_others[j]``
    is how many times ``best`` is preferred to ``j`` (a_Bj ≥ 1; a_BB = 1);
    ``others_to_worst[j]`` is how many times ``j`` is preferred to ``worst``.
    Minimises the max consistency gap; returns weights summing to 1.
    """
    from scipy.optimize import linprog

    crit = list(criteria)
    n = len(crit)
    idx = {c: i for i, c in enumerate(crit)}
    b, w_ = idx[best], idx[worst]
    # variables: weights w[0..n-1] then xi (index n)
    A_ub, b_ub = [], []
    for j, c in enumerate(crit):
        aBj = float(best_to_others.get(c, 1.0))
        # |w_b - aBj*w_j| <= xi
        row = [0.0] * (n + 1); row[b] += 1.0; row[j] -= aBj; row[n] = -1.0
        A_ub.append(row); b_ub.append(0.0)
        row = [0.0] * (n + 1); row[b] -= 1.0; row[j] += aBj; row[n] = -1.0
        A_ub.append(row); b_ub.append(0.0)
        ajW = float(others_to_worst.get(c, 1.0))
        # |w_j - ajW*w_w| <= xi
        row = [0.0] * (n + 1); row[j] += 1.0; row[w_] -= ajW; row[n] = -1.0
        A_ub.append(row); b_ub.append(0.0)
        row = [0.0] * (n + 1); row[j] -= 1.0; row[w_] += ajW; row[n] = -1.0
        A_ub.append(row); b_ub.append(0.0)
    A_eq = [[1.0] * n + [0.0]]
    b_eq = [1.0]
    c = [0.0] * n + [1.0]  # minimise xi
    bounds = [(0.0, None)] * n + [(0.0, None)]
    r = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds,
                method="highs")
    if not r.success:
        return np.full(n, 1.0 / n)
    w = np.clip(np.asarray(r.x[:n], dtype=float), 0.0, None)
    s = w.sum()
    return w / s if s > 0 else np.full(n, 1.0 / n)
