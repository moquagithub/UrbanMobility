"""
Orchestrateur principal du système de surveillance qualité.

Modes :
  once  — un seul scan, affiche le rapport, sort
  fix   — scan + tentative de réparation automatique des issues fixables
  watch — boucle infinie : scan → fix → attente → scan → …

Usage programmatique :
    from shared.quality.monitor import QualityMonitor
    m = QualityMonitor()
    report = m.run_once(fix=True)

Usage CLI :
    python -m shared.quality [--once | --watch | --fix]
    (voir __main__.py)
"""
from __future__ import annotations

import logging
import random
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("quality.monitor")


# Issue types dont la réparation n'implique AUCUN appel LLM (UPDATE de statut,
# kill de process, ou simple déclenchement de pipeline) — priorisées avant le
# tirage aléatoire des réparations LLM dans run_once().
_CHEAP_REPAIR_TYPES = {
    "compile_failed", "report_invalid", "report_stale",
    "no_data", "no_dataset", "dataset_no_anomalies",
    "execution_failed", "no_results", "stuck_running",
    "stale_notebook", "algo_count_mismatch", "zero_figures",
    "minio_file_missing", "no_algorithms", "duplicate_watch",
    "dataset_orphaned",
}

# Soupape anti-acharnement : au-delà de ce nombre d'échecs de réparation cumulés
# pour un même correctif (entity + issue_type), on cesse de le retenter à chaque
# cycle — un rewrite LLM que le modèle rate systématiquement (ex: réécrire un
# Gaussian Process GPy ou un DTW+graphe en numpy correct ET rapide) ne se
# débloquera pas en le relançant à l'identique ; ça brûle le quota LLM et évince
# du tirage les algos encore réparables. On les "gare" (révision manuelle) avec
# une petite proba de re-test pour ne pas les abandonner définitivement.
MAX_REPAIR_FAILURES = 12
RETEST_PROBABILITY = 0.1

# Nombre de réparations consécutives sans aucun provider joignable au-delà duquel
# on abandonne le cycle. Lors d'une coupure réseau, chaque réparation tente
# 3 correctifs × ~9 couples (provider, modèle) : insister sur 20 issues fait des
# centaines d'appels voués à l'échec avant de rendre la main.
_MAX_CONSECUTIVE_UNAVAILABLE = 3

# Plancher historique du ledger d'échecs. L'époque effective est calculée par
# _repair_ledger_epoch() ci-dessous et ne descend jamais sous cette date.
REPAIR_LEDGER_EPOCH = "2026-07-22 00:00:00"

# Fichier d'état : empreinte de stratégie de réparation -> date de sa première
# observation. Sert d'époque effective du ledger.
_LEDGER_STATE_FILE = Path(__file__).resolve().parents[2] / "logs" / "repair_strategy_epoch.json"


def _repair_strategy_fingerprint() -> str:
    """
    Empreinte de la stratégie de réparation courante.

    La soupape anti-acharnement ne doit compter que les échecs survenus sous la
    stratégie ACTUELLE : une entité peut avoir échoué 12 fois parce que la
    réparation lui demandait l'impossible, puis devenir triviale après amélioration
    (ex. l'arrivée de shared.algolib le 2026-07-28, qui remplace « réimplémente un
    processus gaussien en numpy » par une substitution d'import).

    Cette borne était jusqu'ici une date écrite en dur, à avancer manuellement « à
    chaque amélioration notable de repair.py ». Elle ne l'a pas été : les
    algorithmes que algolib venait précisément débloquer sont restés garés, et
    l'amélioration n'a jamais atteint les cas pour lesquels elle avait été écrite.
    Une étape manuelle dans une boucle censée être autonome finit toujours par être
    oubliée — on la supprime.

    L'empreinte couvre ce qui détermine réellement la qualité d'une réparation :
    les recettes de remplacement, le contrat d'environnement, et l'API exposée par
    algolib. Toute évolution de l'un des trois invalide le ledger d'elle-même.
    """
    import hashlib

    parts = []
    try:
        from shared.quality.repair import _FORBIDDEN_IMPORT_RECIPES
        parts.append(_FORBIDDEN_IMPORT_RECIPES)
    except Exception:
        pass
    try:
        from shared.validation.skeleton_contract import ENVIRONMENT_CONTRACT
        parts.append(ENVIRONMENT_CONTRACT)
    except Exception:
        pass
    try:
        import shared.algolib as _algolib
        parts.append(",".join(sorted(getattr(_algolib, "__all__", []))))
    except Exception:
        pass

    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]


