"""
Constructeur de notebooks Jupyter (.ipynb) pour l'Automatisation 2.

Construit des notebooks Jupyter complets à partir des données en base de données
(python_skeleton, math_formulation, pseudocode, evaluation_metrics...).
Aucun appel LLM — tout est construit depuis les données déjà enrichies en DB.
"""
from __future__ import annotations

import json
import keyword
import re
import tempfile
import textwrap
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import nbformat
from nbformat.v4 import new_notebook, new_code_cell, new_markdown_cell


def _fix_newlines(text: str) -> str:
    """Convertit les \\n littéraux en vrais sauts de ligne."""
    if not text:
        return text
    return text.replace("\\n", "\n").replace("\\t", "    ")


def _extract_fn_name(skeleton: str) -> str:
    """Extrait le nom de la fonction d'entrée du skeleton.

    Délègue à shared.validation.skeleton_contract, qui sert aussi à la validation
    par exécution : les deux DOIVENT résoudre le point d'entrée à l'identique,
    sinon la validation rejette du code que ce constructeur exécuterait sans
    problème (ou l'inverse).
    """
    from shared.validation.skeleton_contract import resolve_entry_point

    return resolve_entry_point(skeleton)


def _format_refs(references: List[Dict]) -> str:
    """Formate les références en markdown."""
    if not references:
        return ""
    lines = ["### Références\n"]
    for i, r in enumerate(references, 1):
        authors = r.get("authors", "")
        title   = r.get("title", "")
        year    = r.get("year", "")
        venue   = r.get("venue", "")
        doi     = r.get("doi", "")
        doi_str = f" DOI: {doi}" if doi else ""
        lines.append(f"{i}. {authors} ({year}). *{title}*. {venue}.{doi_str}")
    return "\n".join(lines)


def _imports_cell(required_libraries: List[str]) -> str:
    """Génère la cellule d'imports à partir de required_libraries."""
    base_imports = [
        "import numpy as np",
        "import pandas as pd",
        "import matplotlib",
        "matplotlib.use('Agg')",
        "import matplotlib.pyplot as plt",
        "import matplotlib.dates as mdates",
        "import seaborn as sns",
        "import json, warnings, os",
        "from pathlib import Path",
        "from datetime import datetime",
        "warnings.filterwarnings('ignore')",
        "sns.set_theme(style='whitegrid', palette='muted')",
        # Always needed for Cell 10 (F1 evaluation)
        "from sklearn.metrics import f1_score, precision_score, recall_score, confusion_matrix",
    ]
    extra = set()
    for lib in (required_libraries or []):
        pkg = lib.split(">=")[0].split("==")[0].strip().lower()
        if "sklearn" in pkg or "scikit" in pkg:
            pass  # already in base_imports
        elif "scipy" in pkg:
            extra.add("from scipy import signal, stats")
        elif "statsmodels" in pkg:
            extra.add("import statsmodels.api as sm")

    return "\n".join(base_imports + sorted(extra))


