"""
Validateur JSON pour les algorithmes enrichis générés par le LLM (Step S3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    cleaned: Any = None

    def __bool__(self) -> bool:
        return self.valid


def _clean_list(val, field_name: str, warnings: List[str], prefix: str) -> List:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(item).strip() for item in val if str(item).strip()]
    warnings.append(f"{prefix} : '{field_name}' n'est pas une liste — converti")
    return [str(val).strip()] if str(val).strip() else []


def _clean_json_list(val, field_name: str, warnings: List[str], prefix: str) -> List[Dict]:
    """Valide une liste de dicts (hyperparameters, metrics, references)."""
    if val is None:
        return []
    if not isinstance(val, list):
        warnings.append(f"{prefix} : '{field_name}' n'est pas une liste")
        return []
    result = []
    for item in val:
        if isinstance(item, dict):
            result.append(item)
        else:
            warnings.append(f"{prefix} : item dans '{field_name}' n'est pas un dict — ignoré")
    return result


def validate_algorithms(data: Dict) -> ValidationResult:
    errors   = []
    warnings = []

    if not isinstance(data, dict):
        return ValidationResult(False, ["Réponse non-JSON"])

    if "algorithms" not in data:
        return ValidationResult(False, ['Clé "algorithms" manquante'])

    algorithms = data["algorithms"]
    if not isinstance(algorithms, list) or not algorithms:
        return ValidationResult(False, ['"algorithms" doit être une liste non vide'])

    if len(algorithms) != 3:
        warnings.append(f"{len(algorithms)} algorithme(s) au lieu de 3")

    cleaned    = []
    seen_keys  = set()
    seen_names = set()

    for i, alg in enumerate(algorithms):
        pfx = f"Algorithme #{i+1}"

        if not isinstance(alg, dict):
            errors.append(f"{pfx} : doit être un objet JSON")
            continue

        # ── identité ─────────────────────────────────────────────────────────
        key = str(alg.get("key", "")).strip() or f"alg{i+1}"
        if key in seen_keys:
            errors.append(f"{pfx} : clé dupliquée '{key}'")
        seen_keys.add(key)

        name = str(alg.get("name", "")).strip()
        if not name:
            errors.append(f"{pfx} ({key}) : 'name' manquant")
        elif len(name) > 300:
            name = name[:300]
        if name.lower() in seen_names:
            warnings.append(f"{pfx} ({key}) : nom potentiellement dupliqué")
        seen_names.add(name.lower())

        category = str(alg.get("category", "")).strip()
        if not category:
            warnings.append(f"{pfx} ({key}) : 'category' manquante")

        # ── principe + complexité ─────────────────────────────────────────────
        principle = str(alg.get("principle", "")).strip()
        if not principle:
            warnings.append(f"{pfx} ({key}) : 'principle' manquant")

        complexity_time  = str(alg.get("complexity_time", "")).strip()
        complexity_space = str(alg.get("complexity_space", "")).strip()
        if not complexity_time:
            warnings.append(f"{pfx} ({key}) : complexity_time manquant")

        # ── champs enrichis — non bloquants ──────────────────────────────────
        math_formulation = str(alg.get("math_formulation", "")).strip()
        pseudocode       = str(alg.get("pseudocode", "")).strip()
        input_format     = str(alg.get("input_format", "")).strip()
        output_format    = str(alg.get("output_format", "")).strip()
        use_case_example = str(alg.get("use_case_example", "")).strip()
        python_skeleton  = str(alg.get("python_skeleton", "")).strip()

        if not math_formulation:
            warnings.append(f"{pfx} ({key}) : math_formulation manquant")
        if not python_skeleton:
            warnings.append(f"{pfx} ({key}) : python_skeleton manquant")

        advantages  = _clean_list(alg.get("advantages"),  "advantages",  warnings, f"{pfx}({key})")
        limitations = _clean_list(alg.get("limitations"), "limitations", warnings, f"{pfx}({key})")

        hyperparameters    = _clean_json_list(alg.get("hyperparameters"),    "hyperparameters",    warnings, f"{pfx}({key})")
        required_libraries = _clean_list(alg.get("required_libraries"), "required_libraries", warnings, f"{pfx}({key})")
        evaluation_metrics = _clean_json_list(alg.get("evaluation_metrics"), "evaluation_metrics", warnings, f"{pfx}({key})")
        references         = _clean_json_list(alg.get("references"),         "references",         warnings, f"{pfx}({key})")

        # formulation (ancienne colonne — on garde pour compatibilité)
        formulation = str(alg.get("formulation", math_formulation[:500] if math_formulation else "")).strip()

        cleaned.append({
            "key":               key,
            "name":              name,
            "category":          category,
            "principle":         principle,
            "formulation":       formulation,
            "complexity_time":   complexity_time,
            "complexity_space":  complexity_space,
            "advantages":        advantages,
            "limitations":       limitations,
            # enrichi
            "math_formulation":  math_formulation,
            "pseudocode":        pseudocode,
            "input_format":      input_format,
            "output_format":     output_format,
            "hyperparameters":   hyperparameters,
            "required_libraries":required_libraries,
            "evaluation_metrics":evaluation_metrics,
            "use_case_example":  use_case_example,
            "python_skeleton":   python_skeleton,
            "references":        references,
        })

    if errors:
        return ValidationResult(False, errors, warnings)

    return ValidationResult(True, [], warnings, cleaned=cleaned)
