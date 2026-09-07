"""
Constructeur de documents LaTeX pour l'Automatisation 3.

Prend les données structurées depuis MySQL et produit :
  - main.tex          : document principal (preamble + TOC + \\input des chapitres)
  - chapters/         : un fichier .tex par algorithme
  - annexes.tex       : tableau récapitulatif des métriques (algo × type)

Aucun appel LLM dans ce module — tout est construit depuis les données DB.
Les accents et caractères spéciaux sont gérés via la directive 'literate' de lstlisting
et le package inputenc/T1/babel french.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("auto3.latex_builder")


# ─── Utilitaires LaTeX ────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    """Échappe les caractères spéciaux LaTeX dans du texte courant."""
    if not text:
        return ""
    # Étape 1 : normaliser les espaces Unicode AVANT l'échappement
    text = text.replace(" ", " ")   # narrow no-break space → espace normale
    text = text.replace(" ", "~")   # non-breaking space → ~ LaTeX

    # Étape 2 : échapper les caractères spéciaux LaTeX (ordre : \ en premier)
    replacements = [
        ("\\", r"\textbackslash{}"),
        ("&",  r"\&"),
        ("%",  r"\%"),
        ("$",  r"\$"),
        ("#",  r"\#"),
        ("_",  r"\_"),
        ("{",  r"\{"),
        ("}",  r"\}"),
        ("~",  r"\textasciitilde{}"),
        ("^",  r"\textasciicircum{}"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)

    # Étape 3 : remplacer les symboles Unicode par leur équivalent LaTeX.
    # Appliqué APRÈS l'échappement pour éviter la double-échappement du $.
    unicode_fixes = [
        # Dashes and hyphens
        ("\u2011", "-"),          # non-breaking hyphen → regular hyphen
        ("\u2010", "-"),          # hyphen → regular hyphen
        ("\u2012", "--"),         # figure dash
        ("–", "--"),               # en dash
        ("—", "---"),              # em dash
        # Quotes
        ("\u2018", "'"),          # ' left single quote
        ("\u2019", "'"),          # ' right single quote
        ("\u201C", "``"),         # " left double quote
        ("\u201D", "''"),       # " right double quote
        ("\u201A", ","),          # ‚ single low quotation mark
        # Other punctuation
        ("…", r"\ldots{}"),       # ellipsis
        # Math operators — must come AFTER LaTeX escaping
        ("×", r"$\times$"),
        ("÷", r"$\div$"),
        ("±", r"$\pm$"),
        ("²", r"$^{2}$"),
        ("³", r"$^{3}$"),
        ("≥", r"$\geq$"),
        ("≤", r"$\leq$"),
        ("≠", r"$\neq$"),
        ("≈", r"$\approx$"),
        ("∈", r"$\in$"),
        ("∉", r"$\notin$"),
        ("∑", r"$\sum$"),
        ("∏", r"$\prod$"),
        ("∞", r"$\infty$"),
        ("√", r"$\sqrt{}$"),
        ("∀", r"$\forall$"),
        ("∃", r"$\exists$"),
        ("∧", r"$\wedge$"),
        ("∨", r"$\vee$"),
        ("→", r"$\rightarrow$"),
        ("←", r"$\leftarrow$"),
        ("↔", r"$\leftrightarrow$"),
        ("⇒", r"$\Rightarrow$"),
        ("⇔", r"$\Leftrightarrow$"),
        # Greek letters
        ("α", r"$\alpha$"),
        ("β", r"$\beta$"),
        ("γ", r"$\gamma$"),
        ("δ", r"$\delta$"),
        ("ε", r"$\epsilon$"),
        ("ζ", r"$\zeta$"),
        ("η", r"$\eta$"),
        ("θ", r"$\theta$"),
        ("λ", r"$\lambda$"),
        ("μ", r"$\mu$"),
        ("µ", r"$\mu$"),   # micro sign (U+00B5)
        ("ν", r"$\nu$"),
        ("ξ", r"$\xi$"),
        ("π", r"$\pi$"),
        ("ρ", r"$\rho$"),
        ("σ", r"$\sigma$"),
        ("τ", r"$\tau$"),
        ("φ", r"$\phi$"),
        ("χ", r"$\chi$"),
        ("ψ", r"$\psi$"),
        ("ω", r"$\omega$"),
        ("Δ", r"$\Delta$"),
        ("Σ", r"$\Sigma$"),
        ("Π", r"$\Pi$"),
        ("Ω", r"$\Omega$"),
        ("Λ", r"$\Lambda$"),
        ("Γ", r"$\Gamma$"),
        # Other common symbols
        ("·", r"$\cdot$"),    # middle dot
        ("°", r"$^{\circ}$"), # degree sign
        ("œ", r"\oe{}"),       # oe ligature
        ("æ", r"\ae{}"),       # ae ligature
        ("Œ", r"\OE{}"),       # OE ligature
        ("Æ", r"\AE{}"),       # AE ligature
        # Subscript digits
        ("₀", "$_{0}$"), ("₁", "$_{1}$"), ("₂", "$_{2}$"),
        ("₃", "$_{3}$"), ("₄", "$_{4}$"), ("₅", "$_{5}$"),
        ("₆", "$_{6}$"), ("₇", "$_{7}$"), ("₈", "$_{8}$"), ("₉", "$_{9}$"),
        # Superscript digits
        ("⁰", "$^{0}$"), ("¹", "$^{1}$"),
        ("⁴", "$^{4}$"), ("⁵", "$^{5}$"),
        # Combining characters — remove (can't safely render)
        ("\u0302", ""),   # combining circumflex
        ("\u0301", ""),   # combining acute
        ("\u0300", ""),   # combining grave
        ("\u0308", ""),   # combining diaeresis
    ]
    for bad, good in unicode_fixes:
        text = text.replace(bad, good)
    return text


def _lst(code: str, language: str = "Python") -> str:
    """Encapsule du code dans un environnement lstlisting."""
    if not code:
        return ""
    code = code.replace("\\n", "\n").replace("\\t", "    ")
    # Sanitize characters not handled by lstlisting (multibyte UTF-8 is unreliable in lstlisting)
    _lst_subs = [
        ("‑", "-"), ("‐", "-"), ("‒", "-"),  # hyphens → -
        ("–", "--"), ("—", "---"),                 # en/em dash
        (" ", " "), (" ", " "),                   # non-breaking spaces
        ("≥", ">="), ("≤", "<="),                  # comparison operators
        ("≈", "~="), ("≠", "!="),                  # approx, not equal
        ("∈", "in"), ("∉", "not in"),              # set membership
        ("∞", "inf"),                                   # infinity
        ("∑", "sum"), ("∏", "prod"),               # sum, product
        ("√", "sqrt"),                                  # square root
        ("±", "+-"),                                    # plus-minus
        ("²", "^2"), ("³", "^3"),                  # superscripts
        ("×", "*"), ("÷", "/"),                    # multiply, divide
        ("→", "->"), ("←", "<-"),                  # arrows
        ("⇒", "=>"), ("⇔", "<=>"),                 # double arrows
        ("α", "alpha"), ("β", "beta"), ("γ", "gamma"),
        ("δ", "delta"), ("ε", "epsilon"), ("ζ", "zeta"),
        ("η", "eta"), ("θ", "theta"), ("λ", "lambda"),
        ("μ", "mu"), ("µ", "mu"),  # micro sign ("ν", "nu"), ("ξ", "xi"),
        ("π", "pi"), ("ρ", "rho"), ("σ", "sigma"),
        ("τ", "tau"), ("φ", "phi"), ("χ", "chi"),
        ("ψ", "psi"), ("ω", "omega"),
        ("Δ", "Delta"), ("Σ", "Sigma"), ("Π", "Pi"),
        ("Ω", "Omega"), ("Λ", "Lambda"), ("Γ", "Gamma"),
        ("·", "."),    # middle dot → period
        ("°", "deg"),  # degree sign
        ("œ", "oe"), ("æ", "ae"),  # ligatures
        # Subscript/superscript digits
        ("₀", "0"), ("₁", "1"), ("₂", "2"), ("₃", "3"),
        ("₄", "4"), ("₅", "5"), ("₆", "6"), ("₇", "7"),
        ("₈", "8"), ("₉", "9"),
        ("⁰", "0"), ("¹", "1"), ("²", "2"), ("³", "3"),
        ("⁴", "4"), ("⁵", "5"),
        # Combining chars
        ("\u0302", "^"), ("\u0301", ""), ("\u0300", ""), ("\u0308", ""),
    ]
    for bad, good in _lst_subs:
        code = code.replace(bad, good)
    # Final catch-all: remove any remaining non-ASCII bytes that would
    # cause pdflatex "Invalid UTF-8 byte sequence" inside lstlisting.
    code = "".join(c if ord(c) < 128 or (0x00C0 <= ord(c) <= 0x00FF) else "?" for c in code)
    return (
        f"\\begin{{lstlisting}}[language={language}]\n"
        f"{code}\n"
        f"\\end{{lstlisting}}"
    )


def _algo_block(pseudocode: str, algo_name: str = "") -> str:
    """
    Encapsule un pseudo-code dans un environnement flottant algorithm2e
    (\\usepackage{algorithm2e}, voir _PREAMBLE) au lieu d'un simple bloc
    lstlisting — présentation dédiée à un algorithme (numérotation, légende,
    encadré), demandée explicitement pour toute présentation d'algorithme
    dans les rapports PDF.
    """
    if not pseudocode:
        return ""
    pseudocode = pseudocode.replace("\\n", "\n").replace("\\t", "    ")
    steps = [ln.strip() for ln in pseudocode.split("\n") if ln.strip()]
    caption = _esc(algo_name) if algo_name else "Algorithme"
    body = "\n".join(f"{_esc(step)}\\;" for step in steps)
    return (
        "\\begin{algorithm}[H]\n"
        f"\\caption{{{caption}}}\n"
        f"{body}\n"
        "\\end{algorithm}"
    )


def _esc_text_segments(text: str) -> str:
    """Escape LaTeX special chars in text segments, leaving $...$ math blocks untouched."""
    parts = re.split(r'(\$[^$]+\$)', text)
    out = []
    for part in parts:
        if part.startswith("$") and part.endswith("$") and len(part) > 2:
            out.append(part)
        else:
            out.append(_esc(part))
    return "".join(out)


def _esc_display_segments(text: str, math_context: bool = False) -> str:
    """Escape text outside \\[...\\] display blocks.

    In math_context mode (math_formulation field), inter-block text that has no
    inline $ (i.e. it's a pure LaTeX math expression) is wrapped in \\[...\\] rather
    than escaped as prose — because the LLM sometimes places bare equations between
    display blocks without their own delimiters. Text that contains $ (prose with
    inline math) is still escaped via _esc_text_segments().
    """
    parts = re.split(r'(\\\[.*?\\\])', text, flags=re.DOTALL)
    out = []
    for part in parts:
        if part.startswith('\\[') and part.endswith('\\]'):
            out.append(part)
        elif math_context:
            stripped = part.strip()
            if stripped:
                if '$' in stripped:
                    # Prose with inline math — escape text portions normally
                    out.append(_esc_text_segments(stripped))
                else:
                    # Pure LaTeX math expression — wrap as display block
                    out.append(f"\n\\[\n{stripped}\n\\]\n")
        else:
            out.append(_esc_text_segments(part))
    return "".join(out)


def _normalize_display_math(text: str) -> str:
    """Convert all $$...$$ blocks to \\[...\\], consuming any extra $ at boundaries."""
    def _strip_stray_dollar(s: str) -> str:
        s = s.strip()
        # Remove a trailing lone $ that came from a mismatched LLM delimiter ($$....$)
        if s.endswith("$") and not s.endswith("\\$") and not s.endswith("$$"):
            s = s[:-1].rstrip()
        return s

    result = []
    i = 0
    while i < len(text):
        if text[i:i+2] == "$$":
            j = text.find("$$", i + 2)
            if j == -1:
                # No closing $$ — treat rest as display, strip any stray trailing $
                inner = _strip_stray_dollar(text[i+2:])
                result.append(f"\\[\n{inner}\n\\]")
                break
            inner = _strip_stray_dollar(text[i+2:j])
            result.append(f"\\[\n{inner}\n\\]")
            i = j + 2
            # Skip a single stray $ (e.g. "$$$formula$$$" → lone extra $).
            # Do NOT skip "$$" pairs — they are the opening delimiter of the next block.
            if i < len(text) and text[i] == "$" and (i + 1 >= len(text) or text[i + 1] != "$"):
                i += 1
        else:
            result.append(text[i])
            i += 1
    return "".join(result)


def _fix_text_underscore(formula: str) -> str:
    """Normalise les formules LLM pour LaTeX : double-backslash et _ dans \\text{}."""
    # \\command → \command (LLM JSON over-escaping)
    formula = re.sub(r'\\\\([a-zA-Z])', r'\\\1', formula)
    # Escape bare _ inside \text{...}
    formula = re.sub(
        r'\\text\{([^{}]*)\}',
        lambda m: r'\text{' + re.sub(r'(?<!\\)_', r'\\_', m.group(1)) + '}',
        formula,
    )
    # \d followed by _ or { in math = variable d, not the LaTeX text accent \d
    formula = re.sub(r'(?<!\\)\\d(?=[_{^])', r'd', formula)
    return formula


def _math_block(formula: str) -> str:
    """Formule mathématique — display ou paragraphe selon le contenu LLM."""
    if not formula:
        return ""
    formula = formula.replace("\\n", "\n")
    formula = _fix_text_underscore(formula)
    f = formula.strip()

    # ── Pre-normalize: convert all $$...$$ blocks to \[...\] first ───────────
    if "$$" in f:
        normalized = _normalize_display_math(f).strip()
        # If normalized contains \[, it's already display math — clean up and return
        if "\\[" in normalized:
            # Handle trailing text after last \]
            last_end = normalized.rfind("\\]")
            after_display = normalized[last_end + 2:].strip() if last_end != -1 else ""
            if after_display:
                main = _esc_display_segments(normalized[:last_end + 2], math_context=True)
                return main + "\n" + _esc_text_segments(after_display) + "\n"
            return _esc_display_segments(normalized, math_context=True) + "\n"
        f = normalized  # fall through with normalized content

    # ── Pattern 2 : $formula$ [optional trailing text] ───────────────────────
    if f.startswith("$"):
        # Find first closing $ (single)
        i = 1
        while i < len(f):
            if f[i] == "$":
                break
            i += 1
        if i < len(f):  # Found closing $
            # Check for $formula$$ pattern: extra $ after closing — treat as display
            if i + 1 < len(f) and f[i + 1] == "$":
                inner = f[1:i].strip()
                after = f[i + 2:].strip()  # skip both $$ delimiter
                if after:
                    return f"\\[\n{inner}\n\\]\n" + _math_block(after)
                return f"\\[\n{inner}\n\\]"
            inner = f[1:i].strip()
            after = f[i + 1:].strip()
            if not after:
                # Simple $formula$: use display block if no inner $
                if "$" not in inner:
                    return f"\\[\n{inner}\n\\]"
                return "$" + inner + "\n"
            # $formula$ + trailing text — escape _ in text portions of 'after'
            after_tex = _esc_text_segments(after)
            # Ensure unclosed $ in trailing text are closed
            if after_tex.count("$") % 2 != 0:
                after_tex += "$"
            return "$" + inner + "$" + after_tex + "\n"
        # No closing $ found: strip leading $ and fall through
        f = f[1:].strip()

    # ── At this point f has no leading $ delimiters ───────────────────────────
    if "$" not in f:
        return f"\\[\n{f}\n\\]"

    # Mixed formula with inline $...$ — determine if math context needed at start
    starts_with_math = (
        f.startswith("\\")
        or f.startswith("{")
        or bool(re.match(r'^[A-Za-z][_^]', f))
    )
    if starts_with_math:
        return "$" + f + "\n"
    # Text-first (e.g. "Soit $y_t$ la série..."): escape text segments, leave math as-is
    return _esc_text_segments(f) + "\n"


def _items(lst: List[str]) -> str:
    """Génère un environnement itemize."""
    if not lst:
        return ""
    items = "\n".join(f"    \\item {_esc(str(x))}" for x in lst if x)
    return f"\\begin{{itemize}}\n{items}\n\\end{{itemize}}"


def _table_hyperparams(hyperparams: List[Dict]) -> str:
    """Génère un tableau LaTeX des hyperparamètres."""
    if not hyperparams:
        return ""
    rows = []
    for h in hyperparams:
        name    = _esc(str(h.get("name", "")))
        htype   = _esc(str(h.get("type", "")))
        default = _esc(str(h.get("default", "")))
        rng     = _esc(str(h.get("range", "")))
        desc    = _esc(str(h.get("description", "")))
        rows.append(f"    {name} & {htype} & {default} & {rng} & {desc} \\\\")
    body = "\n    \\midrule\n".join(rows)
    return (
        "\\begin{center}\n"
        "\\begin{tabular}{lllll}\n"
        "\\toprule\n"
        "Paramètre & Type & Défaut & Plage & Description \\\\\n"
        "\\midrule\n"
        f"{body}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
        "\\end{center}"
    )


def _table_metrics(metrics: List[Dict]) -> str:
    """Génère un tableau LaTeX des métriques d'évaluation."""
    if not metrics:
        return ""
    rows = []
    for m in metrics:
        name     = _esc(str(m.get("name", "")))
        form_raw = _fix_text_underscore(str(m.get("formula", "")).strip())
        interp   = _esc(str(m.get("interpretation", "")))
        # Determine how to format the formula cell:
        # - Simple $formula$ with no internal $ → wrap cleanly
        # - Mixed text+math (internal $) → use as-is; it's already formatted
        if form_raw.startswith("$$") and form_raw.endswith("$$") and form_raw.count("$$") == 2:
            form_cell = f"${form_raw[2:-2]}$"
        elif form_raw.startswith("$") and form_raw.endswith("$") and form_raw[1:-1].count("$") == 0:
            form_cell = form_raw  # already clean $formula$
        elif "$" in form_raw:
            form_cell = form_raw  # mixed math+text, use as-is
        else:
            form_cell = f"${form_raw}$"  # plain formula, wrap
        # % is a LaTeX comment char in all modes — escape bare % to \%
        form_cell = re.sub(r'(?<!\\)%', r'\\%', form_cell)
        rows.append(f"    {name} & {form_cell} & {interp} \\\\")
    body = "\n    \\midrule\n".join(rows)
    return (
        "\\begin{center}\n"
        "\\begin{tabular}{lll}\n"
        "\\toprule\n"
        "Métrique & Formule & Interprétation \\\\\n"
        "\\midrule\n"
        f"{body}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
        "\\end{center}"
    )


def _results_table(exec_metrics: Dict) -> str:
    """Génère un tableau des résultats d'exécution depuis notebook_results."""
    if not exec_metrics:
        return ""
    rows = []
    labels = {
        "f1":        "F1-Score",
        "precision": "Précision",
        "recall":    "Rappel",
        "fpr":       "Taux faux positifs",
        "n_anomalies": "Anomalies détectées",
        "anomaly_rate": "Taux d'anomalies",
    }
    for key, label in labels.items():
        val = exec_metrics.get(key)
        if val is not None:
            rows.append(f"    {label} & {val:.4f} \\\\" if isinstance(val, float) else f"    {label} & {val} \\\\")
    if not rows:
        return ""
    body = "\n".join(rows)
    return (
        "\\begin{center}\n"
        "\\begin{tabular}{ll}\n"
        "\\toprule\n"
        "Indicateur & Valeur \\\\\n"
        "\\midrule\n"
        f"{body}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
        "\\end{center}"
    )


def _bibliography(references: List[Dict]) -> str:
    """Génère un environnement thebibliography simple."""
    if not references:
        return ""
    items = []
    for i, r in enumerate(references, 1):
        key     = re.sub(r"\W", "", str(r.get("authors", f"ref{i}")).split(",")[0]) + str(r.get("year", ""))
        authors = _esc(str(r.get("authors", "")))
        title   = _esc(str(r.get("title", "")))
        year    = _esc(str(r.get("year", "")))
        venue   = _esc(str(r.get("venue", "")))
        doi     = _esc(str(r.get("doi", ""))) if r.get("doi") else ""
        doi_str = f" DOI: {doi}." if doi else ""
        items.append(
            f"\\bibitem{{{key}}}\n"
            f"{authors} ({year}). \\textit{{{title}}}. {venue}.{doi_str}"
        )
    body = "\n\n".join(items)
    return f"\\begin{{thebibliography}}{{99}}\n{body}\n\\end{{thebibliography}}"


def _figure(fig_path: str, caption: str, label: str) -> str:
    """Génère une figure LaTeX si le fichier existe."""
    p = Path(fig_path)
    if not p.exists():
        return ""
    rel = p.name  # On copie les figures dans le répertoire figures/
    caption_esc = _esc(caption)
    return (
        "\\begin{figure}[htbp]\n"
        "\\centering\n"
        f"\\includegraphics[width=0.9\\textwidth]{{figures/{rel}}}\n"
        f"\\caption{{{caption_esc}}}\n"
        f"\\label{{fig:{label}}}\n"
        "\\end{figure}"
    )


# ─── Preamble LaTeX ───────────────────────────────────────────────────────────

_PREAMBLE = r"""
\documentclass[11pt,a4paper]{report}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[french]{babel}
\usepackage{lmodern}
\usepackage{microtype}
\usepackage{amsmath,amssymb,amsfonts}
\DeclareMathOperator*{\argmin}{arg\,min}
\DeclareMathOperator*{\argmax}{arg\,max}
\DeclareMathOperator{\diag}{diag}
\DeclareMathOperator{\rank}{rank}
\DeclareMathOperator{\supp}{supp}
\DeclareMathOperator{\sign}{sign}
\DeclareMathOperator{\Var}{Var}
\DeclareMathOperator{\Cov}{Cov}
\DeclareMathOperator{\Corr}{Corr}
\DeclareMathOperator{\softmax}{softmax}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage{array}
\usepackage{longtable}
\usepackage{xcolor}
\usepackage{listings}
\usepackage{hyperref}
\usepackage{geometry}
\usepackage{fancyhdr}
\usepackage{titlesec}
\usepackage{tocloft}

\geometry{margin=2.5cm, top=3cm, bottom=3cm}

\hypersetup{
    colorlinks=true,
    linkcolor=blue!60!black,
    urlcolor=blue,
    citecolor=green!50!black,
}

\lstset{
    language=Python,
    basicstyle=\ttfamily\footnotesize,
    keywordstyle=\color{blue!80!black}\bfseries,
    commentstyle=\color{gray}\itshape,
    stringstyle=\color{red!70!black},
    numberstyle=\tiny\color{gray},
    numbers=left,
    frame=single,
    breaklines=true,
    breakatwhitespace=false,
    showstringspaces=false,
    tabsize=4,
    captionpos=b,
    literate=
        {é}{{\'{e}}}1 {è}{{\`{e}}}1 {ê}{{\^{e}}}1 {ë}{{\"e}}1
        {à}{{\`{a}}}1 {â}{{\^{a}}}1 {ä}{{\"a}}1
        {ù}{{\`{u}}}1 {û}{{\^{u}}}1 {ü}{{\"u}}1
        {î}{{\^{i}}}1 {ï}{{\"i}}1
        {ô}{{\^{o}}}1 {ö}{{\"o}}1
        {ç}{{\c{c}}}1 {Ç}{{\c{C}}}1
        {œ}{{\oe}}1 {æ}{{\ae}}1
        {É}{{\'E}}1 {È}{{\`E}}1 {Ê}{{\^E}}1
        {À}{{\`A}}1 {Î}{{\^I}}1 {Ô}{{\^O}}1
        {→}{$\rightarrow$}1 {←}{$\leftarrow$}1
        {α}{{$\alpha$}}1 {β}{{$\beta$}}1 {γ}{{$\gamma$}}1
        {δ}{{$\delta$}}1 {ε}{{$\epsilon$}}1 {λ}{{$\lambda$}}1
        {μ}{{$\mu$}}1 {σ}{{$\sigma$}}1 {θ}{{$\theta$}}1
        {Σ}{{$\Sigma$}}1 {Δ}{{$\Delta$}}1 {Ω}{{$\Omega$}}1
        {‑}{{-}}1
}

\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\leftmark}
\fancyhead[R]{\thepage}
\fancyfoot[C]{\small Mobility Reporting — Rapport automatique}
\renewcommand{\headrulewidth}{0.4pt}
""".strip()


# ─── Chapitre LaTeX ───────────────────────────────────────────────────────────

def build_chapter(
    data_type: Dict,
    problem: Dict,
    algorithm: Dict,
    exec_metrics: Optional[Dict] = None,
    figures: Optional[List[str]] = None,
    chapter_num: int = 1,
) -> str:
    """
    Génère le contenu LaTeX complet d'un chapitre (un fichier .tex).

    Args:
        data_type    : dict depuis data_types (name, domain, description...)
        problem      : dict depuis problems
        algorithm    : dict depuis algorithms
        exec_metrics : métriques d'exécution (f1, precision, recall, ...)
        figures      : chemins vers les figures .png générées
        chapter_num  : numéro du chapitre

    Retourne une chaîne LaTeX.
    """
    type_name  = data_type.get("name", "")
    type_domain = data_type.get("domain", "")
    type_desc  = data_type.get("description", "")

    prob_title = problem.get("title", "")
    prob_desc  = problem.get("description", "")
    causes     = _parse_list(problem.get("causes", []))
    solutions  = _parse_list(problem.get("solutions_overview", []))
    affected   = _parse_list(problem.get("affected_actors", []))
    indicators = _parse_list(problem.get("detection_indicators", []))
    impact     = problem.get("impact_level", "")
    data_req   = _parse_list(problem.get("data_requirements", []))

    alg_name   = algorithm.get("name", "")
    alg_cat    = algorithm.get("category", "")
    principle  = algorithm.get("principle", "")
    math_form  = algorithm.get("math_formulation", "")
    pseudocode = algorithm.get("pseudocode", "")
    skeleton   = algorithm.get("python_skeleton", "")
    advantages = _parse_list(algorithm.get("advantages", []))
    limitations= _parse_list(algorithm.get("limitations", []))
    hyperparams= _parse_json_list(algorithm.get("hyperparameters", []))
    eval_metrics= _parse_json_list(algorithm.get("evaluation_metrics", []))
    references = _parse_json_list(algorithm.get("references", []))
    use_case   = algorithm.get("use_case_example", "")

    lines = []

    # ── En-tête chapitre ─────────────────────────────────────────────────────
    lines.append(f"\\chapter{{{_esc(alg_name)}}}")
    lines.append(f"\\label{{chap:{chapter_num:02d}_{_slug(alg_name)}}}")
    lines.append("")
    lines.append(
        f"\\noindent\\textbf{{Type de données :}} {_esc(type_name)} "
        f"\\hfill \\textbf{{Catégorie :}} {_esc(alg_cat)}"
    )
    lines.append("")

    # ── Section 1 : Contexte ─────────────────────────────────────────────────
    lines.append("\\section{Contexte et Problématique}")
    if type_domain:
        lines.append(f"Ce travail s'inscrit dans le domaine de la \\textbf{{{_esc(type_domain)}}}.")
    if type_desc:
        lines.append(_esc(type_desc))
    lines.append("")
    lines.append(f"\\subsection{{{_esc(prob_title)}}}")
    if impact:
        lines.append(f"\\noindent\\textbf{{Niveau d'impact :}} {_esc(impact)}")
    if prob_desc:
        lines.append(_esc(prob_desc))
    if causes:
        lines.append("\\paragraph{Causes identifiées :}")
        lines.append(_items(causes))
    if affected:
        lines.append("\\paragraph{Acteurs concernés :}")
        lines.append(_items(affected))
    if indicators:
        lines.append("\\paragraph{Indicateurs de détection :}")
        lines.append(_items(indicators))
    if data_req:
        lines.append("\\paragraph{Données requises :}")
        lines.append(_items(data_req))
    if solutions:
        lines.append("\\paragraph{Pistes de solutions :}")
        lines.append(_items(solutions))
    lines.append("")

    # ── Section 2 : Fondements théoriques ────────────────────────────────────
    lines.append("\\section{Fondements Théoriques}")
    if principle:
        lines.append("\\subsection{Principe général}")
        lines.append(_esc(principle))
        lines.append("")
    if math_form:
        lines.append("\\subsection{Formulation mathématique}")
        lines.append(_math_block(math_form))
        lines.append("")
    if pseudocode:
        lines.append("\\subsection{Pseudo-code}")
        pseudo_clean = pseudocode.replace("\\n", "\n").replace("\\t", "    ")
        lines.append(_lst(pseudo_clean, language=""))
        lines.append("")

    # ── Section 3 : Implémentation ───────────────────────────────────────────
    lines.append("\\section{Implémentation Python}")
    if hyperparams:
        lines.append("\\subsection{Hyperparamètres}")
        lines.append(_table_hyperparams(hyperparams))
        lines.append("")
    if skeleton:
        lines.append("\\subsection{Squelette de code}")
        lines.append(_lst(skeleton))
        lines.append("")
    if use_case:
        lines.append("\\paragraph{Exemple d'application :}")
        lines.append(_esc(use_case))
        lines.append("")

    # ── Section 4 : Résultats expérimentaux ──────────────────────────────────
    lines.append("\\section{Résultats Expérimentaux}")
    if exec_metrics:
        lines.append("\\subsection{Métriques d'évaluation}")
        lines.append(_results_table(exec_metrics))
        lines.append("")
    if eval_metrics:
        lines.append("\\subsection{Métriques documentées}")
        lines.append(_table_metrics(eval_metrics))
        lines.append("")
    if figures:
        lines.append("\\subsection{Visualisations}")
        for i, fig_path in enumerate(figures[:3]):
            fig_label = f"fig{chapter_num:02d}_{i+1}"
            fig_line = _figure(
                fig_path,
                f"Résultats de {_esc(alg_name)} — {['données brutes', 'détection', 'métriques'][i % 3]}",
                fig_label,
            )
            if fig_line:
                lines.append(fig_line)
                lines.append("")

    # ── Section 5 : Discussion ────────────────────────────────────────────────
    lines.append("\\section{Discussion}")
    if advantages:
        lines.append("\\subsection{Avantages}")
        lines.append(_items(advantages))
        lines.append("")
    if limitations:
        lines.append("\\subsection{Limitations}")
        lines.append(_items(limitations))
        lines.append("")

    # ── Section 6 : Conclusion ────────────────────────────────────────────────
    lines.append("\\section{Conclusion}")
    concl = (
        f"L'algorithme \\textbf{{{_esc(alg_name)}}} ({_esc(alg_cat)}) "
        f"a été appliqué pour traiter la problématique \\textit{{{_esc(prob_title)}}} "
        f"sur les données de type \\textbf{{{_esc(type_name)}}}."
    )
    if exec_metrics and "f1" in exec_metrics:
        concl += (
            f" Le F1-score obtenu est de \\textbf{{{exec_metrics['f1']:.4f}}}, "
            f"avec une précision de {exec_metrics.get('precision', 0):.4f} "
            f"et un rappel de {exec_metrics.get('recall', 0):.4f}."
        )
    lines.append(concl)
    lines.append("")

    # ── Bibliographie ─────────────────────────────────────────────────────────
    if references:
        lines.append(_bibliography(references))

    return "\n".join(lines)


def _parse_list(val) -> List[str]:
    if isinstance(val, list):
        return [str(x) for x in val if x]
    if isinstance(val, str) and val:
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if x]
        except Exception:
            pass
        return [val]
    return []