def _repair_ledger_epoch() -> str:
    """
    Date à partir de laquelle compter les échecs, pour la stratégie courante.

    Première observation d'une empreinte donnée → l'instant présent devient
    l'époque : les correctifs améliorés repartent avec un compteur vierge, puis la
    soupape se réengage normalement après MAX_REPAIR_FAILURES échecs sous cette
    stratégie. En cas de problème d'accès au fichier d'état, on retombe sur le
    plancher historique plutôt que d'échouer — la soupape reste alors simplement
    plus permissive.
    """
    import json as _json
    from datetime import datetime

    fp = _repair_strategy_fingerprint()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        _LEDGER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        state = {}
        if _LEDGER_STATE_FILE.exists():
            state = _json.loads(_LEDGER_STATE_FILE.read_text(encoding="utf-8"))
        if fp not in state:
            state[fp] = now
            _LEDGER_STATE_FILE.write_text(_json.dumps(state, indent=2), encoding="utf-8")
            log.info("[REPAIR] Stratégie de réparation modifiée (empreinte %s) — "
                     "compteurs d'échecs remis à zéro, les correctifs garés sont "
                     "retentés", fp)
        return max(state[fp], REPAIR_LEDGER_EPOCH)
    except Exception as exc:
        log.warning("[REPAIR] époque du ledger illisible (%s) — repli sur %s",
                    exc, REPAIR_LEDGER_EPOCH)
        return REPAIR_LEDGER_EPOCH


