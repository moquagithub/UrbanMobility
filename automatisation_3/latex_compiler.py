"""
Compilateur LaTeX avec auto-réparation.

Stratégie :
  1. Compiler main.tex avec pdflatex (3 passes pour TOC + références croisées)
  2. Si la 1ère passe échoue : analyser le .log, extraire les erreurs
  3. Envoyer le chapitre fautif + erreur au LLM pour réparation
  4. Remplacer le .tex corrigé, recompiler (×3 tentatives max)
  5. Retourner (success, pdf_path, errors, rounds)
"""
from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger("auto3.compiler")

MAX_REPAIR_ROUNDS = 3
PDFLATEX_TIMEOUT  = 120  # secondes par passe


def compile_pdf(
    tex_dir: Path,
    max_repair_rounds: int = MAX_REPAIR_ROUNDS,
    use_llm_repair: bool = True,
) -> Tuple[bool, Optional[Path], str, int]:
    """
    Compile main.tex → main.pdf dans tex_dir.

    Args:
        tex_dir           : répertoire contenant main.tex
        max_repair_rounds : nombre max de tentatives de réparation LLM
        use_llm_repair    : activer la réparation automatique par LLM

    Retourne (success, pdf_path, error_log, n_rounds)
    """
    main_tex = tex_dir / "main.tex"
    main_pdf = tex_dir / "main.pdf"

    if not main_tex.exists():
        return False, None, f"main.tex introuvable dans {tex_dir}", 0

    # Vérifier que pdflatex est disponible
    if not _pdflatex_available():
        log.error("[COMPILER] pdflatex non trouvé. Installer: sudo apt-get install texlive-latex-extra")
        return False, None, "pdflatex non installé", 0

    # Nettoyer les auxiliaires d'un run précédent (évite \@writefile corrompu)
    _clean_aux_files(tex_dir)

    rounds = 0
    last_errors = ""

    for attempt in range(max_repair_rounds + 1):
        rounds += 1
        log.info("[COMPILER] Compilation passe #%d (tentative %d/%d)...",
                 rounds, attempt + 1, max_repair_rounds + 1)

        success, log_content = _run_pdflatex(tex_dir, main_tex)

        if success:
            # 2ème et 3ème passes pour TOC/références
            log.info("[COMPILER] Passe 1 OK — 2 passes supplémentaires pour TOC...")
            _run_pdflatex(tex_dir, main_tex)
            _run_pdflatex(tex_dir, main_tex)

            if main_pdf.exists():
                n_pages = _count_pages(main_pdf)
                log.info("[COMPILER] PDF généré : %s (%d pages)", main_pdf, n_pages)
                return True, main_pdf, "", rounds
            else:
                last_errors = "pdflatex a réussi mais main.pdf absent"
        else:
            errors = _parse_errors(log_content)
            last_errors = "\n".join(errors)
            log.warning("[COMPILER] Erreurs LaTeX :\n%s", last_errors[:500])

            if not use_llm_repair or attempt >= max_repair_rounds:
                break

            # Tentative déterministe d'abord : une "Undefined control sequence" sur
            # une commande qui ressemble à un opérateur mathématique (ex: \argmin,
            # \diag) est bien plus fiable à corriger en déclarant l'opérateur dans
            # le préambule qu'en laissant le LLM réécrire tout le chapitre — ce
            # dernier est régulièrement rejeté par la validation anti-troncature.
            if _try_fix_undefined_command(tex_dir, log_content):
                log.info("[COMPILER] Opérateur manquant déclaré dans le préambule, recompilation...")
                continue

            # Repli : réparation LLM du chapitre fautif
            repaired = _repair_with_llm(tex_dir, errors, log_content)
            if not repaired:
                log.warning("[COMPILER] Réparation LLM échouée ou aucune erreur identifiable.")
                break

            log.info("[COMPILER] Réparation LLM appliquée, recompilation...")

    return False, None, last_errors, rounds


def _run_pdflatex(tex_dir: Path, main_tex: Path) -> Tuple[bool, str]:
    """Exécute une passe pdflatex. Retourne (success, log_content)."""
    cmd = [
        "pdflatex",
        "-interaction=nonstopmode",
        "-halt-on-error",
        str(main_tex.name),
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(tex_dir),
            capture_output=True,
            text=True,
            timeout=PDFLATEX_TIMEOUT,
            encoding="utf-8",
            errors="replace",
        )
        log_path = tex_dir / "main.log"
        log_content = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else result.stdout
        success = result.returncode == 0
        return success, log_content
    except subprocess.TimeoutExpired:
        return False, f"Timeout après {PDFLATEX_TIMEOUT}s"
    except Exception as exc:
        return False, str(exc)


