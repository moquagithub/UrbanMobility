"""
Orchestrateur continu — Auto1 → Auto2 → Auto3 + enrichissement en arrière-plan.

Architecture :
  ┌─────────────────────────────────────────────────────────────────┐
  │                     BOUCLE PRINCIPALE (infinie)                 │
  │                                                                 │
  │  ① PHASE AUTO1 — Remplissage MySQL                             │
  │     • Relit le catalogue Excel → détecte nouveaux types         │
  │     • Types pending/catalogue_done  → génère Problèmes (S2)    │
  │     • Types problems_done           → génère Algorithmes (S3)   │
  │                                                                 │
  │  ② PHASE AUTO2 — Traitement réactif                            │
  │     • Types algorithms_done → S1 Datasets + S2 Notebooks        │
  │                             → S3 Exécution + S4 Résultats       │
  │                                                                 │
  │  ③ PHASE AUTO3 PDF — Génération des rapports PDF               │
  │     • Types notebooks_done → compilation LaTeX → PDF            │
  │                                                                 │
  │  ④ PHASE REFINEMENT — Amélioration continue                    │
  │     • Types notebooks_done avec nouveaux algos → nouveaux NB   │
  │     • Types notebooks_done avec NB en erreur  → ré-exécution   │
  │                                                                 │
  │  ⑤ PHASE ENRICHISSEMENT (si --enrich) — Arrière-plan          │
  │     • Tous les types traités → plus de problèmes + algos        │
  │     • Itère sans limite de max jusqu'à saturation totale        │
  │                                                                 │
  │  ⑥ SLEEP(interval_sec) puis recommence                         │
  └─────────────────────────────────────────────────────────────────┘

  État partagé via MySQL processing_status :
    pending/catalogue_done → problems_done → algorithms_done
    → notebooks_done → report_done

Usage :
    python3 orchestrator.py                          # intervalle 120s par défaut
    python3 orchestrator.py --interval 60            # poll toutes les 60s
    python3 orchestrator.py --once                   # une seule passe puis exit
    python3 orchestrator.py --skip-auto1             # auto2 + raffinement seuls
    python3 orchestrator.py --skip-refinement        # pas de raffinement
    python3 orchestrator.py --max-notebooks 5        # limite NB exécutés par passe
    python3 orchestrator.py --dry-run                # affiche l'état sans agir
    python3 orchestrator.py --enrich                 # enrichissement arrière-plan illimité
    python3 orchestrator.py --once --enrich          # passe unique + enrichissement
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("orchestrator")

# Catalogue Excel par défaut
DEFAULT_CATALOGUE = Path("data/temp_catalogue.xlsx")

# ─── Arrêt propre sur Ctrl+C ──────────────────────────────────────────────────
_running = True

def _handle_signal(sig, frame):
    global _running
    log.info("Signal reçu (%s) — arrêt propre après la passe courante...", sig)
    _running = False

signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ─── Récupération de l'état depuis MySQL ──────────────────────────────────────

def _get_state_summary() -> Dict[str, List[str]]:
    """Retourne un dict {status: [type_ids...]} depuis data_types."""
    from shared.db.connection import get_connection
    state: Dict[str, List[str]] = {}
    conn = get_connection()
    if not conn:
        return state
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, processing_status FROM data_types ORDER BY name")
            for row in cur.fetchall():
                st = row[2] or "pending"
                state.setdefault(st, []).append(row[0])
    except Exception as exc:
        log.warning("[ORCH] Erreur lecture état : %s", exc)
    finally:
        conn.close()
    return state


def _print_state(state: Dict[str, List[str]], pass_num: int) -> None:
    """Affiche un tableau de bord console."""
    order = [
        "pending", "catalogue_done", "problems_done",
        "algorithms_done", "notebooks_done", "report_done",
    ]
    total = sum(len(v) for v in state.values())
    log.info("━━━ Passe #%d — %d type(s) en DB ━━━━━━━━━━━━━━━━━━━━━━━━━━━", pass_num, total)
    for st in order:
        ids = state.get(st, [])
        if ids:
            log.info("  %-22s %2d type(s)", st, len(ids))
    # Statuts non attendus
    for st, ids in state.items():
        if st not in order:
            log.info("  %-22s %2d type(s)  ⚠", st, len(ids))
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


# ─── Phase 1 : Auto1 ──────────────────────────────────────────────────────────

def _run_auto1_phase(
    catalogue_path: Path,
    state: Dict[str, List[str]],
    force: bool = False,
) -> Dict:
    """
    Lance l'Auto1 pour les types qui en ont besoin.

    - Types pending/catalogue_done → S2 (problèmes) + S3 (algorithmes)
    - Types problems_done          → S3 seulement (algorithmes)

    Retourne un résumé {"types_processed": N, "errors": N}
    """
    from automatisation_1.runner import run as auto1_run

    need_s2_s3 = (
        state.get("pending", [])
        + state.get("catalogue_done", [])
    )
    need_s3 = state.get("problems_done", [])
    need_s4 = state.get("algorithms_done", [])  # datasets manquants seulement

    total_to_process = len(need_s2_s3) + len(need_s3) + len(need_s4)
    if total_to_process == 0:
        return {"types_processed": 0, "errors": 0}

    log.info("[AUTO1] %d type(s) à traiter (S2+S3: %d, S3: %d, S4: %d)",
             total_to_process, len(need_s2_s3), len(need_s3), len(need_s4))

    processed = 0
    errors     = 0

    from shared.llm.router import AllProvidersExhausted

    # S2+S3 pour les types sans problèmes encore
    if need_s2_s3:
        try:
            result = auto1_run(
                catalogue_path=catalogue_path,
                force=force,
                skip_s1=True,   # S1 déjà fait (catalogue chargé au démarrage)
                skip_s1b=True,  # enrichissement déjà fait
            )
            processed += len(need_s2_s3)
            if not result.success:
                log.warning("[AUTO1] Résultat partiel : %s", result)
                errors += 1
        except AllProvidersExhausted:
            raise  # Remonte directement — l'orchestrateur principal gère la pause
        except Exception as exc:
            log.warning("[AUTO1] Erreur S2+S3 : %s", exc)
            errors += 1

    # S3 seulement pour les types déjà à problems_done
    for type_id in need_s3:
        try:
            result = auto1_run(
                catalogue_path=catalogue_path,
                type_filter=type_id,
                force=force,
                skip_s1=True,
                skip_s1b=True,
                skip_s2=True,
            )
            processed += 1
            if not result.success:
                log.warning("[AUTO1] %s — résultat partiel", type_id)
                errors += 1
        except AllProvidersExhausted:
            raise
        except Exception as exc:
            log.warning("[AUTO1] %s — erreur S3 : %s", type_id, exc)
            errors += 1

    # S4 seulement pour les types algorithms_done — génération des datasets
    for type_id in need_s4:
        try:
            result = auto1_run(
                catalogue_path=catalogue_path,
                type_filter=type_id,
                force=force,
                skip_s1=True,
                skip_s1b=True,
                skip_s2=True,
                skip_s3=True,
            )
            processed += 1
            if not result.success:
                log.warning("[AUTO1] %s — résultat partiel (S4 datasets)", type_id)
                errors += 1
        except AllProvidersExhausted:
            raise
        except Exception as exc:
            log.warning("[AUTO1] %s — erreur S4 : %s", type_id, exc)
            errors += 1

    return {"types_processed": processed, "errors": errors}


def _reload_catalogue(catalogue_path: Path) -> int:
    """
    Relit le catalogue Excel et insère les nouveaux types en DB.
    Retourne le nombre de nouveaux types détectés.
    """
    if not catalogue_path.exists():
        log.warning("[AUTO1] Catalogue introuvable : %s", catalogue_path)
        return 0
    try:
        from automatisation_1.steps.s1_catalogue import run as s1_run
        result = s1_run(catalogue_path, force=False)
        n = result.data.get("nb_types", 0)
        log.info("[AUTO1] Catalogue rechargé — %d types en DB", n)
        return n
    except Exception as exc:
        log.warning("[AUTO1] Erreur rechargement catalogue : %s", exc)
        return 0


# ─── Phase 2 : Auto2 ──────────────────────────────────────────────────────────

def _run_auto2_phase(
    type_ids: List[str],
    max_notebooks: Optional[int] = None,
) -> Dict:
    """
    Lance l'Auto2 complète pour les types avec algorithms_done.

    Retourne un résumé des 4 étapes.
    """
    if not type_ids:
        return {}

    log.info("[AUTO2] %d type(s) prêts pour Auto2 : %s", len(type_ids), type_ids)

    from automatisation_2.runner import run_pipeline

    result = run_pipeline(
        type_ids=type_ids,
        force=False,
        max_notebooks=max_notebooks,
    )
    return result


# ─── Phase 5 : Enrichissement en arrière-plan ────────────────────────────────

def _run_enrichment_phase(
    catalogue_path: Path,
    state: Dict[str, List[str]],
    max_problems: int = 999,
    max_algos: int = 999,
    patience: int = 3,
) -> Dict:
    """
    Enrichit les types déjà traités en cherchant plus de problèmes et d'algorithmes.

    Contrairement à la Phase 1 (Auto1 normal), l'enrichissement :
    - Cible les types DÉJÀ avancés (problems_done et au-delà)
    - N'a pas de max hard-codé — il tourne jusqu'à saturation naturelle
    - Utilise le mode "complétion" des prompts (voir problems_prompt / algorithms_prompt)
    """
    from automatisation_1.runner import run as auto1_run

    enrichable = []
    for st in ("problems_done", "algorithms_done", "notebooks_done", "report_done"):
        enrichable.extend(state.get(st, []))

    if not enrichable:
        log.info("[ENRICH] Aucun type à enrichir (aucun type au-delà de pending)")
        return {"types_enriched": 0, "problems_added": 0, "algos_added": 0}

    log.info("[ENRICH] %d type(s) candidats à l'enrichissement arrière-plan", len(enrichable))

    from shared.llm.router import AllProvidersExhausted
    try:
        result = auto1_run(
            catalogue_path = catalogue_path,
            force          = False,
            skip_s1        = True,
            skip_s1b       = True,
            enrich         = True,
            max_problems   = max_problems,
            max_algos      = max_algos,
            patience       = patience,
        )
        summary = {
            "types_enriched":  len(enrichable),
            "problems_added":  result.nb_problems,
            "algos_added":     result.nb_algorithms,
        }
        log.info("[ENRICH] ✓ +%d problème(s), +%d algo(s)",
                 result.nb_problems, result.nb_algorithms)
        return summary
    except AllProvidersExhausted:
        raise  # remonte au gestionnaire principal pour pause jusqu'à minuit
    except Exception as exc:
        log.warning("[ENRICH] Erreur enrichissement arrière-plan : %s", exc)
        return {"types_enriched": 0, "problems_added": 0, "algos_added": 0, "error": str(exc)}


# ─── Phase 3 : Refinement ─────────────────────────────────────────────────────

def _run_refinement_phase(max_notebooks: Optional[int] = None) -> Dict:
    """
    Vérifie les types notebooks_done et lance le raffinement si nécessaire.
    """
    from automatisation_2.refiner import find_types_needing_refinement, run_refinement

    types_to_refine = find_types_needing_refinement()
    if not types_to_refine:
        log.info("[REFINER] Tout est à jour — aucun raffinement nécessaire.")
        return {"refined": 0}

    log.info("[REFINER] %d type(s) à raffiner", len(types_to_refine))
    result = run_refinement(types_to_refine, max_notebooks=max_notebooks)
    return result


# ─── Gestion quota épuisé ─────────────────────────────────────────────────────

def _notify_quota_exhausted(reason: str) -> None:
    """Alerte visible quand tous les quotas LLM gratuits sont épuisés."""
    bar = "━" * 62
    log.critical(bar)
    log.critical("⚠  QUOTA LLM ÉPUISÉ — TOUS LES PROVIDERS ONT ATTEINT LEUR LIMITE")
    log.critical("⚠  %s", reason)
    log.critical("⚠  Reprise automatique après minuit (reset des quotas gratuits)")
    log.critical(bar)

    # Écrit un fichier flag dans le répertoire courant
    flag = Path("QUOTA_EXHAUSTED.flag")
    try:
        flag.write_text(
            f"[{datetime.now().isoformat()}]\n{reason}\n\n"
            "Supprimez ce fichier ou attendez minuit pour relancer.\n"
        )
        log.info("[QUOTA] Fichier d'alerte créé : %s", flag.resolve())
    except Exception:
        pass

    # Bip terminal
    sys.stdout.write("\a")
    sys.stdout.flush()


def _seconds_until_midnight(extra_minutes: int = 5) -> int:
    """Nombre de secondes jusqu'à minuit + extra_minutes."""
    now  = datetime.now()
    next_reset = (now + timedelta(days=1)).replace(
        hour=0, minute=extra_minutes, second=0, microsecond=0
    )
    return max(60, int((next_reset - now).total_seconds()))