class QualityMonitor:
    """Orchestre les scans de qualité, les réparations et le reporting."""

    def __init__(
        self,
        type_ids:     Optional[List[str]] = None,
        only_checks:  Optional[List[str]] = None,
        skip_checks:  Optional[List[str]] = None,
        watch_interval_sec: int = 300,   # 5 min entre chaque scan en mode watch
        max_fix_per_run: int = 20,       # limite de réparations auto par scan
    ):
        self.type_ids            = type_ids
        self.only_checks         = only_checks
        self.skip_checks         = skip_checks
        self.watch_interval_sec  = watch_interval_sec
        self.max_fix_per_run     = max_fix_per_run

    # ── Vérifications de disponibilité ───────────────────────────────────────

    def _check_infra(self) -> bool:
        """Vérifie que MySQL et MinIO sont disponibles. Bloque si non."""
        ok = True

        # MySQL
        try:
            from shared.db.connection import get_connection
            conn = get_connection()
            if conn:
                conn.close()
                log.debug("MySQL OK")
            else:
                log.error("MySQL indisponible — scan annulé")
                ok = False
        except Exception as e:
            log.error("MySQL erreur : %s", e)
            ok = False

        # MinIO (optionnel — si absent on skips les checks MinIO)
        try:
            from shared.storage.client import get_storage
            s = get_storage()
            if s:
                log.debug("MinIO OK")
            else:
                log.warning("MinIO indisponible — checks MinIO désactivés")
        except Exception:
            log.warning("MinIO non configuré — checks MinIO désactivés")

        return ok

    # ── Scan unique ───────────────────────────────────────────────────────────

    def run_once(self, fix: bool = False, print_report: bool = True) -> Dict:
        """
        Lance un scan complet et optionnellement répare les issues auto-fixables.
        Retourne un dict résumé.
        """
        from shared.llm.router import AllProvidersExhausted
        from shared.quality.checks import run_all_checks
        from shared.quality.repair import RepairUnavailable, repair_issue
        from shared.quality.reporter import (
            build_summary, compute_quality_score,
            print_report as do_print, save_issues_to_db, update_fix_result,
        )
        from shared.db.connection import get_connection

        scan_id = str(uuid.uuid4())
        started = datetime.now()
        log.info("── SCAN QUALITÉ %s ─────────────────────", scan_id[:8])

        if not self._check_infra():
            return {"scan_id": scan_id, "error": "infra_unavailable", "issues": 0}

        # ── Enregistrer le début du scan ──────────────────────────────────
        conn = get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO quality_scan_runs (id, started_at, mode)
                        VALUES (%s, %s, %s)
                    """, (scan_id, started, "fix" if fix else "once"))
                conn.commit()
            except Exception as e:
                log.warning("Impossible d'enregistrer le scan_run : %s", e)
            finally:
                conn.close()

        # ── Lancer les checks ─────────────────────────────────────────────
        t0 = time.perf_counter()
        issues = run_all_checks(
            type_ids=self.type_ids,
            only=self.only_checks,
            skip=self.skip_checks,
        )
        check_elapsed = time.perf_counter() - t0
        log.info("Scan terminé en %.1fs — %d issue(s) trouvée(s)", check_elapsed, len(issues))

        # ── Sauvegarder en base ───────────────────────────────────────────
        n_saved = save_issues_to_db(issues, scan_id)
        log.info("%d issues sauvegardées (scan_id=%s)", n_saved, scan_id[:8])

        # ── Afficher le rapport ───────────────────────────────────────────
        if print_report:
            do_print(issues, scan_id=scan_id[:8])

        # ── Réparations auto ──────────────────────────────────────────────
        # Traitement séquentiel par data_type_id : chaque type est réparé PUIS son
        # pipeline est déclenché et attend sa fin avant de passer au type suivant.
        # On ne traite jamais un deuxième type tant que le premier n'est pas finalisé.
        n_fixed = 0
        n_failed = 0
        n_skipped = 0            # réparations non tentées (LLM indisponible)
        quota_exhausted = False  # tous les quotas LLM épuisés → l'appelant met en pause
        quota_reason = ""
        llm_unreachable = False  # panne réseau : cycle abandonné, retry au suivant
        consecutive_unavailable = 0
        fixed_issues = []
        if fix:
            all_fixable = [i for i in issues if i.auto_fixable]
            # Les réparations "bon marché" (pas d'appel LLM : simple UPDATE de statut,
            # kill de process, ou déclenchement de pipeline) réussissent quasi toujours
            # et passent TOUJOURS en premier — sinon un backlog d'issues LLM difficiles
            # peut les évincer du tirage pendant des cycles entiers (observé : p3
            # compile_failed jamais tiré face à 61 issues algo qui échouaient en boucle).
            cheap = [i for i in all_fixable if i.issue_type in _CHEAP_REPAIR_TYPES]
            costly = [i for i in all_fixable if i.issue_type not in _CHEAP_REPAIR_TYPES]
            # Soupape anti-acharnement : écarter du tirage les correctifs LLM qui ont
            # déjà échoué un grand nombre de fois (voir MAX_REPAIR_FAILURES). Sans ça,
            # une poignée de cas irréductibles (GPy, DTW+graphe, Kalman natif…) tirés
            # au hasard chaque cycle consomment le budget de réparation et empêchent
            # les algos réellement réparables d'être tentés.
            fail_counts = _repair_failure_counts()
            active, parked = [], []
            for iss in costly:
                n = fail_counts.get((iss.entity_type, iss.entity_id, iss.issue_type), 0)
                if n >= MAX_REPAIR_FAILURES and random.random() > RETEST_PROBABILITY:
                    parked.append(iss)
                else:
                    active.append(iss)
            costly = active
            if parked:
                log.info("[REPAIR] %d issue(s) garées (≥%d échecs LLM cumulés — révision "
                         "manuelle) exclues du tirage ce cycle", len(parked), MAX_REPAIR_FAILURES)
            # Échantillon aléatoire du reste plutôt que les N premiers dans l'ordre du
            # scan : avec un backlog > max_fix_per_run, prendre systématiquement les
            # mêmes premières issues (ordre stable des checks/requêtes SQL) fait que si
            # ce sous-ensemble échoue à répétition, le reste n'est JAMAIS tenté.
            # max_fix_per_run borne le COÛT LLM d'un cycle. Les réparations bon marché
            # ne passent par aucun appel LLM (UPDATE de statut, déclenchement de
            # pipeline) : les décompter de ce budget revenait à faire payer aux
            # réparations d'algorithmes le prix d'un travail gratuit. Mesuré le
            # 2026-07-28 : 7 recompilations de rapports + 1 reset de notebook
            # consommaient 8 des 12 créneaux, ne laissant que 4 réparations LLM par
            # cycle — et ces recompilations reviennent à CHAQUE cycle, puisqu'un
            # rapport devient obsolète dès qu'un algorithme change. Le backlog
            # d'algorithmes était ainsi structurellement privé de budget.
            n_slots = self.max_fix_per_run
            if len(costly) > n_slots:
                costly = random.sample(costly, n_slots)
            # Garde-fou : les réparations bon marché sont gratuites en LLM mais pas en
            # temps. On les borne largement, et on le signale si la borne mord — une
            # troncature silencieuse se lirait comme « tout a été traité ».
            cheap_cap = self.max_fix_per_run * 4
            if len(cheap) > cheap_cap:
                log.info("[REPAIR] %d réparations bon marché disponibles, %d traitées "
                         "ce cycle (reste au cycle suivant)", len(cheap), cheap_cap)
                cheap = cheap[:cheap_cap]
            fixable = cheap + costly
            log.info("Tentative de réparation de %d issue(s) auto-fixable(s) sur %d au total",
                      len(fixable), len(all_fixable))

            # Retrouver les IDs en base pour mettre à jour fix_result
            db_ids = _fetch_scan_issue_ids(scan_id)  # {(entity_type,entity_id,issue_type): db_id}

            groups: "Dict[Optional[str], List]" = {}
            for iss in fixable:
                groups.setdefault(getattr(iss, "data_type_id", None), []).append(iss)

            for type_id, group_issues in groups.items():
                if quota_exhausted or llm_unreachable:
                    break
                log.info("[REPAIR] === Début type '%s' (%d issue(s)) ===", type_id, len(group_issues))
                type_fixed = []
                for iss in group_issues:
                    key = (iss.entity_type, iss.entity_id, iss.issue_type)
                    db_id = db_ids.get(key)
                    try:
                        success, detail = repair_issue(iss)
                    except AllProvidersExhausted as exc:
                        # Quota LLM épuisé : on interrompt net le cycle. Poursuivre
                        # marquerait en échec des dizaines d'issues jamais tentées,
                        # ce qui les ferait « garer » à tort par la soupape.
                        if db_id:
                            update_fix_result(db_id, False, str(exc)[:500], skipped=True)
                        n_skipped += 1
                        quota_exhausted = True
                        quota_reason = str(exc)
                        log.warning("[REPAIR] Quota LLM épuisé — cycle de réparation interrompu")
                        break
                    except RepairUnavailable as exc:
                        # Aucun provider n'a répondu pour CETTE réparation : on passe
                        # à la suivante sans compter d'échec.
                        if db_id:
                            update_fix_result(db_id, False, str(exc)[:500], skipped=True)
                        n_skipped += 1
                        consecutive_unavailable += 1
                        if consecutive_unavailable >= _MAX_CONSECUTIVE_UNAVAILABLE:
                            # Panne réseau totale : insister ferait N issues × 3 essais
                            # × 9 providers d'appels voués à l'échec (observé lors
                            # d'une coupure réseau le 2026-07-28). On abandonne le
                            # cycle ; le suivant retentera après l'intervalle normal.
                            llm_unreachable = True
                            log.warning("[REPAIR] %d réparations d'affilée sans provider joignable "
                                        "— cycle interrompu, nouvelle tentative au prochain scan",
                                        consecutive_unavailable)
                            break
                        continue
                    consecutive_unavailable = 0

                    if db_id:
                        update_fix_result(db_id, success, detail)
                    if success:
                        n_fixed += 1
                        fixed_issues.append(iss)
                        type_fixed.append(iss)
                    else:
                        n_failed += 1

                # Finaliser ce type (pipeline complet) avant de passer au suivant
                if type_id and type_fixed:
                    _trigger_pipelines_after_repair(type_fixed)
                log.info("[REPAIR] === Type '%s' finalisé — passage au suivant ===", type_id)

            log.info("Réparations : %d succès / %d échecs / %d non tentées (LLM indisponible)",
                     n_fixed, n_failed, n_skipped)

        # ── Clôturer le scan en base ──────────────────────────────────────
        elapsed = round(time.perf_counter() - t0, 2)
        summary = build_summary(issues)
        score   = compute_quality_score(issues)
        _close_scan_run(scan_id, summary, n_fixed, n_failed)

        return {
            "scan_id":     scan_id,
            "score":       score,
            "issues":      len(issues),
            "fixed":       n_fixed,
            "failed":      n_failed,
            "skipped":     n_skipped,
            "quota_exhausted": quota_exhausted,
            "quota_reason":    quota_reason,
            "elapsed_sec": elapsed,
            "summary":     summary,
        }

    # ── Mode watch (boucle continue) ──────────────────────────────────────────

    def watch(self, fix: bool = True, max_cycles: Optional[int] = None) -> None:
        """
        Boucle de surveillance continue. Ctrl+C pour arrêter.
        Chaque itération : scan → réparations → attente → scan → …

        Conçue pour tourner sans surveillance (nuit entière) :
          - une exception dans un scan ne tue pas la boucle (backoff exponentiel) ;
          - quota LLM épuisé → pause jusqu'au reset de minuit puis reprise ;
          - max_cycles borne le nombre d'itérations (None = infini).
        """
        log.info("Mode WATCH démarré (intervalle=%ds, fix=%s, max_cycles=%s)",
                 self.watch_interval_sec, fix, max_cycles or "∞")
        close_stale_scan_runs()

        cycle = 0
        consecutive_errors = 0
        try:
            while max_cycles is None or cycle < max_cycles:
                cycle += 1
                try:
                    result = self.run_once(fix=fix, print_report=True)
                    consecutive_errors = 0
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    # Un scan qui plante (coupure MySQL/MinIO, bug d'un check…) ne
                    # doit PAS arrêter la surveillance : sans ce filet, la boucle
                    # nocturne mourait à la première anomalie et plus rien n'était
                    # réparé jusqu'au matin.
                    consecutive_errors += 1
                    backoff = min(self.watch_interval_sec * 2 ** (consecutive_errors - 1), 3600)
                    log.exception("[WATCH] Scan #%d en erreur (%d d'affilée) — reprise dans %ds : %s",
                                  cycle, consecutive_errors, backoff, exc)
                    time.sleep(backoff)
                    continue

                if result.get("quota_exhausted"):
                    wait_sec = _seconds_until_midnight()
                    log.warning("[WATCH] Quota LLM épuisé — pause %dh%02dm jusqu'au reset de minuit",
                                wait_sec // 3600, (wait_sec % 3600) // 60)
                    time.sleep(wait_sec)
                    from shared.llm.router import reset_session
                    reset_session()
                    log.info("[WATCH] Reprise après reset quota.")
                    continue

                log.info(
                    "Prochain scan dans %ds (score=%s, issues=%d, réparées=%d)",
                    self.watch_interval_sec, result.get("score", "?"),
                    result.get("issues", 0), result.get("fixed", 0),
                )
                time.sleep(self.watch_interval_sec)
        except KeyboardInterrupt:
            log.info("Monitor arrêté par l'utilisateur.")
        finally:
            close_stale_scan_runs()

    # ── Rapport HTML ──────────────────────────────────────────────────────────

    def generate_html_report(self, scan_id: Optional[str] = None) -> str:
        """
        Génère un rapport HTML à partir du dernier scan (ou d'un scan_id donné).
        Retourne le HTML.
        """
        from shared.quality.reporter import to_html_report
        from shared.quality.checks import QualityIssue as QI

        issues = _load_issues_from_db(scan_id=scan_id)
        return to_html_report(issues, scan_id=scan_id)


# ── Helpers DB internes ───────────────────────────────────────────────────────

def _seconds_until_midnight(extra_minutes: int = 5) -> int:
    """Secondes jusqu'à minuit + extra_minutes (reset des quotas gratuits)."""
    from datetime import timedelta
    now = datetime.now()
    next_reset = (now + timedelta(days=1)).replace(
        hour=0, minute=extra_minutes, second=0, microsecond=0
    )
    return max(60, int((next_reset - now).total_seconds()))


def close_stale_scan_runs() -> int:
    """
    Clôture les scans laissés ouverts (finished_at NULL) par un processus tué.

    Sans ça, chaque arrêt brutal du --watch laissait une ligne quality_scan_runs
    éternellement « en cours » (observé : 2026-07-22 02:40 et 09:05), ce qui fausse
    le suivi et empêche de distinguer un scan réellement actif d'un cadavre.
    """
    from shared.db.connection import get_connection
    conn = get_connection()
    if not conn:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE quality_scan_runs
                SET finished_at = NOW(),
                    summary = COALESCE(summary, '{"aborted": true}')
                WHERE finished_at IS NULL
            """)
            n = cur.rowcount
        conn.commit()
        if n:
            log.info("[WATCH] %d scan(s) fantôme(s) clôturé(s) au démarrage", n)
        return n
    except Exception as exc:
        log.warning("Impossible de clôturer les scans fantômes : %s", exc)
        return 0
    finally:
        conn.close()


def _fetch_scan_issue_ids(scan_id: str) -> Dict:
    """Retourne un dict {(entity_type, entity_id, issue_type): db_id} pour le scan donné."""
    from shared.db.connection import get_connection
    conn = get_connection()
    if not conn:
        return {}
    result = {}
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                "SELECT id, entity_type, entity_id, issue_type FROM quality_issues WHERE scan_run_id=%s",
                (scan_id,)
            )
            for row in cur.fetchall():
                key = (row["entity_type"], row["entity_id"], row["issue_type"])
                result[key] = row["id"]
    finally:
        conn.close()
    return result


def _repair_failure_counts() -> Dict:
    """Nombre d'échecs de réparation cumulés (sur tout l'historique quality_issues)
    par (entity_type, entity_id, issue_type). Utilisé comme soupape anti-acharnement :
    un correctif que le LLM rate systématiquement finit par être écarté du tirage
    (voir MAX_REPAIR_FAILURES dans run_once)."""
    from shared.db.connection import get_connection
    conn = get_connection()
    if not conn:
        return {}
    result = {}
    try:
        with conn.cursor(dictionary=True) as cur:
            cur.execute(
                "SELECT entity_type, entity_id, issue_type, "
                "SUM(fix_result = 'failed') AS fails "
                "FROM quality_issues "
                "WHERE detected_at >= %s "
                "GROUP BY entity_type, entity_id, issue_type",
                (_repair_ledger_epoch(),),
            )
            for row in cur.fetchall():
                key = (row["entity_type"], row["entity_id"], row["issue_type"])
                result[key] = int(row["fails"] or 0)
    finally:
        conn.close()
    return result


def _close_scan_run(scan_id: str, summary: Dict, n_fixed: int, n_failed: int) -> None:
    """Met à jour la ligne quality_scan_runs avec les résultats finaux."""
    from shared.db.connection import get_connection
    import json as _json
    conn = get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE quality_scan_runs
                SET finished_at=%s, issues_found=%s, issues_fixed=%s,
                    issues_failed=%s, summary=%s
                WHERE id=%s
            """, (
                datetime.now(),
                summary.get("total", 0),
                n_fixed,
                n_failed,
                _json.dumps(summary, ensure_ascii=False),
                scan_id,
            ))
        conn.commit()
    finally:
        conn.close()


