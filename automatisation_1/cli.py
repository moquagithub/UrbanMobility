"""
Interface ligne de commande pour l'Automatisation 1.

Usage :
    python -m automatisation_1 run
    python -m automatisation_1 run --type "GPS"
    python -m automatisation_1 run --force
    python -m automatisation_1 run --skip-s1 --skip-s3
    python -m automatisation_1 run --enrich                        # enrichissement jusqu'à saturation
    python -m automatisation_1 run --enrich --max-problems 15      # max 15 problèmes/type
    python -m automatisation_1 run --enrich --max-algos 9 --patience 3
    python -m automatisation_1 status
    python -m automatisation_1 status --type "GPS"
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("auto1.cli")


# ── Sous-commande : check-llm ────────────────────────────────────────────────

def cmd_check_llm(_args):
    """Teste la connexion à tous les providers LLM."""
    from shared.llm.health_check import run_health_check
    results = run_health_check(print_report=True)
    ok = [r["name"] for r in results if r["ok"]]
    if not ok:
        log.error("Aucun provider LLM opérationnel — le pipeline ne peut pas tourner.")
        sys.exit(1)
    log.info("Provider(s) actif(s) : %s", ", ".join(ok))


# ── Sous-commande : run ──────────────────────────────────────────────────────

def cmd_run(args):
    from automatisation_1.runner import run
    catalogue = Path(args.catalogue)

    if not catalogue.exists():
        log.error("Fichier catalogue introuvable : %s", catalogue)
        sys.exit(1)

    log.info("=" * 60)
    log.info("AUTOMATISATION 1 — Démarrage")
    log.info("Catalogue : %s", catalogue)
    log.info("Filtre    : %s", args.type or "Tous les types")
    log.info("Force     : %s", args.force)
    log.info("=" * 60)

    # Résout --type tout de suite : échoue vite si le filtre est ambigu,
    # avant de gaspiller un appel LLM de vérification des providers.
    if args.type:
        from shared.db.repository import CatalogueRepository
        from automatisation_1.type_resolver import resolve_type_filter, AmbiguousTypeFilter
        cat_repo = CatalogueRepository()
        if cat_repo.is_available():
            try:
                matched = resolve_type_filter(cat_repo.list_data_types(), args.type)
            except AmbiguousTypeFilter as exc:
                log.error(str(exc))
                sys.exit(1)
            if not matched:
                log.error("Aucun type trouvé avec le filtre '%s'", args.type)
                sys.exit(1)
            log.info("Type résolu : %s (%s)", matched[0]["id"], matched[0]["name"])

    # Vérification des providers LLM avant de lancer
    from shared.llm.health_check import run_health_check, get_working_providers
    log.info("Vérification des providers LLM...")
    results = run_health_check(print_report=True)
    ok_providers = get_working_providers(results)
    if not ok_providers:
        log.error("Aucun provider LLM opérationnel. Corrigez vos clés API dans .env")
        sys.exit(1)
    log.info("Provider(s) actif(s) : %s", ", ".join(ok_providers))

    def on_progress(step, msg):
        log.info("[%s] %s", step.upper(), msg)

    if args.enrich:
        log.info("Mode ENRICHISSEMENT — max %d problèmes/type, %d algos/problème, patience %d",
                 args.max_problems, args.max_algos, args.patience)

    result = run(
        catalogue_path = catalogue,
        type_filter    = args.type,
        force          = args.force,
        skip_s1        = args.skip_s1,
        skip_s1b       = args.skip_s1b,
        skip_s2        = args.skip_s2,
        skip_s3        = args.skip_s3,
        skip_s4        = args.skip_s4,
        enrich         = args.enrich,
        max_problems   = args.max_problems,
        max_algos      = args.max_algos,
        patience       = args.patience,
        on_progress    = on_progress,
    )

    log.info("=" * 60)
    if result.success:
        log.info("✓ SUCCÈS — %d problème(s), %d algorithme(s) — %.1fs",
                 result.nb_problems, result.nb_algorithms, result.duration_seconds)
        for step in result.steps:
            status = "✓" if step.success else "✗"
            log.info("  %s [%s] %s", status, step.step, step.message)
            for w in step.warnings:
                log.warning("      ⚠ %s", w)
    else:
        log.error("✗ ÉCHEC")
        for step in result.steps:
            if step.errors:
                for e in step.errors:
                    log.error("  ✗ [%s] %s", step.step, e)
        sys.exit(1)
    log.info("=" * 60)


# ── Sous-commande : status ───────────────────────────────────────────────────

def cmd_status(args):
    from shared.db.repository import CatalogueRepository, ProblemRepository, AlgorithmRepository

    cat_repo  = CatalogueRepository()
    prob_repo = ProblemRepository()
    alg_repo  = AlgorithmRepository()

    if not cat_repo.is_available():
        log.error("MySQL non disponible — vérifiez votre .env (DB_HOST, DB_NAME...)")
        sys.exit(1)

    data_types = cat_repo.list_data_types()
    if args.type:
        tf = args.type.lower()
        data_types = [dt for dt in data_types
                      if tf in dt.get("id", "").lower() or tf in dt.get("name", "").lower()]

    if not data_types:
        log.info("Aucun type de données en base")
        return

    print(f"\n{'TYPE':<35} {'STATUT':<20} {'PROBLÈMES':>10} {'ALGOS':>8}")
    print("-" * 78)

    for dt in data_types:
        type_id  = dt["id"]
        name     = dt["name"][:34]
        status   = dt.get("processing_status", "?")
        nb_probs = prob_repo.count_problems(type_id)

        # Compte les algos
        algs = alg_repo.find_all_for_data_type(type_id)
        nb_algs = len(algs)

        print(f"{name:<35} {status:<20} {nb_probs:>10} {nb_algs:>8}")

    print("-" * 78)
    print(f"Total : {len(data_types)} type(s)\n")


# ── Sous-commande : reset ────────────────────────────────────────────────────

def cmd_reset(args):
    """Remet à 'pending' le statut d'un type (permet de forcer la régénération)."""
    from shared.db.repository import CatalogueRepository
    from automatisation_1.type_resolver import resolve_type_filter, AmbiguousTypeFilter
    cat_repo = CatalogueRepository()

    if not args.type:
        log.error("--type requis pour la commande reset")
        sys.exit(1)

    try:
        matched = resolve_type_filter(cat_repo.list_data_types(), args.type)
    except AmbiguousTypeFilter as exc:
        log.error(str(exc))
        sys.exit(1)

    if not matched:
        log.error("Aucun type trouvé avec le filtre '%s'", args.type)
        sys.exit(1)

    for dt in matched:
        ok = cat_repo.update_status(dt["id"], "pending")
        if ok:
            log.info("✓ Statut de '%s' remis à 'pending'", dt["name"])
        else:
            log.error("✗ Impossible de remettre le statut de '%s'", dt["name"])


