"""
Étape S5 — Génération des graphiques de comparaison inter-algorithmes.

Pour chaque type de données ayant des résultats d'exécution :
  1. Génère un graphique PAR PROBLÈME (2-4 algorithmes → lisible)
  2. Génère un graphique global horizontal trié par F1
  3. Sauvegarde les PNG dans data/figures/{type_id}/

Figures générées :
  data/figures/{type_id}/compare_p{prob_key}.png  ← par problème
  data/figures/compare_{type_id}.png               ← global (horizontal)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

import tempfile

log = logging.getLogger("auto2.s5_compare")

FIGURES_DIR = Path(tempfile.gettempdir()) / "mobility_pipeline" / "figures"


def run(type_ids: Optional[List[str]] = None) -> Dict[str, int]:
    """Génère les figures de comparaison par problème + globale."""
    import matplotlib
    matplotlib.use("Agg")

    from shared.db.repository import CatalogueRepository, NotebookRepository, FigureRepository

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    cat_repo = CatalogueRepository()
    nb_repo  = NotebookRepository()
    fig_repo = FigureRepository()

    all_types = cat_repo.list_data_types()
    if type_ids:
        types = [t for t in all_types if t["id"] in type_ids]
    else:
        types = [
            t for t in all_types
            if t.get("processing_status") in ("notebooks_done", "report_done")
        ]

    stats = {"ok": 0, "skip": 0, "error": 0}

    for dt in types:
        type_id   = dt["id"]
        type_name = dt["name"]
        type_fig_dir = FIGURES_DIR / type_id
        type_fig_dir.mkdir(parents=True, exist_ok=True)

        results = nb_repo.get_results_for_type(type_id)
        if not results:
            log.info("[S5] %s — aucun résultat, skip", type_name)
            stats["skip"] += 1
            continue

        # ── Graphique de comparaison par problème (artefact principal) ──
        problems_map = _group_by_problem(results)

        if not problems_map:
            log.info("[S5] %s — aucun résultat, skip", type_name)
            stats["skip"] += 1
            continue

        type_ok = 0
        for prob_key, prob_results in problems_map.items():
            prob_title   = prob_results[0].get("problem_title", prob_key)
            prob_id_raw  = prob_results[0].get("problem_id")
            metrics_data = _extract_metrics(prob_results)
            if not metrics_data:
                continue

            perf_path   = type_fig_dir / f"compare_{prob_key}_performance.png"
            timing_path = type_fig_dir / f"compare_{prob_key}_timing.png"
            try:
                _generate_performance_chart(metrics_data, prob_title, type_name, perf_path)
                _generate_timing_chart(metrics_data, prob_title, timing_path)
            except Exception as exc:
                log.warning("[S5] %s/%s — génération figure erreur : %s",
                            type_name, prob_key, exc)
                stats["error"] += 1
                continue

            minio_key = ""
            for fig_path, fig_suffix in [(perf_path, "performance"), (timing_path, "timing")]:
                if not fig_path.exists():
                    continue
                _key = ""
                try:
                    from shared.storage.catalogue_storage import upload_comparison_figure_named
                    _png_bytes = fig_path.read_bytes()
                    _rc = upload_comparison_figure_named(type_id, prob_key, fig_suffix, _png_bytes)
                    if _rc.success:
                        _key = _rc.object_key
                        if not minio_key:
                            minio_key = _key
                except Exception as exc:
                    log.debug("[S5] Catalogue Minio figure erreur : %s", exc)

                if not _key:
                    try:
                        from shared.storage import get_storage
                        _store = get_storage()
                        _res = _store.upload_figure(
                            type_id, f"{prob_key}/{fig_path.name}", fig_path)
                        if _res.success:
                            _key = _res.object_key
                    except Exception:
                        pass

                if _key:
                    fig_path.unlink(missing_ok=True)

            fig_repo.save_figure(
                data_type_id    = type_id,
                figure_key      = f"compare_{type_id}_{prob_key}",
                figure_type     = "comparison_bar",
                title           = f"Comparaison algorithmes — {prob_title[:80]}",
                file_path       = str(perf_path),
                metrics_json    = {
                    d["name"]: {k: d[k] for k in ("f1", "precision", "recall", "detection_rate")
                                if d.get(k) is not None}
                    for d in metrics_data
                },
                algorithm_names = [d["name"] for d in metrics_data],
                description     = f"Comparaison {len(metrics_data)} algorithmes — {prob_key}",
                minio_key       = minio_key,
                problem_id      = prob_id_raw,
            )
            log.info("[S5] ✓ %s/%s — 2 figures sauvegardées (%d algos)",
                     type_name, prob_key, len(metrics_data))
            type_ok += 1

        if type_ok > 0:
            stats["ok"] += type_ok
        else:
            stats["skip"] += 1

    log.info("[S5] Terminé — ok=%d skip=%d error=%d", stats["ok"], stats["skip"], stats["error"])
    return stats


def _group_by_problem(results: List[Dict]) -> Dict[str, List[Dict]]:
    """Regroupe les résultats par problem_key."""
    groups: Dict[str, List[Dict]] = {}
    for r in results:
        prob_key = r.get("problem_key") or r.get("problem_id", "unknown")
        if prob_key not in groups:
            groups[prob_key] = []
        groups[prob_key].append(r)
    return groups


def _extract_metrics(results: List[Dict]) -> List[Dict]:
    """
    Extrait les métriques universelles par algorithme.

    Métriques prioritaires (toujours disponibles) :
      - detection_rate  : taux de détection d'anomalies
      - correction_rate : taux de correction
      - exec_time       : temps d'exécution (s)

    Métriques optionnelles (uniquement si vérité terrain disponible) :
      - f1, precision, recall
    """
    best: Dict[str, Dict] = {}
    for r in results:
        algo_name = r.get("algorithm_name", "Algo")
        exec_time = r.get("execution_time_sec")
        metrics   = r.get("final_metrics_json") or r.get("metrics_json") or {}
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = {}

        detection_rate  = _safe_rate(metrics.get("detection_rate"))
        correction_rate = _safe_rate(metrics.get("correction_rate"))
        f1              = _safe_float(metrics.get("f1"))
        precision       = _safe_float(metrics.get("precision"))
        recall          = _safe_float(metrics.get("recall"))
        anomaly_count   = metrics.get("anomaly_count")

        # Inclure tout algorithme ayant au moins un résultat d'exécution
        if detection_rate is None and f1 is None and exec_time is None:
            continue

        # Tri par detection_rate puis f1
        score = (detection_rate or 0.0) + (f1 or 0.0) * 0.5
        current_score = best.get(algo_name, {}).get("_score", -1)
        if score >= current_score:
            best[algo_name] = {
                "name":            algo_name,
                "detection_rate":  detection_rate or 0.0,
                "correction_rate": correction_rate or 0.0,
                "f1":              f1,
                "precision":       precision,
                "recall":          recall,
                "anomaly_count":   anomaly_count,
                "exec_time":       round(float(exec_time), 1) if exec_time else None,
                "_score":          score,
            }
    # Trier par detection_rate desc, puis f1 desc
    return sorted(
        best.values(),
        key=lambda d: (d.get("detection_rate") or 0, d.get("f1") or 0),
        reverse=True,
    )


def _safe_rate(val) -> Optional[float]:
    """Valide un taux (0–1)."""
    if val is None:
        return None
    try:
        v = float(val)
        return round(v, 4) if 0 <= v <= 1 else None
    except (TypeError, ValueError):
        return None


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        v = float(val)
        return round(v, 4) if 0 <= v <= 1 else None
    except (TypeError, ValueError):
        return None


def _short_name(name: str, max_len: int = 26) -> str:
    import re
    name = re.sub(r"\s*\([^)]*\)", "", name).strip()
    if len(name) > max_len:
        name = name[:max_len - 1] + "…"
    return name


def _generate_performance_chart(
    metrics_data: List[Dict], prob_title: str, type_name: str, out_path: Path
) -> None:
    """Graphique performances pour UN problème — détection, correction, F1."""
    import matplotlib.pyplot as plt
    import numpy as np

    n      = len(metrics_data)
    labels = [_short_name(d["name"]) for d in metrics_data]
    det    = [d.get("detection_rate")  or 0.0 for d in metrics_data]
    corr   = [d.get("correction_rate") or 0.0 for d in metrics_data]
    f1s    = [d.get("f1") or 0.0 for d in metrics_data]
    has_f1 = any(d.get("f1") is not None for d in metrics_data)

    x     = np.arange(n)
    width = 0.25 if has_f1 else 0.32
    fig, ax = plt.subplots(figsize=(max(7, n * 2.4), 5))

    offset = -width if has_f1 else -width / 2
    b1 = ax.bar(x + offset,             det,  width, label="Taux de détection",  color="#1976D2", alpha=0.9, zorder=3)
    b2 = ax.bar(x + offset + width,     corr, width, label="Taux de correction", color="#388E3C", alpha=0.9, zorder=3)
    bars_all = [b1, b2]
    if has_f1:
        b3 = ax.bar(x + offset + width * 2, f1s, width, label="F1-Score", color="#F57C00", alpha=0.9, zorder=3)
        bars_all.append(b3)

    for bars in bars_all:
        for bar in bars:
            h = bar.get_height()
            if h > 0.005:
                ax.text(bar.get_x() + bar.get_width() / 2, h + 0.015,
                        f"{h:.0%}", ha="center", va="bottom", fontsize=8, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=18, ha="right", fontsize=9)
    ax.set_ylim(0, 1.28)
    ax.set_ylabel("Taux", fontsize=11)
    ax.set_xlabel("Algorithmes", fontsize=10)
    ax.set_title(f"{type_name}", fontsize=11, fontweight="bold", pad=10)
    ax.legend(fontsize=9, loc="upper right", framealpha=0.8)
    ax.grid(axis="y", linestyle="--", alpha=0.35, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)

    title_short = prob_title[:70] + "…" if len(prob_title) > 70 else prob_title
    fig.suptitle(f"Comparaison des performances — {title_short}", fontsize=12, fontweight="bold", y=1.01)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _generate_timing_chart(
    metrics_data: List[Dict], prob_title: str, out_path: Path
) -> None:
    """Graphique temps d'exécution pour UN problème."""
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np

    n      = len(metrics_data)
    labels = [_short_name(d["name"]) for d in metrics_data]
    times  = [d.get("exec_time") or 0.0 for d in metrics_data]

    x      = np.arange(n)
    min_t  = min(times) if times else 0
    colors = ["#2196F3" if t == min_t else "#90A4AE" for t in times]

    fig, ax = plt.subplots(figsize=(max(6, n * 1.8), 4))
    bars = ax.bar(x, times, color=colors, alpha=0.88, zorder=3)

    for bar, t in zip(bars, times):
        if t > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, t + 0.02,
                    f"{t:.2f}s", ha="center", va="bottom", fontsize=8, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=18, ha="right", fontsize=9)
    ax.set_ylabel("Secondes (s)", fontsize=11)
    ax.set_xlabel("Algorithmes", fontsize=10)
    ax.set_ylim(0, max(times) * 1.35 + 0.1 if times else 5)
    ax.grid(axis="y", linestyle="--", alpha=0.35, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(handles=[
        mpatches.Patch(color="#2196F3", label="Plus rapide"),
        mpatches.Patch(color="#90A4AE", label="Autres"),
    ], fontsize=9, loc="upper right")

    title_short = prob_title[:70] + "…" if len(prob_title) > 70 else prob_title
    fig.suptitle(f"Temps d'exécution — {title_short}", fontsize=12, fontweight="bold", y=1.01)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _generate_global_chart(metrics_data: List[Dict], type_name: str, out_path: Path) -> None:
    """
    Graphique global horizontal — métriques universelles pour TOUS les algorithmes.

    Métriques toujours affichées : taux de détection, taux de correction, temps d'exécution.
    Métriques optionnelles (si vérité terrain dispo) : F1-Score.
    """
    import matplotlib.pyplot as plt
    import numpy as np

    data   = metrics_data[:30]
    n      = len(data)
    labels = [_short_name(d["name"], max_len=30) for d in data]
    det    = [d.get("detection_rate")  or 0.0 for d in data]
    corr   = [d.get("correction_rate") or 0.0 for d in data]
    f1s    = [d.get("f1") for d in data]
    times  = [d.get("exec_time") or 0.0 for d in data]
    has_f1 = any(v is not None for v in f1s)
    f1s_plot = [(v or 0.0) for v in f1s]

    y      = np.arange(n)
    height = 0.22 if has_f1 else 0.3

    # Two panels: left = rates, right = execution time
    fig_h = max(6, n * 0.65)
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(13, fig_h),
                                   gridspec_kw={"width_ratios": [3, 1]})

    # Left panel: universal metrics
    b1 = ax.barh(y + height,  det,       height, label="Taux de détection",  color="#1976D2", alpha=0.9)
    b2 = ax.barh(y,            corr,      height, label="Taux de correction", color="#388E3C", alpha=0.9)
    if has_f1:
        b3 = ax.barh(y - height, f1s_plot, height, label="F1-Score",         color="#F57C00", alpha=0.9)
        bars_all = (b1, b2, b3)
    else:
        bars_all = (b1, b2)

    for bars in bars_all:
        for bar in bars:
            w = bar.get_width()
            if w > 0.005:
                ax.text(w + 0.008, bar.get_y() + bar.get_height() / 2,
                        f"{w:.0%}", va="center", fontsize=7.5, fontweight="bold")

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlim(0, 1.25)
    ax.set_xlabel("Taux", fontsize=11)
    ax.set_title(f"Comparaison des performances — {type_name}",
                 fontsize=12, fontweight="bold", pad=12)
    ax.legend(fontsize=9, loc="lower right")
    ax.grid(axis="x", linestyle="--", alpha=0.4)
    ax.spines[["top", "right"]].set_visible(False)
    ax.invert_yaxis()

    # Right panel: execution time (fastest = blue)
    max_t = max(times) if times else 1
    min_t = min(times) if times else 0
    colors = ["#5C6BC0" if t == min_t else "#90A4AE" for t in times]
    ax2.barh(labels[::-1], times[::-1], color=colors[::-1], alpha=0.85)
    for i, t in enumerate(times[::-1]):
        if t > 0:
            ax2.text(t + max_t * 0.02, i, f"{t:.1f}s", va="center", fontsize=8)
    ax2.set_xlabel("Temps (s)", fontsize=10)
    ax2.set_title("Temps\nd'exécution", fontsize=10, fontweight="bold")
    ax2.spines[["top", "right"]].set_visible(False)
    ax2.set_xlim(0, max_t * 1.35 + 1)
    ax2.invert_yaxis()

    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
