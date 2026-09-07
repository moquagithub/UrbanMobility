"""
Test de connexion rapide pour tous les providers LLM configurés.
Utilisé au démarrage du pipeline pour diagnostiquer les clés cassées.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Dict, List

log = logging.getLogger("shared.llm.health_check")

_PROBE_MSG = [{"role": "user", "content": "Reply with: OK"}]


def check_provider(name: str, api_key: str, base_url: str, model: str) -> Dict:
    """Teste un seul provider. Retourne un dict avec le résultat."""
    from openai import OpenAI

    if not api_key.strip():
        return {"name": name, "ok": False, "reason": "clé manquante dans .env", "ms": 0}

    try:
        client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
        t0 = time.time()
        resp = client.chat.completions.create(
            model=model,
            messages=_PROBE_MSG,
            max_tokens=10,
            temperature=0.0,
        )
        ms = int((time.time() - t0) * 1000)
        content = (resp.choices[0].message.content or "").strip()
        return {"name": name, "ok": True, "reason": content, "ms": ms, "model": model}

    except Exception as exc:
        err = str(exc)
        # Catégorisation de l'erreur
        el = err.lower()
        if "resource_exhausted" in el or "quota" in el or "429" in err:
            reason = "quota épuisé (renouveler la clé)"
        elif "invalid" in el and ("key" in el or "api" in el):
            reason = "clé API invalide"
        elif "401" in err:
            reason = "clé API invalide (401)"
        elif "404" in err and "model" in el:
            reason = f"modèle introuvable : {model}"
        elif "connection" in el or "network" in el:
            reason = "erreur réseau"
        else:
            reason = err[:100]
        return {"name": name, "ok": False, "reason": reason, "ms": 0, "model": model}


def run_health_check(print_report: bool = True) -> List[Dict]:
    """
    Teste tous les providers de la chaîne de fallback, ET chacun de leurs modèles.

    La liste est dérivée du routeur (`shared.llm.router`) au lieu d'être recopiée
    ici : la copie locale avait divergé silencieusement de la configuration réelle
    (4 providers testés sur 10, modèles par défaut périmés), si bien que ce
    diagnostic annonçait « tout va bien » alors que les providers réellement
    appelés par le pipeline étaient cassés.
    """
    from dotenv import load_dotenv
    load_dotenv(override=True)

    from shared.llm.router import _models_for, _resolve_order

    results = []
    for p in _resolve_order():
        key  = os.getenv(p["env_key"], "").strip()
        base = os.getenv(p["base_url_env"], p["default_base"]).strip()
        if not key:
            results.append({"name": p["name"], "ok": False, "model": "",
                            "reason": "clé manquante dans .env", "ms": 0})
            continue
        # Chaque modèle de la liste est testé séparément : un seul slug retiré
        # suffit à rendre le provider muet s'il est le seul configuré.
        for model in _models_for(p):
            results.append(check_provider(p["name"], key, base, model))

    if print_report:
        _print_report(results)

    return results


def _print_report(results: List[Dict]) -> None:
    ok_names = {r["name"] for r in results if r["ok"]}
    all_names = {r["name"] for r in results}
    print(f"\n{'─'*78}")
    print(f"  Test connexion LLM — {len(ok_names)}/{len(all_names)} provider(s) opérationnel(s), "
          f"{sum(1 for r in results if r['ok'])}/{len(results)} modèle(s)")
    print(f"{'─'*78}")
    for r in results:
        model = (r.get("model") or "")[:38]
        if r["ok"]:
            print(f"  ✓  {r['name']:<15} {model:<40} {r['ms']:>6}ms")
        else:
            print(f"  ✗  {r['name']:<15} {model:<40} {r['reason'][:60]}")
    print(f"{'─'*78}\n")


def get_working_providers(results: List[Dict] | None = None) -> List[str]:
    """Retourne les noms des providers qui fonctionnent (sans doublon de modèle)."""
    if results is None:
        results = run_health_check(print_report=False)
    seen, names = set(), []
    for r in results:
        if r["ok"] and r["name"] not in seen:
            seen.add(r["name"])
            names.append(r["name"])
    return names


if __name__ == "__main__":
    # Sans ce point d'entrée, `python -m shared.llm.health_check` ne produisait
    # aucune sortie — un diagnostic silencieux qu'on croit à tort « réussi ».
    import sys
    results = run_health_check(print_report=True)
    sys.exit(0 if any(r["ok"] for r in results) else 1)