# ─── Boucle principale ────────────────────────────────────────────────────────

def _run_auto3_phase(type_ids: List[str]) -> Dict:
    """Lance Auto3 pour les types avec notebooks_done (génération PDF)."""
    if not type_ids:
        return {}
    log.info("[AUTO3] %d type(s) prêts pour Auto3 : %s", len(type_ids), type_ids)
    from automatisation_3.runner import run_pipeline
    result = run_pipeline(type_ids=type_ids, force=False, use_llm_repair=True)
    return result


def run_orchestrator(
    catalogue_path: Path = DEFAULT_CATALOGUE,
    interval_sec: int = 120,
    once: bool = False,
    skip_auto1: bool = False,
    skip_auto2: bool = False,
    skip_auto3: bool = False,
    skip_refinement: bool = False,
    max_notebooks: Optional[int] = None,
    dry_run: bool = False,
    force: bool = False,
    enrich: bool = False,
    enrich_max_problems: int = 999,
    enrich_max_algos: int = 999,
    enrich_patience: int = 3,
) -> None:
    """
    Boucle d'orchestration principale.

    Args:
        catalogue_path      : chemin vers le fichier Excel catalogue
        interval_sec        : secondes d'attente entre deux passes
        once                : une seule passe puis exit
        skip_auto1          : ne pas exécuter l'auto1
        skip_auto2          : ne pas exécuter l'auto2
        skip_auto3          : ne pas exécuter l'auto3 (PDF)
        skip_refinement     : ne pas exécuter le raffinement
        max_notebooks       : limite de notebooks exécutés par passe auto2/refinement
        dry_run             : afficher l'état sans agir
        force               : forcer la régénération
        enrich              : activer la Phase 5 — enrichissement arrière-plan illimité
        enrich_max_problems : max problèmes par type en enrichissement (défaut: 999 = sans limite)
        enrich_max_algos    : max algos par problème en enrichissement (défaut: 999 = sans limite)
        enrich_patience     : rounds sans résultat avant saturation (défaut: 3)
    """
    global _running

    pass_num = 0
    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info("║    ORCHESTRATEUR — Auto1 → Auto2 → Auto3 + Enrich      ║")
    log.info("╚══════════════════════════════════════════════════════════╝")
    log.info("Catalogue  : %s", catalogue_path)
    log.info("Intervalle : %ds | once=%s | dry_run=%s | enrich=%s",
             interval_sec, once, dry_run, enrich)

    # Chargement initial du catalogue
    if not skip_auto1 and not dry_run:
        _reload_catalogue(catalogue_path)

    from shared.llm.router import AllProvidersExhausted, reset_session

    while _running:
        pass_num += 1
        t_pass = time.perf_counter()

        # ── État courant ──────────────────────────────────────────────────
        state = _get_state_summary()
        _print_state(state, pass_num)

        if dry_run:
            log.info("[DRY-RUN] Aucune action (--dry-run activé).")
            if once:
                break
            _sleep_interruptible(interval_sec)
            continue

        datasets_done_types = state.get("datasets_done", [])
        pass_did_something = False

        try:
            # ── Phase 1 : Auto1 ──────────────────────────────────────────
            if not skip_auto1:
                need_auto1 = (
                    state.get("pending", [])
                    + state.get("catalogue_done", [])
                    + state.get("problems_done", [])
                    + state.get("algorithms_done", [])   # manque datasets
                )
                if need_auto1:
                    log.info("[ORCH] Phase 1 — Auto1 (%d type(s))", len(need_auto1))
                    a1 = _run_auto1_phase(catalogue_path, state, force=force)
                    log.info("[ORCH] Auto1 terminé : %s", a1)
                    pass_did_something = True

                    # Recharger l'état — certains types sont passés à datasets_done
                    state = _get_state_summary()
                    datasets_done_types = state.get("datasets_done", [])
                else:
                    log.info("[ORCH] Phase 1 — Auto1 : rien à faire (0 type en attente)")
            else:
                log.info("[ORCH] Phase 1 — Auto1 ignorée (--skip-auto1)")

            # ── Phase 2 : Auto2 ──────────────────────────────────────────
            if not skip_auto2 and datasets_done_types:
                log.info("[ORCH] Phase 2 — Auto2 (%d type(s) datasets_done)", len(datasets_done_types))
                a2 = _run_auto2_phase(datasets_done_types, max_notebooks=max_notebooks)
                log.info("[ORCH] Auto2 terminé : %s", {k: v for k, v in a2.items() if k != 'elapsed_sec'})
                pass_did_something = True
            elif not skip_auto2:
                log.info("[ORCH] Phase 2 — Auto2 : aucun type avec datasets_done")
            else:
                log.info("[ORCH] Phase 2 — Auto2 ignorée (--skip-auto2)")

            # ── Phase 3 : Auto3 PDF ──────────────────────────────────────
            state = _get_state_summary()
            nb_done_types = state.get("notebooks_done", [])
            if not skip_auto3 and nb_done_types:
                log.info("[ORCH] Phase 3 — Auto3 PDF (%d type(s) notebooks_done)", len(nb_done_types))
                a3 = _run_auto3_phase(nb_done_types)
                log.info("[ORCH] Auto3 terminé : %s", {k: v for k, v in a3.items() if k != 'elapsed_sec'})
                pass_did_something = True
            elif not skip_auto3:
                log.info("[ORCH] Phase 3 — Auto3 : aucun type avec notebooks_done")
            else:
                log.info("[ORCH] Phase 3 — Auto3 ignorée (--skip-auto3)")

            # ── Phase 4 : Refinement ─────────────────────────────────────
            state = _get_state_summary()
            if not skip_refinement and state.get("notebooks_done"):
                log.info(
                    "[ORCH] Phase 4 — Raffinement (%d type(s) notebooks_done)",
                    len(state["notebooks_done"]),
                )
                rf = _run_refinement_phase(max_notebooks=max_notebooks)
                log.info("[ORCH] Raffinement terminé : %s", rf)
                if rf.get("new_notebooks", 0) or rf.get("retried", 0):
                    pass_did_something = True
            elif not skip_refinement:
                log.info("[ORCH] Phase 4 — Raffinement : aucun type notebooks_done")
            else:
                log.info("[ORCH] Phase 4 — Raffinement ignoré (--skip-refinement)")

            # ── Phase 5 : Enrichissement arrière-plan ────────────────────
            if enrich and not dry_run:
                state = _get_state_summary()
                log.info("[ORCH] Phase 5 — Enrichissement arrière-plan (patience=%d)", enrich_patience)
                enr = _run_enrichment_phase(
                    catalogue_path,
                    state,
                    max_problems = enrich_max_problems,
                    max_algos    = enrich_max_algos,
                    patience     = enrich_patience,
                )
                log.info("[ORCH] Enrichissement : +%d problème(s), +%d algo(s)",
                         enr.get("problems_added", 0), enr.get("algos_added", 0))
                if enr.get("problems_added", 0) or enr.get("algos_added", 0):
                    pass_did_something = True
            elif not enrich:
                log.info("[ORCH] Phase 5 — Enrichissement désactivé (passez --enrich pour l'activer)")

        except AllProvidersExhausted as exc:
            # ── QUOTA ÉPUISÉ : pause jusqu'au reset minuit ───────────────
            _notify_quota_exhausted(str(exc))
            wait_sec = _seconds_until_midnight()
            reset_time = datetime.now() + timedelta(seconds=wait_sec)
            log.info(
                "[ORCH] En pause %dh%02dm — reprise à %s",
                wait_sec // 3600, (wait_sec % 3600) // 60,
                reset_time.strftime("%H:%M le %d/%m"),
            )
            if once:
                log.info("[ORCH] Mode --once : arrêt (quota épuisé, pas de retry automatique).")
                break
            _sleep_interruptible(wait_sec)
            reset_session()   # Efface la blacklist — les quotas ont été réinitialisés
            log.info("[ORCH] Reprise après reset quota minuit.")
            continue

        # ── Recharger le catalogue (nouveaux types pourraient avoir été ajoutés) ──
        if not skip_auto1 and not dry_run:
            _reload_catalogue(catalogue_path)

        # ── Bilan de passe ────────────────────────────────────────────────
        elapsed = round(time.perf_counter() - t_pass, 1)
        if pass_did_something:
            log.info("[ORCH] Passe #%d terminée en %.1fs — travail effectué.", pass_num, elapsed)
        else:
            log.info("[ORCH] Passe #%d terminée en %.1fs — tout à jour, pause %ds.",
                     pass_num, elapsed, interval_sec)

        if once:
            log.info("[ORCH] Mode --once : arrêt après passe #%d.", pass_num)
            break

        _sleep_interruptible(interval_sec)

    log.info("[ORCH] Orchestrateur arrêté proprement.")