def build_notebook(
    data_type: Dict,
    problem: Dict,
    algorithm: Dict,
    dataset_path: str,
    figures_dir: str,
    notebook_num: int = 1,
    dataset_key: str = "",
) -> nbformat.NotebookNode:
    """
    Construit un notebook Jupyter complet depuis les données en DB.

    Args:
        data_type   : dict depuis data_types (name, domain, description...)
        problem     : dict depuis problems (title, description, causes...)
        algorithm   : dict depuis algorithms (name, python_skeleton, math_formulation...)
        dataset_path: chemin CSV (fallback si MySQL indisponible)
        figures_dir : répertoire pour sauvegarder les figures
        notebook_num: numéro du notebook (pour nommage des figures)
        dataset_key : clé MySQL du dataset (source primaire de chargement)

    Retourne un objet nbformat.NotebookNode (.ipynb)
    """
    nb = new_notebook()
    # Kernel : utilise Python du .venv actif (python3 résolu par l'environnement courant)
    nb.metadata["kernelspec"] = {
        "display_name": "Python 3 (.venv)",
        "language": "python",
        "name": "python3",
    }
    nb.metadata["language_info"] = {"name": "python", "version": "3.10"}
    cells = []

    type_name  = data_type.get("name", "")
    type_id    = data_type.get("id", "")
    domain     = data_type.get("domain", "")
    prob_title = problem.get("title", "")
    prob_desc  = problem.get("description", "")
    alg_name   = algorithm.get("name", "")
    alg_cat    = algorithm.get("category", "")
    principle  = algorithm.get("principle", "")
    skeleton   = _fix_newlines(algorithm.get("python_skeleton", ""))
    math_form  = algorithm.get("math_formulation", "")
    pseudocode = _fix_newlines(algorithm.get("pseudocode", ""))
    advantages = algorithm.get("advantages", [])
    limitations= algorithm.get("limitations", [])
    hyperparams= algorithm.get("hyperparameters", [])
    metrics    = algorithm.get("evaluation_metrics", [])
    references = algorithm.get("references", []) if isinstance(algorithm.get("references"), list) else []
    use_case   = algorithm.get("use_case_example", "")
    req_libs   = algorithm.get("required_libraries", [])
    if isinstance(req_libs, str):
        try:
            req_libs = json.loads(req_libs)
        except Exception:
            req_libs = []

    fn_name = _extract_fn_name(skeleton)

    # ── Cell 1 : Titre et contexte ─────────────────────────────────────────
    causes_md = "\n".join(f"- {c}" for c in (problem.get("causes", []) or []))
    adv_md    = "\n".join(f"- {a}" for a in advantages[:3])
    lim_md    = "\n".join(f"- {l}" for l in limitations[:3])

    cells.append(new_markdown_cell(textwrap.dedent(f"""\
        # {alg_name}

        | Champ | Valeur |
        |-------|--------|
        | **Type de données** | {type_name} |
        | **Domaine** | {domain} |
        | **Problème** | {prob_title} |
        | **Catégorie algorithme** | {alg_cat} |

        ## Contexte

        {prob_desc}

        **Causes identifiées :**
        {causes_md}

        ## Avantages / Limitations

        **Avantages :**
        {adv_md}

        **Limitations :**
        {lim_md}
    """)))

    # ── Cell 2 : Configuration (paramètres papermill-style) ───────────────
    params_lines = ["# === PARAMÈTRES (modifiables) ==="]
    for hp in (hyperparams or []):
        if isinstance(hp, dict):
            n_hp  = hp.get("name", "param")
            if keyword.iskeyword(n_hp):
                n_hp = n_hp + "_param"
            default = hp.get("default")
            desc  = hp.get("description", "")
            rng   = hp.get("range", "")
            if default is not None:
                params_lines.append(f"{n_hp} = {json.dumps(default)}  # {desc} — plage: {rng}")
    if len(params_lines) == 1:
        params_lines.append("# (Aucun hyperparamètre documenté)")
    params_lines += [
        "",
        f"DATASET_KEY  = {json.dumps(dataset_key)}",
        f"DATASET_PATH = r'{dataset_path}'",
        f"FIGURES_DIR  = r'{figures_dir}'",
        f"NOTEBOOK_ID  = {notebook_num}",
    ]
    cells.append(new_code_cell("\n".join(params_lines)))

    # ── Cell 3 : Imports ─────────────────────────────────────────────────
    # os.makedirs ici — après import os (qui est dans _imports_cell)
    imports_src = _imports_cell(req_libs) + "\nos.makedirs(FIGURES_DIR, exist_ok=True)"
    cells.append(new_code_cell(imports_src))

    # ── Cell 4 : Chargement du dataset depuis MySQL (ou CSV en fallback) ──
    cells.append(new_code_cell(textwrap.dedent("""\
        # Chargement du dataset depuis MySQL (source primaire)
        # Fallback automatique vers le CSV si MySQL indisponible
        from pathlib import Path
        import os, json as _json

        def _find_env():
            \"\"\"Remonte l'arborescence pour trouver le .env\"\"\"
            for p in [Path.cwd()] + list(Path.cwd().parents):
                if (p / '.env').exists():
                    return p / '.env'
            return None

        def _load_env(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, _, v = line.partition('=')
                        os.environ.setdefault(k.strip(), v.strip().strip('\\"\\' '))

        env_file = _find_env()
        if env_file:
            _load_env(env_file)

        df = None

        # Tentative MySQL
        if DATASET_KEY:
            try:
                import mysql.connector
                _conn = mysql.connector.connect(
                    host=os.environ.get('DB_HOST', 'localhost'),
                    port=int(os.environ.get('DB_PORT', 3306)),
                    user=os.environ.get('DB_USER', 'root'),
                    password=os.environ.get('DB_PASSWORD', ''),
                    database=os.environ.get('DB_NAME', 'urbain_automation'),
                )
                with _conn.cursor(dictionary=True) as _cur:
                    _cur.execute(
                        "SELECT data_json, file_path FROM datasets WHERE dataset_key=%s LIMIT 1",
                        (DATASET_KEY,),
                    )
                    _row = _cur.fetchone()
                _conn.close()

                if _row and _row.get('data_json'):
                    try:
                        _data = _json.loads(_row['data_json'])
                        if isinstance(_data, (list, dict)):
                            df = pd.DataFrame(_data) if isinstance(_data, list) else pd.DataFrame.from_dict(_data)
                            print(f"✓ Dataset chargé depuis MySQL JSON (key={DATASET_KEY}) : {len(df):,} lignes")
                    except Exception:
                        pass
                if df is None and _row:
                    _fp = (_row.get('file_path') or '').strip()
                    if _fp and Path(_fp).exists():
                        df = pd.read_csv(_fp)
                        print(f"✓ Dataset chargé depuis CSV MySQL : {len(df):,} lignes")
                    elif _fp and not Path(_fp).suffix:
                        # Table MySQL (nouveau pipeline : ds_xxx)
                        try:
                            import mysql.connector as _mc2
                            _c2 = _mc2.connect(
                                host=os.environ.get('DB_HOST', 'localhost'),
                                port=int(os.environ.get('DB_PORT', 3306)),
                                user=os.environ.get('DB_USER', 'root'),
                                password=os.environ.get('DB_PASSWORD', ''),
                                database=os.environ.get('DB_NAME', 'urbain_automation'),
                            )
                            with _c2.cursor(dictionary=True) as _cur2:
                                _cur2.execute(f"SELECT * FROM `{_fp}` LIMIT 10000")
                                _rows2 = _cur2.fetchall()
                            _c2.close()
                            df = pd.DataFrame(_rows2)
                            print(f"✓ Dataset chargé depuis table MySQL `{_fp}` : {len(df):,} lignes")
                        except Exception as _te:
                            print(f"⚠ Chargement table MySQL `{_fp}` échoué : {_te}")
            except Exception as _e:
                print(f"⚠ MySQL indisponible ({_e}) — tentative CSV…")

        # Fallback CSV
        if df is None and DATASET_PATH and Path(DATASET_PATH).exists():
            df = pd.read_csv(DATASET_PATH)
            print(f"✓ Dataset chargé depuis CSV : {DATASET_PATH} — {len(df):,} lignes")

        if df is None:
            raise RuntimeError(f"Dataset introuvable : key={DATASET_KEY!r}, path={DATASET_PATH!r}")

        # Post-traitement
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.sort_values('timestamp').reset_index(drop=True)

        print(f"Colonnes : {list(df.columns)}")
        if 'timestamp' in df.columns:
            print(f"Période  : {df['timestamp'].min()} → {df['timestamp'].max()}")
        df.head(5)
    """)))

    # ── Cell 5 : Exploration ──────────────────────────────────────────────
    cells.append(new_code_cell(textwrap.dedent("""\
        # Exploration du dataset
        print("=== Statistiques descriptives ===")
        print(df.describe().round(3).to_string())
        print(f"\\nValeurs manquantes :\\n{df.isnull().sum()}")

        if 'anomaly_flag' in df.columns:
            n_anom = df['anomaly_flag'].sum()
            print(f"\\nAnomalies dans le dataset : {n_anom} ({n_anom/len(df)*100:.1f}%)")

        # Visualisation de la série temporelle brute
        if 'timestamp' in df.columns and 'value' in df.columns:
            fig, ax = plt.subplots(figsize=(14, 4))
            ax.plot(df['timestamp'], df['value'], linewidth=0.7, alpha=0.8, label='value')
            if 'anomaly_flag' in df.columns:
                anom = df[df['anomaly_flag'] == True]
                ax.scatter(anom['timestamp'], anom['value'], color='red', s=15,
                           zorder=5, label='Anomalie injectée', alpha=0.7)
            ax.set_title('Série temporelle — données brutes')
            ax.set_xlabel('Temps')
            ax.legend()
            ax.grid(True, alpha=0.3)
            fig_path = Path(FIGURES_DIR) / f'nb{NOTEBOOK_ID:03d}_raw.png'
            plt.savefig(fig_path, dpi=100, bbox_inches='tight')
            plt.show()
            plt.close()
            print(f"Figure sauvegardée : {fig_path}")
    """)))

    # ── Cell 6 : Théorie de l'algorithme ─────────────────────────────────
    pseudo_md = pseudocode.replace("\n", "\n    ") if pseudocode else "(voir implémentation)"
    cells.append(new_markdown_cell(textwrap.dedent(f"""\
        ## Théorie : {alg_name}

        **Principe :**  {principle}

        **Formulation mathématique :**

        {math_form}

        **Pseudo-code :**
        ```
        {pseudo_md}
        ```
    """)))

    # ── Cell 7 : Implémentation ────────────────────────────────────────────
    impl_code = skeleton if skeleton else textwrap.dedent(f"""\
        def {fn_name}(df: pd.DataFrame, **kwargs) -> pd.DataFrame:
            df = df.copy()
            # TODO: implémenter l'algorithme
            df['anomaly_score'] = 0.0
            df['is_anomaly']    = False
            df['corrected_value'] = df.get('value', 0.0)
            return df
    """)
    cells.append(new_code_cell(f"# Implémentation de l'algorithme\n{impl_code}"))

    # ── Cell 8 : Application ──────────────────────────────────────────────
    # Determine best 'value' column for fallback (first numeric non-flag column)
    cells.append(new_code_cell(textwrap.dedent(f"""\
        # Application de l'algorithme sur le dataset
        print("Application de {alg_name}...")

        # Fallback value column (premier numérique disponible)
        _val_col = next(
            (c for c in ['value','lat','latitude','speed','flow','occupancy']
             if c in df.columns), df.select_dtypes(include='number').columns[0]
        )

        try:
            df_result = {fn_name}(df)
            print("✓ Algorithme exécuté avec succès")
        except Exception as _algo_err:
            print(f"⚠ Erreur algorithme : {{_algo_err}}")
            df_result = df.copy()
            df_result['anomaly_score'] = 0.0
            df_result['is_anomaly']    = False

        # Colonnes de sortie standardisées
        for col in ['anomaly_score', 'is_anomaly']:
            if col not in df_result.columns:
                df_result[col] = 0.0 if col == 'anomaly_score' else False

        if 'value' not in df_result.columns:
            df_result['value'] = df_result.get(_val_col, np.nan)

        if 'corrected_value' not in df_result.columns:
            df_result['corrected_value'] = df_result.get('value', df_result[_val_col])

        # Preserve ground-truth anomaly labels for F1 evaluation (Cell 10)
        if 'anomaly_flag' not in df_result.columns and 'anomaly_flag' in df.columns:
            df_result['anomaly_flag'] = df['anomaly_flag'].values

        n_detected = int(df_result['is_anomaly'].sum())
        rate = float(df_result['is_anomaly'].mean()) * 100
        print(f"Anomalies détectées : {{n_detected}} / {{len(df_result)}} ({{rate:.2f}}%)")
        df_result[['anomaly_score', 'is_anomaly', 'corrected_value']].describe()
    """)))

    # ── Cell 9 : Visualisation résultats ─────────────────────────────────
    cells.append(new_code_cell(textwrap.dedent(f"""\
        # Visualisation des résultats de détection
        has_ts = 'timestamp' in df_result.columns
        x_col  = 'timestamp' if has_ts else df_result.index

        fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
        fig.suptitle({repr(alg_name)}, fontsize=13, fontweight='bold')

        # Série temporelle + anomalies détectées
        axes[0].plot(x_col if not has_ts else df_result['timestamp'],
                     df_result['value'], linewidth=0.7, alpha=0.8, label='Valeur', color='steelblue')
        detected = df_result[df_result['is_anomaly'] == True]
        axes[0].scatter(
            detected['timestamp'] if has_ts else detected.index,
            detected['value'], color='red', s=20, zorder=5, label='Anomalie détectée', alpha=0.8)
        if 'anomaly_flag' in df_result.columns:
            real = df_result[df_result['anomaly_flag'] == True]
            axes[0].scatter(
                real['timestamp'] if has_ts else real.index,
                real['value'], marker='x', color='orange', s=30, zorder=6,
                label='Anomalie réelle', alpha=0.5)
        axes[0].set_ylabel('Valeur')
        axes[0].legend(fontsize=8)
        axes[0].grid(True, alpha=0.3)

        # Score d'anomalie
        axes[1].plot(df_result['timestamp'] if has_ts else df_result.index,
                     df_result['anomaly_score'], color='darkorange', linewidth=0.7, alpha=0.8)
        axes[1].axhline(y=1.0, color='red', linestyle='--', linewidth=1, label='Seuil')
        axes[1].set_ylabel('Score anomalie')
        axes[1].legend(fontsize=8)
        axes[1].grid(True, alpha=0.3)

        # Valeur corrigée
        axes[2].plot(df_result['timestamp'] if has_ts else df_result.index,
                     df_result['corrected_value'], linewidth=0.7, alpha=0.8,
                     color='seagreen', label='Valeur corrigée')
        axes[2].set_ylabel('Valeur corrigée')
        axes[2].legend(fontsize=8)
        axes[2].grid(True, alpha=0.3)

        plt.tight_layout()
        fig_path = Path(FIGURES_DIR) / f'nb{{NOTEBOOK_ID:03d}}_results.png'
        plt.savefig(fig_path, dpi=100, bbox_inches='tight')
        plt.show()
        plt.close()
        print(f"Figure sauvegardée : {{fig_path}}")
        figures_generated = [str(fig_path)]
    """)))

    # ── Cell 10 : Métriques ───────────────────────────────────────────────
    metrics_md = ""
    for m in (metrics or []):
        if isinstance(m, dict):
            metrics_md += f"- **{m.get('name','')}** : {m.get('formula','')} — {m.get('interpretation','')}\n"

    cells.append(new_code_cell(textwrap.dedent(f"""\
        # Évaluation des performances
        # ── Métriques universelles (toujours disponibles) ────────────────────
        _n_total    = len(df_result)
        _n_detected = int(df_result['is_anomaly'].sum())
        _det_rate   = float(df_result['is_anomaly'].mean())

        # Taux de correction : fraction des points où la valeur a été modifiée
        if 'corrected_value' in df_result.columns and 'value' in df_result.columns:
            _changed       = (df_result['corrected_value'].fillna(df_result['value']) != df_result['value']).sum()
            _corr_rate     = round(float(_changed) / max(_n_total, 1), 4)
        else:
            _changed       = 0
            _corr_rate     = 0.0

        metrics_result = {{
            'detection_rate':  round(_det_rate, 4),
            'anomaly_count':   _n_detected,
            'correction_rate': _corr_rate,
        }}

        print(f"Taux de détection  : {{_det_rate*100:.2f}}% ({{_n_detected}} / {{_n_total}})")
        print(f"Taux de correction : {{_corr_rate*100:.2f}}% ({{_changed}} points corrigés)")

        # ── Métriques ML (uniquement si vérité terrain disponible) ───────────
        if 'anomaly_flag' in df_result.columns:
            y_true = df_result['anomaly_flag'].astype(int).values
            y_pred = df_result['is_anomaly'].astype(int).values
            try:
                f1   = f1_score(y_true, y_pred, zero_division=0)
                prec = precision_score(y_true, y_pred, zero_division=0)
                rec  = recall_score(y_true, y_pred, zero_division=0)
                tn, fp, fn_v, tp = confusion_matrix(y_true, y_pred, labels=[0,1]).ravel() if len(set(y_true))>1 else (0,0,0,0)
                fpr  = fp / (fp + tn) if (fp + tn) > 0 else 0.0
                metrics_result.update({{
                    'f1':        round(float(f1), 4),
                    'precision': round(float(prec), 4),
                    'recall':    round(float(rec), 4),
                    'fpr':       round(float(fpr), 4),
                    'tp': int(tp), 'fp': int(fp), 'fn': int(fn_v), 'tn': int(tn),
                }})
                print(f"F1-Score  : {{f1:.4f}}")
                print(f"Précision : {{prec:.4f}}")
                print(f"Rappel    : {{rec:.4f}}")
                print(f"FPR       : {{fpr:.4f}}")
            except Exception as _me:
                print(f"⚠ Calcul métriques ML : {{_me}}")

        print("\\nMétriques JSON :", json.dumps(metrics_result, ensure_ascii=False))
    """)))

    # ── Cell 11 : Conclusion ──────────────────────────────────────────────
    use_case_md = f"\n\n**Exemple concret :** {use_case}" if use_case else ""
    cells.append(new_markdown_cell(textwrap.dedent(f"""\
        ## Conclusion

        L'algorithme **{alg_name}** ({alg_cat}) a été appliqué sur les données synthétiques
        de type **{type_name}** pour traiter le problème : **{prob_title}**.

        **Métriques documentées :**
        {metrics_md or '(voir cellule précédente)'}
        {use_case_md}

        {_format_refs(references)}
    """)))

    nb.cells = cells
    nb.metadata["kernelspec"] = {
        "display_name": "Python 3",
        "language": "python",
        "name": "python3",
    }
    nb.metadata["language_info"] = {
        "name": "python",
        "version": "3.10.0",
    }
    nb.metadata["auto2"] = {
        "data_type_id":  data_type.get("id", ""),
        "problem_key":   problem.get("problem_key", ""),
        "algorithm_key": algorithm.get("algorithm_key", ""),
        "generated_at":  str(pd.Timestamp.now()),
    }
    return nb


