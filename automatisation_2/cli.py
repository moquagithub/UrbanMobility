"""
CLI de l'Automatisation 2.

Usage :
  python3 -m automatisation_2 run
  python3 -m automatisation_2 run --types gps_traces traffic_counts --force
  python3 -m automatisation_2 run --skip-s1 --skip-s2          # exécution seule
  python3 -m automatisation_2 run --max-notebooks 5             # test limité
  python3 -m automatisation_2 status                            # état en DB
  python3 -m automatisation_2 migrate                           # apply migration v3
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
log = logging.getLogger("auto2.cli")


def cmd_migrate(args: argparse.Namespace) -> None:
    """Applique les migrations DB nécessaires à auto2."""
    log.info("Exécution migration v3...")
    try:
        from shared.db.migration_v3 import run_migration
        run_migration()
        log.info("Migration v3 terminée.")
    except Exception as exc:
        log.error("Erreur migration : %s", exc)
        sys.exit(1)


def cmd_run(args: argparse.Namespace) -> None:
    """Lance le pipeline complet (ou partiel)."""
    from shared.llm.health_check import run_health_check, get_working_providers

    if not args.skip_s1:
        log.info("Vérification des providers LLM...")
        hc_results = run_health_check(print_report=True)
        working = get_working_providers(hc_results)
        if not working:
            log.error("Aucun provider LLM disponible. Vérifiez vos clés API dans .env")
            sys.exit(1)
        log.info("Providers disponibles : %s", working)

    from automatisation_2.runner import run_pipeline

    result = run_pipeline(
        type_ids=args.types or None,
        force=args.force,
        skip_s1=args.skip_s1,
        skip_s2=args.skip_s2,
        skip_s3=args.skip_s3,
        skip_s4=args.skip_s4,
        skip_s5=args.skip_s5,
        skip_s6=args.skip_s6,
        max_notebooks=args.max_notebooks,
    )

    print("\n── Résultat pipeline Automatisation 2 ──────────────────────")
    for step, res in result.items():
        print(f"  {step}: {res}")
    print("────────────────────────────────────────────────────────────")


def cmd_status(args: argparse.Namespace) -> None:
    """Affiche l'état actuel des notebooks et datasets en DB."""
    from shared.db.connection import get_connection

    conn = get_connection()
    if not conn:
        log.error("Base de données non disponible")
        sys.exit(1)

    try:
        with conn.cursor() as cur:
            # Datasets
            cur.execute("""
                SELECT dt.name, COUNT(d.id) AS nb_datasets
                FROM data_types dt
                LEFT JOIN datasets d ON d.data_type_id = dt.id
                GROUP BY dt.id, dt.name
                ORDER BY dt.name
            """)
            rows = cur.fetchall()
            print("\n── Datasets par type de données ─────────────────────────")
            for row in rows:
                print(f"  {row[0]:<40} {row[1]} dataset(s)")

            # Notebooks par statut
            cur.execute("""
                SELECT dt.name, n.status, COUNT(*) AS cnt
                FROM notebooks n
                JOIN data_types dt ON n.data_type_id = dt.id
                GROUP BY dt.id, dt.name, n.status
                ORDER BY dt.name, n.status
            """)
            rows = cur.fetchall()
            print("\n── Notebooks par type et statut ─────────────────────────")
            current_type = ""
            for row in rows:
                if row[0] != current_type:
                    print(f"\n  {row[0]}")
                    current_type = row[0]
                print(f"    {row[1]:<15} {row[2]} notebook(s)")

            # Métriques moyennes
            cur.execute("""
                SELECT
                    dt.name,
                    COUNT(nr.id) AS nb_results,
                    AVG(nr.execution_time_sec) AS avg_time
                FROM notebook_results nr
                JOIN notebooks n ON nr.notebook_id = n.id
                JOIN data_types dt ON n.data_type_id = dt.id
                GROUP BY dt.id, dt.name
                ORDER BY dt.name
            """)
            rows = cur.fetchall()
            if rows:
                print("\n── Résultats d'exécution ─────────────────────────────")
                for row in rows:
                    avg_t = f"{row[2]:.1f}s" if row[2] else "N/A"
                    print(f"  {row[0]:<40} {row[1]} résultats | temps moyen: {avg_t}")

    finally:
        conn.close()

    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m automatisation_2",
        description="Automatisation 2 — Datasets → Notebooks → Exécution → Résultats",
    )
    sub = parser.add_subparsers(dest="command")

    # ── run ───────────────────────────────────────────────────────────────
    run_p = sub.add_parser("run", help="Lancer le pipeline complet (ou partiel)")
    run_p.add_argument(
        "--types", nargs="+", metavar="TYPE_ID",
        help="Restreindre à certains type_id (ex: gps_traces traffic_counts)",
    )
    run_p.add_argument("--force",          action="store_true", help="Forcer la régénération")
    run_p.add_argument("--skip-s1",        action="store_true", help="Sauter génération datasets")
    run_p.add_argument("--skip-s2",        action="store_true", help="Sauter construction notebooks")
    run_p.add_argument("--skip-s3",        action="store_true", help="Sauter exécution notebooks")
    run_p.add_argument("--skip-s4",        action="store_true", help="Sauter agrégation résultats")
    run_p.add_argument("--skip-s5",        action="store_true", help="Sauter génération graphiques comparatifs")
    run_p.add_argument("--skip-s6",        action="store_true", help="Sauter notebooks standalone + LaTeX par algorithme")
    run_p.add_argument("--max-notebooks",  type=int, metavar="N",
                       help="Limiter à N notebooks exécutés (pour tests)")
    run_p.set_defaults(func=cmd_run)

    # ── status ────────────────────────────────────────────────────────────
    st_p = sub.add_parser("status", help="Afficher l'état des notebooks et datasets en DB")
    st_p.set_defaults(func=cmd_status)

    # ── migrate ───────────────────────────────────────────────────────────
    mg_p = sub.add_parser("migrate", help="Appliquer les migrations DB (migration_v3)")
    mg_p.set_defaults(func=cmd_migrate)

    return parser


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