def _parse_errors(log_content: str) -> List[str]:
    """Extrait les lignes d'erreur du fichier .log pdflatex."""
    errors = []
    lines  = log_content.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("!"):
            # Prendre la ligne d'erreur + les 3 lignes suivantes de contexte
            context = lines[i: i + 4]
            errors.append("\n".join(context))
    return errors


# Traduction connue pour les opérateurs statistiques/ML les plus fréquents dans
# les formules générées par LLM ; toute autre commande inconnue est déclarée
# telle quelle (ex: \foo -> \DeclareMathOperator{\foo}{foo}), ce qui couvre
# aussi les cas non listés ici sans avoir à les anticiper individuellement.
_KNOWN_MATH_OPERATORS = {
    "argmin": "arg\\,min", "argmax": "arg\\,max", "diag": "diag", "rank": "rank",
    "supp": "supp", "sign": "sign", "sgn": "sgn", "var": "Var", "cov": "Cov",
    "corr": "Corr", "softmax": "softmax", "std": "std", "median": "median",
    "argsort": "argsort", "vec": "vec",
}


def _extract_undefined_command(log_content: str) -> Optional[str]:
    """Extrait le nom de la commande LaTeX en cause d'une erreur 'Undefined control
    sequence' du log pdflatex (ex: 'l.44 \\hat{b}_i = \\argmin' → 'argmin')."""
    lines = log_content.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "! Undefined control sequence.":
            for j in range(i + 1, min(i + 4, len(lines))):
                m = re.match(r"l\.\d+\s+(.*)", lines[j])
                if m:
                    cmds = re.findall(r"\\([a-zA-Z]{2,})", m.group(1))
                    if cmds:
                        return cmds[-1]
    return None


def _try_fix_undefined_command(tex_dir: Path, log_content: str) -> bool:
    """
    Répare une commande LaTeX non définie en la déclarant comme opérateur
    mathématique dans le préambule de main.tex, si elle n'est pas déjà déclarée.
    Retourne True si une correction a été appliquée (à valider par recompilation).
    """
    cmd = _extract_undefined_command(log_content)
    if not cmd:
        return False

    main_tex = tex_dir / "main.tex"
    if not main_tex.exists():
        return False
    content = main_tex.read_text(encoding="utf-8")

    if re.search(rf"\\(?:DeclareMathOperator\*?|newcommand)\{{\\{cmd}\}}", content):
        return False  # déjà déclaré — la commande manquante est ailleurs

    operator_text = _KNOWN_MATH_OPERATORS.get(cmd, cmd)
    declaration = f"\\DeclareMathOperator{{\\{cmd}}}{{{operator_text}}}\n"

    marker = "\\usepackage{amsmath,amssymb,amsfonts}"
    if marker in content:
        content = content.replace(marker, marker + "\n" + declaration, 1)
    elif "\\begin{document}" in content:
        content = content.replace("\\begin{document}", declaration + "\\begin{document}", 1)
    else:
        return False

    main_tex.write_text(content, encoding="utf-8")
    log.info("[REPAIR] Commande '\\%s' déclarée comme opérateur mathématique dans main.tex", cmd)
    return True


