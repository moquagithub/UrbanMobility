"""shared.quality — Système de surveillance et de réparation automatique de la qualité."""

from shared.quality.checks import QualityIssue, run_all_checks
from shared.quality.monitor import QualityMonitor
from shared.quality.repair import repair_issue
from shared.quality.reporter import (
    build_summary,
    compute_quality_score,
    print_report,
    to_json_report,
    to_html_report,
    save_issues_to_db,
)

__all__ = [
    "QualityIssue",
    "QualityMonitor",
    "run_all_checks",
    "repair_issue",
    "build_summary",
    "compute_quality_score",
    "print_report",
    "to_json_report",
    "to_html_report",
    "save_issues_to_db",
]
