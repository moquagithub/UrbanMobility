"""
Autopilote : supervision programmée des cycles qualité et pipeline.

Conçu pour tourner sans surveillance humaine (une nuit, un week-end) :
on le lance avec une condition d'arrêt, il travaille, il s'arrête tout seul et
laisse un rapport.

    python3 -m shared.autopilot --until 07:00 --type traces_gps

Voir shared/autopilot/scheduler.py pour la logique et __main__.py pour la CLI.
"""
from shared.autopilot.scheduler import Autopilot, StopPlan

__all__ = ["Autopilot", "StopPlan"]