def _parse_json_list(val) -> List[Dict]:
    if isinstance(val, list):
        return [x for x in val if isinstance(x, dict)]
    if isinstance(val, str) and val:
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return [x for x in parsed if isinstance(x, dict)]
        except Exception:
            pass
    return []


def _slug(text: str) -> str:
    """Convertit un nom en slug ASCII pour les labels LaTeX."""
    s = re.sub(r"[^a-zA-Z0-9]", "_", text.lower())
    return s[:30].strip("_")


# ─── Annexes ──────────────────────────────────────────────────────────────────

def build_annexes(
    data_type: Dict,
    algorithms_with_metrics: List[Dict],
) -> str:
    """
    Génère le fichier annexes.tex avec un tableau récapitulatif des métriques
    pour tous les algorithmes du type de données.
    """
    type_name = data_type.get("name", "")
    lines = [
        f"\\chapter{{Annexes — {_esc(type_name)}}}",
        "",
        "\\section{Tableau récapitulatif des métriques}",
        "",
        "\\begin{center}",
        "\\begin{longtable}{p{4cm}p{3cm}cccc}",
        "\\toprule",
        "Algorithme & Problème & F1 & Précision & Rappel & Temps (s) \\\\",
        "\\midrule",
        "\\endfirsthead",
        "\\multicolumn{6}{c}{\\textit{(Suite du tableau)}} \\\\",
        "\\toprule",
        "Algorithme & Problème & F1 & Précision & Rappel & Temps (s) \\\\",
        "\\midrule",
        "\\endhead",
        "\\bottomrule",
        "\\endlastfoot",
    ]

    for item in algorithms_with_metrics:
        alg_name  = _esc(item.get("name", "")[:40])
        prob_title = _esc(item.get("problem_title", "")[:40])
        metrics   = item.get("exec_metrics", {}) or {}
        f1   = f"{metrics.get('f1', ''):.4f}"   if metrics.get("f1")  is not None else "—"
        prec = f"{metrics.get('precision', ''):.4f}" if metrics.get("precision") is not None else "—"
        rec  = f"{metrics.get('recall', ''):.4f}"   if metrics.get("recall") is not None else "—"
        tps  = f"{item.get('exec_time', ''):.1f}"   if item.get("exec_time") is not None else "—"
        lines.append(f"    {alg_name} & {prob_title} & {f1} & {prec} & {rec} & {tps} \\\\")

    lines += [
        "\\end{longtable}",
        "\\end{center}",
        "",
        "\\section{Informations sur le dataset}",
        "",
        f"Type de données : \\textbf{{{_esc(type_name)}}}",
        f"Domaine : {_esc(data_type.get('domain', ''))}",
        "",
        "\\subsection{Caractéristiques du dataset synthétique}",
        "\\begin{itemize}",
        "\\item 5\\,000 lignes générées par distribution normale tronquée",
        "\\item 5\\% de lignes annotées comme anomalies (ground-truth)",
        "\\item Anomalies injectées : spikes (×5), chutes à 0, valeurs négatives",
        "\\item Colonnes standards : timestamp, sensor\\_id, value, anomaly\\_flag",
        "\\end{itemize}",
    ]

    return "\n".join(lines)


