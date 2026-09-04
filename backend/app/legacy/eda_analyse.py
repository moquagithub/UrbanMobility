"""
Programme d'Analyse Exploratoire de Données (EDA) avec Anonymisation et Rapports LaTeX
Input:  fichier CSV (brut), optionnellement fichier CSV transformé
Output: rapport_eda.tex    — rapport LaTeX détaillé
        rapport_synthese.tex — rapport LaTeX de synthèse (si données transformées fournies)

Usage:
  python eda_analyse.py data.csv                         → rapport détaillé uniquement
  python eda_analyse.py data.csv data_transforme.csv     → rapport détaillé + synthèse
  python eda_analyse.py --app                            → lance App EDA (Streamlit)
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
import warnings
import sys
import os
import shutil
import traceback
import logging
import csv as csv_module
from datetime import datetime
from io import BytesIO, StringIO

# ── Anonymisation des valeurs PII ──
from pii_anonymizer import load_and_anonymize_v2, PIIAnonymizer

# ── Traitements spécifiques Quiz/questionnaire ──
from quiz_processor import QuizProcessor, build_latex_quiz_section

warnings.filterwarnings('ignore')


# ─────────────────────────────────────────────
# 0. GESTIONNAIRE D'ERREURS — LOGFILE
# ─────────────────────────────────────────────

class ErrorLogger:
    """
    Écrit un diagnostic détaillé dans logfile-errors.log pour chaque
    ligne du CSV qui génère une erreur. Le traitement continue sans
    cette ligne.
    """

    def __init__(self, csv_path: str):
        log_dir = os.path.dirname(os.path.abspath(csv_path))
        self.log_path = os.path.join(log_dir, "logfile-errors.log")
        self.errors: list[dict] = []

        # Configurer le logger Python standard
        self.logger = logging.getLogger("eda_errors")
        self.logger.setLevel(logging.DEBUG)
        # Éviter d'ajouter des handlers dupliqués en cas de réimport
        if not self.logger.handlers:
            fh = logging.FileHandler(self.log_path, mode='w', encoding='utf-8')
            fh.setFormatter(logging.Formatter('%(message)s'))
            self.logger.addHandler(fh)

        self._write_header(csv_path)

    def _write_header(self, csv_path: str):
        sep = "=" * 80
        self.logger.info(sep)
        self.logger.info("LOGFILE D'ERREURS — ANALYSE EXPLORATOIRE DE DONNÉES")
        self.logger.info(f"Fichier source  : {os.path.abspath(csv_path)}")
        self.logger.info(f"Démarrage       : {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        self.logger.info(sep)
        self.logger.info("")

    def log(self, line_number: int, raw_line: str, error: Exception):
        """Enregistre une erreur de parsing/traitement pour une ligne donnée."""
        entry = {
            "ligne": line_number,
            "erreur_type": type(error).__name__,
            "erreur_msg": str(error),
            "raw_line": raw_line.strip() if raw_line else "(non disponible)",
            "traceback": traceback.format_exc(),
            "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        }
        self.errors.append(entry)

        sep = "-" * 80
        self.logger.info(sep)
        self.logger.info(f"[ERREUR] Ligne n°{line_number}  —  {entry['timestamp']}")
        self.logger.info(f"  Type        : {entry['erreur_type']}")
        self.logger.info(f"  Message     : {entry['erreur_msg']}")
        self.logger.info(f"  Contenu brut: {entry['raw_line'][:200]}")
        self.logger.info("  Traceback complet :")
        for tb_line in entry["traceback"].splitlines():
            self.logger.info(f"    {tb_line}")
        self.logger.info(f"  → Ligne ignorée, traitement continué.")
        self.logger.info("")

        # Afficher aussi dans le terminal
        print(f"  ⚠️  Ligne {line_number} ignorée ({entry['erreur_type']}): {entry['erreur_msg'][:80]}")

    def finalize(self, total_lines: int, skipped: int):
        """Écrit le résumé final dans le log."""
        sep = "=" * 80
        self.logger.info(sep)
        self.logger.info("RÉSUMÉ DU TRAITEMENT")
        self.logger.info(f"  Lignes totales lues    : {total_lines}")
        self.logger.info(f"  Lignes traitées        : {total_lines - skipped}")
        self.logger.info(f"  Lignes ignorées        : {skipped}")
        self.logger.info(f"  Fin du traitement      : {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
        self.logger.info(sep)

        if skipped == 0:
            print(f"  ✅ Aucune erreur détectée.")
        else:
            print(f"  📋 {skipped} ligne(s) ignorée(s) — voir : {self.log_path}")

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def skipped_lines(self) -> list[int]:
        return [e["ligne"] for e in self.errors]


# ─────────────────────────────────────────────
# 1. CHARGEMENT ROBUSTE ET ANONYMISATION
# ─────────────────────────────────────────────

def load_and_anonymize(csv_path: str) -> tuple[pd.DataFrame, dict, ErrorLogger]:
    """
    Charge le CSV en détectant automatiquement le séparateur, logue les erreurs
    éventuelles, puis anonymise les noms de colonnes.
    Stratégie : pd.read_csv avec sep="python" (engine python, auto-détection)
    pour éviter tout problème de validation manuelle ligne par ligne.
    """
    error_logger = ErrorLogger(csv_path)

    if not os.path.exists(csv_path):
        raise ValueError(f"Fichier introuvable : {csv_path}")

    # ── Étape 1 : détecter le séparateur sur les premières lignes ────────────
    with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
        sample = f.read(65536)  # lire 64KB max pour la détection

    if not sample.strip():
        raise ValueError("Le fichier CSV est vide.")

    # Compter les occurrences de chaque candidat dans les données
    candidates = [';', ',', '\t', '|']
    lines_sample = [l for l in sample.splitlines() if l.strip()]
    # Utiliser la 2ème ligne (1ère ligne de données) si disponible, sinon la 1ère
    ref_line = lines_sample[1] if len(lines_sample) > 1 else lines_sample[0]
    counts = {c: ref_line.count(c) for c in candidates}
    sep = max(counts, key=counts.get)
    if counts[sep] == 0:
        # Fallback : essayer Sniffer
        try:
            dialect = csv_module.Sniffer().sniff(sample[:4096], delimiters=";,\t|")
            sep = dialect.delimiter
        except csv_module.Error:
            sep = ','

    print(f"  📌 Séparateur détecté : '{sep}'")

    # ── Étape 2 : chargement direct avec pd.read_csv ──────────────────────────
    # on_bad_lines="warn" : les lignes malformées sont ignorées sans planter
    try:
        df = pd.read_csv(
            csv_path,
            sep=sep,
            encoding='utf-8',
            encoding_errors='replace',
            on_bad_lines='warn',   # pandas >= 1.3
            low_memory=False,
        )
    except TypeError:
        # pandas < 1.3 : paramètre différent
        df = pd.read_csv(
            csv_path,
            sep=sep,
            encoding='utf-8',
            error_bad_lines=False,
            warn_bad_lines=True,
            low_memory=False,
        )

    if df.empty:
        raise ValueError(
            f"Aucune ligne valide chargée (séparateur='{sep}'). "
            "Vérifiez le format du fichier."
        )

    total_data_lines = max(len(lines_sample) - 1, 1)
    skipped = max(total_data_lines - len(df), 0)
    error_logger.finalize(total_data_lines, skipped)
    print(f"  ✅ {len(df)} lignes × {len(df.columns)} colonnes chargées (sep='{sep}')")

    # ── Étape 4 : anonymisation ──
    original_cols = list(df.columns)
    anon_map = {col: f"VAR_{i+1:02d}" for i, col in enumerate(original_cols)}
    df.rename(columns=anon_map, inplace=True)

    return df, anon_map, error_logger


# ─────────────────────────────────────────────
# 2. MÉTADONNÉES
# ─────────────────────────────────────────────

def compute_metadata(df: pd.DataFrame, anon_map: dict) -> dict:
    total_cells = df.size if df.size > 0 else 1  # guard division par zéro
    meta = {
        "nb_lignes": len(df),
        "nb_colonnes": len(df.columns),
        "noms_anonymes": list(df.columns),
        "noms_originaux": list(anon_map.keys()),
        "mapping": anon_map,
        "date_analyse": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "taille_totale_cellules": df.size,
        "valeurs_manquantes_total": int(df.isnull().sum().sum()),
        "taux_completude_global": round(100 * (1 - df.isnull().sum().sum() / total_cells), 2),
        "colonnes_numeriques": list(df.select_dtypes(include='number').columns),
        "colonnes_texte": list(df.select_dtypes(include='object').columns),
        "doublons": int(df.duplicated().sum()),
    }
    col_meta = {}
    for col in df.columns:
        s = df[col]
        n_s = len(s) if len(s) > 0 else 1  # guard division par zéro
        missing = int(s.isnull().sum())
        col_meta[col] = {
            "dtype": str(s.dtype),
            "valeurs_manquantes": missing,
            "taux_completude": round(100 * (1 - missing / n_s), 1),
            "valeurs_uniques": int(s.nunique()),
            "valeur_la_plus_freq": str(s.mode().iloc[0]) if not s.empty and not s.dropna().empty else "N/A",
        }
        if pd.api.types.is_numeric_dtype(s):
            col_meta[col].update({
                "min": round(float(s.min(skipna=True)), 4) if not s.dropna().empty else None,
                "max": round(float(s.max(skipna=True)), 4) if not s.dropna().empty else None,
                "moyenne": round(float(s.mean(skipna=True)), 4) if not s.dropna().empty else None,
                "mediane": round(float(s.median(skipna=True)), 4) if not s.dropna().empty else None,
                "ecart_type": round(float(s.std(skipna=True)), 4) if not s.dropna().empty else None,
                "skewness": round(float(s.skew(skipna=True)), 4) if not s.dropna().empty else None,
                "kurtosis": round(float(s.kurtosis(skipna=True)), 4) if not s.dropna().empty else None,
            })
    meta["colonnes"] = col_meta
    return meta


# ─────────────────────────────────────────────
# 3. IMPORTANCE DES COLONNES
# ─────────────────────────────────────────────

def compute_importance(df: pd.DataFrame) -> pd.DataFrame:
    """
    Score d'importance composite basé sur :
      - Taux de complétude (30%)
      - Variance normalisée pour les num. / entropie pour les cat. (35%)
      - Corrélation max avec les autres colonnes num. (20%)
      - Nombre de valeurs uniques normalisé (15%)
    """
    scores = {}
    num_df = df.select_dtypes(include='number')

    for col in df.columns:
        s = df[col]
        n = len(s) if len(s) > 0 else 1  # guard division par zéro

        # Complétude
        completude = 1 - s.isnull().sum() / n

        # Variance / entropie
        if pd.api.types.is_numeric_dtype(s):
            vals = s.dropna()
            if vals.std() == 0 or len(vals) < 2:
                variabilite = 0.0
            else:
                variabilite = float(np.clip((vals.std() / (abs(vals.mean()) + 1e-9)), 0, 1))
                variabilite = min(variabilite, 1.0)
        else:
            freq = s.value_counts(normalize=True, dropna=True)
            entropy = float(-np.sum(freq * np.log2(freq + 1e-9)))
            max_entropy = np.log2(max(s.nunique(), 1))
            variabilite = entropy / max_entropy if max_entropy > 0 else 0.0

        # Corrélation max
        # Guard : col doit exister dans num_df (peut avoir disparu après encodage get_dummies)
        if pd.api.types.is_numeric_dtype(s) and col in num_df.columns and len(num_df.columns) > 1:
            try:
                corr_matrix = num_df.corr()
                if col in corr_matrix.columns:
                    corr_vals = corr_matrix[col].drop(col, errors='ignore').abs()
                    corr_max = float(corr_vals.max()) if not corr_vals.empty else 0.0
                else:
                    corr_max = 0.0
            except Exception:
                corr_max = 0.0
        else:
            corr_max = 0.0

        # Unicité
        unicite = s.nunique() / n if n > 0 else 0.0

        score = (0.30 * completude +
                 0.35 * variabilite +
                 0.20 * corr_max +
                 0.15 * unicite)

        scores[col] = {
            "score_importance": round(score * 100, 1),
            "completude_pct": round(completude * 100, 1),
            "variabilite_norm": round(variabilite * 100, 1),
            "correlation_max": round(corr_max * 100, 1),
            "unicite_norm": round(unicite * 100, 1),
        }

    result = pd.DataFrame(scores).T
    result = result.sort_values("score_importance", ascending=False)
    result["rang"] = range(1, len(result) + 1)
    return result


# ─────────────────────────────────────────────
# 5. DESCRIPTION TEXTUELLE — IMPORTANCE
# ─────────────────────────────────────────────

def describe_importance(imp_df: pd.DataFrame, anon_map: dict) -> list[str]:
    """
    Génère une liste de paragraphes décrivant en langage naturel
    les résultats du graphique d'importance des variables.
    """
    reverse_map = {v: k for k, v in anon_map.items()}
    n = len(imp_df)
    paragraphs = []

    # ── Vue d'ensemble ──
    top1     = imp_df.index[0]
    top1_orig = reverse_map.get(top1, top1)
    top1_score = imp_df.loc[top1, "score_importance"]

    last1      = imp_df.index[-1]
    last1_orig = reverse_map.get(last1, last1)
    last1_score = imp_df.loc[last1, "score_importance"]

    score_mean = imp_df["score_importance"].mean()
    score_std  = imp_df["score_importance"].std()

    paragraphs.append(
        f"Le graphique ci-dessus présente le classement des <b>{n} variables</b> selon leur score "
        f"d'importance composite (de 0 à 100%). Le score moyen s'établit à <b>{score_mean:.1f}%</b> "
        f"avec un écart-type de {score_std:.1f}%, ce qui indique une "
        + ("dispersion importante entre variables — certaines apportent nettement plus d'information que d'autres."
           if score_std > 15 else
           "distribution relativement homogène — les variables ont des niveaux d'information comparables.")
    )

    # ── Variable la plus importante ──
    top_row = imp_df.loc[top1]
    driver = max(
        [("la complétude (peu de valeurs manquantes)", top_row["completude_pct"]),
         ("la variabilité de ses valeurs", top_row["variabilite_norm"]),
         ("sa forte corrélation avec d'autres variables", top_row["correlation_max"]),
         ("la diversité de ses valeurs uniques", top_row["unicite_norm"])],
        key=lambda x: x[1]
    )
    paragraphs.append(
        f"<b>Variable la plus importante : {top1} ({top1_orig})</b> avec un score de "
        f"<b>{top1_score:.1f}%</b>. Son rang de tête s'explique principalement par {driver[0]} "
        f"(composante à {driver[1]:.1f}%). Cette variable constitue un candidat prioritaire "
        f"pour la modélisation ou l'analyse approfondie."
    )

    # ── Top 3 ──
    top3 = imp_df.head(min(3, n))
    top3_desc = ", ".join(
        f"{var} ({reverse_map.get(var, var)}, {top3.loc[var, 'score_importance']:.1f}%)"
        for var in top3.index
    )
    if n >= 3:
        paragraphs.append(
            f"Le <b>trio de tête</b> — {top3_desc} — concentre les variables présentant "
            f"la combinaison la plus favorable de complétude, variabilité et corrélations. "
            f"Elles devraient être conservées en priorité dans tout processus de sélection de variables."
        )

    # ── Variables faibles ──
    threshold_low = 25.0
    weak_vars = imp_df[imp_df["score_importance"] < threshold_low]
    if not weak_vars.empty:
        weak_list = ", ".join(
            f"{v} ({reverse_map.get(v, v)}, {imp_df.loc[v, 'score_importance']:.1f}%)"
            for v in weak_vars.index
        )
        # Diagnostiquer pourquoi elles sont faibles
        reasons = []
        for v in weak_vars.index:
            r = weak_vars.loc[v]
            if r["completude_pct"] < 50:
                reasons.append(f"{v} a un taux de complétude faible ({r['completude_pct']:.1f}%)")
            elif r["variabilite_norm"] < 10:
                reasons.append(f"{v} présente peu de variabilité ({r['variabilite_norm']:.1f}%)")
        reason_str = (". En particulier : " + "; ".join(reasons[:3]) + ".") if reasons else "."
        paragraphs.append(
            f"À l'inverse, <b>{len(weak_vars)} variable(s) présentent un score inférieur à "
            f"{threshold_low}%</b> : {weak_list}{reason_str} "
            f"Ces variables sont candidates à la suppression ou à un traitement préalable "
            f"(imputation, recodage) avant modélisation."
        )

    # ── Analyse par composante dominante ──
    # Identifier quelles composantes dominent globalement
    comp_means = {
        "complétude":   imp_df["completude_pct"].mean(),
        "variabilité":  imp_df["variabilite_norm"].mean(),
        "corrélation":  imp_df["correlation_max"].mean(),
        "unicité":      imp_df["unicite_norm"].mean(),
    }
    dominant_comp = max(comp_means, key=comp_means.get)
    weak_comp     = min(comp_means, key=comp_means.get)
    paragraphs.append(
        f"Sur l'ensemble des variables, la composante <b>{dominant_comp}</b> est celle qui contribue "
        f"le plus au score moyen ({comp_means[dominant_comp]:.1f}%), tandis que la composante "
        f"<b>{weak_comp}</b> est la plus faible ({comp_means[weak_comp]:.1f}%). "
        + ("Le faible niveau moyen de corrélation inter-variables suggère une bonne indépendance "
           "entre features — favorable pour les modèles linéaires."
           if comp_means["corrélation"] < 30 else
           "Le niveau élevé de corrélation inter-variables signale un risque de redondance "
           "— une réduction de dimensionnalité (ACP) est conseillée.")
    )

    # ── Variable la moins importante ──
    paragraphs.append(
        f"<b>Variable la moins informative : {last1} ({last1_orig})</b> avec un score de seulement "
        f"<b>{last1_score:.1f}%</b>. Son faible score reflète une combinaison défavorable sur "
        f"les quatre critères d'évaluation. Son inclusion dans un modèle prédictif pourrait "
        f"introduire du bruit sans apporter de valeur explicative."
    )

    return paragraphs


# ─────────────────────────────────────────────
# 6. UTILITAIRES LATEX
# ─────────────────────────────────────────────

PALETTE = ['#264653','#2A9D8F','#E9C46A','#F4A261','#E63946',
           '#457B9D','#1D3557','#06D6A0','#FFB703','#FB8500']


def _tex(s):
    """Échappe les caractères spéciaux LaTeX."""
    if not isinstance(s, str):
        s = str(s)
        
    # --- AJOUT : On ne touche pas aux macros LaTeX qu'on injecte manuellement ---
    if s.startswith(r'\cellcolor'):
        return s
        
    for old, new in [
        ('\\', r'\textbackslash{}'), ('&', r'\&'), ('%', r'\%'),
        ('$', r'\$'), ('#', r'\#'), ('{', r'\{'), ('}', r'\}'),
        ('~', r'\textasciitilde{}'), ('^', r'\^{}'), ('_', r'\_'),
        ('<', r'\textless{}'), ('>', r'\textgreater{}'),
    ]:
        s = s.replace(old, new)
    return s


def _fig_save(fig, out_dir, name):
    """Sauvegarde une figure matplotlib en PNG et retourne le chemin."""
    import os
    path = os.path.join(out_dir, f"{name}.png")
    fig.savefig(path, dpi=120, bbox_inches='tight', facecolor='white')
    import matplotlib.pyplot as plt
    plt.close(fig)
    return path


def _latex_table(headers, rows, col_spec=None):
    """Génère une table LaTeX (longtable) compatible multi-pages."""
    n = len(headers)
    if col_spec is None:
        col_spec = 'l' + 'r' * (n - 1)
    
    lines = [
        r'\begingroup', 
        r'\small',
        r'\begin{longtable}{' + col_spec + r'}',
        r'\toprule',
        ' & '.join(_tex(str(h)) for h in headers) + r' \\',
        r'\midrule',
        r'\endfirsthead',
        r'\toprule',
        ' & '.join(_tex(str(h)) for h in headers) + r' \textit{(Suite)} \\',
        r'\midrule',
        r'\endhead',
        r'\bottomrule',
        r'\endfoot',
        r'\bottomrule',
        r'\endlastfoot'
    ]
    for row in rows:
        lines.append(' & '.join(_tex(str(c)) for c in row) + r' \\')
    lines += [r'\end{longtable}', r'\endgroup']
    
    return '\n'.join(lines)


def _include_fig(path, caption, label, out_dir, width=r'0.95\linewidth'):
    """Retourne la commande LaTeX pour inclure une figure."""
    import os
    if not path or not os.path.exists(path):
        return r'\textit{(Figure non disponible)}' + '\n\n'
    rel = os.path.relpath(path, out_dir).replace('\\', '/')
    return (
        '\\begin{figure}[H]\n\\centering\n'
        '\\includegraphics[width=' + width + ']{' + rel + '}\n'
        '\\caption{' + _tex(caption) + '}\n'
        '\\label{fig:' + label + '}\n'
        '\\end{figure}\n\n'
    )


# ─────────────────────────────────────────────
# 6b. FIGURES PNG POUR LATEX
# ─────────────────────────────────────────────

def fig_missing_png(df, out_dir):
    import matplotlib.pyplot as plt
    miss = df.isnull().mean() * 100
    miss = miss[miss > 0].sort_values(ascending=False)
    if miss.empty:
        return None
    fig, ax = plt.subplots(figsize=(10, max(3, len(miss) * 0.35)))
    colors_bar = ['#E63946' if v > 20 else '#F4A261' if v > 5 else '#2A9D8F'
                  for v in miss.values]
    ax.barh(miss.index[::-1], miss.values[::-1], color=colors_bar[::-1])
    ax.axvline(20, color='red', linestyle='--', linewidth=1.5, label='Seuil 20%')
    ax.set_xlabel("Taux de valeurs manquantes (%)")
    ax.set_title("Valeurs manquantes par variable")
    ax.legend()
    plt.tight_layout()
    return _fig_save(fig, out_dir, "fig_missing")


def fig_distributions_png(df, out_dir):
    import matplotlib.pyplot as plt
    import numpy as np
    num_cols = df.select_dtypes(include='number').columns.tolist()[:20]
    if not num_cols:
        return None
    ncols = min(4, len(num_cols))
    nrows = (len(num_cols) + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(ncols * 3.5, nrows * 2.8))
    axes = np.array(axes).flatten()
    for i, col in enumerate(num_cols):
        data = df[col].dropna()
        if data.empty:
            axes[i].axis('off')
            continue
        axes[i].hist(data, bins=20, color=PALETTE[i % len(PALETTE)], alpha=0.8, edgecolor='white')
        axes[i].axvline(data.mean(), color='red', linestyle='--', linewidth=1.5)
        axes[i].set_title(col, fontsize=8, fontweight='bold')
        axes[i].tick_params(labelsize=7)
    for j in range(len(num_cols), len(axes)):
        axes[j].axis('off')
    fig.suptitle("Distributions des variables numériques", fontweight='bold', y=1.01)
    plt.tight_layout()
    return _fig_save(fig, out_dir, "fig_distributions")


def fig_boxplots_png(df, out_dir):
    import matplotlib.pyplot as plt
    import numpy as np
    num_cols = df.select_dtypes(include='number').columns.tolist()[:20]
    if not num_cols:
        return None
    fig, axes = plt.subplots(1, len(num_cols), figsize=(max(10, 2.5 * len(num_cols)), 4))
    if len(num_cols) == 1:
        axes = [axes]
    for i, col in enumerate(num_cols):
        data = df[col].dropna()
        if data.empty:
            axes[i].axis('off')
            continue
        axes[i].boxplot(data, patch_artist=True,
                        boxprops=dict(facecolor=PALETTE[i % len(PALETTE)], alpha=0.7),
                        medianprops=dict(color='red', linewidth=2))
        axes[i].set_title(col, fontsize=8, fontweight='bold')
    fig.suptitle("Boxplots — Detection des outliers", fontweight='bold')
    plt.tight_layout()
    return _fig_save(fig, out_dir, "fig_boxplots")


def fig_correlation_png(df, out_dir):
    import matplotlib.pyplot as plt
    num_df = df.select_dtypes(include='number')
    if num_df.shape[1] < 2:
        return None
    cols = num_df.columns.tolist()[:20]
    corr = num_df[cols].corr()
    fig, ax = plt.subplots(figsize=(max(6, len(cols) * 0.6), max(5, len(cols) * 0.55)))
    im = ax.imshow(corr, cmap='RdBu_r', vmin=-1, vmax=1, aspect='auto')
    plt.colorbar(im, ax=ax, fraction=0.046)
    ax.set_xticks(range(len(cols)))
    ax.set_yticks(range(len(cols)))
    ax.set_xticklabels(cols, rotation=45, ha='right', fontsize=8)
    ax.set_yticklabels(cols, fontsize=8)
    for i in range(len(cols)):
        for j in range(len(cols)):
            val = corr.values[i, j]
            color = 'white' if abs(val) > 0.5 else 'black'
            ax.text(j, i, f"{val:.2f}", ha='center', va='center',
                    fontsize=6, color=color, fontweight='bold')
    ax.set_title("Matrice de correlation (Pearson)", fontweight='bold')
    plt.tight_layout()
    return _fig_save(fig, out_dir, "fig_correlation")


def fig_importance_png(imp_df, out_dir, suffix=""):
    import matplotlib.pyplot as plt
    cols_ordered = imp_df.index.tolist()[:30]
    scores = imp_df.loc[cols_ordered, "score_importance"].values
    fig, ax = plt.subplots(figsize=(10, max(4, len(cols_ordered) * 0.35)))
    bar_colors = [PALETTE[i % len(PALETTE)] for i in range(len(cols_ordered))]
    ax.barh(cols_ordered[::-1], scores[::-1], color=bar_colors[::-1])
    ax.set_xlabel("Score d'importance (%)")
    ax.set_title("Importance composite des variables")
    ax.set_xlim(0, 115)
    for i, val in enumerate(scores[::-1]):
        ax.text(val + 1, i, f"{val:.1f}%", va='center', fontsize=7)
    plt.tight_layout()
    return _fig_save(fig, out_dir, f"fig_importance{suffix}")


def fig_compare_missing_png(df_before, df_after, out_dir):
    import matplotlib.pyplot as plt
    import numpy as np
    miss_b = df_before.isnull().mean() * 100
    miss_a = df_after.isnull().mean() * 100
    common = [c for c in miss_b.index if c in miss_a.index and miss_b[c] > 0]
    if not common:
        return None
    x = np.arange(len(common))
    fig, ax = plt.subplots(figsize=(max(8, len(common) * 0.6), 4))
    ax.bar(x - 0.2, miss_b[common].values, 0.4, label='Avant', color='#E63946', alpha=0.8)
    ax.bar(x + 0.2, miss_a[common].values, 0.4, label='Apres', color='#2A9D8F', alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(common, rotation=45, ha='right', fontsize=8)
    ax.set_ylabel("Valeurs manquantes (%)")
    ax.set_title("Comparaison valeurs manquantes : Avant vs Apres transformation")
    ax.legend()
    plt.tight_layout()
    return _fig_save(fig, out_dir, "fig_compare_missing")


def fig_compare_distributions_png(df_before, df_after, out_dir, max_cols=6):
    import matplotlib.pyplot as plt
    import numpy as np
    num_b = df_before.select_dtypes(include='number').columns.tolist()
    num_a = df_after.select_dtypes(include='number').columns.tolist()
    common = [c for c in num_b if c in num_a][:max_cols]
    if not common:
        return None
    fig, axes = plt.subplots(2, len(common), figsize=(len(common) * 3, 5))
    if len(common) == 1:
        axes = axes.reshape(2, 1)
    for j, col in enumerate(common):
        for row_idx, (df_, label) in enumerate([(df_before, 'Avant'), (df_after, 'Apres')]):
            data = df_[col].dropna()
            if data.empty:
                axes[row_idx, j].axis('off')
                continue
            c = '#E63946' if row_idx == 0 else '#2A9D8F'
            axes[row_idx, j].hist(data, bins=20, color=c, alpha=0.8, edgecolor='white')
            axes[row_idx, j].axvline(data.mean(), color='navy', linestyle='--', linewidth=1)
            axes[row_idx, j].set_title(f"{col}\n({label})", fontsize=8)
            axes[row_idx, j].tick_params(labelsize=7)
    plt.tight_layout()
    return _fig_save(fig, out_dir, "fig_compare_distributions")


# ─────────────────────────────────────────────
# 7. RAPPORT LATEX DÉTAILLÉ
# ─────────────────────────────────────────────

def build_latex(csv_path, df, meta, imp_df, output_path, error_logger=None, anonymizer=None, quiz_report=None):
    """Génère rapport_eda.tex — rapport détaillé complet."""
    import os
    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)

    figs = {
        'missing':      fig_missing_png(df, out_dir),
        'distributions': fig_distributions_png(df, out_dir),
        'boxplots':     fig_boxplots_png(df, out_dir),
        'correlation':  fig_correlation_png(df, out_dir),
        'importance':   fig_importance_png(imp_df, out_dir),
    }

    num_cols = meta["colonnes_numeriques"]
    date_str = _tex(meta["date_analyse"])

    # Tableau anonymisation
    # CORRECTION : Retrait de _tex() pour éviter le double échappement
    anon_rows = [[orig, anon, meta["colonnes"][anon]["dtype"]]
                 for orig, anon in list(meta["mapping"].items())[:50]]
    anon_table = _latex_table(["Nom original", "Nom anonymise", "Type"],
                              anon_rows, col_spec='p{7cm}ll')
                              
    # ── Tableau anonymisation des valeurs ──────────────────────────────────────
    if anonymizer and anonymizer.value_log:
        pii_rows = [
            [col,
            # CORRECTION : Retrait des _tex() pour éviter les "VAR\textbackslash{}\_01"
            meta["mapping"].get(
                next((k for k, v in meta["mapping"].items() if v == col), col), col
            ),
            info["pii_type"],
            info["methode"],
            str(info["nb_traitees"])]
            for col, info in anonymizer.value_log.items()
            if info["pii_type"] != "none"
        ]
        anon_values_table = (
            _latex_table(
                ["Variable", "Nom original", "Type PII", "Methode", "Valeurs traitees"],
                pii_rows,
                col_spec='lp{6cm}llr'
            ) if pii_rows else r'\textit{Aucune PII detectee dans les valeurs.}'
        )
    else:
        anon_values_table = r'\textit{Anonymisation des valeurs non executee.}'

    # Tableau métadonnées globales
    global_rows = [
        ["Lignes", str(meta["nb_lignes"])],
        ["Colonnes", str(meta["nb_colonnes"])],
        ["Cellules totales", str(meta["taille_totale_cellules"])],
        ["Valeurs manquantes", str(meta["valeurs_manquantes_total"])],
        # CORRECTION : Utilisation de "%" pur, il sera échappé automatiquement
        ["Taux de completude", str(meta["taux_completude_global"]) + "%"],
        ["Colonnes numeriques", str(len(num_cols))],
        ["Colonnes textuelles", str(len(meta["colonnes_texte"]))],
        ["Doublons", str(meta["doublons"])],
    ]
    global_table = _latex_table(["Metrique", "Valeur"], global_rows, col_spec='lr')

    # Tableau profil colonnes
    col_rows_tex = []
    for col in list(df.columns)[:60]:
        c = meta["colonnes"][col]
        col_rows_tex.append([
            col, c["dtype"], # CORRECTION : Retrait de _tex()
            str(c['taux_completude']) + "%", # CORRECTION : "%" pur
            str(c["valeurs_manquantes"]),
            str(c["valeurs_uniques"]),
            str(c["valeur_la_plus_freq"])[:25],
        ])
        col_profile_table = _latex_table(
            ["Variable", "Type", "Completude", "Manquants", "Uniques", "Mode"],
            col_rows_tex, col_spec='llrrrp{4cm}')

    # Tableau stats descriptives
    # CORRECTION : Petite fonction utilitaire pour empêcher l'affichage de "None"
    def fmt_stat(val):
        return str(val) if val is not None else "N/A"

    stat_rows_tex = []
    for col in num_cols[:40]:
        c = meta["colonnes"][col]
        stat_rows_tex.append([col,
            fmt_stat(c.get("min")), fmt_stat(c.get("max")),
            fmt_stat(c.get("moyenne")), fmt_stat(c.get("mediane")),
            fmt_stat(c.get("ecart_type")), fmt_stat(c.get("skewness"))])
            
    stat_table = (_latex_table(
        ["Variable","Min","Max","Moyenne","Mediane","Ecart-type","Skewness"],
        stat_rows_tex, col_spec='lrrrrrr')
        if stat_rows_tex else r'\textit{Aucune variable numerique.}')
    # Tableau importance
    imp_rows_tex = []
    for var in imp_df.index[:40]:
        row = imp_df.loc[var]
        imp_rows_tex.append([str(int(row["rang"])), var,
            f"{row['score_importance']:.1f}", f"{row['completude_pct']:.1f}",
            f"{row['variabilite_norm']:.1f}", f"{row['correlation_max']:.1f}",
            f"{row['unicite_norm']:.1f}"])
    imp_table = _latex_table(
        ["Rang","Variable","Score","Completude","Variabilite","Correl.","Unicite"],
        imp_rows_tex, col_spec='rlrrrrr')

    # Section erreurs
    if error_logger and error_logger.error_count > 0:
        err_rows = [[str(e["ligne"]), _tex(e["erreur_type"]),
                     _tex(e["erreur_msg"][:70])] for e in error_logger.errors]
        err_section = (
            r'\section{Journal des Erreurs de Parsing}' + '\n\n'
            r'\textbf{' + str(error_logger.error_count) + r' ligne(s) ignor\'{e}e(s)} lors du chargement.' + '\n\n'
            + _latex_table(["N Ligne","Type erreur","Message"], err_rows, col_spec='lll')
        )
    else:
        err_section = (
            r'\section{Journal des Erreurs de Parsing}' + '\n\n'
            r'$\checkmark$~Aucune erreur lors du chargement.' + '\n\n'
        )

    # ── Section Quiz (si traitements Quiz exécutés) ──────────────────────────
    if quiz_report is not None:
        quiz_section_tex = build_latex_quiz_section(quiz_report, meta["mapping"], out_dir)
    else:
        quiz_section_tex = ""

    # Recommandations
    reco_rows_tex = [
        [r'\cellcolor{red!20}Haute',   'Imputer les valeurs manquantes',
         str(meta['valeurs_manquantes_total']) + ' valeurs manquantes'],
        [r'\cellcolor{yellow!30}Moy.', 'Verifier la normalite',
         'Skewness non nul sur plusieurs variables'],
        [r'\cellcolor{yellow!30}Moy.', 'Traiter les outliers',
         'Boxplots revelent des distributions asymetriques'],
        [r'\cellcolor{green!20}Basse', 'Encoder les categorielles',
         'Necessaire pour la modelisation ML'],
        [r'\cellcolor{green!20}Basse', 'Enrichir les donnees',
         str(meta['nb_lignes']) + ' observations seulement'],
    ]
    reco_table = _latex_table(["Priorite","Action","Justification"],
                               reco_rows_tex, col_spec='lp{5cm}p{7cm}')

    doc = (
        r'\documentclass[11pt,a4paper]{article}' + '\n'
        r'\usepackage[utf8]{inputenc}' + '\n'
        r'\usepackage[T1]{fontenc}' + '\n'
        r'\usepackage{lmodern}' + '\n'
        r'\usepackage[french]{babel}' + '\n'
        r'\usepackage[left=2.5cm,right=2.5cm,top=2.5cm,bottom=2.5cm,headheight=14pt]{geometry}' + '\n'
        r'\usepackage{booktabs,longtable,array,xcolor,colortbl,graphicx,float}' + '\n'
        r'\usepackage{hyperref,fancyhdr,titlesec,parskip,amsmath,microtype}' + '\n'
        r'\definecolor{headerblue}{HTML}{2E4057}' + '\n'
        r'\definecolor{accentteal}{HTML}{048A81}' + '\n'
        r'\pagestyle{fancy}\fancyhf{}' + '\n'
        r'\rhead{\textcolor{headerblue}{\small Rapport EDA --- ' + _tex(os.path.basename(csv_path)) + r'}}' + '\n'
        r'\lhead{\textcolor{accentteal}{\small Analyse Exploratoire Automatisee}}' + '\n'
        r'\rfoot{\thepage}' + '\n'
        r'\lfoot{\small ' + date_str + r'}' + '\n'
        r'\renewcommand{\headrulewidth}{0.4pt}' + '\n'
        r'\titleformat{\section}{\Large\bfseries\color{headerblue}}{\thesection.}{1em}{}[\titlerule]' + '\n'
        r'\titleformat{\subsection}{\large\bfseries\color{accentteal}}{\thesubsection.}{1em}{}' + '\n'
        r'\hypersetup{pdftitle={Rapport EDA},colorlinks=true,linkcolor=headerblue,urlcolor=accentteal}' + '\n'
        r'\begin{document}' + '\n\n'

        r'\begin{titlepage}\centering' + '\n'
        r'\vspace*{3cm}' + '\n'
        r"{\Huge\bfseries\color{headerblue} RAPPORT D'ANALYSE EXPLORATOIRE}\\[0.5cm]" + '\n'
        r'\hrule height 2pt\vspace{0.5cm}' + '\n'
        r'{\large\color{accentteal} Donnees Anonymisees --- EDA Automatise v2.0}' + '\n'
        r'\vspace{1.5cm}' + '\n'
        r'\begin{center}\begin{tabular}{ll}' + '\n'
        r'\textbf{Fichier source} & ' + _tex(os.path.basename(csv_path)) + r' \\' + '\n'
        r'\textbf{Date} & ' + date_str + r' \\' + '\n'
        r'\textbf{Lignes} & ' + str(meta['nb_lignes']) + r' \\' + '\n'
        r'\textbf{Colonnes} & ' + str(meta['nb_colonnes']) + r' \\' + '\n'
        r'\textbf{Completude} & ' + str(meta['taux_completude_global']) + r'\% \\' + '\n'
        r'\textbf{Doublons} & ' + str(meta['doublons']) + r' \\' + '\n'
        r'\end{tabular}\end{center}' + '\n'
        r'\vfill{\small\textit{Rapport genere automatiquement}}' + '\n'
        r'\end{titlepage}' + '\n\n'
        r'\tableofcontents\newpage' + '\n\n'

        r'\section{Anonymisation des Variables}' + '\n\n'
        r'\subsection{Anonymisation des noms de colonnes}' + '\n\n'
        "Tous les noms de colonnes ont ete remplaces par des identifiants \\textbf{VAR\\_XX}.\n\n"
        + anon_table + '\n\n'
        r'\subsection{Anonymisation des valeurs (PII)}' + '\n\n'
        "Les cellules contenant des donnees personnelles ont ete traitees selon leur type. "
        "Seules les colonnes avec PII detectees sont listees ci-dessous.\n\n"
        + anon_values_table + '\n\n'
        r"\section{Metadonnees et Vue d'Ensemble}" + '\n\n'
        r'\subsection{Statistiques globales}' + '\n\n'
        + global_table + '\n\n'
        r'\subsection{Profil par variable}' + '\n\n'
        + col_profile_table + '\n\n'
        r'\subsection{Statistiques descriptives (variables numeriques)}' + '\n\n'
        + stat_table + '\n\n'
        r'\newpage' + '\n\n'

        r'\section{Analyse des Valeurs Manquantes}' + '\n\n'
        "La presence de valeurs manquantes peut biaiser les modeles statistiques. "
        "On distingue trois mecanismes : \\textbf{MCAR}, \\textbf{MAR} et \\textbf{MNAR}. "
        "Le seuil critique de 20\\% est indique en rouge.\n\n"
        + _include_fig(figs['missing'], "Taux de valeurs manquantes par variable",
                       "missing", out_dir) + '\n'
        r'\newpage' + '\n\n'

        r'\section{Distributions des Variables Numeriques}' + '\n\n'
        "Les histogrammes visualisent la forme de distribution. "
        "La ligne rouge indique la moyenne. Une asymetrie (skewness $\\neq$ 0) "
        "peut necessiter une transformation logarithmique ou Box-Cox.\n\n"
        + _include_fig(figs['distributions'], "Distributions", "distributions", out_dir)
        + r'\subsection{Detection des Outliers}' + '\n\n'
        "Les boites a moustaches identifient les outliers ($1.5 \\times \\text{IQR}$).\n\n"
        + _include_fig(figs['boxplots'], "Boxplots", "boxplots", out_dir)
        + r'\newpage' + '\n\n'

        r'\section{Analyse des Correlations}' + '\n\n'
        "Correlation de Pearson : $-1$ a $+1$. "
        "Une correlation $|r| > 0.7$ indique une forte colinearite.\n\n"
        + _include_fig(figs['correlation'], "Matrice de correlation (Pearson)",
                       "correlation", out_dir) + '\n'
        r'\newpage' + '\n\n'

        r'\section{Importance et Pertinence des Variables}' + '\n\n'
        + _latex_table(
            ["Critere","Poids","Description"],
            [["Completude","30%","Proportion de valeurs non manquantes"],
             ["Variabilite","35%","CV pour num. / entropie de Shannon pour cat."],
             ["Correlation max","20%","Correlation de Pearson max avec les autres variables"],
             ["Unicite","15%","Proportion de valeurs distinctes"]],
            col_spec='llp{8cm}')
        + '\n\n' + imp_table + '\n\n'
        + _include_fig(figs['importance'], "Importance composite des variables",
                       "importance", out_dir)
        + r'\newpage' + '\n\n'

        r'\section{Methodes et Algorithmes}' + '\n\n'
        r'\subsection{Statistiques descriptives univariees}' + '\n'
        "Indicateurs de tendance centrale (moyenne, mediane, mode), de dispersion "
        "(ecart-type, IQR), de forme (skewness, kurtosis) et quantiles.\n\n"
        r'\subsection{Analyse des valeurs manquantes}' + '\n'
        "Identification du mecanisme (MCAR/MAR/MNAR). Strategies : imputation par "
        "mediane/mode, KNN, MICE (Multiple Imputation by Chained Equations).\n\n"
        r'\subsection{Detection des outliers}' + '\n'
        "Regle des $1.5 \\times \\text{IQR}$ (Tukey), score Z ($|Z|>3$), "
        "Isolation Forest pour les donnees multivariees.\n\n"
        r'\subsection{Analyse des correlations}' + '\n'
        "Pearson (variables continues), Spearman (rang), Kendall $\\tau$, "
        "V de Cramer (variables categorielles).\n\n"
        r"\subsection{Score d'importance}" + '\n'
        "Score composite : completude, variabilite (CV/entropie), correlation "
        "inter-variables, unicite.\n\n"
        r'\subsection{Tests statistiques}' + '\n'
        "Shapiro-Wilk (normalite), Levene (homogeneite des variances), "
        "ANOVA / Kruskal-Wallis (comparaison de groupes), $\\chi^2$ (independance).\n\n"
        r'\newpage' + '\n\n'

        + err_section + '\n'
        r'\newpage' + '\n\n'

        + quiz_section_tex + '\n'
        r'\newpage' + '\n\n'

        r'\section{Recommandations et Prochaines Etapes}' + '\n\n'
        + reco_table + '\n\n'
        r'\vfill\hrule' + '\n'
        r'\begin{center}\small\textit{Rapport genere automatiquement le '
        + date_str + r' --- EDA v2.0}\end{center}' + '\n\n'
        r'\end{document}' + '\n'
    )

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(doc)
    print(f"OK Rapport LaTeX detaille genere : {output_path}")
    return output_path


# ─────────────────────────────────────────────
# 8. RAPPORT LATEX DE SYNTHÈSE
# ─────────────────────────────────────────────

def build_latex_summary(csv_path_before, csv_path_after,
                         df_before, df_after,
                         meta_before, meta_after,
                         imp_before, imp_after,
                         pipeline_steps, output_path,
                         error_logger=None):
    """Génère rapport_synthese.tex — vue générale comparative avant/après."""
    import os, shutil
    out_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(out_dir, exist_ok=True)
    date_str = _tex(datetime.now().strftime("%d/%m/%Y %H:%M:%S"))

    # Figures
    figs = {
        'miss_before':  fig_missing_png(df_before, out_dir),
        'miss_compare': fig_compare_missing_png(df_before, df_after, out_dir),
        'dist_compare': fig_compare_distributions_png(df_before, df_after, out_dir),
        'imp_before':   fig_importance_png(imp_before, out_dir, suffix="_before"),
        'imp_after':    fig_importance_png(imp_after,  out_dir, suffix="_after"),
    }

    # Métriques
    n_b, n_a       = meta_before["nb_lignes"],   meta_after["nb_lignes"]
    c_b, c_a       = meta_before["nb_colonnes"],  meta_after["nb_colonnes"]
    miss_b, miss_a = meta_before["valeurs_manquantes_total"], meta_after["valeurs_manquantes_total"]
    comp_b, comp_a = meta_before["taux_completude_global"],   meta_after["taux_completude_global"]
    dup_b, dup_a   = meta_before["doublons"], meta_after["doublons"]
    num_b = len(meta_before["colonnes_numeriques"])
    num_a = len(meta_after["colonnes_numeriques"])
    cat_b = len(meta_before["colonnes_texte"])
    cat_a = len(meta_after["colonnes_texte"])

    # Tableau comparatif
    def delta(a, b, fmt="+d"):
        d = a - b
        return (f"{d:+d}" if fmt == "+d" else f"{d:+.1f}") if d != 0 else "="
    compare_rows = [
        ["Lignes",              str(n_b), str(n_a), delta(n_a, n_b)],
        ["Colonnes",            str(c_b), str(c_a), delta(c_a, c_b)],
        ["Valeurs manquantes",  str(miss_b), str(miss_a), delta(miss_a, miss_b)],
        ["Completude (\\%)",    str(comp_b), str(comp_a), delta(comp_a, comp_b, "+.1f")],
        ["Doublons",            str(dup_b), str(dup_a), delta(dup_a, dup_b)],
        ["Cols. numeriques",    str(num_b), str(num_a), delta(num_a, num_b)],
        ["Cols. textuelles",    str(cat_b), str(cat_a), delta(cat_a, cat_b)],
    ]
    compare_table = _latex_table(["Metrique","Avant","Apres","Delta"],
                                  compare_rows, col_spec='lrrr')

    # Pipeline
    if pipeline_steps:
        pipeline_tex = r'\begin{enumerate}' + '\n'
        for step in pipeline_steps:
            pipeline_tex += (
                '  \\item \\textbf{' + _tex(step.get('nom','')) + '} \\\\\n'
                '  \\textit{Description :} ' + _tex(step.get('description','')) + ' \\\\\n'
                '  \\textit{Interet :} ' + _tex(step.get('interet','')) + '\n\n'
            )
        pipeline_tex += r'\end{enumerate}' + '\n'
    else:
        pipeline_tex = r'\textit{Aucune etape documentee.}' + '\n'

    # Top 5 variables
    top5_b = imp_before.index[:5].tolist()
    top5_a = imp_after.index[:5].tolist()
    top5_rows = []
    for i in range(5):
        vb = top5_b[i] if i < len(top5_b) else "---"
        va = top5_a[i] if i < len(top5_a) else "---"
        sb = f"{imp_before.loc[vb,'score_importance']:.1f}" if vb != "---" else "---"
        sa = f"{imp_after.loc[va,'score_importance']:.1f}"  if va != "---" else "---"
        top5_rows.append([str(i+1), vb, sb, va, sa])
    top5_table = _latex_table(
        ["Rang","Variable (avant)","Score","Variable (apres)","Score"],
        top5_rows, col_spec='rllll')

    # Bilan des améliorations
    ameliorations = []
    if miss_b > miss_a:
        ameliorations.append(
            '\\item \\textbf{Reduction des valeurs manquantes} : '
            f'{miss_b - miss_a} valeurs traitees, completude : '
            f'{comp_b}\\% $\\to$ {comp_a}\\%. '
            'Reduit les biais d\'estimation et permet les algorithmes '
            'qui n\'acceptent pas les NaN (SVM, regression logistique).'
        )
    if dup_b > dup_a:
        ameliorations.append(
            '\\item \\textbf{Suppression des doublons} : '
            f'{dup_b - dup_a} ligne(s) retiree(s). '
            'Les doublons faussent les distributions et biaisent les '
            'modeles vers les profils sur-representes.'
        )
    if num_a > num_b:
        ameliorations.append(
            '\\item \\textbf{Encodage des variables categorielles} : '
            f'{num_a - num_b} nouvelle(s) colonne(s) numerique(s) creee(s). '
            'Les algorithmes ML requierent des entrees numeriques ; '
            'l\'encodage preserve l\'information categorielle sous '
            'une forme traitable mathematiquement.'
        )
    if cat_a < cat_b:
        ameliorations.append(
            '\\item \\textbf{Reduction des colonnes textuelles} : '
            f'{cat_b - cat_a} colonne(s) transformee(s) ou supprimee(s), '
            'ameliorant la compatibilite avec les algorithmes de clustering.'
        )
    if c_a > 100:
        ameliorations.append(
            '\\item \\textbf{Note sur la dimensionnalite} : '
            f'le jeu passe a {c_a} colonnes apres encodage. '
            'Si $p \\gg n$, une reduction PCA est recommandee avant '
            'la modelisation pour eviter le fleaux de la dimensionnalite.'
        )
    if not ameliorations:
        ameliorations.append(
            '\\item Aucune amelioration quantifiable detectee --- '
            'les donnees semblent deja bien preparees.'
        )
    ameliorations_tex = (r'\begin{itemize}' + '\n'
                         + '\n'.join(ameliorations) + '\n'
                         + r'\end{itemize}' + '\n')

    # Qualite avant
    qual_b_color = 'dangerred' if comp_b < 90 else 'successgreen'
    qual_b_msg   = ('attention, taux insuffisant $<$ 90\\%' if comp_b < 90
                    else 'satisfaisant')
    qual_a_color = 'dangerred' if comp_a < 90 else 'successgreen'
    qual_a_msg   = ('encore insuffisant' if comp_a < 90 else 'satisfaisant')

    doc = (
        r'\documentclass[11pt,a4paper]{article}' + '\n'
        r'\usepackage[utf8]{inputenc}' + '\n'
        r'\usepackage[T1]{fontenc}' + '\n'
        r'\usepackage[french]{babel}' + '\n'
        r'\usepackage[left=2.5cm,right=2.5cm,top=2.5cm,bottom=2.5cm]{geometry}' + '\n'
        r'\usepackage{booktabs,array,xcolor,colortbl,graphicx,float}' + '\n'
        r'\usepackage{hyperref,fancyhdr,titlesec,parskip,amsmath,microtype,tcolorbox}' + '\n'
        r'\tcbuselibrary{skins}' + '\n'
        r'\definecolor{headerblue}{HTML}{2E4057}' + '\n'
        r'\definecolor{accentteal}{HTML}{048A81}' + '\n'
        r'\definecolor{successgreen}{HTML}{2A9D8F}' + '\n'
        r'\definecolor{warnorange}{HTML}{F4A261}' + '\n'
        r'\definecolor{dangerred}{HTML}{E63946}' + '\n'
        r'\pagestyle{fancy}\fancyhf{}' + '\n'
        r'\rhead{\textcolor{headerblue}{\small Rapport de Synthese EDA}}' + '\n'
        r'\lhead{\textcolor{accentteal}{\small Vue Generale --- Pipeline de Preparation}}' + '\n'
        r'\rfoot{\thepage}' + '\n'
        r'\lfoot{\small ' + date_str + r'}' + '\n'
        r'\renewcommand{\headrulewidth}{0.4pt}' + '\n'
        r'\titleformat{\section}{\Large\bfseries\color{headerblue}}{\thesection.}{1em}{}[\titlerule]' + '\n'
        r'\titleformat{\subsection}{\large\bfseries\color{accentteal}}{\thesubsection.}{1em}{}' + '\n'
        r'\hypersetup{pdftitle={Rapport de Synthese EDA},colorlinks=true,' + '\n'
        r'  linkcolor=headerblue,urlcolor=accentteal}' + '\n'
        r'\begin{document}' + '\n\n'

        r'\begin{titlepage}\centering' + '\n'
        r'\vspace*{2cm}' + '\n'
        r'{\Huge\bfseries\color{headerblue} RAPPORT DE SYNTHESE}\\[0.3cm]' + '\n'
        r'{\Huge\bfseries\color{headerblue} PREPARATION DES DONNEES}' + '\n'
        r'\vspace{0.8cm}\hrule height 2pt\vspace{0.5cm}' + '\n'
        r'{\large\color{accentteal} Vue Generale --- Pipeline --- Comparaison Avant/Apres}' + '\n'
        r'\vspace{1.5cm}' + '\n'
        r'\begin{center}\begin{tabular}{ll}' + '\n'
        r'\textbf{Donnees brutes} & ' + _tex(os.path.basename(csv_path_before)) + r' \\' + '\n'
        r'\textbf{Donnees transformees} & ' + _tex(os.path.basename(csv_path_after)) + r' \\' + '\n'
        r'\textbf{Date} & ' + date_str + r' \\' + '\n'
        r'\textbf{Lignes (avant/apres)} & ' + str(n_b) + r' $\to$ ' + str(n_a) + r' \\' + '\n'
        r'\textbf{Colonnes (avant/apres)} & ' + str(c_b) + r' $\to$ ' + str(c_a) + r' \\' + '\n'
        r'\textbf{Completude (avant/apres)} & ' + str(comp_b) + r'\% $\to$ ' + str(comp_a) + r'\% \\' + '\n'
        r'\end{tabular}\end{center}' + '\n'
        r'\vfill{\small\textit{Rapport de synthese genere automatiquement --- EDA v2.0}}' + '\n'
        r'\end{titlepage}' + '\n\n'
        r'\tableofcontents\newpage' + '\n\n'

        # Section 1 : Description données brutes
        r"\section{Description Generale des Donnees d'Entree}" + '\n\n'
        r'\subsection{Apercu du jeu de donnees brut}' + '\n\n'
        'Le jeu de donnees source \\textbf{' + _tex(os.path.basename(csv_path_before)) + '} '
        'contient \\textbf{' + str(n_b) + ' observations} et \\textbf{' + str(c_b) + ' variables} '
        '(' + str(num_b) + ' numeriques, ' + str(cat_b) + ' categorielles). '
        'Le taux de completude global est de \\textbf{' + str(comp_b) + '\\%}, '
        'avec \\textbf{' + str(miss_b) + ' valeurs manquantes}. '
        + ('\\textbf{' + str(dup_b) + ' doublon(s)} ont ete detectes.'
           if dup_b > 0 else 'Aucun doublon detecte.')
        + '\n\n'
        r'\subsection{Qualite initiale des donnees}' + '\n\n'
        r'\begin{tcolorbox}[colback=lightgray,colframe=headerblue,' + '\n'
        r'  title=Diagnostic qualite initial]' + '\n'
        r'\begin{itemize}' + '\n'
        '  \\item \\textbf{Completude} : ' + str(comp_b) + '\\% --- '
        '\\textcolor{' + qual_b_color + '}{' + qual_b_msg + '}\n'
        '  \\item \\textbf{Doublons} : ' + str(dup_b) + ' ligne(s) --- '
        + ('\\textcolor{warnorange}{a traiter}' if dup_b > 0 else '\\textcolor{successgreen}{aucun}') + '\n'
        '  \\item \\textbf{Variables categorielles} : ' + str(cat_b)
        + (' --- \\textcolor{warnorange}{necessitent un encodage}' if cat_b > 0 else '') + '\n'
        '  \\item \\textbf{Variables numeriques} : ' + str(num_b) + '\n'
        r'\end{itemize}' + '\n'
        r'\end{tcolorbox}' + '\n\n'
        + _include_fig(figs['miss_before'],
                       "Taux de valeurs manquantes --- donnees brutes",
                       "miss_before", out_dir)
        + r'\newpage' + '\n\n'

        # Section 2 : Pipeline
        r'\section{Pipeline de Preparation --- Transformations Appliquees}' + '\n\n'
        r"\subsection{Vue d'ensemble du pipeline}" + '\n\n'
        'Le pipeline est compose de \\textbf{' + str(len(pipeline_steps)) + ' etape(s)} '
        'successives appliquees dans l\'ordre ci-dessous.\n\n'
        r'\subsection{Detail des etapes}' + '\n\n'
        + pipeline_tex + '\n'
        r'\subsection{Impact global du pipeline}' + '\n\n'
        + compare_table + '\n\n'
        r'\newpage' + '\n\n'

        # Section 3 : Données transformées
        r'\section{Description des Donnees Transformees}' + '\n\n'
        r'\subsection{Apercu apres transformation}' + '\n\n'
        'Apres application du pipeline : \\textbf{' + str(n_a) + ' observations}, '
        '\\textbf{' + str(c_a) + ' variables} (' + str(num_a) + ' numeriques, '
        + str(cat_a) + ' categorielles). '
        'Completude : \\textbf{' + str(comp_a) + '\\%}.\n\n'
        r'\begin{tcolorbox}[colback=lightgray,colframe=successgreen,' + '\n'
        r'  title=Diagnostic qualite apres transformation]' + '\n'
        r'\begin{itemize}' + '\n'
        '  \\item \\textbf{Completude} : ' + str(comp_a) + '\\% --- '
        '\\textcolor{' + qual_a_color + '}{' + qual_a_msg + '}\n'
        '  \\item \\textbf{Doublons residuels} : ' + str(dup_a) + '\n'
        '  \\item \\textbf{Colonnes numeriques} : ' + str(num_a)
        + ' (dont ' + str(num_a - num_b) + ' nouvelles issues de l\'encodage)\n'
        '  \\item \\textbf{Colonnes textuelles residuelles} : ' + str(cat_a) + '\n'
        r'\end{itemize}' + '\n'
        r'\end{tcolorbox}' + '\n\n'
        r'\subsection{Comparaison des distributions}' + '\n\n'
        + _include_fig(figs['dist_compare'],
                       "Distributions avant/apres --- variables communes",
                       "dist_compare", out_dir)
        + r'\subsection{Evolution des valeurs manquantes}' + '\n\n'
        + _include_fig(figs['miss_compare'],
                       "Valeurs manquantes avant/apres transformation",
                       "miss_compare", out_dir)
        + r'\newpage' + '\n\n'

        # Section 4 : Améliorations
        r'\section{Ameliorations Apportees et Leurs Interets}' + '\n\n'
        r'\subsection{Bilan quantitatif}' + '\n\n'
        + ameliorations_tex + '\n'
        r"\subsection{Evolution de l'importance des variables}" + '\n\n'
        + top5_table + '\n\n'
        + _include_fig(figs['imp_before'],
                       "Importance des variables --- donnees brutes",
                       "imp_before", out_dir, width=r'0.85\linewidth')
        + _include_fig(figs['imp_after'],
                       "Importance des variables --- donnees transformees",
                       "imp_after", out_dir, width=r'0.85\linewidth')
        + r'\subsection{Recommandations post-transformation}' + '\n\n'
        r'\begin{itemize}' + '\n'
        + (r'  \item Si $p \gg n$, appliquer une \textbf{reduction PCA} avant modelisation.' + '\n'
           if c_a > 100 else '')
        + r'  \item Verifier l''absence de \textbf{fuite de donnees} (data leakage) train/test.' + '\n'
        r'  \item Appliquer une \textbf{normalisation} (StandardScaler / RobustScaler) pour' + '\n'
        r'        les algorithmes sensibles aux echelles (SVM, KNN, reseaux de neurones).' + '\n'
        r'  \item Utiliser les donnees transformees avec \textbf{App ML} pour le clustering.' + '\n'
        + ('  \\item Les ' + str(cat_a) + ' variable(s) categorielle(s) restante(s) '
           'necessitent encore un encodage.\n' if cat_a > 0 else
           '  \\item Toutes les variables categorielles ont ete traitees.\n')
        + r'\end{itemize}' + '\n\n'
        r'\vfill\hrule' + '\n'
        r'\begin{center}\small\textit{Rapport de synthese genere automatiquement le '
        + date_str + r' --- EDA v2.0}\end{center}' + '\n\n'
        r'\end{document}' + '\n'
    )

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(doc)
    print(f"OK Rapport LaTeX de synthese genere : {output_path}")
    return output_path


# ─────────────────────────────────────────────
# 9. AUTO-DETECTION DU PIPELINE
# ─────────────────────────────────────────────

def _auto_detect_pipeline(df_before, df_after, meta_before, meta_after):
    """Detecte automatiquement les transformations appliquees."""
    steps = []
    miss_b = meta_before["valeurs_manquantes_total"]
    miss_a = meta_after["valeurs_manquantes_total"]
    dup_b  = meta_before["doublons"]
    dup_a  = meta_after["doublons"]
    num_b  = len(meta_before["colonnes_numeriques"])
    num_a  = len(meta_after["colonnes_numeriques"])
    cat_b  = len(meta_before["colonnes_texte"])
    cat_a  = len(meta_after["colonnes_texte"])

    if miss_b > miss_a:
        steps.append({
            "nom": "Imputation des valeurs manquantes",
            "description": (
                f"Remplacement des {miss_b - miss_a} valeurs manquantes : "
                "mediane pour les variables numeriques, mode pour les categorielles."
            ),
            "interet": (
                "Les valeurs manquantes introduisent des biais dans les estimations "
                "statistiques et sont incompatibles avec la plupart des algorithmes de ML. "
                "L'imputation preserve toutes les observations tout en restituant "
                "une valeur statistiquement plausible."
            ),
        })
    elif miss_b > 0 and miss_a == 0:
        steps.append({
            "nom": "Suppression des lignes avec valeurs manquantes",
            "description": "Retrait des lignes contenant au moins une valeur manquante.",
            "interet": "Garantit un jeu de donnees complet pour les algorithmes intolerants aux NaN.",
        })

    if dup_b > dup_a:
        steps.append({
            "nom": "Suppression des doublons",
            "description": f"Retrait de {dup_b - dup_a} ligne(s) dupliquee(s).",
            "interet": (
                "Les doublons faussent les distributions, surestiment la taille "
                "effective de l'echantillon et biaisent les modeles."
            ),
        })

    if num_a > num_b and cat_b > cat_a:
        steps.append({
            "nom": "Encodage des variables categorielles",
            "description": (
                f"Transformation de {cat_b - cat_a} variable(s) en colonnes numeriques "
                "par encodage one-hot ou label encoding selon la cardinalite."
            ),
            "interet": (
                "Les algorithmes de clustering et de ML operent sur des espaces "
                "metriques et requierent des entrees numeriques. L'encodage one-hot "
                "preserve l'information sans imposer d'ordre artificiel entre les modalites."
            ),
        })

    cols_b = set(df_before.columns.astype(str))
    cols_a = set(df_after.columns.astype(str))
    pure_dropped = [c for c in cols_b - cols_a
                    if not any(c in ca for ca in cols_a)]
    if pure_dropped:
        steps.append({
            "nom": "Suppression de variables peu informatives",
            "description": (
                "Retrait de : " + ", ".join(pure_dropped[:5])
                + ("..." if len(pure_dropped) > 5 else ".")
            ),
            "interet": (
                "Variables a variance nulle, quasi-constantes ou redondantes "
                "n'apportent pas d'information discriminante et augmentent "
                "inutilement la dimensionnalite."
            ),
        })

    if not steps:
        steps.append({
            "nom": "Transformation des donnees",
            "description": "Transformations appliquees via App EDA.",
            "interet": "Amelioration de la qualite et de la compatibilite des donnees.",
        })
    return steps


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if "--app" in args:
        import subprocess
        app_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_eda.py")
        if not os.path.exists(app_path):
            print(f"Fichier app_eda.py introuvable : {app_path}")
            sys.exit(1)
        print("Lancement de l'application Streamlit...")
        subprocess.run([sys.executable, "-m", "streamlit", "run", app_path], check=True)
        return

    csv_args = [a for a in args if not a.startswith("--")]
    if not csv_args:
        csv_args = ["donnees_produits.csv"]

    csv_before = csv_args[0]
    csv_after  = csv_args[1] if len(csv_args) > 1 else None
    out_dir    = os.path.dirname(os.path.abspath(csv_before))

    print(f"Chargement : {csv_before}")
    keys_dir = os.path.join(out_dir, "anonymization_keys")
    df_before, anon_before, _anonymizer_before, err_before = load_and_anonymize_v2(
    csv_path=csv_before,
    salt=os.environ.get("EDA_ANON_SALT", "eda_anon_2025"),
    keys_dir=keys_dir,
    )
    meta_before = compute_metadata(df_before, anon_before)
    imp_before  = compute_importance(df_before)

    print("Analyse des champs Quiz/questionnaire...")
    quiz_processor = QuizProcessor(df_before, anon_before, verbose=True)
    quiz_report    = quiz_processor.run_all()

    tex_detail = os.path.join(out_dir, "rapport_eda.tex")
    print("Generation du rapport LaTeX detaille...")
    build_latex(csv_before, df_before, meta_before, imp_before, tex_detail, err_before,
            anonymizer=_anonymizer_before)

    if csv_after and os.path.exists(csv_after):
        print(f"Chargement donnees transformees : {csv_after}")
        df_after, anon_after, _anonymizer_after, err_after = load_and_anonymize_v2(
            csv_path=csv_after,
            salt=os.environ.get("EDA_ANON_SALT", "eda_anon_2025"),
            keys_dir=keys_dir,   # même dossier que le premier
        )
        meta_after = compute_metadata(df_after, anon_after)
        imp_after  = compute_importance(df_after)
        pipeline   = _auto_detect_pipeline(df_before, df_after, meta_before, meta_after)
        tex_summary = os.path.join(out_dir, "rapport_synthese.tex")
        print("Generation du rapport LaTeX de synthese...")
        build_latex_summary(
            csv_path_before=csv_before, csv_path_after=csv_after,
            df_before=df_before, df_after=df_after,
            meta_before=meta_before, meta_after=meta_after,
            imp_before=imp_before, imp_after=imp_after,
            pipeline_steps=pipeline, output_path=tex_summary,
            error_logger=err_before,
        )
    else:
        if csv_after:
            print(f"Fichier transforme introuvable : {csv_after}")
        else:
            print("Usage avec synthese : python eda_analyse.py brut.csv transforme.csv")

    print("\nTermine.")
    print(f"  -> {tex_detail}")
    if csv_after:
        print(f"  -> {os.path.join(out_dir, 'rapport_synthese.tex')}")
    print("  Compiler avec : pdflatex rapport_eda.tex")


if __name__ == "__main__":
    main()
