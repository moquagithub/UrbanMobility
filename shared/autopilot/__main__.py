"""
CLI de l'autopilote — surveillance & réparation programmée.

Exemples :
  # Travailler toute la nuit, s'arrêter à 7h00
  python3 -m shared.autopilot --until 07:00 --type traces_gps

  # Travailler 8 heures maximum, s'arrêter avant si le score atteint 95
  python3 -m shared.autopilot --for 8h --target-score 95 --type traces_gps

  # Nuit complète, pipeline inclus, arrêt si 3 cycles ne réparent plus rien
  python3 -m shared.autopilot --until 07:00 --with-orchestrator --patience 3

  # Voir l'avancement d'un autopilote en cours (depuis un autre terminal)
  python3 -m shared.autopilot --status

  # Programmer un lancement automatique chaque nuit à 23h00 (systemd --user)
  python3 -m shared.autopilot --install-systemd --at 23:00 --until 07:00 --type traces_gps

  # Idem via cron
  python3 -m shared.autopilot --install-cron --at 23:00 --until 07:00 --type traces_gps
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from shared.autopilot.scheduler import (
    LOG_DIR, PROJECT_ROOT, Autopilot, StopPlan, parse_clock, parse_duration,
)

SERVICE_NAME = "mobility-autopilot"


# ── Journalisation ────────────────────────────────────────────────────────────


def _setup_logging(verbose: bool, log_file: Path | None) -> None:
    """Console + fichier tournant (5 Mo x 5) : une nuit de logs ne sature pas le disque."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        from logging.handlers import RotatingFileHandler
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=5_000_000,
                                            backupCount=5, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        handlers=handlers,
        force=True,
    )
    # Le client HTTP d'OpenAI logue une ligne INFO par appel : trop bavard sur une nuit.
    logging.getLogger("httpx").setLevel(logging.WARNING)


# ── Sous-commandes utilitaires ────────────────────────────────────────────────


def _show_status(status_path: Path) -> int:
    from shared.runtime_lock import holder_pid

    if not status_path.exists():
        print(f"Aucun statut trouvé ({status_path}).")
        return 1
    data = json.loads(status_path.read_text(encoding="utf-8"))
    pid = holder_pid("autopilot")
    running = pid is not None

    print(f"\n{'─' * 58}")
    print(f"  AUTOPILOTE — {'EN COURS' if running else 'ARRÊTÉ'}"
          + (f" (PID {pid})" if running else ""))
    print(f"{'─' * 58}")
    print(f"  Démarré      : {data.get('started_at')}")
    print(f"  Mis à jour   : {data.get('updated_at')}")
    print(f"  Cycle        : {data.get('cycle')}")
    print(f"  Score        : {data.get('last_score')}  ({data.get('last_issues')} issues)")
    print(f"  Réparées     : {data.get('total_fixed')}")
    print(f"  Échecs       : {data.get('total_failed')}")
    print(f"  Non tentées  : {data.get('total_skipped')}")
    print(f"  Pauses quota : {data.get('quota_pauses')} | erreurs : {data.get('errors')}")
    if data.get("stopped_reason"):
        print(f"  Arrêt        : {data['stopped_reason']}")
    hist = data.get("score_history", [])[-10:]
    if hist:
        print(f"\n  Derniers cycles :")
        for h in hist:
            print(f"    #{h['cycle']:<4} {h['at'][11:19]}  score={str(h.get('score')):>4}"
                  f"  issues={h.get('issues', 0):>3}  réparées={h.get('fixed', 0)}")
    print(f"{'─' * 58}\n")
    return 0


def _autopilot_argv(args: argparse.Namespace) -> list[str]:
    """Reconstruit les arguments d'exécution pour une unité systemd/cron."""
    argv = ["-m", "shared.autopilot"]
    if args.until:
        argv += ["--until", args.until]
    if args.run_for:
        argv += ["--for", args.run_for]
    if args.type_ids:
        argv += ["--type", *args.type_ids]
    if args.target_score is not None:
        argv += ["--target-score", str(args.target_score)]
    if args.patience:
        argv += ["--patience", str(args.patience)]
    if args.max_cycles:
        argv += ["--max-cycles", str(args.max_cycles)]
    if args.with_orchestrator:
        argv += ["--with-orchestrator"]
    if args.interval != 300:
        argv += ["--interval", str(args.interval)]
    if args.max_fix != 20:
        argv += ["--max-fix", str(args.max_fix)]
    return argv


def _python_exe() -> str:
    """Interpréteur du venv du projet — `python3` nu n'a pas les dépendances."""
    venv = PROJECT_ROOT / ".venv" / "bin" / "python3"
    return str(venv if venv.exists() else sys.executable)