# ─── Chapitre de synthèse comparative ────────────────────────────────────────

def build_comparative_chapter(
    data_type: Dict,
    algos_metrics: List[Dict],
    comparison_figure: Optional[str] = None,
    per_problem_figs: Optional[Dict[str, str]] = None,
    comparison_figure_timing: Optional[str] = None,
) -> str:
    """
    Génère le chapitre de synthèse : une section par problème avec graphique,
    puis un classement global.
    """
    type_name = data_type.get("name", "")
    n_algos   = len(algos_metrics)
    per_problem_figs = per_problem_figs or {}

    lines: List[str] = [
        "\\chapter{Synthèse \\& Comparaison des Algorithmes}",
        "\\label{chap:synthese}",
        "",
        f"Ce chapitre compare les performances des {n_algos} algorithmes analysés"
        f" pour le type de données \\textbf{{{_esc(type_name)}}}.",
        "Chaque section présente un problème et compare les algorithmes qui le traitent.",
        "Les métriques (F1, Précision, Rappel) sont calculées sur un dataset synthétique de 5\\,000 observations.",
        "",
    ]

    # ── Section par problème ──────────────────────────────────────────────
    # Regrouper les algorithmes par problème
    problems_order: List[str] = []
    problems_map: Dict[str, List[Dict]] = {}
    for m in algos_metrics:
        pk = m.get("problem_key") or "autres"
        if pk not in problems_map:
            problems_map[pk] = []
            problems_order.append(pk)
        problems_map[pk].append(m)

    lines.append("\\section{Comparaison par Problème}")
    lines.append("")

    for pk in problems_order:
        prob_algos = problems_map[pk]
        prob_title = prob_algos[0].get("problem_title", pk)

        lines += [
            f"\\subsection{{{_esc(prob_title[:80])}}}",
            "",
        ]

        # Tableau des algorithmes de ce problème
        lines += [
            "\\begin{center}",
            "\\begin{tabular}{lcccc}",
            "\\toprule",
            "    \\textbf{Algorithme} & \\textbf{F1} & \\textbf{Précision} & \\textbf{Rappel} & \\textbf{Temps (s)} \\\\",
            "\\midrule",
        ]
        for m in prob_algos:
            em   = m.get("exec_metrics") or {}
            name = _esc(m.get("name", "—")[:50])
            f1   = f"{em['f1']:.3f}"        if em.get("f1")        is not None else "—"
            prec = f"{em['precision']:.3f}" if em.get("precision") is not None else "—"
            rec  = f"{em['recall']:.3f}"    if em.get("recall")    is not None else "—"
            tps  = f"{m['exec_time']:.1f}"  if m.get("exec_time")  is not None else "—"
            lines.append(f"    {name} & {f1} & {prec} & {rec} & {tps} \\\\")
        lines += ["\\bottomrule", "\\end{tabular}", "\\end{center}", ""]

        # Graphique par problème
        if pk in per_problem_figs:
            fig_path = per_problem_figs[pk]
            fig_label = f"fig:compare_{_slug(pk)}"
            lines += [
                "\\begin{figure}[htbp]",
                "\\centering",
                f"\\includegraphics[width=0.95\\linewidth]{{{fig_path}}}",
                f"\\caption{{Comparaison des algorithmes pour : {_esc(prob_title[:60])}}}",
                f"\\label{{{fig_label}}}",
                "\\end{figure}",
                "",
            ]

    # ── Classement global ──────────────────────────────────────────────────
    lines += ["\\section{Classement Global par F1-Score}", ""]

    ranked = sorted(
        [m for m in algos_metrics if m.get("exec_metrics", {}).get("f1") is not None],
        key=lambda m: m["exec_metrics"]["f1"],
        reverse=True,
    )

    if ranked:
        # Tableau global trié par F1
        lines += [
            "\\begin{center}",
            "\\begin{tabular}{clcccc}",
            "\\toprule",
            "    \\textbf{Rang} & \\textbf{Algorithme} & \\textbf{Problème} & \\textbf{F1} & \\textbf{Rappel} & \\textbf{Temps (s)} \\\\",
            "\\midrule",
        ]
        for rank, m in enumerate(ranked[:20], 1):  # top 20
            em   = m.get("exec_metrics") or {}
            name = _esc(m.get("name", "—")[:40])
            prob = _esc(m.get("problem_title", "—")[:35])
            f1   = f"{em['f1']:.3f}"
            rec  = f"{em.get('recall', 0):.3f}" if em.get("recall") is not None else "—"
            tps  = f"{m['exec_time']:.1f}" if m.get("exec_time") is not None else "—"
            lines.append(f"    {rank} & {name} & {prob} & {f1} & {rec} & {tps} \\\\")
        lines += ["\\bottomrule", "\\end{tabular}", "\\end{center}", ""]

        # Graphique global
        if comparison_figure:
            lines += [
                "\\begin{figure}[htbp]",
                "\\centering",
                f"\\includegraphics[width=0.92\\linewidth]{{{comparison_figure}}}",
                f"\\caption{{Classement global — F1/Précision/Rappel pour tous les algorithmes ({_esc(type_name)})}}",
                f"\\label{{fig:compare_global_{_slug(type_name)}}}",
                "\\end{figure}",
                "",
            ]

        if comparison_figure_timing:
            lines += [
                "\\begin{figure}[htbp]",
                "\\centering",
                f"\\includegraphics[width=0.85\\linewidth]{{{comparison_figure_timing}}}",
                f"\\caption{{Temps d'exécution comparés ({_esc(type_name)})}}",
                f"\\label{{fig:compare_timing_{_slug(type_name)}}}",
                "\\end{figure}",
                "",
            ]

        best = ranked[0]
        lines.append(
            f"\\textbf{{Meilleur algorithme :}} {_esc(best['name'])}"
            f" (F1~=~{best['exec_metrics']['f1']:.3f}, Rappel~=~{best['exec_metrics'].get('recall', 0):.3f})."
        )
        lines.append("")
        lines.append(
            "Ces résultats sont obtenus sur un dataset synthétique et servent"
            " d'indicateurs relatifs — une validation sur des données réelles est recommandée."
        )
    else:
        lines.append(
            "\\textit{Aucune métrique d'exécution disponible."
            " Relancer l'Automatisation~2 pour exécuter les notebooks.}"
        )

    lines.append("")
    return "\n".join(lines)


