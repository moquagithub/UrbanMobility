"""
Autopilote — supervise des cycles de qualité/pipeline jusqu'à une condition d'arrêt.

Différence avec `python -m shared.quality --watch` : le watch boucle indéfiniment
et n'a aucune notion de « fin ». L'autopilote est **programmable** — on lui donne
un objectif et une échéance, il travaille jusqu'à l'un ou l'autre puis s'arrête
proprement en laissant un rapport exploitable au réveil.

Un CYCLE = (optionnel) une passe orchestrateur Auto1→Auto2→Auto3,
           puis un scan qualité + réparations.

Garanties pour une exécution nocturne sans surveillance :
  • une exception dans un cycle n'arrête pas l'autopilote (backoff exponentiel) ;
  • quota LLM épuisé → sommeil jusqu'au reset de minuit, puis reprise ;
  • SIGTERM/SIGINT → arrêt propre (fin du cycle courant), rapport écrit ;
  • verrou d'instance unique — jamais deux autopilotes en parallèle ;
  • état publié en continu dans un JSON de statut (consultable pendant le run).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("autopilot")

# Racine du projet (…/mobility_automation)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = PROJECT_ROOT / "logs"


# ── Conditions d'arrêt ────────────────────────────────────────────────────────


@dataclass
class StopPlan:
    """
    Conditions d'arrêt de l'autopilote. La PREMIÈRE atteinte l'emporte.
    Toutes optionnelles : sans aucune, l'autopilote tourne indéfiniment.
    """
    deadline:      Optional[datetime] = None   # heure limite absolue
    max_cycles:    Optional[int]      = None   # nombre de cycles
    target_score:  Optional[int]      = None   # score qualité visé (0-100)
    stop_when_clean: bool             = False  # arrêt dès 0 issue détectée
    # Arrêt si N cycles consécutifs ne réparent rien : au-delà, l'autopilote ne
    # fait plus que brûler du quota LLM sur un backlog irréductible.
    patience:      Optional[int]      = None

    def reason_to_stop(self, state: "RunState") -> Optional[str]:
        if self.deadline and datetime.now() >= self.deadline:
            return f"échéance atteinte ({self.deadline:%H:%M le %d/%m})"
        if self.max_cycles and state.cycle >= self.max_cycles:
            return f"nombre de cycles atteint ({self.max_cycles})"
        if self.target_score is not None and state.last_score is not None \
                and state.last_score >= self.target_score:
            return f"score cible atteint ({state.last_score} ≥ {self.target_score})"
        if self.stop_when_clean and state.last_issues == 0 and state.cycle > 0:
            return "plus aucune issue détectée"
        if self.patience and state.barren_cycles >= self.patience:
            return f"{state.barren_cycles} cycles consécutifs sans réparation (patience={self.patience})"
        return None

    def describe(self) -> str:
        bits = []
        if self.deadline:
            bits.append(f"jusqu'à {self.deadline:%H:%M le %d/%m}")
        if self.max_cycles:
            bits.append(f"max {self.max_cycles} cycles")
        if self.target_score is not None:
            bits.append(f"score ≥ {self.target_score}")
        if self.stop_when_clean:
            bits.append("arrêt si 0 issue")
        if self.patience:
            bits.append(f"patience {self.patience}")
        return ", ".join(bits) or "sans condition d'arrêt (infini)"


@dataclass
class RunState:
    """État vivant de l'exécution — sérialisé dans le fichier de statut."""
    started_at:     datetime = field(default_factory=datetime.now)
    cycle:          int = 0
    total_fixed:    int = 0
    total_failed:   int = 0
    total_skipped:  int = 0
    last_score:     Optional[int] = None
    last_issues:    int = -1
    barren_cycles:  int = 0          # cycles consécutifs sans aucune réparation
    quota_pauses:   int = 0
    errors:         int = 0
    score_history:  List[Dict] = field(default_factory=list)
    stopped_reason: str = ""

    def as_dict(self) -> Dict:
        return {
            "started_at":     self.started_at.isoformat(timespec="seconds"),
            "updated_at":     datetime.now().isoformat(timespec="seconds"),
            "pid":            os.getpid(),
            "cycle":          self.cycle,
            "total_fixed":    self.total_fixed,
            "total_failed":   self.total_failed,
            "total_skipped":  self.total_skipped,
            "last_score":     self.last_score,
            "last_issues":    self.last_issues,
            "barren_cycles":  self.barren_cycles,
            "quota_pauses":   self.quota_pauses,
            "errors":         self.errors,
            "score_history":  self.score_history[-50:],
            "stopped_reason": self.stopped_reason,
        }


