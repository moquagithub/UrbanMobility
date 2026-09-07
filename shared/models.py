"""
Modèles de données partagés entre toutes les automations.
Simples dataclasses — pas de logique métier ici.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class DataType:
    id: str
    name: str
    description: str = ""
    specifications: str = ""
    units: str = ""
    frequency: str = ""
    processing_status: str = "catalogue_done"


@dataclass
class Problem:
    key: str                         # p1, p2, p3
    title: str
    description: str
    causes: List[str] = field(default_factory=list)
    consequences: str = ""
    frequency: str = "courant"
    db_id: Optional[int] = None
    data_type_id: str = ""


@dataclass
class Algorithm:
    key: str                         # alg1, alg2, alg3
    name: str
    category: str = ""
    principle: str = ""
    formulation: str = ""
    complexity_time: str = ""
    complexity_space: str = ""
    advantages: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    db_id: Optional[int] = None
    problem_id: Optional[int] = None


@dataclass
class StepResult:
    """Résultat d'une étape du pipeline."""
    step: str
    success: bool
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None

    def finish(self, success: bool, message: str = "") -> "StepResult":
        self.success = success
        self.message = message
        self.completed_at = datetime.now()
        return self

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.success = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


@dataclass
class RunResult:
    """Résultat complet d'une exécution de l'automatisation 1."""
    data_type_id: str
    data_type_name: str
    steps: List[StepResult] = field(default_factory=list)
    nb_problems: int = 0
    nb_algorithms: int = 0
    success: bool = False
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None

    @property
    def duration_seconds(self) -> float:
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0