# ── Déclenchement pipelines post-repair ──────────────────────────────────────

# Niveau de pipeline requis par type d'issue (du plus complet au plus ciblé)
_ISSUE_TO_PIPELINE = {
    "no_algorithms":                 "auto1",      # recréer les algos complets
    "missing_required_columns":      "auto1_s4",   # recréer dataset → auto2 → auto3
    "no_data":                       "auto1_s4",   # dataset vide → régénérer
    "no_dataset":                    "auto1_s4",   # problème sans dataset → générer
    "dataset_no_anomalies":          "auto1_s4",   # dataset sans anomalies injectées → régénérer
    "skeleton_syntax_error":         "auto2",      # skeleton corrigé → reconstruire notebook
    "missing_skeleton":              "auto2",
    "missing_is_anomaly_assignment": "auto2",
    "forbidden_import":              "auto2",
    "column_mismatch":               "auto2",
    "algo_execution_error":          "auto2",      # skeleton corrigé → reconstruire + ré-exécuter le notebook
    "algo_miscalibrated":            "auto2",
    "stale_notebook":                "auto2",
    "algo_count_mismatch":           "auto2",
    "notebook:minio_missing":        "auto2",
    "execution_failed":              "auto2_s3",   # ré-exécuter le notebook existant
    "no_results":                    "auto2_s3",   # exécuté mais 0 résultat extrait → ré-exécuter
    "stuck_running":                 "auto2_s3",   # bloqué en 'running' → ré-exécuter
    "minio_file_missing":            "auto2_s3",
    # Figures absentes → reconstruction complète S2+S3 pour régénérer les données sources dans MinIO
    "zero_figures":                  "auto2",
    # Rapport PDF en échec de compilation / invalide / obsolète → recompiler (Auto3)
    "compile_failed":                "auto3",
    "report_invalid":                "auto3",
    "report_stale":                  "auto3",
}

