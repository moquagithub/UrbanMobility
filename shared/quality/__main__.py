"""
CLI du module qualité.

Usage :
  python -m shared.quality                     # scan unique, affiche le rapport
  python -m shared.quality --once              # idem
  python -m shared.quality --fix               # scan + réparations auto
  python -m shared.quality --watch             # boucle continue (scan + fix)
  python -m shared.quality --watch --no-fix    # boucle continue sans réparation
  python -m shared.quality --report            # rapport HTML depuis dernier scan → stdout
  python -m shared.quality --report --out r.html  # idem → fichier

Options de filtrage :
  --type traces_gps capteurs_iot    # restreindre aux data_type_id
  --only algo:syntax notebook       # ne lancer que les checks dont le label contient ces termes
  --skip low                        # ignorer les checks dont le label contient ces termes
  --interval 600                    # intervalle en secondes entre scans watch (défaut 300)
  --max-fix 50                      # limite de réparations auto par scan (défaut 20)
  --json                            # sortie JSON au lieu du rapport console
"""
from __future__ import annotations

import argparse
import json
import logging
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="python -m shared.quality",
        description="Surveillance qualité & cohérence du pipeline Mobility PDF Generator",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once",   action="store_true", help="Scan unique (défaut)")
    mode.add_argument("--watch",  action="store_true", help="Boucle continue")
    mode.add_argument("--fix",    action="store_true", help="Scan unique + réparations auto")
    mode.add_argument("--report", action="store_true", help="Générer rapport HTML/JSON depuis dernier scan")

    parser.add_argument("--no-fix",  action="store_true", help="Désactiver les réparations en mode watch")
    parser.add_argument("--type",    nargs="*", metavar="TYPE_ID", dest="type_ids",
                        help="Restreindre aux data_type_id")
    parser.add_argument("--only",    nargs="*", metavar="LABEL",
                        help="Ne lancer que les checks dont le label contient ces termes")
    parser.add_argument("--skip",    nargs="*", metavar="LABEL",
                        help="Ignorer les checks dont le label contient ces termes")
    parser.add_argument("--interval", type=int, default=300, metavar="SEC",
                        help="Intervalle entre scans watch (défaut 300)")
    parser.add_argument("--max-fix",  type=int, default=20, metavar="N",
                        help="Réparations auto max par scan (défaut 20)")
    parser.add_argument("--max-cycles", type=int, default=None, metavar="N",
                        help="Nombre max d'itérations en mode watch (défaut : illimité)")
    parser.add_argument("--json",    action="store_true", help="Sortie JSON (rapport console uniquement)")
    parser.add_argument("--out",     metavar="FILE", help="Fichier de sortie pour --report HTML")
    parser.add_argument("--scan-id", metavar="UUID", help="scan_id pour --report")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    )

    from shared.quality.monitor import QualityMonitor
    monitor = QualityMonitor(
        type_ids=args.type_ids,
        only_checks=args.only,
        skip_checks=args.skip,
        watch_interval_sec=args.interval,
        max_fix_per_run=args.max_fix,
    )

    # ── Mode rapport HTML ─────────────────────────────────────────────────────
    if args.report:
        html = monitor.generate_html_report(scan_id=args.scan_id)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"Rapport écrit dans {args.out}")
        else:
            sys.stdout.write(html)
        return

    # ── Mode watch ─────────────────────────────────────────────────────────────
    if args.watch:
        from shared.runtime_lock import LockBusy, single_instance
        do_fix = not args.no_fix
        try:
            # Verrou d'instance unique : deux --watch en parallèle réparent les
            # mêmes issues, s'écrasent mutuellement et doublent la conso de quota.
            with single_instance("quality-watch"):
                monitor.watch(fix=do_fix, max_cycles=args.max_cycles)
        except LockBusy as exc:
            print(f"⚠  {exc}", file=sys.stderr)
            sys.exit(2)
        return

    # ── Mode scan unique (--once | --fix | défaut) ─────────────────────────────
    do_fix = args.fix
    result = monitor.run_once(fix=do_fix, print_report=not args.json)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        score = result.get("score", "?")
        issues = result.get("issues", 0)
        fixed = result.get("fixed", 0)
        elapsed = result.get("elapsed_sec", 0)
        print(f"\nScore : {score}/100 | Issues : {issues} | Réparés : {fixed} | Durée : {elapsed}s")

    # Code de retour : 0 si score ≥ 80, sinon 1
    sys.exit(0 if result.get("score", 0) >= 80 else 1)


if __name__ == "__main__":
    main()
