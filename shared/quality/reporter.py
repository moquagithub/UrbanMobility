"""
Génère des rapports de qualité à partir des résultats de scan.

Peut produire :
  - Un résumé console (texte)
  - Un rapport JSON (pour API / dashboard)
  - Un rapport HTML (pour lecture humaine)
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from shared.quality.checks import QualityIssue

log = logging.getLogger("quality.reporter")


# ── Statistiques ──────────────────────────────────────────────────────────────

def build_summary(issues: List[QualityIssue]) -> Dict:
    """Construit un dict de statistiques à partir d'une liste d'issues."""
    by_severity = defaultdict(int)
    by_type     = defaultdict(int)
    by_entity   = defaultdict(int)
    by_data_type = defaultdict(int)
    fixable = 0

    for iss in issues:
        by_severity[iss.severity]    += 1
        by_type[iss.issue_type]      += 1
        by_entity[iss.entity_type]   += 1
        if iss.data_type_id:
            by_data_type[iss.data_type_id] += 1
        if iss.auto_fixable:
            fixable += 1

    return {
        "total":       len(issues),
        "auto_fixable": fixable,
        "by_severity": dict(by_severity),
        "by_issue_type": dict(by_type),
        "by_entity_type": dict(by_entity),
        "by_data_type": dict(by_data_type),
    }


def compute_quality_score(issues: List[QualityIssue]) -> int:
    """Retourne un score 0-100 (100 = aucun problème)."""
    if not issues:
        return 100
    weights = {"critical": 25, "high": 10, "medium": 4, "low": 1}
    total_penalty = sum(weights.get(i.severity, 1) for i in issues)
    # score décroît rapidement au-delà de 10 critical
    score = max(0, 100 - total_penalty)
    return score


# ── Rapport console ───────────────────────────────────────────────────────────

def print_report(
    issues: List[QualityIssue],
    scan_id: Optional[str] = None,
    show_all: bool = False,
    max_per_type: int = 5,
) -> None:
    """Affiche un rapport de qualité dans la console."""
    summary = build_summary(issues)
    score = compute_quality_score(issues)
    bar = "█" * (score // 5) + "░" * (20 - score // 5)

    print()
    print("=" * 65)
    print(f"  RAPPORT QUALITÉ  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    if scan_id:
        print(f"  scan_id: {scan_id}")
    print("=" * 65)
    print(f"  Score global : [{bar}] {score}/100")
    print(f"  Total issues : {summary['total']}  (fixable auto : {summary['auto_fixable']})")
    print()

    COLORS = {
        "critical": "\033[91m",  # rouge
        "high":     "\033[93m",  # jaune
        "medium":   "\033[96m",  # cyan
        "low":      "\033[37m",  # gris
    }
    RESET = "\033[0m"

    for sev in ("critical", "high", "medium", "low"):
        count = summary["by_severity"].get(sev, 0)
        if not count:
            continue
        c = COLORS.get(sev, "")
        print(f"  {c}{'■' * min(count, 20)} {sev.upper()} : {count}{RESET}")
    print()

    # Détail par type d'issue
    grouped: Dict[str, List[QualityIssue]] = defaultdict(list)
    for iss in issues:
        grouped[iss.issue_type].append(iss)

    for issue_type, group in sorted(grouped.items(), key=lambda kv: (
        {"critical": 0, "high": 1, "medium": 2, "low": 3}[kv[1][0].severity], kv[0]
    )):
        sev = group[0].severity
        c = COLORS.get(sev, "")
        print(f"  {c}[{sev.upper()}] {issue_type} — {len(group)} issue(s){RESET}")
        display = group if show_all else group[:max_per_type]
        for iss in display:
            label = iss.entity_key or f"id={iss.entity_id}"
            fix_tag = " [fixable]" if iss.auto_fixable else ""
            print(f"         • {label}{fix_tag}: {iss.description[:90]}")
        if not show_all and len(group) > max_per_type:
            print(f"         … et {len(group) - max_per_type} autre(s)")
        print()

    print("=" * 65)


# ── Rapport JSON ──────────────────────────────────────────────────────────────

def to_json_report(
    issues: List[QualityIssue],
    scan_id: Optional[str] = None,
    scan_duration_sec: Optional[float] = None,
) -> str:
    """Sérialise le rapport en JSON (pour API / stockage)."""
    summary = build_summary(issues)
    return json.dumps({
        "scan_id":    scan_id,
        "generated_at": datetime.now().isoformat(),
        "duration_sec": scan_duration_sec,
        "score": compute_quality_score(issues),
        "summary": summary,
        "issues": [i.as_dict() for i in issues],
    }, ensure_ascii=False, default=str)


# ── Rapport HTML ──────────────────────────────────────────────────────────────

_SEV_BADGE = {
    "critical": "background:#dc2626;color:#fff",
    "high":     "background:#f59e0b;color:#fff",
    "medium":   "background:#3b82f6;color:#fff",
    "low":      "background:#6b7280;color:#fff",
}

def to_html_report(issues: List[QualityIssue], scan_id: Optional[str] = None) -> str:
    """Génère un rapport HTML autonome."""
    summary = build_summary(issues)
    score   = compute_quality_score(issues)
    now     = datetime.now().strftime("%Y-%m-%d %H:%M")

    rows = []
    for iss in sorted(issues, key=lambda i: (
        {"critical": 0, "high": 1, "medium": 2, "low": 3}[i.severity], i.issue_type
    )):
        sty = _SEV_BADGE.get(iss.severity, "background:#666;color:#fff")
        fix = "✔" if iss.auto_fixable else ""
        rows.append(
            f"<tr>"
            f"<td><span style='padding:2px 7px;border-radius:4px;font-size:.8em;{sty}'>{iss.severity}</span></td>"
            f"<td>{iss.entity_type}</td>"
            f"<td>{iss.entity_key or iss.entity_id or ''}</td>"
            f"<td>{iss.data_type_id or ''}</td>"
            f"<td>{iss.issue_type}</td>"
            f"<td>{iss.description}</td>"
            f"<td style='text-align:center'>{fix}</td>"
            f"</tr>"
        )

    score_color = "#16a34a" if score >= 80 else "#f59e0b" if score >= 50 else "#dc2626"
    html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<title>Rapport Qualité — {now}</title>
<style>
body {{font-family:sans-serif;margin:24px;background:#f8fafc;color:#1e293b}}
h1 {{color:#1e293b;font-size:1.4em}}
.score {{display:inline-block;font-size:2.5em;font-weight:bold;color:{score_color}}}
.kpi {{display:flex;gap:24px;margin:16px 0}}
.kpi-card {{background:#fff;border-radius:8px;padding:12px 20px;box-shadow:0 1px 4px #0001}}
.kpi-card h3 {{margin:0;font-size:.85em;color:#64748b;text-transform:uppercase}}
.kpi-card p {{margin:4px 0 0;font-size:1.6em;font-weight:bold}}
table {{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 4px #0001;margin-top:16px}}
th {{background:#1e293b;color:#fff;padding:8px 12px;text-align:left;font-size:.85em}}
td {{padding:7px 12px;border-bottom:1px solid #e2e8f0;font-size:.85em;vertical-align:top}}
tr:last-child td {{border-bottom:none}}
</style></head><body>
<h1>Rapport Qualité Pipeline</h1>
<p style="color:#64748b;font-size:.9em">{now}{' — scan ' + scan_id if scan_id else ''}</p>

<div class="kpi">
  <div class="kpi-card"><h3>Score</h3><p><span class="score">{score}</span>/100</p></div>
  <div class="kpi-card"><h3>Issues totales</h3><p>{summary['total']}</p></div>
  <div class="kpi-card"><h3>Critical</h3><p style="color:#dc2626">{summary['by_severity'].get('critical',0)}</p></div>
  <div class="kpi-card"><h3>High</h3><p style="color:#f59e0b">{summary['by_severity'].get('high',0)}</p></div>
  <div class="kpi-card"><h3>Medium</h3><p style="color:#3b82f6">{summary['by_severity'].get('medium',0)}</p></div>
  <div class="kpi-card"><h3>Fixable auto</h3><p style="color:#16a34a">{summary['auto_fixable']}</p></div>
</div>

<table>
<thead><tr>
  <th>Sévérité</th><th>Entité</th><th>Clé</th><th>Type de données</th>
  <th>Issue</th><th>Description</th><th>Fix auto</th>
</tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
</body></html>"""
    return html