def build_comparison_notebook(
    data_type: Dict,
    problem: Dict,
    algorithms: List[Dict],
    dataset_key: str,
    dataset_path: str = "",
    figures_dir: str = "",
    notebook_num: int = 1,
) -> nbformat.NotebookNode:
    """
    Construit un notebook de COMPARAISON pour UN problème avec TOUS ses algorithmes.

    Un seul notebook remplace les N notebooks individuels précédents.
    Chaque algorithme est exécuté sur le même dataset, les métriques sont comparées.
    S3 détecte les lignes "ALGO_RESULT: {...}" pour sauvegarder les métriques par algo.

    Args:
        data_type    : dict data_types (name, domain, description...)
        problem      : dict problems (title, description, problem_key...)
        algorithms   : liste de dicts algorithms pour CE problème
        dataset_key  : clé MySQL du dataset (source primaire)
        dataset_path : chemin CSV fallback
        figures_dir  : répertoire pour sauvegarder les figures
        notebook_num : numéro pour nommage des figures
    """
    nb = new_notebook()
    nb.metadata["kernelspec"] = {"display_name": "Python 3", "language": "python", "name": "python3"}
    nb.metadata["language_info"] = {"name": "python", "version": "3.10.0"}

    type_name  = data_type.get("name", "")
    type_id    = data_type.get("id", "")
    domain     = data_type.get("domain", "")
    prob_key   = problem.get("problem_key", "")
    prob_title = problem.get("title", "")
    prob_desc  = problem.get("description", "")

    for algo in algorithms:
        for f in ("hyperparameters", "required_libraries", "evaluation_metrics",
                  "input_format", "output_format"):
            if isinstance(algo.get(f), str) and algo[f]:
                try:
                    algo[f] = json.loads(algo[f])
                except Exception:
                    algo[f] = []
        if isinstance(algo.get("python_skeleton"), str):
            algo["python_skeleton"] = algo["python_skeleton"].replace("\\n", "\n").replace("\\t", "    ")

    cells = []

    # ── Cell 1 (MD) : Titre et contexte ───────────────────────────────────
    algos_list_md = "\n".join(
        f"| {i} | {a.get('name','—')} | {a.get('category','—')} |"
        for i, a in enumerate(algorithms, 1)
    )
    causes_md = "\n".join(f"- {c}" for c in (problem.get("causes", []) or []))
    cells.append(new_markdown_cell(textwrap.dedent(f"""\
        # Comparaison des Algorithmes — {prob_title}

        | Champ | Valeur |
        |-------|--------|
        | **Type de données** | {type_name} |
        | **Domaine** | {domain} |
        | **Problème** | {prob_title} |
        | **Clé** | `{prob_key}` |

        ## Description du problème

        {prob_desc}

        **Causes identifiées :**
        {causes_md}

        ## Algorithmes comparés ({len(algorithms)})

        | # | Nom | Catégorie |
        |---|-----|-----------|
        {algos_list_md}
    """)))

    # ── Cell 2 (Code) : Paramètres ────────────────────────────────────────
    algo_registry = json.dumps(
        [{"key": a.get("algorithm_key", f"alg{i}"), "id": a.get("id", 0), "name": a.get("name", "")}
         for i, a in enumerate(algorithms, 1)],
        ensure_ascii=False,
    )
    cells.append(new_code_cell(textwrap.dedent(f"""\
        # === PARAMÈTRES ===
        DATASET_KEY  = {json.dumps(dataset_key)}
        DATASET_PATH = r{json.dumps(dataset_path)}
        FIGURES_DIR  = r{json.dumps(figures_dir or str(Path(tempfile.gettempdir()) / "mobility_pipeline" / "figures" / type_id / prob_key))}
        NOTEBOOK_ID  = {notebook_num}
        PROB_KEY     = {json.dumps(prob_key)}
        TYPE_ID      = {json.dumps(type_id)}

        ALGO_REGISTRY = {algo_registry}
    """)))

    # ── Cell 3 (Code) : Imports ───────────────────────────────────────────
    all_libs: set = set()
    for a in algorithms:
        for lib in (a.get("required_libraries") or []):
            pkg = str(lib).split(">=")[0].split("==")[0].strip().lower()
            if "scipy" in pkg:
                all_libs.add("from scipy import signal, stats")
            elif "statsmodels" in pkg:
                all_libs.add("import statsmodels.api as sm")
    # Join with 8-space indent so textwrap.dedent can strip properly
    extra_imports = ("\n        ".join(sorted(all_libs))) if all_libs else ""
    cells.append(new_code_cell(textwrap.dedent(f"""\
        import json, warnings, os, time as _time
        from pathlib import Path
        from datetime import datetime
        import numpy as np
        import pandas as pd
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import seaborn as sns
        from sklearn.metrics import f1_score, precision_score, recall_score, confusion_matrix
        warnings.filterwarnings('ignore')
        sns.set_theme(style='whitegrid', palette='muted')
        os.makedirs(FIGURES_DIR, exist_ok=True)
        {extra_imports}

        def _compute_algo_metrics(df_r, algo_key, algo_id, algo_name, elapsed):
            n = len(df_r)
            n_det = int(df_r['is_anomaly'].sum()) if 'is_anomaly' in df_r.columns else 0
            det_rate = n_det / max(n, 1)
            if 'corrected_value' in df_r.columns and 'value' in df_r.columns:
                changed = (df_r['corrected_value'].fillna(df_r['value']) != df_r['value']).sum()
                corr_rate = round(float(changed) / max(n, 1), 4)
            else:
                corr_rate = 0.0
            m = {{
                'algo_key': algo_key, 'algo_id': algo_id, 'name': algo_name,
                'detection_rate': round(det_rate, 4), 'anomaly_count': n_det,
                'correction_rate': corr_rate, 'exec_time_sec': elapsed,
            }}
            if 'anomaly_flag' in df_r.columns:
                y_true = df_r['anomaly_flag'].astype(int).values
                y_pred = (df_r['is_anomaly'].astype(int).values if 'is_anomaly' in df_r.columns
                          else np.zeros(n, dtype=int))
                try:
                    m['f1']        = round(float(f1_score(y_true, y_pred, zero_division=0)), 4)
                    m['precision'] = round(float(precision_score(y_true, y_pred, zero_division=0)), 4)
                    m['recall']    = round(float(recall_score(y_true, y_pred, zero_division=0)), 4)
                except Exception:
                    pass
            return m
    """)))

    # ── Cell 4 (Code) : Chargement dataset ───────────────────────────────
    cells.append(new_code_cell(textwrap.dedent("""\
        # Chargement du dataset : MySQL data_json → CSV
        from pathlib import Path
        import os, json as _json
        try:
            from dotenv import load_dotenv
            load_dotenv(override=True)
        except ImportError:
            pass

        df = None
        try:
            import mysql.connector as _mc
            _conn = _mc.connect(
                host=os.environ.get('DB_HOST', 'localhost'),
                port=int(os.environ.get('DB_PORT', 3306)),
                user=os.environ.get('DB_USER', 'root'),
                password=os.environ.get('DB_PASSWORD', ''),
                database=os.environ.get('DB_NAME', 'urbain_automation'),
                connect_timeout=5,
            )
            with _conn.cursor(dictionary=True) as _cur:
                _cur.execute(
                    "SELECT data_json, file_path FROM datasets WHERE dataset_key=%s LIMIT 1",
                    (DATASET_KEY,),
                )
                _row = _cur.fetchone()
            _conn.close()
            _dj = (_row or {}).get('data_json') or ''
            if _row and _dj and not _dj.startswith('[MySQL table:'):
                _data = _json.loads(_dj)
                df = pd.DataFrame(_data) if isinstance(_data, list) else pd.DataFrame.from_dict(_data)
                print(f"✓ Dataset chargé depuis MySQL JSON : {len(df):,} lignes")
            if df is None and _row and _row.get('file_path'):
                _fp = _row['file_path'].strip()
                if Path(_fp).exists():
                    df = pd.read_csv(_fp)
                    print(f"✓ Dataset depuis CSV MySQL : {len(df):,} lignes")
                elif _fp and not Path(_fp).suffix:
                    _c2 = _mc.connect(
                        host=os.environ.get('DB_HOST', 'localhost'),
                        port=int(os.environ.get('DB_PORT', 3306)),
                        user=os.environ.get('DB_USER', 'root'),
                        password=os.environ.get('DB_PASSWORD', ''),
                        database=os.environ.get('DB_NAME', 'urbain_automation'),
                        connect_timeout=5,
                    )
                    with _c2.cursor(dictionary=True) as _cur2:
                        _cur2.execute(f"SELECT * FROM `{_fp}` LIMIT 10000")
                        df = pd.DataFrame(_cur2.fetchall())
                    _c2.close()
                    print(f"✓ Dataset chargé depuis table MySQL `{_fp}` : {len(df):,} lignes")
        except Exception as _e:
            print(f"⚠ MySQL indisponible ({_e}) — tentative CSV…")

        if df is None and DATASET_PATH and Path(DATASET_PATH).exists():
            df = pd.read_csv(DATASET_PATH)
            print(f"✓ Dataset depuis CSV : {len(df):,} lignes")

        if df is None:
            raise RuntimeError(f"Dataset introuvable : key={DATASET_KEY!r}")

        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.sort_values('timestamp').reset_index(drop=True)

        print(f"Colonnes : {list(df.columns)}")
        df.head(3)
    """)))

    # ── Cell 5 (Code) : Exploration ───────────────────────────────────────
    cells.append(new_code_cell(textwrap.dedent("""\
        print("=== Statistiques descriptives ===")
        print(df.describe().round(3).to_string())
        print(f"\\nValeurs manquantes:\\n{df.isnull().sum()}")
        if 'anomaly_flag' in df.columns:
            n_a = df['anomaly_flag'].sum()
            print(f"\\nAnomalies injectées : {n_a} ({n_a/len(df)*100:.1f}%)")
    """)))

    # ── Cells 6+ : une cell par algorithme (définition + run) ─────────────
    for algo in algorithms:
        algo_key  = algo.get("algorithm_key", f"alg_{algo.get('id',0)}")
        algo_id   = algo.get("id", 0)
        algo_name = algo.get("name", "")
        skeleton  = _fix_newlines(algo.get("python_skeleton", ""))
        principle = algo.get("principle", "")
        math_form = algo.get("math_formulation", "")

        fn_name = _extract_fn_name(skeleton) or f"run_{algo_key}"
        safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", algo_key)

        cells.append(new_markdown_cell(f"## Algorithme : {algo_name}\n\n**Principe :** {principle}\n\n{math_form}"))

        skeleton_clean = textwrap.dedent(skeleton).strip()
        # Wrap skeleton in try/except so top-level import failures don't crash the cell.
        # We capture the error msg in _skel_msg_ before the except scope ends (Python deletes
        # exception variables after the except block, so the stub function can't reference them).
        skeleton_indented = "\n".join("    " + line for line in skeleton_clean.split("\n"))
        algo_code = (
            f"# ── {algo_name} ──────────────────────────────────────────────\n"
            f"try:\n"
            + skeleton_indented + "\n"
            + f"except Exception as _skel_load_err_{safe_key}:\n"
            + f"    _skel_msg_{safe_key} = str(_skel_load_err_{safe_key})\n"
            + f"    print('⚠ Skeleton non chargé (' + {json.dumps(algo_name)} + '): ' + _skel_msg_{safe_key})\n"
            + f"    def {fn_name}(df, _msg=_skel_msg_{safe_key}):\n"
            + f"        raise RuntimeError(f'Skeleton non chargé: {{_msg}}')\n"
            + "\n\n"
            + textwrap.dedent(f"""\
                _t0_{safe_key} = _time.perf_counter()
                try:
                    _df_{safe_key} = {fn_name}(df.copy())
                    _elapsed_{safe_key} = round(_time.perf_counter() - _t0_{safe_key}, 2)
                    for _c in ['anomaly_score', 'is_anomaly', 'corrected_value']:
                        if _c not in _df_{safe_key}.columns:
                            _df_{safe_key}[_c] = 0.0 if _c == 'anomaly_score' else (False if _c == 'is_anomaly' else _df_{safe_key}.get('value', np.nan))
                    if 'anomaly_flag' in df.columns and 'anomaly_flag' not in _df_{safe_key}.columns:
                        _df_{safe_key}['anomaly_flag'] = df['anomaly_flag'].values
                    _m_{safe_key} = _compute_algo_metrics(_df_{safe_key}, {json.dumps(algo_key)}, {algo_id}, {json.dumps(algo_name)}, _elapsed_{safe_key})
                    _n_det = int(_df_{safe_key}['is_anomaly'].sum())
                    print("✓ " + {json.dumps(algo_name)} + f" — {{_n_det}} anomalies détectées ({{_n_det/len(df)*100:.1f}}%) en {{_elapsed_{safe_key}:.2f}}s")
                except Exception as _err_{safe_key}:
                    _elapsed_{safe_key} = round(_time.perf_counter() - _t0_{safe_key}, 2)
                    _df_{safe_key} = None
                    _m_{safe_key} = {{'algo_key': {json.dumps(algo_key)}, 'algo_id': {algo_id}, 'name': {json.dumps(algo_name)}, 'error': str(_err_{safe_key})}}
                    print("⚠ " + {json.dumps(algo_name)} + f" erreur : {{_err_{safe_key}}}")
                print("ALGO_RESULT:", json.dumps(_m_{safe_key}))
            """)
        )
        cells.append(new_code_cell(algo_code))

    # ── Cell N (Code) : Comparaison graphique ────────────────────────────
    safe_keys = [re.sub(r"[^a-zA-Z0-9_]", "_", a.get("algorithm_key", f"alg_{a.get('id',0)}"))
                 for a in algorithms]
    metrics_collect = "\n".join(
        f"        if _m_{sk} and 'error' not in _m_{sk}: _all_metrics.append(_m_{sk})"
        for sk in safe_keys
    )
    cells.append(new_code_cell(textwrap.dedent(f"""\
        # === GRAPHIQUES DE COMPARAISON (2 figures séparées) ===
        _all_metrics = []
{metrics_collect}

        if not _all_metrics:
            print("⚠ Aucune métrique disponible pour la comparaison")
        else:
            _labels = [m['name'][:24] for m in _all_metrics]
            _det    = [m.get('detection_rate', 0) for m in _all_metrics]
            _f1s    = [m.get('f1') or 0 for m in _all_metrics]
            _corr   = [m.get('correction_rate', 0) for m in _all_metrics]
            _times  = [m.get('exec_time_sec', 0) for m in _all_metrics]
            _has_f1 = any(m.get('f1') is not None for m in _all_metrics)
            n = len(_all_metrics)
            x = np.arange(n)
            w = 0.25 if _has_f1 else 0.32

            # ── Figure 1 : Performances ───────────────────────────────────────
            fig1, ax1 = plt.subplots(figsize=(max(7, n * 2.4), 5))

            b1 = ax1.bar(x - w,     _det,  w, label="Taux de détection",  color="#1976D2", alpha=0.9, zorder=3)
            b2 = ax1.bar(x,         _corr, w, label="Taux de correction", color="#388E3C", alpha=0.9, zorder=3)
            if _has_f1:
                b3 = ax1.bar(x + w, _f1s,  w, label="F1-Score",           color="#F57C00", alpha=0.9, zorder=3)
            for _bars in ([b1, b2] + ([b3] if _has_f1 else [])):
                for _bar in _bars:
                    _h = _bar.get_height()
                    if _h > 0.01:
                        ax1.text(_bar.get_x() + _bar.get_width() / 2, _h + 0.018,
                                 f"{{_h:.0%}}", ha='center', va='bottom', fontsize=8, fontweight='bold')
            ax1.set_xticks(x)
            ax1.set_xticklabels(_labels, rotation=18, ha='right', fontsize=9)
            ax1.set_ylim(0, 1.28)
            ax1.set_ylabel("Taux", fontsize=11)
            ax1.set_xlabel("Algorithmes", fontsize=10)
            ax1.set_title("{prob_title[:60]}", fontsize=11, fontweight='bold', pad=10)
            ax1.legend(fontsize=9, loc='upper right', framealpha=0.8)
            ax1.grid(axis='y', linestyle='--', alpha=0.35, zorder=0)
            ax1.spines[['top', 'right']].set_visible(False)
            fig1.suptitle("Comparaison des performances", fontsize=13, fontweight='bold', y=1.01)
            fig1.tight_layout()
            _perf_path = Path(FIGURES_DIR) / f"nb{{NOTEBOOK_ID:03d}}_performance.png"
            fig1.savefig(_perf_path, dpi=150, bbox_inches='tight')
            plt.show()
            plt.close(fig1)
            print(f"Figure performances : {{_perf_path}}")

            # ── Figure 2 : Temps d'exécution ─────────────────────────────────
            fig2, ax2 = plt.subplots(figsize=(max(6, n * 1.8), 4))

            _min_t  = min(_times) if _times else 0
            _colors = ["#2196F3" if t == _min_t else "#90A4AE" for t in _times]
            _bars2  = ax2.bar(x, _times, color=_colors, alpha=0.88, zorder=3)
            for _bar2, _t in zip(_bars2, _times):
                if _t > 0:
                    ax2.text(_bar2.get_x() + _bar2.get_width() / 2, _t + 0.02,
                             f"{{_t:.2f}}s", ha='center', va='bottom', fontsize=8, fontweight='bold')
            ax2.set_xticks(x)
            ax2.set_xticklabels(_labels, rotation=18, ha='right', fontsize=9)
            ax2.set_ylabel("Secondes (s)", fontsize=11)
            ax2.set_xlabel("Algorithmes", fontsize=10)
            ax2.set_title("{prob_title[:60]}", fontsize=11, fontweight='bold', pad=10)
            ax2.grid(axis='y', linestyle='--', alpha=0.35, zorder=0)
            ax2.spines[['top', 'right']].set_visible(False)
            ax2.set_ylim(0, max(_times) * 1.35 + 0.1 if _times else 5)
            from matplotlib.patches import Patch as _Patch
            ax2.legend(handles=[_Patch(color="#2196F3", label="Plus rapide"),
                                 _Patch(color="#90A4AE", label="Autres")],
                       fontsize=9, loc='upper right')
            fig2.suptitle("Temps d'exécution par algorithme", fontsize=13, fontweight='bold', y=1.01)
            fig2.tight_layout()
            _timing_path = Path(FIGURES_DIR) / f"nb{{NOTEBOOK_ID:03d}}_timing.png"
            fig2.savefig(_timing_path, dpi=150, bbox_inches='tight')
            plt.show()
            plt.close(fig2)
            print(f"Figure timing : {{_timing_path}}")
    """)))

    # ── Cell finale (MD) : Conclusion ────────────────────────────────────
    cells.append(new_markdown_cell(textwrap.dedent(f"""\
        ## Conclusion

        Comparaison de **{len(algorithms)} algorithmes** appliqués sur le problème :
        **{prob_title}** (type de données : **{type_name}**).

        Les métriques ci-dessus sont obtenues sur un dataset synthétique de test.
        Une validation sur données réelles est recommandée avant déploiement.
    """)))

    nb.cells = cells
    nb.metadata["auto2"] = {
        "data_type_id":  type_id,
        "problem_key":   prob_key,
        "notebook_type": "comparison",
        "n_algorithms":  len(algorithms),
        "generated_at":  str(pd.Timestamp.now()),
    }
    return nb