# ─── Document principal ───────────────────────────────────────────────────────

def build_main_tex(
    data_type: Dict,
    chapter_files: List[str],
    has_annexes: bool = True,
    problem: Optional[Dict] = None,
) -> str:
    """
    Génère le fichier main.tex du rapport.

    Args:
        data_type     : données du type de données
        chapter_files : liste des chemins relatifs des fichiers chapitre
        has_annexes   : inclure le fichier annexes.tex
        problem       : problème ciblé par ce rapport (architecture par problème)
    """
    type_name   = data_type.get("name", "")
    type_domain = data_type.get("domain", "")
    type_desc   = data_type.get("description", "")

    prob_title = problem.get("title", "") if problem else ""
    prob_desc  = problem.get("description", "") if problem else ""

    inputs = "\n".join(f"\\input{{{cf}}}" for cf in chapter_files)
    annexe_line = "\\input{annexes}" if has_annexes else ""

    prob_line = f"\\\\[0.4em]\n\\normalsize Problème : {_esc(prob_title)}" if prob_title else ""

    abstract_text = _esc(
        prob_desc or type_desc
        or f"Rapport d'analyse pour les données de type {type_name}."
    )

    intro_prob = (
        f" La problématique traitée est \\textit{{{_esc(prob_title)}}}."
        if prob_title else ""
    )

    return f"""{_PREAMBLE}

\\title{{\\Huge\\bfseries Analyse des Données de Mobilité Urbaine \\\\[1em]
\\Large Type : {_esc(type_name)}{prob_line} \\\\[0.5em]
\\large Domaine : {_esc(type_domain)}}}
\\author{{Mobility Reporting \\\\
\\textit{{Pipeline automatisé — Automatisation 3}}}}
\\date{{\\today}}

\\begin{{document}}

\\maketitle

\\begin{{abstract}}
{abstract_text}
\\end{{abstract}}

\\tableofcontents
\\listoffigures
\\clearpage

\\chapter{{Introduction}}
\\label{{chap:intro}}

Ce rapport présente une analyse approfondie des algorithmes associés
aux données de mobilité urbaine de type \\textbf{{{_esc(type_name)}}}.{intro_prob}

Chaque chapitre décrit un algorithme de traitement spécifique :
sa formalisation mathématique, son implémentation Python, et ses résultats
d'évaluation obtenus sur un dataset synthétique avec anomalies injectées.

Les données proviennent de l'Automatisation 1 (génération LLM de problèmes et algorithmes)
et de l'Automatisation 2 (exécution des notebooks et capture des métriques).

\\clearpage

{inputs}

{annexe_line}

\\end{{document}}
"""