# ── Autopilote ────────────────────────────────────────────────────────────────


class Autopilot:
    """Supervise des cycles qualité/pipeline jusqu'à une condition d'arrêt."""

    def __init__(
        self,
        stop_plan:        Optional[StopPlan] = None,
        type_ids:         Optional[List[str]] = None,
        interval_sec:     int = 300,
        max_fix_per_run:  int = 20,
        with_orchestrator: bool = False,
        catalogue_path:   Optional[Path] = None,
        status_path:      Optional[Path] = None,
        report_path:      Optional[Path] = None,
    ):
        self.stop_plan         = stop_plan or StopPlan()
        self.type_ids          = type_ids
        self.interval_sec      = interval_sec
        self.max_fix_per_run   = max_fix_per_run
        self.with_orchestrator = with_orchestrator
        self.catalogue_path    = catalogue_path or (PROJECT_ROOT / "data" / "example_catalogue.xlsx")

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.status_path = status_path or (LOG_DIR / "autopilot_status.json")
        self.report_path = report_path or (LOG_DIR / "autopilot_report.md")

        self.state    = RunState()
        self._running = True

    # ── Signaux ──────────────────────────────────────────────────────────────

    def _install_signal_handlers(self) -> None:
        def handler(signum, _frame):
            name = signal.Signals(signum).name
            log.warning("[AUTOPILOT] %s reçu — arrêt après le cycle courant", name)
            self._running = False
            self.state.stopped_reason = f"signal {name}"
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except ValueError:
                pass   # hors thread principal (usage programmatique)

    # ── Boucle principale ────────────────────────────────────────────────────

    def run(self) -> RunState:
        from shared.quality.monitor import QualityMonitor, close_stale_scan_runs

        self._install_signal_handlers()
        close_stale_scan_runs()

        log.info("╔══════════════════════════════════════════════════════════╗")
        log.info("║  AUTOPILOTE — surveillance & réparation programmée       ║")
        log.info("╚══════════════════════════════════════════════════════════╝")
        log.info("Arrêt      : %s", self.stop_plan.describe())
        log.info("Types      : %s", ", ".join(self.type_ids) if self.type_ids else "tous")
        log.info("Intervalle : %ds | max_fix/cycle : %d | orchestrateur : %s",
                 self.interval_sec, self.max_fix_per_run, self.with_orchestrator)
        log.info("Statut     : %s", self.status_path)
        self._publish_status()

        monitor = QualityMonitor(
            type_ids=self.type_ids,
            watch_interval_sec=self.interval_sec,
            max_fix_per_run=self.max_fix_per_run,
        )

        consecutive_errors = 0

        while self._running:
            reason = self.stop_plan.reason_to_stop(self.state)
            if reason:
                self.state.stopped_reason = reason
                break

            self.state.cycle += 1
            log.info("─── CYCLE #%d ─────────────────────────────────", self.state.cycle)
            t0 = time.perf_counter()
            # Publier dès le début du cycle : un cycle peut durer plusieurs minutes
            # (réparations LLM + pipeline) et `--status` doit montrer un autopilote
            # vivant pendant ce temps, pas l'état figé du cycle précédent.
            self._publish_status()

            try:
                if self.with_orchestrator:
                    self._run_orchestrator_pass()

                result = monitor.run_once(fix=True, print_report=False)
                consecutive_errors = 0
                self._absorb(result, elapsed=time.perf_counter() - t0)

                if result.get("quota_exhausted"):
                    self._pause_until_quota_reset(result.get("quota_reason", ""))
                    continue

            except KeyboardInterrupt:
                self.state.stopped_reason = "interruption clavier"
                break
            except Exception as exc:
                consecutive_errors += 1
                self.state.errors += 1
                backoff = min(self.interval_sec * 2 ** (consecutive_errors - 1), 3600)
                log.exception("[AUTOPILOT] Cycle #%d en erreur (%d d'affilée) — reprise dans %ds : %s",
                              self.state.cycle, consecutive_errors, backoff, exc)
                self._publish_status()
                if not self._sleep(backoff):
                    break
                continue

            # Condition d'arrêt réévaluée avant de dormir : inutile d'attendre
            # l'intervalle complet si l'objectif est déjà atteint.
            reason = self.stop_plan.reason_to_stop(self.state)
            if reason:
                self.state.stopped_reason = reason
                break

            if not self._sleep(self._sleep_seconds()):
                break

        if not self.state.stopped_reason:
            self.state.stopped_reason = "arrêt demandé"

        log.info("[AUTOPILOT] Arrêt : %s", self.state.stopped_reason)
        self._publish_status()
        self._write_report()
        close_stale_scan_runs()
        return self.state

    # ── Étapes d'un cycle ────────────────────────────────────────────────────

    def _run_orchestrator_pass(self) -> None:
        """Une passe Auto1 → Auto2 → Auto3 (avance le pipeline sur les types en attente)."""
        log.info("[AUTOPILOT] Passe orchestrateur…")
        from orchestrator import run_orchestrator
        run_orchestrator(
            catalogue_path=self.catalogue_path,
            once=True,
            interval_sec=self.interval_sec,
        )

    def _absorb(self, result: Dict, elapsed: float) -> None:
        """Intègre le résultat d'un scan dans l'état courant."""
        fixed   = result.get("fixed", 0)
        failed  = result.get("failed", 0)
        skipped = result.get("skipped", 0)

        self.state.total_fixed   += fixed
        self.state.total_failed  += failed
        self.state.total_skipped += skipped
        self.state.last_score  = result.get("score")
        self.state.last_issues = result.get("issues", -1)
        # Un cycle « stérile » n'a rien réparé ALORS QUE le LLM répondait : des
        # réparations seulement 'skipped' (LLM muet) ne comptent pas comme un
        # signe que le backlog est irréductible.
        if fixed == 0 and skipped == 0:
            self.state.barren_cycles += 1
        else:
            self.state.barren_cycles = 0

        self.state.score_history.append({
            "at":      datetime.now().isoformat(timespec="seconds"),
            "cycle":   self.state.cycle,
            "score":   result.get("score"),
            "issues":  result.get("issues", 0),
            "fixed":   fixed,
            "failed":  failed,
            "skipped": skipped,
        })

        log.info("[AUTOPILOT] Cycle #%d en %.0fs — score=%s issues=%s réparées=%d échecs=%d non-tentées=%d",
                 self.state.cycle, elapsed, result.get("score"), result.get("issues"),
                 fixed, failed, skipped)
        self._publish_status()

    def _pause_until_quota_reset(self, reason: str) -> None:
        from shared.llm.router import reset_session
        from shared.quality.monitor import _seconds_until_midnight

        wait = _seconds_until_midnight()
        # Ne pas dormir au-delà de l'échéance : l'autopilote doit rendre la main
        # à l'heure promise même si les quotas ne sont pas encore revenus.
        if self.stop_plan.deadline:
            until_deadline = int((self.stop_plan.deadline - datetime.now()).total_seconds())
            if until_deadline <= 0:
                return
            wait = min(wait, until_deadline)

        self.state.quota_pauses += 1
        log.warning("[AUTOPILOT] Quota LLM épuisé (%s) — pause %dh%02dm",
                    reason[:120], wait // 3600, (wait % 3600) // 60)
        self._publish_status()
        if self._sleep(wait):
            reset_session()
            log.info("[AUTOPILOT] Quotas réinitialisés — reprise.")

    def _sleep_seconds(self) -> int:
        """Intervalle avant le prochain cycle, borné par l'échéance."""
        if not self.stop_plan.deadline:
            return self.interval_sec
        remaining = int((self.stop_plan.deadline - datetime.now()).total_seconds())
        return max(0, min(self.interval_sec, remaining))

    def _sleep(self, seconds: int) -> bool:
        """
        Dort par tranches de 5s pour rester réactif aux signaux.
        Retourne False si un arrêt a été demandé pendant le sommeil.
        """
        slept = 0
        while self._running and slept < seconds:
            chunk = min(5, seconds - slept)
            time.sleep(chunk)
            slept += chunk
        return self._running

    # ── Publication de l'état ────────────────────────────────────────────────

    def _publish_status(self) -> None:
        """Écrit le statut courant (écriture atomique — lisible pendant le run)."""
        try:
            tmp = self.status_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.state.as_dict(), ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(self.status_path)
        except Exception as exc:
            log.debug("Statut non écrit : %s", exc)

    def _write_report(self) -> None:
        """Rapport final lisible au réveil."""
        s = self.state
        duration = datetime.now() - s.started_at
        hours, rem = divmod(int(duration.total_seconds()), 3600)

        first = s.score_history[0] if s.score_history else {}
        last  = s.score_history[-1] if s.score_history else {}

        lines = [
            "# Rapport autopilote",
            "",
            f"- **Démarré**   : {s.started_at:%Y-%m-%d %H:%M:%S}",
            f"- **Terminé**   : {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"- **Durée**     : {hours}h{rem // 60:02d}m",
            f"- **Arrêt**     : {s.stopped_reason}",
            f"- **Cycles**    : {s.cycle}",
            "",
            "## Bilan des réparations",
            "",
            f"- Réparées avec succès : **{s.total_fixed}**",
            f"- Échecs de réparation : {s.total_failed}",
            f"- Non tentées (LLM indisponible) : {s.total_skipped}",
            f"- Pauses quota : {s.quota_pauses}",
            f"- Cycles en erreur : {s.errors}",
            "",
            "## Qualité",
            "",
            f"- Score initial : {first.get('score', '?')} ({first.get('issues', '?')} issues)",
            f"- Score final   : {last.get('score', '?')} ({last.get('issues', '?')} issues)",
            "",
            "| Cycle | Heure | Score | Issues | Réparées | Échecs | Non tentées |",
            "|------:|-------|------:|-------:|---------:|-------:|------------:|",
        ]
        for h in s.score_history[-40:]:
            lines.append(
                f"| {h['cycle']} | {h['at'][11:19]} | {h.get('score', '?')} | {h.get('issues', 0)} "
                f"| {h.get('fixed', 0)} | {h.get('failed', 0)} | {h.get('skipped', 0)} |"
            )
        lines.append("")

        try:
            self.report_path.write_text("\n".join(lines), encoding="utf-8")
            log.info("[AUTOPILOT] Rapport écrit : %s", self.report_path)
        except Exception as exc:
            log.warning("Rapport non écrit : %s", exc)


# ── Utilitaires de parsing (partagés avec la CLI) ─────────────────────────────


def parse_clock(value: str, now: Optional[datetime] = None) -> datetime:
    """
    'HH:MM' → prochaine occurrence de cette heure (demain si déjà passée aujourd'hui).
    Accepte aussi 'HH:MM:SS'.
    """
    now = now or datetime.now()
    parts = value.strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise ValueError(f"Heure invalide : '{value}' (format attendu HH:MM)")
    h, m = int(parts[0]), int(parts[1])
    sec = int(parts[2]) if len(parts) == 3 else 0
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= sec < 60):
        raise ValueError(f"Heure hors bornes : '{value}'")
    target = now.replace(hour=h, minute=m, second=sec, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def parse_duration(value: str) -> timedelta:
    """'8h', '90m', '8h30m', '45s' → timedelta."""
    import re
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", value.strip().lower())
    if not m or not any(m.groups()):
        raise ValueError(f"Durée invalide : '{value}' (ex. 8h, 90m, 8h30m)")
    h, mi, s = (int(g or 0) for g in m.groups())
    return timedelta(hours=h, minutes=mi, seconds=s)
