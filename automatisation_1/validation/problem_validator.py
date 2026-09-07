"""
Validateur JSON pour les problèmes enrichis générés par le LLM (Step S2).
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


_VALID_FREQUENCIES  = {"rare", "occasionnel", "courant", "très courant", "tres courant"}
_VALID_IMPACTS      = {"faible", "moyen", "élevé", "eleve", "critique"}


def validate_problems(data: Dict) -> ValidationResult:
    errors   = []
    warnings = []

    if not isinstance(data, dict):
        return ValidationResult(False, ["La réponse doit être un objet JSON"])

    if "problems" not in data:
        return ValidationResult(False, ['Clé "problems" manquante'])

    problems = data["problems"]
    if not isinstance(problems, list) or not problems:
        return ValidationResult(False, ['"problems" doit être une liste non vide'])

    if len(problems) != 3:
        warnings.append(f"{len(problems)} problème(s) au lieu de 3")

    cleaned   = []
    seen_keys = set()

    for i, p in enumerate(problems):
        pfx = f"Problème #{i+1}"

        if not isinstance(p, dict):
            errors.append(f"{pfx} : doit être un objet JSON")
            continue

        # ── clé ─────────────────────────────────────────────────────────────
        key = str(p.get("key", "")).strip() or f"p{i+1}"
        if key in seen_keys:
            errors.append(f"{pfx} : clé dupliquée '{key}'")
        seen_keys.add(key)

        # ── champs obligatoires ──────────────────────────────────────────────
        title = str(p.get("title", "")).strip()
        if not title:
            errors.append(f"{pfx} ({key}) : 'title' manquant")
        elif len(title) > 500:
            title = title[:500]

        description = str(p.get("description", "")).strip()
        if not description:
            errors.append(f"{pfx} ({key}) : 'description' manquante")

        # ── causes ──────────────────────────────────────────────────────────
        causes = p.get("causes", [])
        if not isinstance(causes, list):
            causes = [str(causes)]
        causes = [str(c).strip() for c in causes if str(c).strip()]
        if not causes:
            warnings.append(f"{pfx} ({key}) : aucune cause fournie")

        # ── conséquences ─────────────────────────────────────────────────────
        consequences = str(p.get("consequences", "")).strip()

        # ── fréquence ────────────────────────────────────────────────────────
        frequency = str(p.get("frequency", "courant")).strip().lower()
        if frequency not in _VALID_FREQUENCIES:
            frequency = "courant"

        # ── impact ───────────────────────────────────────────────────────────
        impact_level = str(p.get("impact_level", "moyen")).strip().lower()
        if impact_level not in _VALID_IMPACTS:
            warnings.append(f"{pfx} ({key}) : impact_level '{impact_level}' → 'moyen'")
            impact_level = "moyen"
        # normalise "eleve" → "élevé"
        if impact_level == "eleve":
            impact_level = "élevé"

        # ── priorité ─────────────────────────────────────────────────────────
        try:
            priority = int(p.get("priority", 2))
            if priority not in (1, 2, 3):
                priority = 2
        except (TypeError, ValueError):
            priority = 2

        # ── acteurs ──────────────────────────────────────────────────────────
        affected_actors = p.get("affected_actors", [])
        if not isinstance(affected_actors, list):
            affected_actors = []
        affected_actors = [str(a).strip() for a in affected_actors if str(a).strip()]

        # ── indicateurs ──────────────────────────────────────────────────────
        detection_indicators = p.get("detection_indicators", [])
        if not isinstance(detection_indicators, list):
            detection_indicators = []
        detection_indicators = [str(d).strip() for d in detection_indicators if str(d).strip()]

        solutions_overview = str(p.get("solutions_overview", "")).strip()
        data_requirements  = str(p.get("data_requirements", "")).strip()

        if not solutions_overview:
            warnings.append(f"{pfx} ({key}) : solutions_overview manquant")
        if not detection_indicators:
            warnings.append(f"{pfx} ({key}) : detection_indicators manquants")

        cleaned.append({
            "key":                 key,
            "title":               title,
            "description":         description,
            "causes":              causes,
            "consequences":        consequences,
            "frequency":           frequency,
            "impact_level":        impact_level,
            "priority":            priority,
            "affected_actors":     affected_actors,
            "detection_indicators":detection_indicators,
            "solutions_overview":  solutions_overview,
            "data_requirements":   data_requirements,
        })

    if errors:
        return ValidationResult(False, errors, warnings)

    return ValidationResult(True, [], warnings, cleaned=cleaned)
