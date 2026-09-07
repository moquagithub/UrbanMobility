"""
Refiner — Automatisation 2 en mode raffinement.

Détecte les types de données dont le statut est 'notebooks_done' mais pour lesquels
l'Automatisation 1 a ajouté de nouveaux algorithmes depuis la dernière génération de notebooks.

Si nouveaux algorithmes → génère les notebooks manquants + les exécute.
Si aucun nouveau algo → raffinement qualitatif : ré-exécute les notebooks en erreur/timeout.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from shared.db.connection import get_connection

log = logging.getLogger("auto2.refiner")


def find_types_needing_refinement() -> List[Dict]:
    """
    Retourne les types avec 'notebooks_done' qui ont de nouveaux algorithmes
    sans notebook associé, OU des notebooks en erreur/timeout à re-exécuter.

    Chaque dict contient : type_id, type_name, new_algo_ids, failed_nb_ids
    """
    conn = get_connection()
    if not conn:
        return []

    results = []
    try:
        with conn.cursor(dictionary=True) as cur:
            # Types notebooks_done
            cur.execute("""
                SELECT id, name FROM data_types
                WHERE processing_status = 'notebooks_done'
                ORDER BY name
            """)
            types = cur.fetchall() or []

            for dt in types:
                type_id   = dt["id"]
                type_name = dt["name"]
                new_algo_ids = []
                failed_nb_ids = []

                # Algorithmes sans notebook pour ce type
                cur.execute("""
                    SELECT a.id, a.algorithm_key, a.created_at
                    FROM algorithms a
                    JOIN problems p ON a.problem_id = p.id
                    WHERE p.data_type_id = %s
                      AND NOT EXISTS (
                          SELECT 1 FROM notebooks nb
                          WHERE nb.algorithm_id = a.id
                            AND nb.data_type_id = %s
                      )
                    ORDER BY a.created_at DESC
                """, (type_id, type_id))
                missing = cur.fetchall() or []
                new_algo_ids = [r["id"] for r in missing]

                # Notebooks en erreur ou timeout
                cur.execute("""
                    SELECT id, notebook_key
                    FROM notebooks
                    WHERE data_type_id = %s AND status IN ('error', 'timeout', 'failed')
                    ORDER BY updated_at DESC
                    LIMIT 20
                """, (type_id,))
                failed = cur.fetchall() or []
                failed_nb_ids = [r["id"] for r in failed]

                if new_algo_ids or failed_nb_ids:
                    results.append({
                        "type_id":       type_id,
                        "type_name":     type_name,
                        "new_algo_ids":  new_algo_ids,
                        "failed_nb_ids": failed_nb_ids,
                    })
                    log.info(
                        "[REFINER] %s — %d nouveaux algos, %d notebooks en erreur",
                        type_name, len(new_algo_ids), len(failed_nb_ids),
                    )

    except Exception as exc:
        log.warning("[REFINER] Erreur find_types : %s", exc)
    finally:
        conn.close()

    return results


def run_refinement(
    types_to_refine: List[Dict],
    max_notebooks: Optional[int] = None,
) -> Dict[str, int]:
    """
    Lance le raffinement pour les types détectés.

    Args:
        types_to_refine : résultat de find_types_needing_refinement()
        max_notebooks   : limite de notebooks à exécuter par passe

    Retourne {"new_notebooks": N, "retried": N, "types_updated": N}
    """
    from automatisation_2.steps.s2_notebooks import run as s2_run
    from automatisation_2.steps.s3_execution import run as s3_run
    from automatisation_2.steps.s4_results   import run as s4_run

    if not types_to_refine:
        log.info("[REFINER] Rien à raffiner.")
        return {"new_notebooks": 0, "retried": 0, "types_updated": 0}

    stats = {"new_notebooks": 0, "retried": 0, "types_updated": 0}
    type_ids = [t["type_id"] for t in types_to_refine]

    log.info("[REFINER] Raffinement pour %d type(s) : %s", len(type_ids), type_ids)

    # S2 — générer les notebooks manquants (force=False → skip les existants)
    s2_result = s2_run(type_ids=type_ids, force=False)
    stats["new_notebooks"] = s2_result.get("ok", 0)
    log.info("[REFINER] S2 : %s", s2_result)

    # Remettre les notebooks en erreur à 'generated' pour les ré-exécuter
    retried = _reset_failed_notebooks(types_to_refine)
    stats["retried"] = retried

    # S3 — exécuter tous les notebooks pending (nouveaux + retried)
    s3_result = s3_run(type_ids=type_ids, max_notebooks=max_notebooks)
    log.info("[REFINER] S3 : %s", s3_result)

    # S4 — ré-agréger les métriques
    s4_result = s4_run(type_ids=type_ids)
    stats["types_updated"] = s4_result.get("done", 0)
    log.info("[REFINER] S4 : %s", s4_result)

    return stats


def _reset_failed_notebooks(types_to_refine: List[Dict]) -> int:
    """Remet à 'generated' les notebooks en erreur/timeout pour les ré-exécuter."""
    conn = get_connection()
    if not conn:
        return 0
    total = 0
    try:
        with conn.cursor() as cur:
            for t in types_to_refine:
                failed_ids = t.get("failed_nb_ids", [])
                if not failed_ids:
                    continue
                fmt = ",".join(["%s"] * len(failed_ids))
                cur.execute(
                    f"""
                    UPDATE notebooks
                    SET status='generated', error_message=NULL, updated_at=NOW()
                    WHERE id IN ({fmt})
                    """,
                    tuple(failed_ids),
                )
                total += cur.rowcount
        conn.commit()
        if total:
            log.info("[REFINER] %d notebooks remis à 'generated' pour ré-exécution", total)
    except Exception as exc:
        log.warning("[REFINER] reset_failed erreur : %s", exc)
    finally:
        conn.close()
    return total