# Ordre de priorité : index bas = pipeline le plus complet (gagne sur les autres)
_PIPELINE_PRIORITY = ["auto1", "auto1_s4", "auto2", "auto2_s3", "auto3"]


def _trigger_pipelines_after_repair(fixed_issues: List) -> None:
    """
    Après un cycle de réparations, déclenche Auto1/Auto2/Auto3 pour chaque
    data_type_id affecté, selon le niveau de pipeline requis par les issues réparées.
    """
    # Déterminer le niveau de pipeline le plus complet pour chaque type
    type_pipeline: Dict[str, str] = {}
    for iss in fixed_issues:
        tid = getattr(iss, "data_type_id", None)
        if not tid:
            continue
        level = _ISSUE_TO_PIPELINE.get(iss.issue_type)
        if not level:
            continue
        current = type_pipeline.get(tid)
        if current is None:
            type_pipeline[tid] = level
        elif _PIPELINE_PRIORITY.index(level) < _PIPELINE_PRIORITY.index(current):
            type_pipeline[tid] = level  # prend le plus complet

    if not type_pipeline:
        return

    log.info("[PIPELINE] Déclenchement post-repair pour %d type(s) : %s",
             len(type_pipeline), {k: v for k, v in type_pipeline.items()})

    for type_id, level in type_pipeline.items():
        _run_pipeline_for_type(type_id, level)