# ── Sauvegarde du rapport en base ─────────────────────────────────────────────

def save_issues_to_db(
    issues: List[QualityIssue],
    scan_id: str,
    conn=None,
) -> int:
    """Insère les issues dans la table quality_issues. Retourne le nombre inséré."""
    from shared.db.connection import get_connection
    close_after = conn is None
    if conn is None:
        conn = get_connection()
    if not conn:
        return 0

    inserted = 0
    try:
        with conn.cursor() as cur:
            for iss in issues:
                cur.execute("""
                    INSERT INTO quality_issues
                        (entity_type, entity_id, entity_key, data_type_id,
                         issue_type, severity, description, auto_fixable,
                         fix_attempted, fix_result, scan_run_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, 'pending', %s)
                """, (
                    iss.entity_type,
                    iss.entity_id,
                    iss.entity_key,
                    iss.data_type_id,
                    iss.issue_type,
                    iss.severity,
                    iss.description,
                    1 if iss.auto_fixable else 0,
                    scan_id,
                ))
                inserted += 1
        conn.commit()
    finally:
        if close_after:
            conn.close()

    return inserted


def update_fix_result(
    issue_db_id: int,
    success: bool,
    detail: str,
    conn=None,
    skipped: bool = False,
) -> None:
    """
    Met à jour le résultat d'une réparation dans quality_issues.

    skipped=True : la réparation n'a PAS pu être tentée (aucun provider LLM n'a
    répondu, quota épuisé…). On l'enregistre en 'skipped' et non en 'failed', car
    _repair_failure_counts() ne compte que les 'failed' pour la soupape
    anti-acharnement — compter une indisponibilité d'infrastructure comme un échec
    de réparation finissait par « garer » définitivement des issues parfaitement
    réparables (voir MAX_REPAIR_FAILURES dans monitor.py).
    """
    from shared.db.connection import get_connection
    from datetime import datetime as dt
    close_after = conn is None
    if conn is None:
        conn = get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            result = "skipped" if skipped else ("success" if success else "failed")
            cur.execute("""
                UPDATE quality_issues
                SET fix_attempted=1, fix_result=%s, fix_detail=%s,
                    resolved_at=%s
                WHERE id=%s
            """, (result, detail[:2000], dt.now() if success else None, issue_db_id))
        conn.commit()
    finally:
        if close_after:
            conn.close()