# ── Sous-commande : migrate ──────────────────────────────────────────────────

def cmd_migrate(_args):
    """Applique la migration v6 (colonnes needs_skeleton_regen + skeleton_regen_reason)."""
    from shared.db.migration_v6 import run_migration
    try:
        run_migration()
        log.info("✓ Migration v6 appliquée avec succès.")
    except Exception as exc:
        log.error("✗ Erreur migration v6 : %s", exc)


def cmd_migrate_v8(_args):
    """Applique la migration v8 (colonnes minio_dir sur problems + algorithms)."""
    from shared.db.migration_v8 import run as run_migration_v8
    try:
        stats = run_migration_v8()
        log.info("✓ Migration v8 appliquée — ok=%d skip=%d error=%d",
                 stats["ok"], stats["skip"], stats["error"])
        if stats["error"] > 0:
            sys.exit(1)
    except Exception as exc:
        log.error("✗ Erreur migration v8 : %s", exc)
        sys.exit(1)


def cmd_migrate_v9(_args):
    """Applique la migration v9 (problem_id sur figures/reports, algorithm_id nullable sur notebooks)."""
    from shared.db.migration_v9 import run as run_migration_v9
    try:
        stats = run_migration_v9()
        log.info("✓ Migration v9 appliquée — ok=%d skip=%d error=%d",
                 stats["ok"], stats["skip"], stats["error"])
        if stats["error"] > 0:
            sys.exit(1)
    except Exception as exc:
        log.error("✗ Erreur migration v9 : %s", exc)
        sys.exit(1)