def _repair_with_llm(
    tex_dir: Path,
    errors: List[str],
    full_log: str,
) -> bool:
    """
    Tente de réparer les fichiers .tex via le LLM.

    Stratégie :
    1. Identifier le fichier fautif depuis le log (l.XX in chapter_XX.tex)
    2. Envoyer le contenu + erreur au LLM — réponse LaTeX brute (pas de JSON)
    3. Remplacer le fichier .tex par la version corrigée
    """
    return False  # DESACTIVE: reparation LLM corrompait les .tex
    from shared.llm.router import chat_with_meta

    # Identifier quel fichier .tex est en cause
    faulty_file, line_num = _identify_faulty_file(full_log, tex_dir)
    if not faulty_file:
        log.warning("[REPAIR] Impossible d'identifier le fichier en erreur")
        return False

    try:
        tex_content = faulty_file.read_text(encoding="utf-8")
    except Exception as exc:
        log.warning("[REPAIR] Lecture fichier %s : %s", faulty_file, exc)
        return False

    errors_str = "\n".join(errors[:5])

    # Le LLM retourne le LaTeX corrigé directement — pas de JSON
    # (un fichier LaTeX contient trop de \, {, } pour être encodé fiablement en JSON)
    system_prompt = (
        "Tu es un expert LaTeX. Corrige les erreurs de compilation dans le fichier donné. "
        "Retourne UNIQUEMENT le code LaTeX corrigé, sans aucun JSON, sans balise markdown, "
        "sans explication. La structure du fichier doit rester identique, seules les erreurs sont corrigées."
    )
    user_prompt = f"""Fichier : {faulty_file.name}

Erreurs pdflatex (ligne {line_num}) :
{errors_str}

Contenu du fichier à corriger :
{tex_content[:6000]}

Retourne UNIQUEMENT le LaTeX corrigé. Aucun texte avant ou après."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt},
    ]

    meta = chat_with_meta(messages, max_tokens=4000, temperature=0.1)

    if not meta.get("success") or not meta.get("content"):
        log.warning("[REPAIR] LLM n'a pas retourné de correction : %s", meta.get("error"))
        return False

    fixed = meta["content"].strip()

    # Nettoyer les balises markdown éventuelles
    if fixed.startswith("```"):
        fixed = re.sub(r"^```(?:latex|tex)?\n?", "", fixed)
        fixed = re.sub(r"\n?```$", "", fixed).strip()

    if not fixed or len(fixed) < 50:
        log.warning("[REPAIR] Correction LLM vide ou trop courte")
        return False

    # Strip any full-document preamble/footer the LLM may have added (chapters must not have \documentclass)
    if faulty_file.name.startswith("chapter_"):
        if "\\documentclass" in fixed:
            chapter_start = fixed.find("\\chapter{")
            if chapter_start == -1:
                chapter_start = fixed.find("\\chapter[")
            if chapter_start != -1:
                fixed = fixed[chapter_start:]
                log.info("[REPAIR] Preamble strippée du fichier chapitre %s", faulty_file.name)
            else:
                log.warning("[REPAIR] LLM a écrit un document complet sans \\chapter{} — réparation annulée")
                return False
        # Also strip \end{document} if the LLM appended it
        if "\\end{document}" in fixed:
            fixed = fixed[:fixed.rfind("\\end{document}")].rstrip()
            log.info("[REPAIR] \\end{document} supprimé du fichier chapitre %s", faulty_file.name)

    # Reject if LLM truncated the file (less than 60% of original length)
    if len(fixed) < len(tex_content) * 0.60:
        log.warning("[REPAIR] Correction LLM trop courte (%d < 60%% de %d) — réparation annulée",
                    len(fixed), len(tex_content))
        return False

    # Backup + remplacement
    backup = faulty_file.with_suffix(".tex.bak")
    faulty_file.rename(backup)
    faulty_file.write_text(fixed, encoding="utf-8")
    log.info("[REPAIR] Fichier corrigé : %s (backup: %s)", faulty_file.name, backup.name)
    return True


def _identify_faulty_file(log_content: str, tex_dir: Path) -> Tuple[Optional[Path], int]:
    """
    Tente de trouver le fichier .tex en erreur dans le log pdflatex.
    Cherche des patterns comme : ./chapters/chapter_01_zscore.tex
    """
    # Pattern: (./chapters/chapter_XX.tex ou ./annexes.tex ou ./main.tex)
    pattern = r"\((\.\/(?:chapters\/)?[\w]+\.tex)"
    matches = re.findall(pattern, log_content)

    # Chercher la ligne d'erreur et le numéro de ligne
    line_num = 0
    m = re.search(r"l\.(\d+)", log_content)
    if m:
        line_num = int(m.group(1))

    # Le dernier fichier mentionné avant l'erreur est généralement le fautif
    error_pos = log_content.find("\n!")
    if error_pos > 0 and matches:
        # Trouver le dernier match avant l'erreur
        pre_error = log_content[:error_pos]
        pre_matches = re.findall(pattern, pre_error)
        if pre_matches:
            candidate = tex_dir / pre_matches[-1].lstrip("./")
            if candidate.exists():
                return candidate, line_num

    # Fallback : main.tex
    main_tex = tex_dir / "main.tex"
    if main_tex.exists():
        return main_tex, line_num

    return None, 0


def _clean_aux_files(tex_dir: Path) -> None:
    """Supprime les fichiers auxiliaires LaTeX (.aux, .toc, .lof, .lot, .out, .log)."""
    aux_extensions = {".aux", ".toc", ".lof", ".lot", ".out", ".log"}
    removed = []
    for p in tex_dir.iterdir():
        if p.suffix in aux_extensions and p.stem == "main":
            p.unlink(missing_ok=True)
            removed.append(p.name)
    if removed:
        log.debug("[COMPILER] Auxiliaires nettoyés : %s", removed)


def _pdflatex_available() -> bool:
    """Vérifie si pdflatex est installé."""
    try:
        result = subprocess.run(
            ["pdflatex", "--version"],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _count_pages(pdf_path: Path) -> int:
    """Compte les pages d'un PDF via pdfinfo si disponible, sinon retourne 0."""
    try:
        result = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if "Pages:" in line:
                return int(line.split(":")[1].strip())
    except Exception:
        pass
    return 0