def _install_systemd(args: argparse.Namespace) -> int:
    """Installe un service + timer systemd --user déclenché chaque jour à --at."""
    if not args.at:
        print("--install-systemd requiert --at HH:MM (heure de démarrage)", file=sys.stderr)
        return 2

    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    argv = " ".join(f"'{a}'" if " " in a else a for a in _autopilot_argv(args))

    service = f"""[Unit]
Description=Mobility automation — autopilote qualité
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory={PROJECT_ROOT}
Environment=PYTHONUNBUFFERED=1
ExecStart={_python_exe()} {argv}
# Pas de Restart= : l'autopilote gère lui-même ses erreurs et doit pouvoir
# s'arrêter volontairement (échéance atteinte) sans que systemd le relance.
TimeoutStopSec=300

[Install]
WantedBy=default.target
"""
    timer = f"""[Unit]
Description=Mobility automation — démarrage quotidien de l'autopilote à {args.at}

[Timer]
OnCalendar=*-*-* {args.at}:00
Persistent=true
Unit={SERVICE_NAME}.service

[Install]
WantedBy=timers.target
"""
    (unit_dir / f"{SERVICE_NAME}.service").write_text(service, encoding="utf-8")
    (unit_dir / f"{SERVICE_NAME}.timer").write_text(timer, encoding="utf-8")

    for cmd in (["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", f"{SERVICE_NAME}.timer"]):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"⚠  {' '.join(cmd)} a échoué : {r.stderr.strip()}", file=sys.stderr)
            print(f"   Unités écrites dans {unit_dir} — activez-les manuellement.", file=sys.stderr)
            return 1

    print(f"✓ Timer systemd installé : démarrage chaque jour à {args.at}")
    print(f"  Unités  : {unit_dir}/{SERVICE_NAME}.{{service,timer}}")
    print(f"  Commande: {_python_exe()} {argv}")
    print(f"  Vérifier: systemctl --user list-timers {SERVICE_NAME}.timer")
    print(f"  Logs    : journalctl --user -u {SERVICE_NAME} -f")
    print(f"  Retirer : python3 -m shared.autopilot --uninstall-schedule")
    print("\n  NB : pour que le timer se déclenche même sans session ouverte,")
    print(f"       activez le linger : sudo loginctl enable-linger {os.getenv('USER', '')}")
    return 0


def _install_cron(args: argparse.Namespace) -> int:
    """Ajoute une entrée crontab quotidienne à --at."""
    if not args.at:
        print("--install-cron requiert --at HH:MM (heure de démarrage)", file=sys.stderr)
        return 2
    h, m = args.at.split(":")[:2]
    argv = " ".join(_autopilot_argv(args))
    log_file = LOG_DIR / "autopilot_cron.log"
    marker = "# mobility-autopilot"
    line = (f"{int(m)} {int(h)} * * * cd '{PROJECT_ROOT}' && "
            f"{_python_exe()} {argv} >> '{log_file}' 2>&1  {marker}")

    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    kept = [l for l in current.splitlines() if marker not in l]
    new = "\n".join([*kept, line]) + "\n"

    r = subprocess.run(["crontab", "-"], input=new, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"⚠  Écriture crontab échouée : {r.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"✓ Entrée cron installée (chaque jour à {args.at})")
    print(f"  {line}")
    print(f"  Logs   : {log_file}")
    print(f"  Retirer: python3 -m shared.autopilot --uninstall-schedule")
    return 0


def _uninstall_schedule() -> int:
    """Retire le timer systemd ET l'entrée cron."""
    done = []
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    if (unit_dir / f"{SERVICE_NAME}.timer").exists():
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{SERVICE_NAME}.timer"],
                       capture_output=True, text=True)
        for suffix in ("service", "timer"):
            (unit_dir / f"{SERVICE_NAME}.{suffix}").unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True)
        done.append("timer systemd")

    marker = "# mobility-autopilot"
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    if marker in current:
        kept = [l for l in current.splitlines() if marker not in l]
        subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n",
                       capture_output=True, text=True)
        done.append("entrée cron")

    print(f"✓ Retiré : {', '.join(done)}" if done else "Rien à retirer.")
    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m shared.autopilot",
        description="Autopilote — surveillance & réparation qualité programmée",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    stop = p.add_argument_group("conditions d'arrêt (la première atteinte l'emporte)")
    stop.add_argument("--until", metavar="HH:MM",
                      help="S'arrêter à cette heure (demain si déjà passée)")
    stop.add_argument("--for", dest="run_for", metavar="DUREE",
                      help="S'arrêter après cette durée (ex. 8h, 90m, 8h30m)")
    stop.add_argument("--max-cycles", type=int, metavar="N",
                      help="S'arrêter après N cycles")
    stop.add_argument("--target-score", type=int, metavar="N",
                      help="S'arrêter dès que le score qualité atteint N (0-100)")
    stop.add_argument("--stop-when-clean", action="store_true",
                      help="S'arrêter dès qu'aucune issue n'est détectée")
    stop.add_argument("--patience", type=int, metavar="N",
                      help="S'arrêter après N cycles consécutifs sans aucune réparation")

    run = p.add_argument_group("exécution")
    run.add_argument("--type", nargs="*", metavar="TYPE_ID", dest="type_ids",
                     help="Restreindre aux data_type_id (ex. traces_gps)")
    run.add_argument("--interval", type=int, default=300, metavar="SEC",
                     help="Intervalle entre cycles (défaut 300)")
    run.add_argument("--max-fix", type=int, default=20, metavar="N",
                     help="Réparations max par cycle (défaut 20)")
    run.add_argument("--with-orchestrator", action="store_true",
                     help="Lancer aussi une passe Auto1→Auto2→Auto3 à chaque cycle")
    run.add_argument("--log-file", metavar="FILE",
                     help=f"Fichier de log (défaut {LOG_DIR}/autopilot.log)")
    run.add_argument("-v", "--verbose", action="store_true")

    sched = p.add_argument_group("programmation")
    sched.add_argument("--status", action="store_true",
                       help="Afficher l'état de l'autopilote (en cours ou dernier run) puis sortir")
    sched.add_argument("--at", metavar="HH:MM",
                       help="Heure de démarrage quotidien (avec --install-systemd / --install-cron)")
    sched.add_argument("--install-systemd", action="store_true",
                       help="Installer un timer systemd --user quotidien")
    sched.add_argument("--install-cron", action="store_true",
                       help="Installer une entrée crontab quotidienne")
    sched.add_argument("--uninstall-schedule", action="store_true",
                       help="Retirer le timer systemd et l'entrée cron")
    sched.add_argument("--dry-run", action="store_true",
                       help="Afficher le plan d'exécution puis sortir (sans rien lancer)")
    return p