# ─── Écriture des fichiers sur disque ────────────────────────────────────────

def _resolve_figure_ref(ref: str, figures_dir: Path) -> Optional[str]:
    """
    Résout une référence de figure vers un fichier local dans figures_dir.

    Ordre de résolution :
    1. Chemin disque local existant
    2. Clé MinIO dans bucket `figures` (algo result figures uploadées par S3)
    3. Clé MinIO dans bucket `catalogue` (comparison figures uploadées par S5)
    """
    if not ref:
        return None

    # 1) Chemin local
    local_p = Path(ref)
    if local_p.exists():
        dst = figures_dir / local_p.name
        shutil.copy2(local_p, dst)
        return f"figures/{dst.name}"

    try:
        from shared.storage.client import get_storage, BUCKET_CATALOGUE, BUCKET_FIGURES
        store    = get_storage()
        dst_name = Path(ref).name or "figure.png"
        if not dst_name.lower().endswith(".png"):
            dst_name += ".png"
        dst = figures_dir / dst_name

        # 2) Bucket figures (résultats algo uploadés par S3)
        try:
            store.download_file(BUCKET_FIGURES, ref, dst)
            log.debug("[BUILDER] Figure téléchargée depuis bucket figures : %s", ref)
            return f"figures/{dst_name}"
        except Exception:
            pass

        # 3) Bucket catalogue (comparison figures uploadées par S5)
        try:
            store.download_file(BUCKET_CATALOGUE, ref, dst)
            log.debug("[BUILDER] Figure téléchargée depuis bucket catalogue : %s", ref)
            return f"figures/{dst_name}"
        except Exception:
            pass

    except Exception as exc:
        log.warning("[BUILDER] Figure introuvable (%s) : %s", ref, exc)

    return None


