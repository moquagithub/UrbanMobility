"""
Validateur JSON pour l'enrichissement des types de données (Step S1b).
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

    def __bool__(self):
        return self.valid


_VALID_DOMAINS = {
    "gestion du trafic routier", "transport en commun", "mobilité douce",
    "stationnement", "environnement et qualité de l'air", "multimodalité",
    "logistique urbaine",
}


def validate_enrichment(data: Dict) -> ValidationResult:
    errors = []
    warnings = []

    if not isinstance(data, dict):
        return ValidationResult(False, ["Réponse non-JSON"])

    required_keys = ["domain", "data_format", "sources", "use_cases",
                     "typical_volume", "standardization", "data_challenges"]
    for k in required_keys:
        if k not in data:
            warnings.append(f"Clé manquante : '{k}'")

    domain = str(data.get("domain", "")).strip()
    if not domain:
        warnings.append("domain vide — sera ignoré")

    data_format = str(data.get("data_format", "")).strip()
    if not data_format:
        warnings.append("data_format vide")

    is_real_time = bool(data.get("is_real_time", False))

    typical_volume = str(data.get("typical_volume", "")).strip()

    sources = data.get("sources", [])
    if not isinstance(sources, list):
        sources = []
        warnings.append("sources converti en liste vide")
    sources = [str(s).strip() for s in sources if str(s).strip()]
    if not sources:
        warnings.append("Aucune source fournie")

    use_cases = data.get("use_cases", [])
    if not isinstance(use_cases, list):
        use_cases = []
        warnings.append("use_cases converti en liste vide")
    use_cases = [str(u).strip() for u in use_cases if str(u).strip()]
    if not use_cases:
        warnings.append("Aucun cas d'usage fourni")

    standardization = str(data.get("standardization", "")).strip()
    data_challenges = str(data.get("data_challenges", "")).strip()

    if errors:
        return ValidationResult(False, errors, warnings)

    cleaned = {
        "domain":          domain,
        "data_format":     data_format,
        "is_real_time":    is_real_time,
        "typical_volume":  typical_volume,
        "sources":         sources,
        "use_cases":       use_cases,
        "standardization": standardization,
        "data_challenges": data_challenges,
    }
    return ValidationResult(True, [], warnings, cleaned=cleaned)