def main() -> None:
    args = _build_parser().parse_args()

    status_path = LOG_DIR / "autopilot_status.json"
    if args.status:
        sys.exit(_show_status(status_path))
    if args.uninstall_schedule:
        sys.exit(_uninstall_schedule())
    if args.install_systemd:
        sys.exit(_install_systemd(args))
    if args.install_cron:
        sys.exit(_install_cron(args))

    # ── Construire le plan d'arrêt ────────────────────────────────────────────
    deadline = None
    if args.until:
        deadline = parse_clock(args.until)
    if args.run_for:
        by_duration = datetime.now() + parse_duration(args.run_for)
        deadline = min(deadline, by_duration) if deadline else by_duration

    plan = StopPlan(
        deadline=deadline,
        max_cycles=args.max_cycles,
        target_score=args.target_score,
        stop_when_clean=args.stop_when_clean,
        patience=args.patience,
    )

    if args.dry_run:
        print("Plan d'exécution de l'autopilote :")
        print(f"  Arrêt        : {plan.describe()}")
        if deadline:
            rem = deadline - datetime.now()
            print(f"  Durée prévue : {int(rem.total_seconds()) // 3600}h"
                  f"{(int(rem.total_seconds()) % 3600) // 60:02d}m")
        print(f"  Types        : {', '.join(args.type_ids) if args.type_ids else 'tous'}")
        print(f"  Intervalle   : {args.interval}s | max_fix/cycle : {args.max_fix}")
        print(f"  Orchestrateur: {args.with_orchestrator}")
        print(f"  Statut       : {status_path}")
        sys.exit(0)

    log_file = Path(args.log_file) if args.log_file else (LOG_DIR / "autopilot.log")
    _setup_logging(args.verbose, log_file)

    from shared.runtime_lock import LockBusy, single_instance
    try:
        # Verrou : un seul autopilote à la fois. Un timer qui redéclenche alors
        # que le run précédent tourne encore ne doit pas doubler la charge.
        with single_instance("autopilot"):
            pilot = Autopilot(
                stop_plan=plan,
                type_ids=args.type_ids,
                interval_sec=args.interval,
                max_fix_per_run=args.max_fix,
                with_orchestrator=args.with_orchestrator,
                status_path=status_path,
            )
            state = pilot.run()
    except LockBusy as exc:
        print(f"⚠  {exc}", file=sys.stderr)
        sys.exit(2)

    print(f"\nAutopilote terminé — {state.cycle} cycles, {state.total_fixed} réparation(s), "
          f"score final {state.last_score}. Rapport : {pilot.report_path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
