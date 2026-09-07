"""
Fonctions utilitaires LLM partagées :
  - call_llm_json  : appelle le router et retourne un dict Python (avec retry)
  - repair_json    : répare un JSON malformé retourné par le LLM
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from shared.llm.router import chat_with_meta

log = logging.getLogger("shared.llm.client")

MAX_RETRIES = 3


def repair_json(raw: str) -> str:
    """
    Tente de réparer un JSON malformé retourné par un LLM.
    Extrait le premier bloc JSON valide trouvé dans la réponse.
    """
    # Supprime les caractères de contrôle invalides en JSON (hors \t \n \r)
    raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", raw)

    # Cherche un bloc ```json ... ```
    match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    if match:
        raw = match.group(1)

    # Cherche un bloc ``` ... ```
    match = re.search(r"```\s*(.*?)\s*```", raw, re.DOTALL)
    if match:
        candidate = match.group(1).strip()
        if candidate.startswith("{") or candidate.startswith("["):
            raw = candidate

    # Cherche directement le premier { ou [
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        idx = raw.find(start_char)
        if idx != -1:
            # Trouve la dernière accolade/crochet fermant correspondant
            last = raw.rfind(end_char)
            if last > idx:
                raw = raw[idx : last + 1]
                break

    # Supprime les virgules traînantes avant } ou ]
    raw = re.sub(r",\s*([}\]])", r"\1", raw)

    # Remplace les guillemets non standard (Unicode curly quotes)
    raw = raw.replace("\u201c", '"').replace("\u201d", '"')
    raw = raw.replace("\u2018", "'").replace("\u2019", "'")

    # Tente de fermer une chaîne non terminée (réponse tronquée par max_tokens)
    try:
        json.loads(raw)
    except json.JSONDecodeError as _e:
        if "Unterminated string" in str(_e):
            # Tronque avant la chaîne non terminée et ferme les structures ouvertes
            raw = raw[: _e.pos]
            # Supprime la dernière virgule traînante
            raw = raw.rstrip().rstrip(",").rstrip()
            # Compte les structures ouvertes et ferme-les
            opens = raw.count("{") - raw.count("}")
            closes = raw.count("[") - raw.count("]")
            raw = raw + "]" * max(closes, 0) + "}" * max(opens, 0)

    return raw.strip()


def call_llm_json(
    messages: List[Dict[str, str]],
    expected_keys: Optional[List[str]] = None,
    max_tokens: int = 2000,
    temperature: float = 0.3,
    max_retries: int = MAX_RETRIES,
    step: str = "",
    data_type_id: Optional[str] = None,
    problem_id: Optional[int] = None,
    log_repo=None,
    profile: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Appelle le LLM via le router et parse la réponse JSON.
    Réessaie max_retries fois en cas d'échec de parsing.

    Retourne :
        {
            "data": dict|list,       # contenu parsé
            "meta": dict,            # provider, model, tokens, duration_ms
            "success": bool,
            "error": str|None,
        }
    """
    last_error = ""
    last_raw = ""

    for attempt in range(1, max_retries + 1):
        meta = chat_with_meta(messages, max_tokens=max_tokens, temperature=temperature, profile=profile)

        if log_repo and step:
            log_repo.log_llm_call(
                step=step,
                provider=meta["provider"],
                model=meta["model"],
                tokens_in=meta["tokens_in"],
                tokens_out=meta["tokens_out"],
                duration_ms=meta["duration_ms"],
                success=meta["success"],
                data_type_id=data_type_id,
                problem_id=problem_id,
                error_message=meta.get("error"),
            )

        if not meta["success"]:
            last_error = meta.get("error", "Provider error")
            log.warning("[CLIENT] Tentative %d/%d — provider error : %s", attempt, max_retries, last_error)
            continue

        raw = meta["content"]
        last_raw = raw

        try:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                # Fallback: allow literal control characters in strings (LLM embeds code in JSON)
                data = json.JSONDecoder(strict=False).decode(raw)
            # Vérifie les clés attendues
            if expected_keys:
                missing = [k for k in expected_keys if k not in data]
                if missing:
                    raise ValueError(f"Clés manquantes : {missing}")

            log.info("[CLIENT] ✓ JSON parsé (tentative %d, %d tokens out)", attempt, meta["tokens_out"])
            return {"data": data, "meta": meta, "success": True, "error": None}

        except (json.JSONDecodeError, ValueError) as exc:
            last_error = str(exc)
            log.warning("[CLIENT] Tentative %d/%d — JSON invalide (%s), tentative de réparation…",
                        attempt, max_retries, last_error)

            if log_repo and step:
                log_repo.log_validation_error(
                    step=step,
                    error_type="json_parse" if isinstance(exc, json.JSONDecodeError) else "missing_key",
                    error_detail=last_error,
                    raw_response=raw,
                    attempt=attempt,
                    data_type_id=data_type_id,
                    problem_id=problem_id,
                )

            # Tente la réparation
            repaired = repair_json(raw)
            try:
                data = json.loads(repaired)
                if expected_keys:
                    missing = [k for k in expected_keys if k not in data]
                    if missing:
                        raise ValueError(f"Clés manquantes après réparation : {missing}")
                log.info("[CLIENT] ✓ JSON réparé (tentative %d)", attempt)
                return {"data": data, "meta": meta, "success": True, "error": None}
            except Exception:
                pass  # On continue avec la prochaine tentative

    log.error("[CLIENT] Échec après %d tentatives. Dernière erreur : %s", max_retries, last_error)
    return {
        "data": {},
        "meta": {},
        "success": False,
        "error": last_error,
        "raw": last_raw,
    }
