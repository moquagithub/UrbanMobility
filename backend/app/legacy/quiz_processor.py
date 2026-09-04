"""
quiz_processor.py -- Traitements specifiques aux donnees Quiz / questionnaire
=============================================================================
1. Detection automatique des types de champs Quiz
2. Parsing des champs multi-reponses (separes par |)
3. Decodage des champs JSON {"min", "max", "mid"}
4. Taux de reponse par question + profils de non-reponse
5. Variables ordinales / echelles : detection + stats adaptees
6. Texte libre : longueur, presence, nettoyage de base
7. Coherence inter-questions (questions filtres/conditionnelles)
8. Generation du rapport LaTeX Quiz
"""

import re
import json
import warnings
from typing import Optional

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────

ORDINAL_SCALES = {
    "likert_5":    ["tout a fait d'accord", "d'accord", "neutre",
                    "pas d'accord", "pas du tout d'accord"],
    "likert_sat":  ["tres satisfait", "satisfait", "neutre",
                    "insatisfait", "tres insatisfait"],
    "frequency":   ["jamais", "rarement", "parfois", "souvent", "toujours"],
    "frequency_en":["never", "rarely", "sometimes", "often", "always"],
    "importance":  ["pas important", "peu important", "assez important",
                    "important", "tres important"],
    "agreement":   ["strongly disagree", "disagree", "neutral",
                    "agree", "strongly agree"],
}

MULTI_RESPONSE_SEP  = "|"
MIN_MULTI_RESPONSE  = 0.10
JSON_FIELD_KEYS     = {"min", "max", "mid"}
MIN_JSON_RATIO      = 0.30
MAX_UNIQUE_ORDINAL  = 8
FREETEXT_MIN_WORDS  = 4

FREETEXT_COL_RE = re.compile(
    r"(commentaire|comment|note|texte|text|description|observation|"
    r"remarque|precis|autre|other|feedback|suggestion|motif|raison|"
    r"detail|expliqu|libre)",
    re.IGNORECASE,
)
FILTER_COL_RE = re.compile(
    r"(si.oui|si.non|precis|detail|autre|other|si.vous|if.yes|if.no|"
    r"veuillez.precis|please.specify)",
    re.IGNORECASE,
)
TIME_COL_RE = re.compile(
    r"(heure|horaire|time|depart|arrivee|arrival)",
    re.IGNORECASE,
)