def write_report_files(
    report_dir: Path,
    data_type: Dict,
    chapters_data: List[Dict],
    problem: Optional[Dict] = None,
    comparison_figure: Optional[Dict[str, str]] = None,
) -> List[str]:
    """
    Écrit tous les fichiers .tex dans report_dir.

    Args:
        report_dir         : répertoire de sortie (créé si nécessaire)
        data_type          : données du type de données
        chapters_data      : liste de dicts avec les données de chaque chapitre
        problem            : problème ciblé par ce rapport (architecture par problème)
        comparison_figure  : {"performance": ref, "timing": ref} — chaque ref étant
                              un chemin local ou une clé Minio (bucket catalogue)

    Retourne la liste des chemins de fichiers créés.
    """
    chapters_dir = report_dir / "chapters"
    figures_dir  = report_dir / "figures"
    if chapters_dir.exists():
        for stale in chapters_dir.glob("chapter_*.tex"):
            stale.unlink()
    chapters_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    chapter_files = []
    all_algos_metrics = []

    # Dériver la clé du problème pour le titre des figures
    prob = problem or {}
    prob_key = prob.get("problem_key", "")

    for i, ch in enumerate(chapters_data, 1):
        algo     = ch["algorithm"]
        ch_prob  = ch.get("problem") or prob
        metrics  = ch.get("exec_metrics") or {}
        figs     = ch.get("figures") or []

        local_figs = []
        for fig_path in figs:
            # Résoudre chaque figure : chemin local ou clé MinIO (figures ou catalogue)
            resolved = _resolve_figure_ref(fig_path, figures_dir)
            if resolved:
                local_figs.append(str(figures_dir / Path(resolved).name))

        chapter_content = build_chapter(
            data_type=data_type,
            problem=ch_prob,
            algorithm=algo,
            exec_metrics=metrics,
            figures=local_figs,
            chapter_num=i,
        )

        chapter_fname = f"chapter_{i:02d}_{_slug(algo.get('name', f'algo{i}'))}.tex"
        chapter_path  = chapters_dir / chapter_fname
        chapter_path.write_text(chapter_content, encoding="utf-8")
        chapter_files.append(f"chapters/{chapter_fname}")

        log.info("[BUILDER] Chapitre %d écrit : %s", i, chapter_fname)

        all_algos_metrics.append({
            "name":          algo.get("name", ""),
            "problem_title": ch_prob.get("title", ""),
            "problem_key":   ch_prob.get("problem_key", prob_key),
            "exec_metrics":  metrics,
            "exec_time":     ch.get("exec_time"),
        })

    # Figures de comparaison (performance + timing) — locale ou Minio
    comparison_figure = comparison_figure or {}
    compare_fig_dest = _resolve_figure_ref(comparison_figure.get("performance", ""), figures_dir)
    compare_fig_dest_timing = _resolve_figure_ref(comparison_figure.get("timing", ""), figures_dir)
    if compare_fig_dest:
        log.info("[BUILDER] Figure comparative (performance) : %s", compare_fig_dest)
    if compare_fig_dest_timing:
        log.info("[BUILDER] Figure comparative (timing) : %s", compare_fig_dest_timing)

    # Fallback local historique si rien n'a été résolu
    if not compare_fig_dest and prob_key:
        type_id  = data_type.get("id", "")
        fallback = Path("data/figures") / type_id / f"compare_{prob_key}.png"
        if fallback.exists():
            dst = figures_dir / fallback.name
            shutil.copy2(fallback, dst)
            compare_fig_dest = f"figures/{fallback.name}"
            log.info("[BUILDER] Figure comparative (fallback) copiée : %s", compare_fig_dest)

    # Chapitre de synthèse comparative
    synthese_content = build_comparative_chapter(
        data_type, all_algos_metrics, compare_fig_dest, {},
        comparison_figure_timing=compare_fig_dest_timing,
    )
    synthese_path = chapters_dir / "chapter_synthese.tex"
    synthese_path.write_text(synthese_content, encoding="utf-8")
    chapter_files.append("chapters/chapter_synthese.tex")
    log.info("[BUILDER] chapter_synthese.tex écrit.")

    # Annexes
    annexes_content = build_annexes(data_type, all_algos_metrics)
    (report_dir / "annexes.tex").write_text(annexes_content, encoding="utf-8")
    log.info("[BUILDER] annexes.tex écrit.")

    # Main
    main_content = build_main_tex(
        data_type=data_type,
        chapter_files=chapter_files,
        has_annexes=True,
        problem=problem,
    )
    (report_dir / "main.tex").write_text(main_content, encoding="utf-8")
    log.info("[BUILDER] main.tex écrit.")

    created = [str(report_dir / "main.tex")] + [str(chapters_dir / cf.split("/")[1]) for cf in chapter_files]
    return created
