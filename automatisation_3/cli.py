"""
CLI de l'Automatisation 3.

Usage :
  python3 -m automatisation_3 run
  python3 -m automatisation_3 run --types traffic_counts gps_traces
  python3 -m automatisation_3 run --force                # régénérer tout
  python3 -m automatisation_3 run --no-llm               # sans réparation LLM
  python3 -m automatisation_3 run --skip-s3              # LaTeX seul, pas de compilation
  python3 -m automatisation_3 status                     # voir les PDFs générés
  python3 -m automatisation_3 migrate                    # appliquer migration v4
"""
from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("auto3.cli")


def cmd_run(args: argparse.Namespace) -> None:
    from automatisation_3.runner import run_pipeline

    result = run_pipeline(
        type_ids=args.types or None,
        force=args.force,
        skip_s1=args.skip_s1,
        skip_s2=args.skip_s2,
        skip_s3=args.skip_s3,
        skip_s4=args.skip_s4,
        use_llm_repair=not args.no_llm,
        max_repair_rounds=args.max_repairs,
    )

    print("\n── Résultat Automatisation 3 ────────────────────────────────")
    for step, res in result.items():
        print(f"  {step}: {res}")
    print("─────────────────────────────────────────────────────────────\n")


def cmd_status(args: argparse.Namespace) -> None:
    from shared.db.repository import ReportRepository

    reports = ReportRepository().list_all()
    if not reports:
        print("Aucun rapport en base de données.")
        return

    print(f"\n── {len(reports)} rapport(s) en DB ──────────────────────────────────")
    print(f"  {'Type':<35} {'Statut':<12} {'Pages':>5}  PDF")
    print("  " + "─" * 80)
    for r in reports:
        status = "✓ OK" if r.get("compile_success") else "✗ ERR"
        pages  = r.get("n_pages", 0) or 0
        pdf    = r.get("pdf_path", "") or "—"
        name   = (r.get("type_name") or r.get("data_type_id", ""))[:35]
        print(f"  {name:<35} {status:<12} {pages:>5}  {pdf}")
    print()


def cmd_migrate(args: argparse.Namespace) -> None:
    from shared.db.migration_v4 import run_migration
    log.info("Migration v4...")
    run_migration()
    log.info("Migration v4 terminée.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m automatisation_3",
        description="Automatisation 3 — MySQL → LaTeX → PDF",
    )
    sub = p.add_subparsers(dest="command")

    # ── run ───────────────────────────────────────────────────────────────
    run_p = sub.add_parser("run", help="Générer les rapports PDF")
    run_p.add_argument("--types", nargs="+", metavar="TYPE_ID",
                       help="Restreindre à certains type_id")
    run_p.add_argument("--force",       action="store_true", help="Régénérer même si PDF déjà existant")
    run_p.add_argument("--skip-s1",     action="store_true", help="Sauter collecte données")
    run_p.add_argument("--skip-s2",     action="store_true", help="Sauter génération LaTeX")
    run_p.add_argument("--skip-s3",     action="store_true", help="Sauter compilation PDF")
    run_p.add_argument("--skip-s4",     action="store_true", help="Sauter stockage MySQL")
    run_p.add_argument("--no-llm",      action="store_true", help="Désactiver réparation LLM")
    run_p.add_argument("--max-repairs", type=int, default=3, metavar="N",
                       help="Tentatives de réparation LLM max (défaut: 3)")
    run_p.set_defaults(func=cmd_run)

    # ── status ────────────────────────────────────────────────────────────
    st_p = sub.add_parser("status", help="Voir les PDFs générés")
    st_p.set_defaults(func=cmd_status)

    # ── migrate ───────────────────────────────────────────────────────────
    mg_p = sub.add_parser("migrate", help="Appliquer migration v4 (table reports)")
    mg_p.set_defaults(func=cmd_migrate)

    return p


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)
    args.func(args)


if __name__ == "__main__":
    main()