EMPTY_PATTERNS_RE = re.compile(
    r"^\s*(rien|aucun|aucune|neant|n/a|na|non|aucun commentaire|"
    r"pas de commentaire|sans|nothing|none|no comment|no|/|\.)\s*$",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────
# DETECTION DES TYPES QUIZ
# ─────────────────────────────────────────────

def detect_quiz_fields(df: pd.DataFrame, anon_map: dict) -> dict:
    """
    Analyse chaque colonne et retourne {col_anon -> type_quiz}.

    Types: multi_response | json_range | ordinal_scale | free_text |
           filter_question | time_field | binary | numeric | categorical | unknown
    """
    reverse_map = {v: k for k, v in anon_map.items()}
    field_types = {}

    for col in df.columns:
        col_orig = reverse_map.get(col, col).lower()
        s = df[col].dropna().astype(str)
        n = len(s)

        if n == 0:
            field_types[col] = "unknown"
            continue

        # 1. Question filtre (par nom de colonne)
        if FILTER_COL_RE.search(col_orig):
            field_types[col] = "filter_question"
            continue

        # 2. Multi-reponses (presence de |)
        has_pipe = s.str.contains(r"\|", regex=True)
        if has_pipe.mean() >= MIN_MULTI_RESPONSE:
            field_types[col] = "multi_response"
            continue

        # 3. JSON range
        def _is_json_range(v):
            try:
                obj = json.loads(v)
                return isinstance(obj, dict) and bool(JSON_FIELD_KEYS & set(obj.keys()))
            except Exception:
                return False

        if s.apply(_is_json_range).mean() >= MIN_JSON_RATIO:
            field_types[col] = "json_range"
            continue

        # 4. Horaire (par nom)
        if TIME_COL_RE.search(col_orig):
            field_types[col] = "time_field"
            continue

        # 5. Binaire
        unique_lower = set(s.str.lower().str.strip().unique())
        binary_sets = [
            {"oui", "non"}, {"yes", "no"}, {"vrai", "faux"},
            {"true", "false"}, {"0", "1"}, {"1", "2"},
        ]
        if any(unique_lower <= b for b in binary_sets):
            field_types[col] = "binary"
            continue

        # 6. Ordinal / echelle
        n_unique = df[col].nunique()
        if n_unique <= MAX_UNIQUE_ORDINAL:
            values_lower = set(s.str.lower().str.strip().unique())
            for scale_vals in ORDINAL_SCALES.values():
                scale_set = {v.lower() for v in scale_vals}
                if len(values_lower & scale_set) >= 2:
                    field_types[col] = "ordinal_scale"
                    break
            if col in field_types:
                continue
            if pd.api.types.is_numeric_dtype(df[col]):
                field_types[col] = "ordinal_scale"
                continue

        # 7. Texte libre (par nom ou longueur moyenne)
        if FREETEXT_COL_RE.search(col_orig):
            field_types[col] = "free_text"
            continue
        avg_words = s.str.split().apply(len).mean()
        if avg_words >= FREETEXT_MIN_WORDS and not pd.api.types.is_numeric_dtype(df[col]):
            field_types[col] = "free_text"
            continue

        # 8. Numerique / categoriel standard
        if pd.api.types.is_numeric_dtype(df[col]):
            field_types[col] = "numeric"
        else:
            field_types[col] = "categorical"

    return field_types


# ─────────────────────────────────────────────
# MULTIRESPONSEPARSER
# ─────────────────────────────────────────────

class MultiResponseParser:
    """Parse les colonnes multi-reponses separees par |."""

    def __init__(self, sep="|"):
        self.sep = sep
        self.modalities_ = []
        self.frequency_ = None
        self.cooccurrence_ = None

    def fit_transform(self, series: pd.Series, col_name: str = "Q") -> pd.DataFrame:
        """Retourne un DataFrame one-hot (une colonne booleenne par modalite)."""
        def _split(v):
            if pd.isna(v) or str(v).lower().strip() in ("nan", "none", ""):
                return []
            return [x.strip() for x in str(v).split(self.sep) if x.strip()]

        split = series.apply(_split)
        all_mods = sorted({mod for lst in split for mod in lst})
        self.modalities_ = all_mods

        onehot_data = {
            col_name + "|" + mod: split.apply(lambda lst: mod in lst)
            for mod in all_mods
        }
        onehot_df = pd.DataFrame(onehot_data, index=series.index)

        # Frequences marginales
        freq = onehot_df.sum().sort_values(ascending=False)
        freq.index = [i.split("|", 1)[-1] for i in freq.index]
        self.frequency_ = freq

        # Co-occurrence
        if len(all_mods) >= 2:
            mat = onehot_df.astype(int)
            coocc_arr = mat.T.values.dot(mat.values)
            np.fill_diagonal(coocc_arr, 0)
            short = [i.split("|", 1)[-1] for i in onehot_df.columns]
            self.cooccurrence_ = pd.DataFrame(coocc_arr, index=short, columns=short)
        else:
            self.cooccurrence_ = pd.DataFrame()

        return onehot_df

    def top_cooccurrences(self, n: int = 10) -> pd.DataFrame:
        """Retourne les n paires les plus co-choisies."""
        if self.cooccurrence_ is None or self.cooccurrence_.empty:
            return pd.DataFrame(columns=["Modalite A", "Modalite B", "Co-occurrences"])
        co = self.cooccurrence_
        rows = []
        idx = list(co.index)
        for i in range(len(idx)):
            for j in range(i + 1, len(idx)):
                rows.append({
                    "Modalite A": idx[i],
                    "Modalite B": idx[j],
                    "Co-occurrences": int(co.iloc[i, j]),
                })
        return (pd.DataFrame(rows)
                .sort_values("Co-occurrences", ascending=False)
                .head(n)
                .reset_index(drop=True))


# ─────────────────────────────────────────────
# JSONRANGEDECODER
# ─────────────────────────────────────────────

class JsonRangeDecoder:
    """Decode les champs JSON {"min":x, "max":y, "mid":z}."""

    KEY_ALIASES = {
        "min": {"min", "minimum", "debut", "start", "from", "de"},
        "max": {"max", "maximum", "fin", "end", "to", "a"},
        "mid": {"mid", "middle", "milieu", "median", "med", "moyen"},
    }

    def decode_series(self, series: pd.Series, col_name: str = "Q") -> pd.DataFrame:
        """Retourne {col_name}_min, {col_name}_max, {col_name}_mid."""
        records = series.apply(self._decode_value)
        result = pd.DataFrame(list(records), index=series.index)
        result.columns = [col_name + "_min", col_name + "_max", col_name + "_mid"]
        return result

    def _decode_value(self, v) -> dict:
        out = {"min": None, "max": None, "mid": None}
        if pd.isna(v) or str(v).strip() in ("", "nan", "None"):
            return out
        raw = str(v).strip()

        # Etape 1 : JSON standard (preserve les strings "07:00")
        obj = None
        try:
            obj = json.loads(raw)
        except Exception:
            pass

        # Etape 2 : JSON5 - quoter les cles non quotees
        # Pattern : mot en debut ou apres { ou , puis :
        if obj is None:
            fixed = re.sub(r"(?:(?<={)|(?<=,))\s*([A-Za-z_]\w*)\s*:",
                           lambda m: m.group(0).replace(m.group(1),
                                                        '"' + m.group(1) + '"'),
                           raw)
            fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
            try:
                obj = json.loads(fixed)
            except Exception:
                pass

        # Etape 3 : regex fallback
        if obj is None:
            for canonical, aliases in self.KEY_ALIASES.items():
                for alias in aliases:
                    pat = alias + r"""["']?\s*:\s*["']?([^"',}\]]+)"""
                    m = re.search(pat, raw, re.IGNORECASE)
                    if m and out[canonical] is None:
                        val_str = m.group(1).strip().strip('"\'')
                        try:
                            out[canonical] = float(val_str)
                        except ValueError:
                            out[canonical] = val_str
            return out

        if not isinstance(obj, dict):
            return out

        # Mapper avec aliases
        for canonical, aliases in self.KEY_ALIASES.items():
            for k, val in obj.items():
                if k.lower() in aliases:
                    try:
                        out[canonical] = float(val) if val is not None else None
                    except (ValueError, TypeError):
                        out[canonical] = val
                    break

        return out


# ─────────────────────────────────────────────
# FREETEXTANALYZER
# ─────────────────────────────────────────────

class FreeTextAnalyzer:
    """Analyse legere des champs texte libre."""

    def analyze(self, series: pd.Series, col_name: str = "Q") -> pd.DataFrame:
        s = series.astype(str)
        cleaned = s.apply(self._clean)
        has_response = (
            ~series.isna()
            & ~cleaned.str.strip().eq("")
            & ~cleaned.apply(lambda v: bool(EMPTY_PATTERNS_RE.match(v)))
        )
        return pd.DataFrame({
            col_name + "_has_response": has_response,
            col_name + "_char_count":   cleaned.str.len().where(has_response, 0),
            col_name + "_word_count":   cleaned.str.split().apply(len).where(has_response, 0),
            col_name + "_cleaned":      cleaned.where(has_response, ""),
        }, index=series.index)

    @staticmethod
    def _clean(text) -> str:
        if text is None or (not isinstance(text, str)) or text in ("nan", "None", ""):
            return ""
        text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text


# ─────────────────────────────────────────────
# TAUX DE REPONSE PAR QUESTION
# ─────────────────────────────────────────────

def compute_response_rates(df: pd.DataFrame, anon_map: dict,
                           field_types: dict) -> pd.DataFrame:
    """
    Calcule pour chaque colonne : taux de reponse, n_null, n_refus,
    qualification du niveau, flag question filtre.
    """
    reverse_map = {v: k for k, v in anon_map.items()}
    records = []
    n_total = len(df)

    for col in df.columns:
        s = df[col]
        orig = reverse_map.get(col, col)
        ftype = field_types.get(col, "unknown")

        n_null = int(s.isna().sum())
        n_empty = 0
        if not pd.api.types.is_numeric_dtype(s):
            n_empty = int(s.dropna().astype(str).apply(
                lambda v: bool(EMPTY_PATTERNS_RE.match(v))
            ).sum())

        n_non_response = n_null + n_empty
        n_response = n_total - n_non_response
        rate = round(100 * n_response / max(n_total, 1), 1)

        if rate >= 90:
            level = "Eleve"
        elif rate >= 70:
            level = "Moyen"
        elif rate >= 50:
            level = "Faible"
        else:
            level = "Critique"

        records.append({
            "col_anon":         col,
            "col_original":     orig,
            "type_quiz":        ftype,
            "n_total":          n_total,
            "n_null":           n_null,
            "n_empty_refus":    n_empty,
            "n_non_response":   n_non_response,
            "n_response":       n_response,
            "taux_reponse_pct": rate,
            "niveau":           level,
            "question_filtre":  (ftype == "filter_question"),
        })

    return pd.DataFrame(records).set_index("col_anon")


# ─────────────────────────────────────────────
# COHERENCE INTER-QUESTIONS
# ─────────────────────────────────────────────

def check_inter_question_consistency(df: pd.DataFrame, field_types: dict,
                                     response_rates: pd.DataFrame) -> list:
    """
    Detecte 3 types d'incoherences :
    1. Question filtre repondue sans question parente
    2. Colonnes quasi-identiques (r >= 0.95)
    3. Taux de reponse heterogenes dans une meme section
    """
    issues = []
    filter_cols = [c for c, t in field_types.items() if t == "filter_question"]
    col_list = list(df.columns)

    # Regle 1
    for fc in filter_cols:
        fc_answered = df[fc].notna() & (df[fc].astype(str).str.strip() != "")
        idx = col_list.index(fc)
        if idx > 0:
            parent = col_list[idx - 1]
            parent_empty = df[parent].isna() | (
                df[parent].astype(str).str.strip().isin(["", "nan", "None"])
            )
            n_bad = int((fc_answered & parent_empty).sum())
            if n_bad > 0:
                issues.append({
                    "type":     "filtre_sans_parent",
                    "colonnes": [parent, fc],
                    "message":  (
                        str(n_bad) + " repondant(s) ont rempli la question filtre '"
                        + fc + "' sans avoir repondu a la question parente '" + parent + "'."
                    ),
                    "severite": "Moyen",
                })

    # Regle 2
    num_df = df.select_dtypes(include="number")
    if num_df.shape[1] >= 2:
        corr = num_df.corr().abs()
        cols_num = list(num_df.columns)
        seen = set()
        for i in range(len(cols_num)):
            for j in range(i + 1, len(cols_num)):
                r = corr.iloc[i, j]
                if not np.isnan(r) and r >= 0.95:
                    pair = tuple(sorted([cols_num[i], cols_num[j]]))
                    if pair not in seen:
                        seen.add(pair)
                        issues.append({
                            "type":     "quasi_doublon",
                            "colonnes": list(pair),
                            "message":  (
                                "Correlation de " + str(round(float(r), 3))
                                + " entre '" + pair[0] + "' et '" + pair[1]
                                + "' -- probable doublon."
                            ),
                            "severite": "Faible",
                        })

    # Regle 3
    if not response_rates.empty:
        rates = response_rates["taux_reponse_pct"]
        window = 5
        for start in range(0, len(col_list) - window + 1, window):
            group = col_list[start: start + window]
            group_rates = rates.reindex(group).dropna()
            if len(group_rates) >= 3:
                spread = group_rates.max() - group_rates.min()
                if spread >= 30:
                    worst = group_rates.idxmin()
                    issues.append({
                        "type":     "heterogeneite_section",
                        "colonnes": list(group),
                        "message":  (
                            "Ecart de " + str(round(float(spread), 1))
                            + "% entre les taux de reponse de colonnes consecutives ("
                            + group[0] + "..." + group[-1] + "). '"
                            + worst + "' a le taux le plus bas ("
                            + str(round(float(group_rates.min()), 1)) + "%)."
                        ),
                        "severite": "Moyen",
                    })

    return issues


# ─────────────────────────────────────────────
# STATS ORDINALES
# ─────────────────────────────────────────────

def ordinal_stats(series: pd.Series, col_name: str = "Q") -> dict:
    """
    Statistiques adaptees aux variables ordinales :
    mode, mediane ordinale, frequences, entropie de Shannon normalisee,
    centralite.
    """
    counts = series.value_counts(dropna=True).sort_index()
    n = counts.sum()
    if n == 0:
        return {}

    freqs = counts / n
    entropy = float(-np.sum(freqs * np.log2(freqs + 1e-12)))
    max_entropy = np.log2(max(len(counts), 1))
    entropy_norm = round(entropy / max_entropy, 4) if max_entropy > 0 else 0.0

    cumsum = counts.cumsum()
    median_idx = (cumsum >= n / 2).idxmax()
    mode_val = counts.idxmax()

    n_mods = len(counts)
    if n_mods >= 4:
        mid_start = n_mods // 2 - 1
        mid_end   = n_mods // 2 + 1
        central_vals = list(counts.index[mid_start:mid_end])
        central_pct = round(100 * counts.loc[central_vals].sum() / n, 1)
    else:
        central_pct = round(100 * float(counts.max()) / n, 1)

    return {
        "col":              col_name,
        "n_repondants":     int(n),
        "mode":             str(mode_val),
        "mediane_ordinale": str(median_idx),
        "entropie_norm":    entropy_norm,
        "centralite_pct":   central_pct,
        "frequences":       {str(k): round(float(v), 4) for k, v in freqs.items()},
        "freq_cumulees":    {str(k): round(float(v), 4)
                             for k, v in freqs.cumsum().items()},
    }


# ─────────────────────────────────────────────
# QUIZ REPORT
# ─────────────────────────────────────────────

class QuizReport:
    """Conteneur des resultats d'analyse Quiz."""
    def __init__(self):
        self.field_types        = {}
        self.response_rates     = pd.DataFrame()
        self.multi_onehot       = {}
        self.multi_freq         = {}
        self.multi_coocc        = {}
        self.json_decoded       = {}
        self.freetext_stats     = {}
        self.ordinal_stats      = {}
        self.consistency_issues = []
        self.n_multi            = 0
        self.n_json             = 0
        self.n_ordinal          = 0
        self.n_freetext         = 0
        self.n_filter           = 0

    def summary(self) -> str:
        lines = [
            "======= RAPPORT QUIZ =======",
            "Champs multi-reponses   : " + str(self.n_multi),
            "Champs JSON range       : " + str(self.n_json),
            "Variables ordinales     : " + str(self.n_ordinal),
            "Textes libres           : " + str(self.n_freetext),
            "Questions filtres       : " + str(self.n_filter),
            "Incoherences detectees  : " + str(len(self.consistency_issues)),
        ]
        if not self.response_rates.empty:
            low = self.response_rates[self.response_rates["taux_reponse_pct"] < 70]
            lines.append("Questions taux faible (<70%) : " + str(len(low)))
        lines.append("============================")
        return "\n".join(lines)


# ─────────────────────────────────────────────
# QUIZPROCESSOR
# ─────────────────────────────────────────────

class QuizProcessor:
    """
    Orchestre tous les traitements Quiz sur un DataFrame deja anonymise.

    Args:
        df       : DataFrame (colonnes VAR_XX)
        anon_map : {nom_original -> VAR_XX}
        verbose  : afficher la progression
    """

    def __init__(self, df: pd.DataFrame, anon_map: dict, verbose: bool = True):
        self.df       = df.copy()
        self.anon_map = anon_map
        self.verbose  = verbose
        self._report  = QuizReport()

    def _log(self, msg: str):
        if self.verbose:
            print("  [Quiz] " + msg)

    def run_all(self) -> QuizReport:
        """Execute tous les traitements et retourne le QuizReport."""
        self._log("Detection des types de champs Quiz...")
        self._report.field_types = detect_quiz_fields(self.df, self.anon_map)

        self._log("Calcul des taux de reponse...")
        self._report.response_rates = compute_response_rates(
            self.df, self.anon_map, self._report.field_types
        )

        self._log("Parsing des champs multi-reponses...")
        self._process_multi_response()

        self._log("Decodage des champs JSON range...")
        self._process_json_range()

        self._log("Analyse des textes libres...")
        self._process_free_text()

        self._log("Statistiques ordinales...")
        self._process_ordinal()

        self._log("Verification de coherence inter-questions...")
        self._report.consistency_issues = check_inter_question_consistency(
            self.df, self._report.field_types, self._report.response_rates
        )

        ft = self._report.field_types
        self._report.n_multi    = sum(1 for t in ft.values() if t == "multi_response")
        self._report.n_json     = sum(1 for t in ft.values() if t == "json_range")
        self._report.n_ordinal  = sum(1 for t in ft.values() if t == "ordinal_scale")
        self._report.n_freetext = sum(1 for t in ft.values() if t == "free_text")
        self._report.n_filter   = sum(1 for t in ft.values() if t == "filter_question")

        if self.verbose:
            print(self._report.summary())

        return self._report

    def _process_multi_response(self):
        cols = [c for c, t in self._report.field_types.items()
                if t == "multi_response"]
        parser = MultiResponseParser(sep=MULTI_RESPONSE_SEP)
        for col in cols:
            try:
                onehot = parser.fit_transform(self.df[col], col_name=col)
                self._report.multi_onehot[col] = onehot
                self._report.multi_freq[col]   = parser.frequency_.copy()
                self._report.multi_coocc[col]  = parser.top_cooccurrences(10)
            except Exception as e:
                self._log("Erreur multi-reponse " + col + " : " + str(e))

    def _process_json_range(self):
        cols = [c for c, t in self._report.field_types.items()
                if t == "json_range"]
        decoder = JsonRangeDecoder()
        for col in cols:
            try:
                decoded = decoder.decode_series(self.df[col], col_name=col)
                self._report.json_decoded[col] = decoded
            except Exception as e:
                self._log("Erreur JSON " + col + " : " + str(e))

    def _process_free_text(self):
        cols = [c for c, t in self._report.field_types.items()
                if t == "free_text"]
        analyzer = FreeTextAnalyzer()
        for col in cols:
            try:
                stats_df = analyzer.analyze(self.df[col], col_name=col)
                self._report.freetext_stats[col] = stats_df
            except Exception as e:
                self._log("Erreur texte libre " + col + " : " + str(e))

    def _process_ordinal(self):
        cols = [c for c, t in self._report.field_types.items()
                if t == "ordinal_scale"]
        for col in cols:
            try:
                stats = ordinal_stats(self.df[col], col_name=col)
                self._report.ordinal_stats[col] = stats
            except Exception as e:
                self._log("Erreur ordinal " + col + " : " + str(e))

    @property
    def report(self) -> QuizReport:
        return self._report


# ─────────────────────────────────────────────
# SECTION LATEX QUIZ
# ─────────────────────────────────────────────

def _tex_q(s: str) -> str:
    """Echappe les caracteres speciaux LaTeX."""
    if not isinstance(s, str):
        s = str(s)
    for old, new in [
        ("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"),
        ("$", r"\$"), ("#", r"\#"), ("{", r"\{"), ("}", r"\}"),
        ("~", r"\textasciitilde{}"), ("^", r"\^{}"), ("_", r"\_"),
        ("<", r"\textless{}"), (">", r"\textgreater{}"),
    ]:
        s = s.replace(old, new)
    return s


def _latex_table_q(headers: list, rows: list, col_spec: str = None) -> str:
    """Genere une table LaTeX booktabs."""
    n = len(headers)
    if col_spec is None:
        col_spec = "l" + "r" * (n - 1)
    lines = [
        r"\begin{table}[H]", r"\centering", r"\small",
        r"\begin{tabular}{" + col_spec + r"}",
        r"\toprule",
        " & ".join(_tex_q(str(h)) for h in headers) + r" \\",
        r"\midrule",
    ]
    for row in rows:
        lines.append(" & ".join(_tex_q(str(c)) for c in row) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines)


def build_latex_quiz_section(report: QuizReport, anon_map: dict,
                              out_dir: str) -> str:
    """
    Genere le bloc LaTeX de la section Quiz.
    Retourne une chaine LaTeX prete a inclure dans rapport_eda.tex.
    """
    reverse_map = {v: k for k, v in anon_map.items()}
    lines = [r"\section{Analyse Specifique Quiz / Questionnaire}", ""]

    # Taux de reponse
    lines += [r"\subsection{Taux de reponse par question}", ""]
    if not report.response_rates.empty:
        rr = report.response_rates.reset_index()
        headers = ["Variable", "Nom original", "Type", "Taux reponse", "Niveau", "Filtre?"]
        rows = []
        for _, row in rr.iterrows():
            rows.append([
                str(row["col_anon"]),
                _tex_q(str(row["col_original"])[:30]),
                _tex_q(str(row["type_quiz"])),
                str(row["taux_reponse_pct"]) + r"\%",
                _tex_q(str(row["niveau"])),
                "Oui" if row["question_filtre"] else "Non",
            ])
        lines.append(_latex_table_q(headers, rows, col_spec="llllll"))
        lines.append("")

    # Multi-reponses
    if report.multi_freq:
        lines += [r"\subsection{Champs multi-reponses}", ""]
        for col, freq in report.multi_freq.items():
            orig = reverse_map.get(col, col)
            lines.append(
                r"\textbf{" + _tex_q(col) + " -- " + _tex_q(orig[:40]) + "} : "
                + str(len(freq)) + " modalites distinctes."
            )
            top5 = freq.head(5)
            r_rows = [[_tex_q(str(k)), str(int(v))] for k, v in top5.items()]
            lines.append(_latex_table_q(["Modalite", "Frequence (n)"], r_rows, "lr"))
            lines.append("")

    # JSON range
    if report.json_decoded:
        lines += [r"\subsection{Champs JSON (plages min/max/mid)}", ""]
        for col, decoded_df in report.json_decoded.items():
            orig = reverse_map.get(col, col)
            lines.append(
                r"\textbf{" + _tex_q(col) + " -- " + _tex_q(orig[:40]) + "} :"
            )
            stat_rows = []
            for sub in [col + "_min", col + "_max", col + "_mid"]:
                if sub in decoded_df.columns:
                    s = decoded_df[sub].dropna()
                    if not s.empty and pd.api.types.is_numeric_dtype(s):
                        stat_rows.append([
                            sub.split("_")[-1],
                            str(round(float(s.mean()), 2)),
                            str(round(float(s.median()), 2)),
                            str(round(float(s.min()), 2)),
                            str(round(float(s.max()), 2)),
                            str(len(s)),
                        ])
            if stat_rows:
                lines.append(_latex_table_q(
                    ["Sous-champ", "Moyenne", "Mediane", "Min", "Max", "N"],
                    stat_rows, "lrrrrr",
                ))
            lines.append("")

    # Ordinales
    if report.ordinal_stats:
        lines += [r"\subsection{Variables ordinales / echelles}", ""]
        h = ["Variable", "N", "Mode", "Mediane ord.", "Entropie norm.", "Centralite (%)"]
        r_rows = []
        for col, stats in report.ordinal_stats.items():
            orig = reverse_map.get(col, col)
            r_rows.append([
                _tex_q(col) + " (" + _tex_q(orig[:20]) + ")",
                str(stats.get("n_repondants", "")),
                _tex_q(str(stats.get("mode", ""))),
                _tex_q(str(stats.get("mediane_ordinale", ""))),
                str(stats.get("entropie_norm", "")),
                str(stats.get("centralite_pct", "")) + r"\%",
            ])
        lines.append(_latex_table_q(h, r_rows, "lrllrr"))
        lines.append("")

    # Incoherences
    if report.consistency_issues:
        lines += [
            r"\subsection{Incoherences inter-questions}", "",
            str(len(report.consistency_issues)) + " incoherence(s) detectee(s) :",
            r"\begin{itemize}",
        ]
        for issue in report.consistency_issues:
            lines.append("  \\item " + _tex_q(issue["message"]))
        lines += [r"\end{itemize}", ""]

    return "\n".join(lines)