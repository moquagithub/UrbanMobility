"""
Checks de qualité et de cohérence des données du pipeline.

Chaque fonction `check_*` scanne une catégorie de problèmes et retourne
une liste d'objets QualityIssue. Les checks sont indépendants et peuvent
être lancés séparément ou tous ensemble via run_all_checks().

Niveaux de sévérité :
  critical — bloque l'utilisation des données (skeleton vide, dataset absent)
  high     — résultat incorrect ou inutilisable (SyntaxError, minio manquant)
  medium   — qualité dégradée (colonnes manquantes, 0 figures)
  low      — recommandation d'amélioration (description trop courte)
"""
from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

log = logging.getLogger("quality.checks")


# ── Modèle de données ────────────────────────────────────────────────────────

@dataclass
class QualityIssue:
    entity_type:  str           # 'algorithm' | 'problem' | 'dataset' | 'notebook' | 'figure' | 'data_type'
    entity_id:    Optional[int]
    entity_key:   Optional[str]
    data_type_id: Optional[str]
    issue_type:   str           # identifiant machine de l'issue
    severity:     str           # critical | high | medium | low
    description:  str           # message lisible
    auto_fixable: bool = False
    fix_hint:     str  = ""     # contexte pour la réparation auto

    def as_dict(self) -> Dict:
        return {
            "entity_type":  self.entity_type,
            "entity_id":    self.entity_id,
            "entity_key":   self.entity_key,
            "data_type_id": self.data_type_id,
            "issue_type":   self.issue_type,
            "severity":     self.severity,
            "description":  self.description,
            "auto_fixable": self.auto_fixable,
        }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_conn():
    from shared.db.connection import get_connection
    return get_connection()


def _get_storage():
    try:
        from shared.storage.client import get_storage
        return get_storage()
    except Exception:
        return None


def _minio_exists(store, bucket: str, key: str) -> bool:
    """Vérifie qu'un objet existe dans MinIO sans lever d'exception."""
    try:
        return store.exists(bucket, key)
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════════════════
# BLOC 1 — Intégrité MySQL (algorithmes)
# ══════════════════════════════════════════════════════════════════════════════