# ── Point d'entrée ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="automatisation_1",
        description="Automatisation 1 — Catalogue → Problèmes → Algorithmes → MySQL",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Lance l'automatisation complète")
    p_run.add_argument("--catalogue", default="data/example_catalogue.xlsx",
                       help="Chemin vers le fichier Excel (défaut: data/example_catalogue.xlsx)")
    p_run.add_argument("--type",    default=None,
                       help="Traite uniquement ce type (id ou nom partiel)")
    p_run.add_argument("--force",   action="store_true",
                       help="Régénère même si les données existent déjà")
    p_run.add_argument("--skip-s1",  action="store_true", dest="skip_s1",
                       help="Saute l'étape 1 (lecture catalogue Excel)")
    p_run.add_argument("--skip-s1b", action="store_true", dest="skip_s1b",
                       help="Saute l'étape 1b (enrichissement LLM des types)")
    p_run.add_argument("--skip-s2",  action="store_true", dest="skip_s2",
                       help="Saute l'étape 2 (génération problèmes)")
    p_run.add_argument("--skip-s3",  action="store_true", dest="skip_s3",
                       help="Saute l'étape 3 (génération algorithmes)")
    p_run.add_argument("--skip-s4",  action="store_true", dest="skip_s4",
                       help="Saute l'étape 4 (génération datasets synthétiques)")
    p_run.add_argument("--enrich",   action="store_true",
                       help="Mode enrichissement : itère jusqu'à saturation par type/problème")
    p_run.add_argument("--max-problems", type=int, default=12, dest="max_problems",
                       metavar="N", help="(enrich) Max problèmes par type (défaut: 12)")
    p_run.add_argument("--max-algos",    type=int, default=9,  dest="max_algos",
                       metavar="N", help="(enrich) Max algorithmes par problème (défaut: 9)")
    p_run.add_argument("--patience",     type=int, default=2,  dest="patience",
                       metavar="N", help="(enrich) Rounds sans résultat avant stop (défaut: 2)")
    p_run.set_defaults(func=cmd_run)

    # status
    p_status = sub.add_parser("status", help="Affiche l'état MySQL de tous les types")
    p_status.add_argument("--type", default=None, help="Filtre sur un type")
    p_status.set_defaults(func=cmd_status)

    # reset
    p_reset = sub.add_parser("reset", help="Remet le statut d'un type à 'pending'")
    p_reset.add_argument("--type", required=True, help="Type à remettre à zéro")
    p_reset.set_defaults(func=cmd_reset)

    # check-llm
    p_check = sub.add_parser("check-llm", help="Teste la connexion à tous les providers LLM")
    p_check.set_defaults(func=cmd_check_llm)

    # migrate
    p_migrate = sub.add_parser("migrate", help="Applique la migration v6 (colonnes qualité squelettes)")
    p_migrate.set_defaults(func=cmd_migrate)

    # migrate-v8
    p_migrate_v8 = sub.add_parser(
        "migrate-v8",
        help="Applique la migration v8 (colonnes minio_dir sur problems + algorithms)"
    )
    p_migrate_v8.set_defaults(func=cmd_migrate_v8)

    # migrate-v9
    p_migrate_v9 = sub.add_parser(
        "migrate-v9",
        help="Applique la migration v9 (problem_id sur figures/reports, algorithm_id nullable sur notebooks)"
    )
    p_migrate_v9.set_defaults(func=cmd_migrate_v9)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