def _run_pipeline_for_type(type_id: str, level: str) -> None:
    """
    Exécute le pipeline de bout en bout pour un type donné,
    en partant du niveau indiqué (auto1 | auto1_s4 | auto2 | auto2_s3).
    """
    log.info("[PIPELINE] %s — démarrage niveau=%s", type_id, level)

    try:
        # ── Auto1 complet (si no_algorithms) ─────────────────────────────
        if level == "auto1":
            from automatisation_1.runner import run as auto1_run
            from pathlib import Path as _Path
            import os as _os
            _catalogue = _Path(_os.path.dirname(__file__)).parent.parent / "data" / "example_catalogue.xlsx"
            log.info("[PIPELINE] %s → Auto1 complet (catalogue=%s)", type_id, _catalogue.name)
            auto1_run(_catalogue, type_filter=type_id, force=True)
            level = "auto2"  # enchaîner

        # ── Auto1 S4 seulement (si missing_required_columns) ─────────────
        elif level == "auto1_s4":
            from automatisation_1.runner import run as auto1_run
            from pathlib import Path as _Path
            import os as _os
            _catalogue = _Path(_os.path.dirname(__file__)).parent.parent / "data" / "example_catalogue.xlsx"
            log.info("[PIPELINE] %s → Auto1 S4 (datasets, catalogue=%s)", type_id, _catalogue.name)
            auto1_run(
                _catalogue, type_filter=type_id, force=True,
                skip_s1=True, skip_s1b=True, skip_s2=True, skip_s3=True,
            )
            level = "auto2"  # enchaîner

        # ── Auto2 complet S2+S3+S5 (notebooks reconstruits) ───────────────
        # S6 est sauté ici : c'est un notebook + une explication LaTeX générés
        # par LLM PAR ALGORITHME (1 appel LLM chacun — ~80+ pour traces_gps).
        # Aucune des issues mappées vers "auto2" (skeleton, exécution, calibrage,
        # figures...) ne concerne cet artefact — seulement le skeleton et le
        # notebook de COMPARAISON (S2/S3). Avec force=True, S6 ignorait son
        # propre cache "déjà dans MinIO, skip" et régénérait TOUS les algos à
        # chaque cycle de réparation (10+ minutes rien que pour S6, à chaque
        # scan --watch) sans que ce soit jamais nécessaire pour la réparation.
        if level == "auto2":
            from automatisation_2.runner import run_pipeline as auto2_run
            log.info("[PIPELINE] %s → Auto2 S2+S3+S5 (S6 sauté — non requis par ces issues)", type_id)
            auto2_run(type_ids=[type_id], force=True, skip_s1=True, skip_s6=True)
            level = "auto3"  # enchaîner

        # ── Auto2 S3 seulement (ré-exécuter notebooks existants) ──────────
        elif level == "auto2_s3":
            from automatisation_2.runner import run_pipeline as auto2_run
            log.info("[PIPELINE] %s → Auto2 S3 (ré-exécution)", type_id)
            auto2_run(
                type_ids=[type_id], force=True,
                skip_s1=True, skip_s2=True, skip_s4=True, skip_s5=True, skip_s6=True,
            )
            level = "auto3"  # enchaîner

        # ── Auto3 — toujours en dernier ────────────────────────────────────
        if level == "auto3":
            from automatisation_3.runner import run_pipeline as auto3_run
            log.info("[PIPELINE] %s → Auto3 PDF", type_id)
            auto3_run(type_ids=[type_id], force=True)

        log.info("[PIPELINE] %s — pipeline terminé ✓", type_id)

    except Exception as exc:
        log.error("[PIPELINE] %s — erreur : %s", type_id, exc)


def _load_issues_from_db(
    scan_id: Optional[str] = None,
    limit: int = 1000,
) -> List:
    """Charge les issues depuis la DB pour un scan_id donné (ou le dernier scan)."""
    from shared.db.connection import get_connection
    from shared.quality.checks import QualityIssue
    conn = get_connection()
    if not conn:
        return []
    issues = []
    try:
        with conn.cursor(dictionary=True) as cur:
            if scan_id:
                cur.execute(
                    "SELECT * FROM quality_issues WHERE scan_run_id=%s LIMIT %s",
                    (scan_id, limit)
                )
            else:
                # dernier scan
                cur.execute(
                    "SELECT * FROM quality_issues ORDER BY detected_at DESC LIMIT %s",
                    (limit,)
                )
            for row in cur.fetchall():
                issues.append(QualityIssue(
                    entity_type=row["entity_type"],
                    entity_id=row["entity_id"],
                    entity_key=row["entity_key"],
                    data_type_id=row["data_type_id"],
                    issue_type=row["issue_type"],
                    severity=row["severity"],
                    description=row["description"] or "",
                    auto_fixable=bool(row["auto_fixable"]),
                ))
    finally:
        conn.close()
    return issues