def check_algorithms_missing_skeleton(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """CRITICAL — algorithmes sans python_skeleton."""
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT a.id, a.algorithm_key, a.name, dt.id AS type_id
                FROM algorithms a
                JOIN problems p ON a.problem_id = p.id
                JOIN data_types dt ON p.data_type_id = dt.id
                WHERE (a.python_skeleton IS NULL OR TRIM(a.python_skeleton) = '')
            """
            params = []
            if type_ids:
                sql += " AND dt.id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                issues.append(QualityIssue(
                    entity_type="algorithm",
                    entity_id=row["id"],
                    entity_key=row["algorithm_key"],
                    data_type_id=row["type_id"],
                    issue_type="missing_skeleton",
                    severity="critical",
                    description=f"Algorithme '{row['name']}' ({row['algorithm_key']}) : python_skeleton vide ou absent.",
                    auto_fixable=True,
                    fix_hint=f"Régénérer le skeleton via LLM pour algo_id={row['id']}",
                ))
    finally:
        conn.close()
    return issues


def check_algorithms_skeleton_syntax(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """HIGH — algorithmes dont le skeleton a une SyntaxError Python."""
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT a.id, a.algorithm_key, a.name, a.python_skeleton, dt.id AS type_id
                FROM algorithms a
                JOIN problems p ON a.problem_id = p.id
                JOIN data_types dt ON p.data_type_id = dt.id
                WHERE a.python_skeleton IS NOT NULL AND TRIM(a.python_skeleton) != ''
            """
            params = []
            if type_ids:
                sql += " AND dt.id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                skel = (row["python_skeleton"] or "").strip()
                try:
                    ast.parse(skel)
                except SyntaxError as e:
                    issues.append(QualityIssue(
                        entity_type="algorithm",
                        entity_id=row["id"],
                        entity_key=row["algorithm_key"],
                        data_type_id=row["type_id"],
                        issue_type="skeleton_syntax_error",
                        severity="high",
                        description=f"SyntaxError dans le skeleton de '{row['name']}' : {e.msg} (ligne {e.lineno})",
                        auto_fixable=True,
                        fix_hint=f"SyntaxError: {e.msg} at line {e.lineno}. Skeleton debut: {skel[:200]}",
                    ))
    finally:
        conn.close()
    return issues


def check_algorithms_missing_is_anomaly(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """HIGH — skeletons qui n'assignent pas df['is_anomaly']."""
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT a.id, a.algorithm_key, a.name, a.python_skeleton, dt.id AS type_id
                FROM algorithms a
                JOIN problems p ON a.problem_id = p.id
                JOIN data_types dt ON p.data_type_id = dt.id
                WHERE a.python_skeleton IS NOT NULL AND TRIM(a.python_skeleton) != ''
            """
            params = []
            if type_ids:
                sql += " AND dt.id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                skel = (row["python_skeleton"] or "").strip()
                has_is_anomaly = bool(
                    re.search(r"is_anomaly['\"]?\]?\s*=", skel) or
                    re.search(r"is_anomaly\s*=", skel) or
                    re.search(r"assign\s*\(.*is_anomaly", skel)
                )
                if not has_is_anomaly:
                    issues.append(QualityIssue(
                        entity_type="algorithm",
                        entity_id=row["id"],
                        entity_key=row["algorithm_key"],
                        data_type_id=row["type_id"],
                        issue_type="missing_is_anomaly_assignment",
                        severity="high",
                        description=f"'{row['name']}' : skeleton ne définit pas df['is_anomaly'].",
                        auto_fixable=True,
                        fix_hint="Ajouter l'assignation df['is_anomaly'] = ... dans la fonction.",
                    ))
    finally:
        conn.close()
    return issues


def check_algorithms_forbidden_imports(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """
    MEDIUM — skeletons qui importent des bibliothèques non autorisées.

    Liste BLANCHE (pas noire) : c'est la politique documentée dans tous les prompts
    de réparation LLM ("Bibliothèques autorisées : numpy, pandas, scipy, sklearn,
    statsmodels, matplotlib"). L'ancienne version de ce check utilisait une liste
    NOIRE incomplète (tensorflow, keras, theano...) qui ne détectait aucun des
    imports natifs C fragiles réellement rencontrés en pratique (rtree, pyproj,
    shapely, networkx, numba, filterpy, pykalman, cvxpy, torch, geopandas...) —
    certains d'entre eux causent des crashs natifs non-catchables (segfault,
    corruption mémoire glibc) qui tuent le kernel Jupyter entier, pas juste
    l'algorithme concerné.
    """
    _ALLOWED_STDLIB = {
        "os", "sys", "re", "json", "time", "math", "itertools", "collections",
        "functools", "warnings", "datetime", "typing", "dataclasses", "abc",
        "copy", "random", "string", "textwrap", "pickle", "tempfile", "pathlib",
    }
    _ALLOWED_THIRD_PARTY = {
        "numpy", "pandas", "scipy", "sklearn", "statsmodels", "matplotlib", "seaborn",
        # shared.algolib fournit, en numpy/scipy/sklearn purs et testés, les primitives
        # que les bibliothèques interdites apportaient (DTW, Kalman, ondelettes,
        # graphes, processus gaussien, géodésie). L'autoriser transforme une
        # réécriture que les modèles échouaient systématiquement en simple
        # substitution d'import — voir shared/algolib/__init__.py.
        "shared",
    }
    _ALLOWED = _ALLOWED_STDLIB | _ALLOWED_THIRD_PARTY

    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT a.id, a.algorithm_key, a.name, a.python_skeleton, dt.id AS type_id
                FROM algorithms a
                JOIN problems p ON a.problem_id = p.id
                JOIN data_types dt ON p.data_type_id = dt.id
                WHERE a.python_skeleton IS NOT NULL AND TRIM(a.python_skeleton) != ''
            """
            params = []
            if type_ids:
                sql += " AND dt.id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                skel = row["python_skeleton"] or ""
                bad_imports = []
                try:
                    tree = ast.parse(skel)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                pkg = alias.name.split(".")[0]
                                if pkg and pkg not in _ALLOWED:
                                    bad_imports.append(pkg)
                        elif isinstance(node, ast.ImportFrom):
                            if node.module and node.level == 0:
                                pkg = node.module.split(".")[0]
                                if pkg and pkg not in _ALLOWED:
                                    bad_imports.append(pkg)
                except SyntaxError:
                    pass
                if bad_imports:
                    unique = list(dict.fromkeys(bad_imports))
                    issues.append(QualityIssue(
                        entity_type="algorithm",
                        entity_id=row["id"],
                        entity_key=row["algorithm_key"],
                        data_type_id=row["type_id"],
                        issue_type="forbidden_import",
                        severity="medium",
                        description=f"'{row['name']}' : imports non disponibles : {unique}",
                        auto_fixable=True,
                        fix_hint=f"Remplacer les imports interdits {unique} par des alternatives disponibles.",
                    ))
    finally:
        conn.close()
    return issues


# ══════════════════════════════════════════════════════════════════════════════
# BLOC 2 — Intégrité MySQL (problèmes / types)
# ══════════════════════════════════════════════════════════════════════════════

def check_problems_without_algorithms(type_ids: Optional[List[str]] = None,
                                       min_algos: int = 1) -> List[QualityIssue]:
    """HIGH — problèmes avec moins de min_algos algorithmes."""
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT p.id, p.problem_key, p.title, dt.id AS type_id,
                       COUNT(a.id) AS n_algos
                FROM problems p
                JOIN data_types dt ON p.data_type_id = dt.id
                LEFT JOIN algorithms a ON a.problem_id = p.id
                GROUP BY p.id, p.problem_key, p.title, dt.id
                HAVING n_algos < %s
            """
            params = [min_algos]
            if type_ids:
                sql = sql.replace(
                    "GROUP BY",
                    "WHERE dt.id IN (%s) GROUP BY" % ",".join(["%s"] * len(type_ids))
                )
                params = type_ids + [min_algos]
            cur.execute(sql, params)
            for row in cur.fetchall():
                issues.append(QualityIssue(
                    entity_type="problem",
                    entity_id=row["id"],
                    entity_key=row["problem_key"],
                    data_type_id=row["type_id"],
                    issue_type="no_algorithms",
                    severity="high",
                    description=(
                        f"Problème '{row['title']}' ({row['problem_key']}) "
                        f"n'a que {row['n_algos']} algorithme(s) (minimum {min_algos})."
                    ),
                    auto_fixable=True,
                    fix_hint=f"Générer des algorithmes via Auto1 S3 pour problem_id={row['id']}",
                ))
    finally:
        conn.close()
    return issues


def check_data_types_without_problems(type_ids: Optional[List[str]] = None,
                                       min_problems: int = 1) -> List[QualityIssue]:
    """MEDIUM — types de données sans problèmes définis."""
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT dt.id AS type_id, dt.name, COUNT(p.id) AS n_problems
                FROM data_types dt
                LEFT JOIN problems p ON p.data_type_id = dt.id
                GROUP BY dt.id, dt.name
                HAVING n_problems < %s
            """
            params = [min_problems]
            if type_ids:
                sql = sql.replace("GROUP BY", "WHERE dt.id IN (%s) GROUP BY" % ",".join(["%s"] * len(type_ids)))
                params = type_ids + [min_problems]
            cur.execute(sql, params)
            for row in cur.fetchall():
                issues.append(QualityIssue(
                    entity_type="data_type",
                    entity_id=None,
                    entity_key=row["type_id"],
                    data_type_id=row["type_id"],
                    issue_type="no_problems",
                    severity="medium",
                    description=f"Type '{row['name']}' n'a que {row['n_problems']} problème(s).",
                    auto_fixable=True,
                    fix_hint=f"Lancer Auto1 S2 pour data_type_id={row['type_id']}",
                ))
    finally:
        conn.close()
    return issues


# ══════════════════════════════════════════════════════════════════════════════
# BLOC 3 — Datasets
# ══════════════════════════════════════════════════════════════════════════════

def check_datasets_without_data(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """CRITICAL — datasets sans data_json ET sans file_path."""
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT d.id, d.dataset_key, d.data_type_id
                FROM datasets d
                WHERE (d.data_json IS NULL OR LENGTH(d.data_json) < 10)
                  AND (d.file_path IS NULL OR TRIM(d.file_path) = '')
            """ + _EXCLUDE_ORPHANS
            params = []
            if type_ids:
                sql += " AND d.data_type_id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                issues.append(QualityIssue(
                    entity_type="dataset",
                    entity_id=row["id"],
                    entity_key=row["dataset_key"],
                    data_type_id=row["data_type_id"],
                    issue_type="no_data",
                    severity="critical",
                    description=f"Dataset '{row['dataset_key']}' : aucune donnée (data_json=NULL et file_path=NULL).",
                    auto_fixable=True,
                    fix_hint=f"Relancer Auto1 S4 pour dataset_key={row['dataset_key']} type_id={row['data_type_id']}",
                ))
    finally:
        conn.close()
    return issues


# Les datasets orphelins (problem_id NULL) sont des reliquats de l'ancien pipeline
# monolithique supprimé le 2026-07-09 : plus aucun problème ni algorithme ne les
# référence, ils ne sont ni régénérés ni consommés par Auto1/2/3. Ils suivaient
# l'ancienne convention de colonnes (vehicle_id/latitude/longitude au lieu de
# sensor_id/value/anomaly_flag), si bien que check_datasets_schema_coherence les
# signalait en HIGH non-auto-fixable — 4 issues fantômes indéboulonnables sur
# traces_gps, qui plafonnaient le score sans qu'aucune réparation ne puisse
# jamais les résoudre. Ils sont exclus des checks de contenu et traités à part
# par check_datasets_orphaned.
_EXCLUDE_ORPHANS = " AND d.problem_id IS NOT NULL"


def check_datasets_orphaned(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """LOW — datasets qui ne sont rattachés à aucun problème (problem_id NULL)."""
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT d.id, d.dataset_key, d.data_type_id
                FROM datasets d
                WHERE d.problem_id IS NULL
            """
            params = []
            if type_ids:
                sql += " AND d.data_type_id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                issues.append(QualityIssue(
                    entity_type="dataset",
                    entity_id=row["id"],
                    entity_key=row["dataset_key"],
                    data_type_id=row["data_type_id"],
                    issue_type="dataset_orphaned",
                    severity="low",
                    description=(
                        f"Dataset '{row['dataset_key']}' (#{row['id']}) n'est rattaché à "
                        "aucun problème — reliquat de l'ancien pipeline, jamais consommé."
                    ),
                    auto_fixable=True,
                    fix_hint="Supprimer la ligne datasets orpheline.",
                ))
    finally:
        conn.close()
    return issues


def check_datasets_schema_coherence(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """HIGH — datasets dont les colonnes ne contiennent pas les colonnes obligatoires."""
    _REQUIRED = {"timestamp", "sensor_id", "value", "anomaly_flag"}
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT d.id, d.dataset_key, d.data_type_id,
                       LEFT(d.data_json, 2000) AS data_sample
                FROM datasets d
                WHERE d.data_json IS NOT NULL AND LENGTH(d.data_json) > 10
            """ + _EXCLUDE_ORPHANS
            params = []
            if type_ids:
                sql += " AND d.data_type_id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                try:
                    sample = json.loads(row["data_sample"] + ("]}" if not row["data_sample"].rstrip().endswith("]") else ""))
                except Exception:
                    try:
                        # essai de parsing partiel — extraire les clés du 1er objet
                        first_obj_match = re.search(r'\{([^}]+)\}', row["data_sample"])
                        if not first_obj_match:
                            continue
                        sample = json.loads("{" + first_obj_match.group(1) + "}")
                        columns = set(sample.keys())
                    except Exception:
                        continue
                else:
                    if isinstance(sample, list) and sample:
                        columns = set(sample[0].keys()) if isinstance(sample[0], dict) else set()
                    elif isinstance(sample, dict):
                        # peut être un dict de listes (orient=records)
                        columns = set(sample.keys())
                    else:
                        continue

                missing = _REQUIRED - columns
                if missing:
                    issues.append(QualityIssue(
                        entity_type="dataset",
                        entity_id=row["id"],
                        entity_key=row["dataset_key"],
                        data_type_id=row["data_type_id"],
                        issue_type="missing_required_columns",
                        severity="high",
                        description=(
                            f"Dataset '{row['dataset_key']}' : colonnes obligatoires absentes : {missing}. "
                            f"Colonnes présentes : {list(columns)[:8]}"
                        ),
                        auto_fixable=False,
                        fix_hint="Régénérer le dataset via Auto2 S1 avec un prompt précisant les colonnes requises.",
                    ))
    finally:
        conn.close()
    return issues


# ══════════════════════════════════════════════════════════════════════════════
# BLOC 4 — Notebooks
# ══════════════════════════════════════════════════════════════════════════════

def check_notebooks_failed(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """HIGH — notebooks avec status='failed'."""
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT n.id, n.notebook_key, n.data_type_id, n.error_message,
                       n.execution_time_sec
                FROM notebooks n
                WHERE n.status = 'failed'
            """
            params = []
            if type_ids:
                sql += " AND n.data_type_id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                err = (row["error_message"] or "")[:120]
                issues.append(QualityIssue(
                    entity_type="notebook",
                    entity_id=row["id"],
                    entity_key=row["notebook_key"],
                    data_type_id=row["data_type_id"],
                    issue_type="execution_failed",
                    severity="high",
                    description=f"Notebook '{row['notebook_key']}' en échec : {err}",
                    auto_fixable=True,
                    fix_hint=f"Remettre status='generated' pour relancer S3. notebook_id={row['id']}",
                ))
    finally:
        conn.close()
    return issues


def check_algorithm_execution_errors(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """
    HIGH — algorithme qui échoue à l'intérieur d'un notebook de comparaison par ailleurs
    'executed' (status global du notebook = succès, car chaque cellule algo est protégée
    par son propre try/except — voir notebook_builder.py). Ces échecs par-algorithme ne
    sont jamais catchés par check_notebooks_failed (qui ne regarde que status='failed'
    au niveau du notebook entier) : sans ce check, ils restent invisibles indéfiniment.
    """
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT nr.algorithm_id, nr.notebook_id, nr.final_metrics_json,
                       n.notebook_key, n.data_type_id,
                       a.name AS algo_name, a.algorithm_key
                FROM notebook_results nr
                JOIN (
                    SELECT notebook_id, algorithm_id, MAX(id) AS max_id
                    FROM notebook_results
                    WHERE algorithm_id IS NOT NULL AND algorithm_id > 0
                    GROUP BY notebook_id, algorithm_id
                ) latest ON nr.id = latest.max_id
                JOIN notebooks n   ON n.id = nr.notebook_id
                JOIN algorithms a  ON a.id = nr.algorithm_id
                WHERE JSON_EXTRACT(nr.final_metrics_json, '$.error') IS NOT NULL
            """
            params = []
            if type_ids:
                sql += " AND n.data_type_id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                try:
                    metrics = row["final_metrics_json"]
                    metrics = json.loads(metrics) if isinstance(metrics, str) else metrics
                    err = str(metrics.get("error", ""))[:300]
                except Exception:
                    err = ""
                issues.append(QualityIssue(
                    entity_type="algorithm",
                    entity_id=row["algorithm_id"],
                    entity_key=row["algorithm_key"] or row["algo_name"],
                    data_type_id=row["data_type_id"],
                    issue_type="algo_execution_error",
                    severity="high",
                    description=(
                        f"Algorithme '{row['algo_name']}' échoue dans le notebook "
                        f"'{row['notebook_key']}' (notebook globalement 'executed', "
                        f"échec par-algorithme silencieux) : {err}"
                    ),
                    auto_fixable=True,
                    fix_hint=err,
                ))
    finally:
        conn.close()
    return issues


def check_algorithm_miscalibration(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """
    MEDIUM — algorithme qui s'exécute SANS erreur mais dont le taux de détection est
    incohérent (0% ou > 40% des lignes flaguées comme anomalies). Complète
    check_algorithm_execution_errors : ici le code ne plante pas, mais le seuil/la
    logique de détection est mal calibré (ex: condition d'anomalie inversée, seuil
    absolu non adapté à l'échelle réelle des données).
    """
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT nr.algorithm_id, nr.notebook_id, nr.final_metrics_json,
                       n.notebook_key, n.data_type_id,
                       a.name AS algo_name, a.algorithm_key
                FROM notebook_results nr
                JOIN (
                    SELECT notebook_id, algorithm_id, MAX(id) AS max_id
                    FROM notebook_results
                    WHERE algorithm_id IS NOT NULL AND algorithm_id > 0
                    GROUP BY notebook_id, algorithm_id
                ) latest ON nr.id = latest.max_id
                JOIN notebooks n   ON n.id = nr.notebook_id
                JOIN algorithms a  ON a.id = nr.algorithm_id
                WHERE JSON_EXTRACT(nr.final_metrics_json, '$.error') IS NULL
                  AND JSON_EXTRACT(nr.final_metrics_json, '$.detection_rate') IS NOT NULL
            """
            params = []
            if type_ids:
                sql += " AND n.data_type_id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                try:
                    metrics = row["final_metrics_json"]
                    metrics = json.loads(metrics) if isinstance(metrics, str) else metrics
                    det_rate = float(metrics.get("detection_rate") or 0.0)
                except Exception:
                    continue

                if det_rate == 0.0:
                    reason = "détecte 0% des lignes comme anomalies"
                elif det_rate > 0.4:
                    reason = f"détecte {det_rate:.0%} des lignes comme anomalies (bien trop élevé)"
                else:
                    continue

                issues.append(QualityIssue(
                    entity_type="algorithm",
                    entity_id=row["algorithm_id"],
                    entity_key=row["algorithm_key"] or row["algo_name"],
                    data_type_id=row["data_type_id"],
                    issue_type="algo_miscalibrated",
                    severity="medium",
                    description=(
                        f"Algorithme '{row['algo_name']}' s'exécute sans erreur mais {reason} "
                        f"dans le notebook '{row['notebook_key']}' — seuil/logique de détection "
                        f"probablement mal calibré."
                    ),
                    auto_fixable=True,
                    fix_hint=(
                        f"L'algorithme s'exécute sans erreur mais {reason}. Le taux d'anomalies "
                        f"attendu est d'environ 5% des lignes. Le seuil de détection (souvent une "
                        f"constante absolue) n'est probablement pas calibré à l'échelle réelle des "
                        f"données, ou la condition d'anomalie (ex: score > seuil) est peut-être "
                        f"inversée par rapport à l'intention (les points normaux ont parfois le "
                        f"score le plus élevé selon la métrique utilisée)."
                    ),
                ))
    finally:
        conn.close()
    return issues


def check_notebooks_zero_figures(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """MEDIUM — notebooks exécutés mais sans aucune figure dans MinIO."""
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    store = _get_storage()
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT n.id, n.notebook_key, n.data_type_id, n.executed_minio_key
                FROM notebooks n
                WHERE n.status = 'executed'
            """
            params = []
            if type_ids:
                sql += " AND n.data_type_id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            rows = cur.fetchall()

        for row in rows:
            # chercher des figures associées à ce notebook dans MinIO
            type_id = row["data_type_id"] or ""
            nb_key = row["notebook_key"] or ""
            has_figures = False
            if store:
                try:
                    # Les figures ne portent PAS l'ID DB du notebook : le pipeline
                    # les nomme par un compteur séquentiel (nb001…) et les range sous
                    # figures/{type_id}/{prob_key}/… + une figure comparative
                    # figures/{type_id}/compare_{prob_key}.png. On matche donc sur la
                    # clé de problème (p1, p11…) extraite du notebook_key, avec des
                    # délimiteurs pour ne pas confondre p1 avec p10/p11/p12.
                    m = re.search(r"_(p\d+)_", nb_key + "_")
                    prob_key = m.group(1) if m else None
                    if prob_key:
                        # sous-dossier propre au problème
                        sub = store.list_objects("figures", prefix=f"{type_id}/{prob_key}/")
                        has_figures = any(sub)
                        if not has_figures:
                            # ou la figure comparative à plat
                            cmp = store.list_objects(
                                "figures", prefix=f"{type_id}/compare_{prob_key}.")
                            has_figures = any(cmp)
                    else:
                        # notebook non-comparaison (clé sans pN) : ne pas signaler
                        # faute de convention de nommage fiable
                        has_figures = True
                except Exception:
                    has_figures = True  # on ne peut pas vérifier → on ne signale pas

            if not has_figures:
                issues.append(QualityIssue(
                    entity_type="notebook",
                    entity_id=row["id"],
                    entity_key=nb_key,
                    data_type_id=type_id,
                    issue_type="zero_figures",
                    severity="medium",
                    description=(
                        f"Notebook '{nb_key}' exécuté mais aucune figure trouvée dans MinIO. "
                        "Tous les algorithmes ont peut-être échoué (colonnes manquantes dans le dataset)."
                    ),
                    auto_fixable=True,
                    fix_hint="Forcer reconstruction complète du notebook (S2+S3) pour régénérer les figures dans MinIO.",
                ))
    finally:
        conn.close()
    return issues


def check_notebooks_stale(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """MEDIUM — notebooks obsolètes (moins d'algos que dans problems actuel)."""
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT n.id, n.notebook_key, n.data_type_id,
                       n.n_algorithms AS nb_algos_at_build,
                       COUNT(a.id) AS current_algos
                FROM notebooks n
                JOIN problems p ON n.problem_id = p.id
                JOIN algorithms a ON a.problem_id = p.id
                WHERE n.algorithm_id IS NULL
                  AND n.n_algorithms IS NOT NULL
                GROUP BY n.id
                HAVING current_algos > nb_algos_at_build
            """
            params = []
            if type_ids:
                sql = sql.replace("WHERE n.algorithm_id", "WHERE n.data_type_id IN (%s) AND n.algorithm_id" % ",".join(["%s"] * len(type_ids)))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                issues.append(QualityIssue(
                    entity_type="notebook",
                    entity_id=row["id"],
                    entity_key=row["notebook_key"],
                    data_type_id=row["data_type_id"],
                    issue_type="stale_notebook",
                    severity="medium",
                    description=(
                        f"Notebook '{row['notebook_key']}' obsolète : "
                        f"généré avec {row['nb_algos_at_build']} algo(s) "
                        f"mais {row['current_algos']} disponibles maintenant."
                    ),
                    auto_fixable=True,
                    fix_hint=f"Régénérer via Auto2 S2 --force pour notebook_id={row['id']}",
                ))
    finally:
        conn.close()
    return issues


def check_reports_compile_failed(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """
    HIGH — rapports PDF (table `reports`) dont la dernière tentative de
    compilation LaTeX a échoué (compile_success=0). Auto3 tente déjà une
    réparation interne (déterministe + LLM) pendant sa propre exécution, mais
    sans ce check, un rapport qui échoue reste invisible pour la boucle
    --watch tant qu'aucune AUTRE issue du même data_type ne déclenche un
    re-run d'Auto3 — voir _ISSUE_TO_PIPELINE dans monitor.py.
    """
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT r.id, r.report_key, r.data_type_id, r.problem_id,
                       r.compile_errors, p.problem_key, p.title AS problem_title
                FROM reports r
                LEFT JOIN problems p ON p.id = r.problem_id
                WHERE r.compile_success = 0
            """
            params = []
            if type_ids:
                sql += " AND r.data_type_id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                err = (row["compile_errors"] or "")[:300]
                label = row["problem_title"] or row["problem_key"] or row["report_key"]
                issues.append(QualityIssue(
                    entity_type="report",
                    entity_id=row["id"],
                    entity_key=row["report_key"],
                    data_type_id=row["data_type_id"],
                    issue_type="compile_failed",
                    severity="high",
                    description=f"Rapport '{label}' : compilation LaTeX en échec : {err}",
                    auto_fixable=True,
                    fix_hint=f"Relancer Auto3 (S2+S3+S4) pour data_type_id={row['data_type_id']}. Erreur : {err}",
                ))
    finally:
        conn.close()
    return issues


# ══════════════════════════════════════════════════════════════════════════════
# BLOC 5 — Cohérence MySQL ↔ MinIO
# ══════════════════════════════════════════════════════════════════════════════

def check_notebook_minio_exists(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """HIGH — notebooks avec minio_key mais fichier absent de MinIO."""
    issues = []
    store = _get_storage()
    if not store:
        return issues
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT n.id, n.notebook_key, n.data_type_id, n.minio_key
                FROM notebooks n
                WHERE n.minio_key IS NOT NULL AND n.minio_key != ''
            """
            params = []
            if type_ids:
                sql += " AND n.data_type_id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                key = row["minio_key"]
                if not _minio_exists(store, "notebooks", key):
                    issues.append(QualityIssue(
                        entity_type="notebook",
                        entity_id=row["id"],
                        entity_key=row["notebook_key"],
                        data_type_id=row["data_type_id"],
                        issue_type="minio_file_missing",
                        severity="high",
                        description=f"Notebook '{row['notebook_key']}' : minio_key='{key}' introuvable dans MinIO.",
                        auto_fixable=True,
                        fix_hint=f"Régénérer et uploader via Auto2 S2. notebook_id={row['id']}",
                    ))
    finally:
        conn.close()
    return issues


def check_dataset_minio_exists(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """HIGH — datasets avec minio_key mais fichier absent de MinIO."""
    issues = []
    store = _get_storage()
    if not store:
        return issues
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT d.id, d.dataset_key, d.data_type_id, d.minio_key
                FROM datasets d
                WHERE d.minio_key IS NOT NULL AND d.minio_key != ''
            """ + _EXCLUDE_ORPHANS
            params = []
            if type_ids:
                sql += " AND d.data_type_id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                key = row["minio_key"]
                if not _minio_exists(store, "datasets", key):
                    issues.append(QualityIssue(
                        entity_type="dataset",
                        entity_id=row["id"],
                        entity_key=row["dataset_key"],
                        data_type_id=row["data_type_id"],
                        issue_type="minio_file_missing",
                        severity="high",
                        description=f"Dataset '{row['dataset_key']}' : minio_key='{key}' introuvable dans MinIO.",
                        auto_fixable=True,
                        fix_hint=f"Relancer Auto2 S1 pour dataset_id={row['id']}",
                    ))
    finally:
        conn.close()
    return issues


# ══════════════════════════════════════════════════════════════════════════════
# BLOC 6 — Cohérence données croisées
# ══════════════════════════════════════════════════════════════════════════════

def check_algorithm_column_mismatch(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """MEDIUM — colonnes utilisées dans skeleton absent du dataset du problème."""
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            # Récupérer les datasets problem-level et les algos du même problème
            sql = """
                SELECT a.id AS algo_id, a.algorithm_key, a.name AS algo_name,
                       a.python_skeleton, p.id AS prob_id, p.problem_key,
                       dt.id AS type_id,
                       d.dataset_key, LEFT(d.data_json, 1000) AS data_sample
                FROM algorithms a
                JOIN problems p ON a.problem_id = p.id
                JOIN data_types dt ON p.data_type_id = dt.id
                LEFT JOIN datasets d ON d.data_type_id = dt.id
                    AND d.dataset_key = CONCAT(dt.id, '_', p.problem_key)
                WHERE a.python_skeleton IS NOT NULL
                  AND d.data_json IS NOT NULL
            """
            params = []
            if type_ids:
                sql += " AND dt.id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                skel = row["python_skeleton"] or ""
                data_sample = row["data_sample"] or ""

                # Extraire les colonnes du dataset
                try:
                    sample = json.loads(data_sample + ("]" if data_sample.count("[") > data_sample.count("]") else ""))
                    if isinstance(sample, list) and sample and isinstance(sample[0], dict):
                        dataset_cols = set(sample[0].keys())
                    else:
                        continue
                except Exception:
                    continue

                # Extraire les accès df['colonne'] ou df["colonne"] dans le skeleton
                col_accesses = set(re.findall(r'df\[[\'"]([^\'"]+)[\'"]\]', skel))
                # Exclure les assignations de nouvelles colonnes
                assigned = set(re.findall(r'df\[[\'"]([^\'"]+)[\'"]\]\s*=', skel))
                read_only = col_accesses - assigned
                # Colonnes standard toujours présentes
                standard = {"timestamp", "sensor_id", "value", "anomaly_flag",
                            "anomaly_score", "is_anomaly", "corrected_value"}
                missing_cols = read_only - dataset_cols - standard

                if missing_cols:
                    issues.append(QualityIssue(
                        entity_type="algorithm",
                        entity_id=row["algo_id"],
                        entity_key=row["algorithm_key"],
                        data_type_id=row["type_id"],
                        issue_type="column_mismatch",
                        severity="medium",
                        description=(
                            f"'{row['algo_name']}' ({row['algorithm_key']}) accède à des colonnes "
                            f"absentes du dataset {row['dataset_key']}: {missing_cols}. "
                            f"Colonnes disponibles: {list(dataset_cols)[:6]}"
                        ),
                        auto_fixable=True,
                        fix_hint=(
                            f"Adapter le skeleton pour utiliser les colonnes disponibles : {list(dataset_cols)}. "
                            f"Colonnes manquantes accédées : {missing_cols}"
                        ),
                    ))
    finally:
        conn.close()
    return issues


def check_notebooks_algo_count_vs_db(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """LOW — notebooks de comparaison dont le nombre d'algos ne correspond pas au problème actuel."""
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT n.id, n.notebook_key, n.data_type_id, n.n_algorithms,
                       COUNT(a.id) AS actual_algos
                FROM notebooks n
                JOIN problems p ON n.problem_id = p.id
                LEFT JOIN algorithms a ON a.problem_id = p.id
                WHERE n.algorithm_id IS NULL
                  AND n.n_algorithms IS NOT NULL
                GROUP BY n.id
                HAVING actual_algos != n.n_algorithms
            """
            params = []
            if type_ids:
                sql = sql.replace("WHERE n.algorithm_id", "WHERE n.data_type_id IN (%s) AND n.algorithm_id" % ",".join(["%s"] * len(type_ids)))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                issues.append(QualityIssue(
                    entity_type="notebook",
                    entity_id=row["id"],
                    entity_key=row["notebook_key"],
                    data_type_id=row["data_type_id"],
                    issue_type="algo_count_mismatch",
                    severity="low",
                    description=(
                        f"Notebook '{row['notebook_key']}' : "
                        f"construit avec {row['n_algorithms']} algo(s), "
                        f"problème actuel en a {row['actual_algos']}."
                    ),
                    auto_fixable=True,
                    fix_hint="Régénérer via Auto2 S2 --force",
                ))
    finally:
        conn.close()
    return issues


# ══════════════════════════════════════════════════════════════════════════════
# BLOC 7 — Rapports PDF, notebooks bloqués, datasets dégénérés, infra
# ══════════════════════════════════════════════════════════════════════════════

def check_reports_integrity(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """
    HIGH — rapports marqués compile_success=1 mais dont le PDF est inutilisable :
    minio_key vide (jamais uploadé), fichier absent de MinIO, ou n_pages=0
    (PDF vide/corrompu). Complète check_reports_compile_failed qui ne voit que
    les échecs de compilation explicites.
    """
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    store = _get_storage()
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT r.id, r.report_key, r.data_type_id, r.minio_key, r.n_pages,
                       p.problem_key
                FROM reports r
                LEFT JOIN problems p ON p.id = r.problem_id
                WHERE r.compile_success = 1
            """
            params = []
            if type_ids:
                sql += " AND r.data_type_id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                reasons = []
                key = (row["minio_key"] or "").strip()
                if not key:
                    reasons.append("minio_key vide (PDF jamais uploadé)")
                elif store and not (_minio_exists(store, "catalogue", key)
                                    or _minio_exists(store, "reports", key)):
                    reasons.append(f"fichier '{key}' absent de MinIO")
                if (row["n_pages"] or 0) == 0:
                    reasons.append("n_pages=0 (PDF vide ou corrompu)")
                if not reasons:
                    continue
                issues.append(QualityIssue(
                    entity_type="report",
                    entity_id=row["id"],
                    entity_key=row["report_key"],
                    data_type_id=row["data_type_id"],
                    issue_type="report_invalid",
                    severity="high",
                    description=f"Rapport '{row['report_key']}' déclaré compilé mais : {'; '.join(reasons)}.",
                    auto_fixable=True,
                    fix_hint="Relancer Auto3 pour recompiler + re-uploader le PDF.",
                ))
    finally:
        conn.close()
    return issues


def check_reports_stale(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """
    MEDIUM — rapports plus anciens que la dernière exécution du notebook de
    comparaison de leur problème : les métriques/figures du PDF ne reflètent
    plus les derniers résultats (ex: Auto3 a crashé après une ré-exécution Auto2).
    """
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT r.id, r.report_key, r.data_type_id, r.updated_at AS report_at,
                       MAX(n.last_executed_at) AS last_exec
                FROM reports r
                JOIN notebooks n ON n.problem_id = r.problem_id
                    AND n.algorithm_id IS NULL AND n.status = 'executed'
                WHERE r.compile_success = 1 AND n.last_executed_at IS NOT NULL
            """
            params = []
            if type_ids:
                sql += " AND r.data_type_id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            sql += " GROUP BY r.id, r.report_key, r.data_type_id, r.updated_at HAVING last_exec > report_at"
            cur.execute(sql, params)
            for row in cur.fetchall():
                issues.append(QualityIssue(
                    entity_type="report",
                    entity_id=row["id"],
                    entity_key=row["report_key"],
                    data_type_id=row["data_type_id"],
                    issue_type="report_stale",
                    severity="medium",
                    description=(
                        f"Rapport '{row['report_key']}' généré le {row['report_at']} mais le "
                        f"notebook de comparaison a été ré-exécuté le {row['last_exec']} — PDF obsolète."
                    ),
                    auto_fixable=True,
                    fix_hint="Relancer Auto3 pour régénérer le PDF avec les derniers résultats.",
                ))
    finally:
        conn.close()
    return issues


def check_notebooks_no_results(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """
    HIGH — notebooks de comparaison status='executed' mais sans AUCUNE ligne dans
    notebook_results : l'extraction des résultats a échoué silencieusement
    (le notebook a tourné mais aucun algo n'a produit de métriques exploitables).
    """
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT n.id, n.notebook_key, n.data_type_id
                FROM notebooks n
                LEFT JOIN notebook_results nr ON nr.notebook_id = n.id
                WHERE n.status = 'executed' AND n.algorithm_id IS NULL
            """
            params = []
            if type_ids:
                sql += " AND n.data_type_id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            sql += " GROUP BY n.id, n.notebook_key, n.data_type_id HAVING COUNT(nr.id) = 0"
            cur.execute(sql, params)
            for row in cur.fetchall():
                issues.append(QualityIssue(
                    entity_type="notebook",
                    entity_id=row["id"],
                    entity_key=row["notebook_key"],
                    data_type_id=row["data_type_id"],
                    issue_type="no_results",
                    severity="high",
                    description=(
                        f"Notebook '{row['notebook_key']}' exécuté mais 0 résultat d'algorithme "
                        "extrait (notebook_results vide) — exécution silencieusement inutile."
                    ),
                    auto_fixable=True,
                    fix_hint="Remettre status='generated' et ré-exécuter via Auto2 S3.",
                ))
    finally:
        conn.close()
    return issues


def check_notebooks_stuck_running(type_ids: Optional[List[str]] = None,
                                   max_hours: int = 2) -> List[QualityIssue]:
    """
    HIGH — notebooks bloqués en status='running' depuis plus de max_hours :
    le processus d'exécution est mort en cours de route (crash, kernel tué,
    redémarrage machine) sans jamais mettre à jour le statut. Sans ce check,
    ces notebooks restent invisibles : ni 'failed' (pas repris par
    check_notebooks_failed), ni 'generated' (pas repris par Auto2 S3).
    """
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT n.id, n.notebook_key, n.data_type_id, n.updated_at
                FROM notebooks n
                WHERE n.status = 'running'
                  AND n.updated_at < NOW() - INTERVAL %s HOUR
            """
            params = [max_hours]
            if type_ids:
                sql += " AND n.data_type_id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = params + type_ids
            cur.execute(sql, params)
            for row in cur.fetchall():
                issues.append(QualityIssue(
                    entity_type="notebook",
                    entity_id=row["id"],
                    entity_key=row["notebook_key"],
                    data_type_id=row["data_type_id"],
                    issue_type="stuck_running",
                    severity="high",
                    description=(
                        f"Notebook '{row['notebook_key']}' bloqué en 'running' depuis "
                        f"{row['updated_at']} (> {max_hours}h) — processus d'exécution mort."
                    ),
                    auto_fixable=True,
                    fix_hint="Remettre status='generated' pour relance via Auto2 S3.",
                ))
    finally:
        conn.close()
    return issues


def check_problems_without_dataset(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """
    HIGH — problèmes ayant des algorithmes mais aucun dataset associé (ni par
    problem_id, ni par la convention dataset_key = '{type}_{prob_key}') :
    les notebooks ne peuvent pas s'exécuter sur des données réelles.
    """
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT p.id, p.problem_key, p.title, dt.id AS type_id
                FROM problems p
                JOIN data_types dt ON p.data_type_id = dt.id
                JOIN algorithms a ON a.problem_id = p.id
                LEFT JOIN datasets d ON (d.problem_id = p.id
                    OR d.dataset_key = CONCAT(dt.id, '_', p.problem_key))
                WHERE d.id IS NULL
            """
            params = []
            if type_ids:
                sql += " AND dt.id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            sql += " GROUP BY p.id, p.problem_key, p.title, dt.id"
            cur.execute(sql, params)
            for row in cur.fetchall():
                issues.append(QualityIssue(
                    entity_type="problem",
                    entity_id=row["id"],
                    entity_key=row["problem_key"],
                    data_type_id=row["type_id"],
                    issue_type="no_dataset",
                    severity="high",
                    description=f"Problème '{row['title']}' ({row['problem_key']}) : aucun dataset associé.",
                    auto_fixable=True,
                    fix_hint=f"Générer le dataset via Auto1 S4 pour problem_id={row['id']}.",
                ))
    finally:
        conn.close()
    return issues


def check_datasets_no_anomalies(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """
    MEDIUM — datasets stockés en table MySQL dont AUCUNE ligne n'a anomaly_flag=1 :
    rien à détecter, tous les algorithmes paraissent 'miscalibrés' (0%) alors que
    la cause racine est le dataset lui-même (injection d'anomalies ratée).
    """
    issues = []
    conn = _get_conn()
    if not conn:
        return issues
    try:
        with conn.cursor(dictionary=True) as cur:
            sql = """
                SELECT d.id, d.dataset_key, d.data_type_id, d.file_path
                FROM datasets d
                WHERE d.data_json LIKE '[MySQL table:%%'
                  AND d.file_path IS NOT NULL AND TRIM(d.file_path) != ''
            """ + _EXCLUDE_ORPHANS
            params = []
            if type_ids:
                sql += " AND d.data_type_id IN (%s)" % ",".join(["%s"] * len(type_ids))
                params = type_ids
            cur.execute(sql, params)
            rows = cur.fetchall()

        for row in rows:
            table = row["file_path"]
            if not re.fullmatch(r"[A-Za-z0-9_]+", table or ""):
                continue  # nom de table suspect — ne pas l'injecter dans du SQL
            try:
                with conn.cursor(dictionary=True) as cur:
                    cur.execute(
                        f"SELECT COUNT(*) AS n, COALESCE(SUM(anomaly_flag = 1), 0) AS n_anom "
                        f"FROM `{table}`"
                    )
                    stats = cur.fetchone()
            except Exception:
                continue  # table absente ou sans colonne anomaly_flag — hors périmètre
            if stats and stats["n"] > 0 and stats["n_anom"] == 0:
                issues.append(QualityIssue(
                    entity_type="dataset",
                    entity_id=row["id"],
                    entity_key=row["dataset_key"],
                    data_type_id=row["data_type_id"],
                    issue_type="dataset_no_anomalies",
                    severity="medium",
                    description=(
                        f"Dataset '{row['dataset_key']}' ({stats['n']} lignes) : aucune ligne "
                        "avec anomaly_flag=1 — rien à détecter, algos faussement miscalibrés."
                    ),
                    auto_fixable=True,
                    fix_hint="Régénérer le dataset via Auto1 S4 (injection d'anomalies).",
                ))
    finally:
        conn.close()
    return issues


def check_duplicate_watch_processes(type_ids: Optional[List[str]] = None) -> List[QualityIssue]:
    """
    HIGH — plusieurs processus `shared.quality --watch` tournent en parallèle :
    réparations LLM en double (coût ×N), écritures concurrentes sur les mêmes
    entités, cycles de pipeline entremêlés. Cas réellement observé après des
    relances de session sans vérifier ps aux.
    """
    import os
    issues = []
    watch_pids = _find_watch_pids()
    if len(watch_pids) <= 1:
        return issues
    me = os.getpid()
    others = [p for p in watch_pids if p != me]
    issues.append(QualityIssue(
        entity_type="data_type",
        entity_id=None,
        entity_key="quality_watch",
        data_type_id=None,
        issue_type="duplicate_watch",
        severity="high",
        description=(
            f"{len(watch_pids)} processus 'shared.quality --watch' détectés "
            f"(PIDs {watch_pids}) — réparations et pipelines en double."
        ),
        auto_fixable=True,
        fix_hint=f"Tuer les doublons (garder pid={me if me in watch_pids else min(watch_pids)}) : {others}",
    ))
    return issues


def _find_watch_pids() -> List[int]:
    """PIDs des processus python exécutant `-m shared.quality` avec --watch
    (le wrapper bash/nohup parent est exclu : argv[0] doit être un python)."""
    import os
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                argv = f.read().decode("utf-8", errors="replace").split("\0")
        except OSError:
            continue
        if not argv or "python" not in os.path.basename(argv[0]):
            continue
        if "shared.quality" in argv and "--watch" in argv:
            pids.append(int(entry))
    return pids


# ══════════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE : run_all_checks()
# ══════════════════════════════════════════════════════════════════════════════

# Registre ordonné : (fonction, label, activé_par_défaut)
_CHECK_REGISTRY = [
    (check_algorithms_missing_skeleton,    "algo:missing_skeleton",      True),
    (check_algorithms_skeleton_syntax,     "algo:syntax_error",          True),
    (check_algorithms_missing_is_anomaly,  "algo:missing_is_anomaly",    True),
    (check_algorithms_forbidden_imports,   "algo:forbidden_imports",     True),
    (check_problems_without_algorithms,    "problem:no_algorithms",      True),
    (check_data_types_without_problems,    "type:no_problems",           True),
    (check_datasets_without_data,          "dataset:no_data",            True),
    (check_datasets_orphaned,              "dataset:orphaned",           True),
    (check_datasets_schema_coherence,      "dataset:schema",             True),
    (check_notebooks_failed,               "notebook:failed",            True),
    (check_algorithm_execution_errors,     "algo:execution_error",       True),
    (check_algorithm_miscalibration,       "algo:miscalibrated",         True),
    (check_notebooks_zero_figures,         "notebook:zero_figures",      True),
    (check_notebooks_stale,                "notebook:stale",             True),
    (check_notebook_minio_exists,          "notebook:minio_missing",     True),
    (check_dataset_minio_exists,           "dataset:minio_missing",      True),
    (check_algorithm_column_mismatch,      "algo:column_mismatch",       True),
    (check_notebooks_algo_count_vs_db,     "notebook:algo_count",        False),
    (check_reports_compile_failed,         "report:compile_failed",      True),
    (check_reports_integrity,              "report:invalid",             True),
    (check_reports_stale,                  "report:stale",               True),
    (check_notebooks_no_results,           "notebook:no_results",        True),
    (check_notebooks_stuck_running,        "notebook:stuck_running",     True),
    (check_problems_without_dataset,       "problem:no_dataset",         True),
    (check_datasets_no_anomalies,          "dataset:no_anomalies",       True),
    (check_duplicate_watch_processes,      "system:duplicate_watch",     True),
]


def run_all_checks(
    type_ids:  Optional[List[str]] = None,
    only:      Optional[List[str]] = None,
    skip:      Optional[List[str]] = None,
) -> List[QualityIssue]:
    """
    Lance tous les checks enregistrés et retourne la liste consolidée d'issues.

    Args:
        type_ids : restreindre aux data_type_id spécifiés
        only     : ne lancer que les checks dont le label contient un de ces termes
        skip     : exclure les checks dont le label contient un de ces termes
    """
    all_issues: List[QualityIssue] = []

    for fn, label, enabled_default in _CHECK_REGISTRY:
        if not enabled_default and (only is None or not any(t in label for t in (only or []))):
            continue
        if only and not any(t in label for t in only):
            continue
        if skip and any(t in label for t in skip):
            continue
        try:
            log.debug("[CHECK] %s …", label)
            t0 = datetime.now()
            result = fn(type_ids=type_ids)
            elapsed = (datetime.now() - t0).total_seconds()
            log.info("[CHECK] %-40s → %3d issues (%.2fs)", label, len(result), elapsed)
            all_issues.extend(result)
        except Exception as exc:
            log.error("[CHECK] %s erreur : %s", label, exc)

    return all_issues