def _sleep_interruptible(seconds: int) -> None:
    """Dors par tranches de 5s pour rester réactif aux signaux Ctrl+C."""
    global _running
    slept = 0
    while _running and slept < seconds:
        chunk = min(5, seconds - slept)
        time.sleep(chunk)
        slept += chunk


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 orchestrator.py",
        description="Orchestrateur continu Auto1 (MySQL) + Auto2 (Notebooks) en pipeline réactif",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python3 orchestrator.py                        # boucle toutes les 120s (Auto1→Auto2→Auto3)
  python3 orchestrator.py --once                 # une seule passe puis exit
  python3 orchestrator.py --once --enrich        # une passe + enrichissement arrière-plan
  python3 orchestrator.py --enrich               # boucle continue + enrichissement illimité
  python3 orchestrator.py --interval 30          # poll toutes les 30s
  python3 orchestrator.py --skip-auto1           # auto2 + auto3 + raffinement seulement
  python3 orchestrator.py --dry-run              # affiche l'état sans agir
  python3 orchestrator.py --max-notebooks 3      # exécute max 3 notebooks/passe
  python3 orchestrator.py --catalogue data/mon_catalogue.xlsx
        """,
    )
    p.add_argument(
        "--catalogue", type=Path, default=DEFAULT_CATALOGUE,
        metavar="FICHIER",
        help=f"Chemin vers le catalogue Excel (défaut: {DEFAULT_CATALOGUE})",
    )
    p.add_argument(
        "--interval", type=int, default=120, metavar="SEC",
        help="Intervalle en secondes entre deux passes (défaut: 120)",
    )
    p.add_argument(
        "--once", action="store_true",
        help="Exécuter une seule passe puis quitter",
    )
    p.add_argument(
        "--skip-auto1", action="store_true",
        help="Ne pas lancer l'Auto1 (suppose que MySQL est déjà rempli)",
    )
    p.add_argument(
        "--skip-auto2", action="store_true",
        help="Ne pas lancer l'Auto2 (datasets + notebooks)",
    )
    p.add_argument(
        "--skip-auto3", action="store_true",
        help="Ne pas lancer l'Auto3 (génération PDF)",
    )
    p.add_argument(
        "--skip-refinement", action="store_true",
        help="Ne pas lancer la phase de raffinement",
    )
    p.add_argument(
        "--max-notebooks", type=int, metavar="N",
        help="Limiter à N notebooks exécutés par passe (utile pour les tests)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Afficher l'état de la base sans lancer de traitement",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Forcer la régénération même si les données existent",
    )
    p.add_argument(
        "--enrich", action="store_true",
        help=(
            "Active la Phase 5 — enrichissement arrière-plan illimité : "
            "après Auto1→Auto2→Auto3, continue d'ajouter des problèmes et algos "
            "jusqu'à saturation complète de tous les types."
        ),
    )
    p.add_argument(
        "--enrich-max-problems", type=int, default=999, metavar="N", dest="enrich_max_problems",
        help="(enrich) Max problèmes par type — défaut 999 (= sans limite pratique)",
    )
    p.add_argument(
        "--enrich-max-algos", type=int, default=999, metavar="N", dest="enrich_max_algos",
        help="(enrich) Max algorithmes par problème — défaut 999 (= sans limite pratique)",
    )
    p.add_argument(
        "--enrich-patience", type=int, default=3, metavar="N", dest="enrich_patience",
        help="(enrich) Rounds sans résultat avant saturation (défaut: 3)",
    )
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()
    run_orchestrator(
        catalogue_path      = args.catalogue,
        interval_sec        = args.interval,
        once                = args.once,
        skip_auto1          = args.skip_auto1,
        skip_auto2          = args.skip_auto2,
        skip_auto3          = args.skip_auto3,
        skip_refinement     = args.skip_refinement,
        max_notebooks       = args.max_notebooks,
        dry_run             = args.dry_run,
        force               = args.force,
        enrich              = args.enrich,
        enrich_max_problems = args.enrich_max_problems,
        enrich_max_algos    = args.enrich_max_algos,
        enrich_patience     = args.enrich_patience,
    )
