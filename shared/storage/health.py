"""
shared.storage.health — Vérification de santé de l'infrastructure (MySQL + MinIO).

Utilisé en pré-vol avant chaque lancement de pipeline.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List

log = logging.getLogger(__name__)


@dataclass
class ComponentHealth:
    name: str
    ok: bool
    latency_ms: float = 0.0
    details: Dict = field(default_factory=dict)
    error: str = ""


@dataclass
class InfrastructureHealth:
    all_ok: bool
    components: List[ComponentHealth] = field(default_factory=list)
    checked_at: float = field(default_factory=time.time)

    def report(self) -> str:
        lines = [f"{'✓' if self.all_ok else '✗'} Infrastructure Health Check"]
        for c in self.components:
            icon = "✓" if c.ok else "✗"
            latency = f"{c.latency_ms:.0f}ms" if c.latency_ms > 0 else ""
            detail = ""
            if c.details:
                detail = " | " + ", ".join(f"{k}={v}" for k, v in c.details.items())
            extra = f" — {c.error}" if c.error else f" {latency}{detail}"
            lines.append(f"  {icon} {c.name:12s}{extra}")
        return "\n".join(lines)


def check_infrastructure() -> InfrastructureHealth:
    """Vérifie MySQL et MinIO. Retourne un rapport complet."""
    components: List[ComponentHealth] = []

    # ── MySQL ────────────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    try:
        from shared.db.connection import get_connection
        conn = get_connection()
        if conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM data_types")
                n_types = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM algorithms")
                n_algos = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM notebooks")
                n_nbs = cur.fetchone()[0]
            conn.close()
            latency = round((time.perf_counter() - t0) * 1000, 1)
            components.append(ComponentHealth(
                name="MySQL", ok=True, latency_ms=latency,
                details={"types": n_types, "algos": n_algos, "notebooks": n_nbs},
            ))
        else:
            components.append(ComponentHealth(
                name="MySQL", ok=False, error="Connexion impossible"))
    except Exception as exc:
        components.append(ComponentHealth(
            name="MySQL", ok=False, error=str(exc)[:200]))

    # ── MinIO ────────────────────────────────────────────────────────────────
    try:
        from shared.storage.client import get_storage
        store = get_storage()
        status = store.health_check()
        components.append(ComponentHealth(
            name="MinIO", ok=status.ok, latency_ms=status.latency_ms,
            details={"buckets": len(status.buckets)},
            error=status.error,
        ))
    except Exception as exc:
        components.append(ComponentHealth(
            name="MinIO", ok=False, error=str(exc)[:200]))

    all_ok = all(c.ok for c in components)
    health = InfrastructureHealth(all_ok=all_ok, components=components)
    log.info("[HEALTH]\n%s", health.report())
    return health