def build_algorithm_standalone_notebook(
    algo_name: str,
    algo_key: str,
    algo_id: int,
    skeleton_code: str,
    prob_title: str,
    prob_key: str,
    type_name: str,
    type_id: str,
    dataset_path: str,
    figures_dir: str,
    notebook_num: int = 1,
) -> nbformat.NotebookNode:
    """
    Notebook standalone pour UN seul algorithme.
    Génère 2 graphiques : performances + temps d'exécution.
    """
    nb    = new_notebook()
    cells = []

    cells.append(new_markdown_cell(textwrap.dedent(f"""\
        # Algorithme : {algo_name}

        **Problème :** {prob_title}
        **Type de données :** {type_name}

        Ce notebook exécute l'algorithme de manière autonome et produit :
        - Un graphique des performances (taux de détection, correction, F1)
        - Un graphique du temps d'exécution
    """)))

    cells.append(new_code_cell(textwrap.dedent(f"""\
        # === PARAMÈTRES ===
        ALGO_NAME    = {json.dumps(algo_name)}
        ALGO_KEY     = {json.dumps(algo_key)}
        ALGO_ID      = {algo_id}
        DATASET_PATH = r{json.dumps(dataset_path)}
        FIGURES_DIR  = r{json.dumps(figures_dir)}
        NOTEBOOK_ID  = {notebook_num}
        import os
        os.makedirs(FIGURES_DIR, exist_ok=True)
    """)))

    cells.append(new_code_cell(_imports_cell(["numpy", "pandas", "matplotlib", "sklearn"])))

    cells.append(new_code_cell(textwrap.dedent("""\
        # Chargement du dataset
        import pandas as pd
        from pathlib import Path

        df = None
        if DATASET_PATH and Path(DATASET_PATH).exists():
            df = pd.read_csv(DATASET_PATH)
            print(f"Dataset chargé : {len(df)} lignes, {df.shape[1]} colonnes")
        else:
            import numpy as np
            np.random.seed(42)
            n = 500
            df = pd.DataFrame({
                "timestamp": pd.date_range("2024-01-01", periods=n, freq="1min"),
                "value":     np.random.randn(n) + np.sin(np.arange(n) * 0.1),
                "label":     np.where(np.random.rand(n) < 0.05, 1, 0),
            })
            print(f"Dataset synthétique généré : {len(df)} lignes")
        print(df.head(3))
    """)))

    safe_key = re.sub(r"[^a-zA-Z0-9_]", "_", algo_key)
    cells.append(new_code_cell(textwrap.dedent(f"""\
        # Exécution de l'algorithme : {algo_name}
        import time, json
        import numpy as np
        import pandas as pd

        def run_{safe_key}(df):
{textwrap.indent(skeleton_code, '            ')}

        _t0 = time.perf_counter()
        try:
            _result = run_{safe_key}(df.copy())
            _exec_time = round(time.perf_counter() - _t0, 4)
            if isinstance(_result, dict):
                _metrics = _result
            else:
                _metrics = {{'detection_rate': 0.0, 'correction_rate': 0.0}}
            _metrics['exec_time_sec'] = _exec_time
            _metrics['algo_name']     = ALGO_NAME
            print("Résultat :", json.dumps({{k: v for k, v in _metrics.items() if not callable(v)}}, indent=2, default=str))
        except Exception as _err:
            _exec_time = round(time.perf_counter() - _t0, 4)
            _metrics   = {{'detection_rate': 0.0, 'correction_rate': 0.0,
                           'exec_time_sec': _exec_time, 'error': str(_err)}}
            print(f"⚠ Erreur algorithme : {{_err}}")
    """)))

    cells.append(new_code_cell(textwrap.dedent(f"""\
        # === GRAPHIQUE 1 : PERFORMANCES ===
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from pathlib import Path

        _det  = _metrics.get('detection_rate',  0.0) or 0.0
        _corr = _metrics.get('correction_rate', 0.0) or 0.0
        _f1   = _metrics.get('f1')

        _metric_names  = ["Taux de détection", "Taux de correction"]
        _metric_values = [_det, _corr]
        _metric_colors = ["#1976D2", "#388E3C"]
        if _f1 is not None:
            _metric_names.append("F1-Score")
            _metric_values.append(float(_f1))
            _metric_colors.append("#F57C00")

        fig1, ax1 = plt.subplots(figsize=(max(5, len(_metric_names) * 1.8), 4.5))
        _bars1 = ax1.bar(range(len(_metric_names)), _metric_values,
                         color=_metric_colors, alpha=0.9, zorder=3)
        for _b, _v in zip(_bars1, _metric_values):
            if _v > 0.01:
                ax1.text(_b.get_x() + _b.get_width() / 2, _v + 0.02,
                         f"{{_v:.1%}}", ha='center', va='bottom', fontsize=11, fontweight='bold')
        ax1.set_xticks(range(len(_metric_names)))
        ax1.set_xticklabels(_metric_names, fontsize=10)
        ax1.set_ylim(0, 1.25)
        ax1.set_ylabel("Taux", fontsize=11)
        ax1.grid(axis='y', linestyle='--', alpha=0.35, zorder=0)
        ax1.spines[['top', 'right']].set_visible(False)
        fig1.suptitle(f"Performances — {{ALGO_NAME}}", fontsize=13, fontweight='bold')
        ax1.set_title("{prob_title[:70]}", fontsize=10, pad=8)
        fig1.tight_layout()
        _perf_path = Path(FIGURES_DIR) / f"algo_{{NOTEBOOK_ID:03d}}_performance.png"
        fig1.savefig(_perf_path, dpi=150, bbox_inches='tight')
        plt.show()
        plt.close(fig1)
        print(f"Figure performances : {{_perf_path}}")
    """)))

    cells.append(new_code_cell(textwrap.dedent(f"""\
        # === GRAPHIQUE 2 : TEMPS D'EXÉCUTION ===
        import matplotlib.pyplot as plt
        from pathlib import Path

        _t = _metrics.get('exec_time_sec', 0.0) or 0.0

        fig2, ax2 = plt.subplots(figsize=(4, 3.5))
        _bar = ax2.bar([ALGO_NAME], [_t], color="#2196F3", alpha=0.88, zorder=3)
        ax2.text(_bar[0].get_x() + _bar[0].get_width() / 2, _t + 0.005,
                 f"{{_t:.3f}}s", ha='center', va='bottom', fontsize=12, fontweight='bold')
        ax2.set_ylabel("Secondes (s)", fontsize=11)
        ax2.set_ylim(0, _t * 1.4 + 0.1)
        ax2.grid(axis='y', linestyle='--', alpha=0.35, zorder=0)
        ax2.spines[['top', 'right']].set_visible(False)
        fig2.suptitle(f"Temps d'exécution — {{ALGO_NAME}}", fontsize=13, fontweight='bold')
        ax2.set_title("{prob_title[:70]}", fontsize=10, pad=8)
        fig2.tight_layout()
        _timing_path = Path(FIGURES_DIR) / f"algo_{{NOTEBOOK_ID:03d}}_timing.png"
        fig2.savefig(_timing_path, dpi=150, bbox_inches='tight')
        plt.show()
        plt.close(fig2)
        print(f"Figure timing : {{_timing_path}}")
    """)))

    cells.append(new_markdown_cell(textwrap.dedent(f"""\
        ## Résumé

        Algorithme **{algo_name}** exécuté sur le problème **{prob_title}**.
        Les figures ci-dessus montrent les performances et le temps d'exécution
        sur un dataset synthétique représentatif de **{type_name}**.
    """)))

    nb.cells = cells
    nb.metadata["auto2"] = {
        "data_type_id":  type_id,
        "problem_key":   prob_key,
        "algo_key":      algo_key,
        "notebook_type": "standalone_algorithm",
        "generated_at":  str(pd.Timestamp.now()),
    }
    return nb


def save_notebook(nb: nbformat.NotebookNode, path: str) -> str:
    """Sauvegarde un notebook sur disque. Retourne le chemin absolu."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        nbformat.write(nb, f)
    return str(p.resolve())


def notebook_to_json_str(nb: nbformat.NotebookNode) -> str:
    """Sérialise un notebook en string JSON pour stockage MySQL."""
    return nbformat.writes(nb)
