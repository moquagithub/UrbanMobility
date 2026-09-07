"""
Routeur LLM multi-fournisseur avec fallback automatique.

Priorité : pilotée par la variable d'env LLM_ROUTER_ORDER (.env), ex. :
    DeepSeek → GoogleAIStudio → Groq → NVIDIA → Cerebras → Mistral →
    HuggingFace → Together → OpenRouter → Gemini
Un provider absent de LLM_ROUTER_ORDER n'est jamais appelé même si sa clé
est configurée (permet de garder une clé cassée/désactivée dans .env sans
la supprimer). Si LLM_ROUTER_ORDER est vide, _DEFAULT_ORDER sert de repli.

- Blacklist session si quota journalier épuisé
- Passage immédiat au suivant sur 429 temporaire
- Lève AllProvidersExhausted si TOUS les quotas journaliers sont épuisés
- Lève RuntimeError si tous échouent pour d'autres raisons
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Set

from dotenv import load_dotenv

load_dotenv(override=True)

log = logging.getLogger("shared.llm.router")

# Timeout explicite par requête — sans ça, le SDK OpenAI attend jusqu'à 10 min par
# défaut ; observé en pratique : le watcher --watch peut rester bloqué des heures
# sur un appel qui ne se termine jamais (connexion TCP qui traîne sans réponse
# complète), ce qui gèle tout le pipeline de réparation autonome.
_LLM_REQUEST_TIMEOUT_SEC = float(os.getenv("LLM_REQUEST_TIMEOUT_SEC", "45"))

_PROVIDERS = [
    {
        "name":          "Gemini",
        "env_key":       "GEMINI_API_KEY",
        "base_url_env":  "GEMINI_BASE_URL",
        "model_env":     "GEMINI_MODEL",
        "default_base":  "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.0-flash",
    },
    {
        "name":          "Groq",
        "env_key":       "GROQ_API_KEY",
        "base_url_env":  "GROQ_BASE_URL",
        "model_env":     "GROQ_MODEL",
        "default_base":  "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
    },
    {
        "name":          "Cerebras",
        "env_key":       "CEREBRAS_API_KEY",
        "base_url_env":  "CEREBRAS_BASE_URL",
        "model_env":     "CEREBRAS_MODEL",
        "default_base":  "https://api.cerebras.ai/v1",
        "default_model": "llama3.3-70b",
    },
    {
        "name":          "Mistral",
        "env_key":       "MISTRAL_API_KEY",
        "base_url_env":  "MISTRAL_BASE_URL",
        "model_env":     "MISTRAL_MODEL",
        "default_base":  "https://api.mistral.ai/v1",
        "default_model": "mistral-small-latest",
    },
    {
        "name":          "OpenRouter",
        "env_key":       "OPENROUTER_API_KEY",
        "base_url_env":  "OPENROUTER_BASE_URL",
        "model_env":     "OPENROUTER_MODEL",
        "default_base":  "https://openrouter.ai/api/v1",
        # OpenRouter retire régulièrement des slugs « :free ». L'ancien
        # meta-llama/llama-3.3-70b-instruct:free renvoyait 404 « unavailable for
        # free », ce qui rendait le provider muet. On configure une LISTE de repli
        # (voir _models_for) : si un slug meurt, le suivant prend le relais.
        #
        # Ordre volontaire : nemotron-3-ultra (550B) est un modèle à raisonnement —
        # il dépense son budget de tokens en chaîne de pensée avant de produire le
        # code. Mesuré en production le 2026-07-28 : 100 % de ses réponses tronquées
        # à 4000 tokens et 44-73 s par appel, contre 4-15 s pour les autres. Le
        # 120B non-raisonnant passe donc en premier ; l'ultra reste en dernier
        # recours, où l'élargissement automatique du budget le rend exploitable.
        "default_model": (
            "nvidia/nemotron-3-super-120b-a12b:free,"
            "openai/gpt-oss-20b:free,"
            "nvidia/nemotron-3-ultra-550b-a55b:free"
        ),
    },
    {
        "name":          "DeepSeek",
        "env_key":       "DEEPSEEK_API_KEY",
        "base_url_env":  "DEEPSEEK_BASE_URL",
        "model_env":     "DEEPSEEK_MODEL",
        "default_base":  "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    {
        "name":          "GoogleAIStudio",
        "env_key":       "GOOGLE_AI_STUDIO_API_KEY",
        "base_url_env":  "GOOGLE_AI_STUDIO_BASE_URL",
        "model_env":     "GOOGLE_AI_STUDIO_MODEL",
        "default_base":  "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-1.5-flash",
    },
    {
        "name":          "NVIDIA",
        "env_key":       "NVIDIA_NIM_API_KEY",
        "base_url_env":  "NVIDIA_NIM_BASE_URL",
        "model_env":     "NVIDIA_NIM_MODEL",
        "default_base":  "https://integrate.api.nvidia.com/v1",
        "default_model": "nvidia/nemotron-3-super-8b-instruct",
    },
    {
        "name":          "HuggingFace",
        "env_key":       "HUGGINGFACE_API_KEY",
        "base_url_env":  "HUGGINGFACE_BASE_URL",
        "model_env":     "HUGGINGFACE_MODEL",
        # api-inference.huggingface.co est retiré : chaque appel échouait en
        # « Connection error », donc le provider était muet. Le routeur d'inférence
        # HF le remplace et expose des modèles nettement plus capables.
        "default_base":  "https://router.huggingface.co/v1",
        "default_model": "Qwen/Qwen2.5-Coder-32B-Instruct,meta-llama/Llama-3.3-70B-Instruct",
    },
    {
        "name":          "Together",
        "env_key":       "TOGETHER_AI_API_KEY",
        "base_url_env":  "TOGETHER_AI_BASE_URL",
        "model_env":     "TOGETHER_AI_MODEL",
        "default_base":  "https://api.together.xyz/v1",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    },
]

# Catalogue indexé par nom, pour résoudre LLM_ROUTER_ORDER.
_PROVIDERS_BY_NAME: Dict[str, Dict] = {p["name"]: p for p in _PROVIDERS}

# Repli si LLM_ROUTER_ORDER est absent/vide de .env.
_DEFAULT_ORDER = [
    "DeepSeek", "GoogleAIStudio", "Groq", "NVIDIA", "Cerebras",
    "Mistral", "HuggingFace", "Together", "OpenRouter", "Gemini",
]

_QUOTA_EXHAUSTED_KEYWORDS = (
    "resource_exhausted", "resource exhausted", "quota exceeded",
    "exceeded your current quota", "free_tier", "free tier",
    "free-models-per-day", "daily", "insufficient_quota", "billing",
    # Messages observés en production — limites journalières de tokens
    "token per day limit exceeded",   # Groq : "Token per day limit exceeded for model..."
    "too many tokens processed",      # Cerebras : "Too many tokens processed..."
    "tokens per day",                 # Groq TPD : "tokens per day (TPD): Limit ..."
    "per day limit",                  # variante générique
    # OpenRouter — crédits épuisés (nécessite recharge, pas un reset minuit)
    "insufficient credits",           # OpenRouter 402 : pas de crédits disponibles
    "no credits",                     # variante OpenRouter
    "insufficient balance",           # DeepSeek 402 : crédits gratuits épuisés
)

# Erreurs imputables au MODÈLE, pas au provider : le slug a été retiré/renommé, ou
# n'est plus servi gratuitement. Il ne faut PAS blacklister le provider (ses autres
# modèles marchent) — seulement écarter ce modèle pour la session et passer au
# suivant de la liste. Sans cette distinction, un slug mort rendait tout le
# provider silencieusement inutilisable (cas observé : OpenRouter et HuggingFace
# tous deux muets alors que leurs quotas étaient intacts).
_MODEL_UNAVAILABLE_KEYWORDS = (
    "unavailable for free", "no endpoints found", "model_not_found",
    "does not exist", "is not a valid model", "model not found",
    "no such model", "unknown model",
)


class AllProvidersExhausted(RuntimeError):
    """
    Levée quand TOUS les providers LLM configurés ont atteint leur quota journalier.
    L'automatisation doit se mettre en pause jusqu'au reset (minuit).
    """
    pass


# Profils de routing par type de tâche.
# Cerebras était exclu de "code"/"json" car son modèle d'alors (llama3.3-70b)
# retournait du contenu vide ; avec gemma-4-31b il produit du code valide, donc il
# est réintégré (vérifié 2026-07-27 sur un prompt de réparation réel).
# Gemini reste hors de tous les profils : quota gratuit à 0, il ne serait tenté
# que via un appel chat(profile=None).
_PROFILES: Dict[str, List[str]] = {
    "code": ["DeepSeek", "GoogleAIStudio", "Groq", "NVIDIA", "Cerebras", "Mistral", "HuggingFace", "Together", "OpenRouter"],
    "json": ["DeepSeek", "GoogleAIStudio", "Groq", "NVIDIA", "Cerebras", "Mistral", "HuggingFace", "Together", "OpenRouter"],
    "text": ["DeepSeek", "GoogleAIStudio", "Groq", "Cerebras", "NVIDIA", "Mistral", "HuggingFace", "Together", "OpenRouter"],
}

_blacklisted: Set[str] = set()
_dead_models: Set[str] = set()   # "Provider:model" dont le slug n'est plus servi

# "Provider:model" -> nombre de réponses tronquées par max_tokens dans la session.
# Au-delà de _TRUNCATION_DEMOTION_THRESHOLD, le modèle passe en fin de liste de son
# provider : un modèle à raisonnement dépense son budget en chaîne de pensée avant
# de produire du code, revient tronqué, et fait recommencer l'appel avec un budget
# doublé — sans jamais produire de correctif exploitable.
#
# Cet ordre est mesuré, pas supposé. Le classement en dur de la session précédente
# reposait sur l'hypothèse que nemotron-3-super-120b était non-raisonnant : mesuré
# le 2026-07-28, il tronque 3 fois sur 3 en 45 à 203 s par appel, soit 307 s brûlées
# sur un cycle de 7 minutes. Une rétrogradation fondée sur l'observation se corrige
# toute seule quand un fournisseur change ses modèles.
_truncation_counts: Dict[str, int] = {}
_TRUNCATION_DEMOTION_THRESHOLD = 2
_request_counts: Dict[str, int] = {}
_error_counts:   Dict[str, int] = {}
_rr_index: int = 0  # round-robin cursor across active providers
_order_logged: bool = False  # évite de logguer la chaîne résolue à chaque appel


def reset_session() -> None:
    """Réinitialise la blacklist de session (après reset quota minuit)."""
    global _rr_index, _order_logged
    _blacklisted.clear()
    _dead_models.clear()   # un slug retiré peut revenir / être remplacé côté provider
    _truncation_counts.clear()
    _request_counts.clear()
    _error_counts.clear()
    _rr_index = 0
    _order_logged = False  # relogue la chaîne de fallback résolue au prochain appel
    log.info("[ROUTER] Blacklist réinitialisée — tous les providers actifs sont réintégrés")


def _models_for(p: Dict) -> List[str]:
    """
    Modèles à essayer pour ce provider, dans l'ordre.

    La variable d'env <PROVIDER>_MODEL accepte une LISTE séparée par des virgules
    (ex. OPENROUTER_MODEL="a:free,b:free"). Les slugs déjà repérés comme retirés
    pendant la session sont écartés — voir _MODEL_UNAVAILABLE_KEYWORDS.
    """
    raw = os.getenv(p["model_env"], "").strip() or p["default_model"]
    models = [m.strip() for m in raw.split(",") if m.strip()]
    alive = [m for m in models if f"{p['name']}:{m}" not in _dead_models]
    # Si tous les slugs configurés sont morts, on retente quand même le premier :
    # mieux vaut une erreur explicite qu'un provider silencieusement sauté.
    alive = alive or models[:1]

    # Les modèles qui tronquent à répétition passent derrière ceux qui répondent —
    # tri stable, donc l'ordre configuré est conservé à l'intérieur de chaque groupe.
    return sorted(
        alive,
        key=lambda m: _truncation_counts.get(f"{p['name']}:{m}", 0)
        >= _TRUNCATION_DEMOTION_THRESHOLD,
    )


def get_session_stats() -> Dict:
    """Expose le statut de chaque provider pour le dashboard."""
    active_names = {p["name"] for p in _resolve_order()}
    models_by_provider = {p["name"]: _models_for(p) for p in _PROVIDERS}
    return {
        "providers": [
            {
                "name":        p["name"],
                "model":       (models_by_provider[p["name"]] or [""])[0],
                "models":      models_by_provider[p["name"]],
                "configured":  bool(os.getenv(p["env_key"], "").strip()),
                "active":      p["name"] in active_names,  # présent dans LLM_ROUTER_ORDER
                "blacklisted": p["name"] in _blacklisted,
                "requests":    _request_counts.get(p["name"], 0),
                "errors":      _error_counts.get(p["name"], 0),
            }
            for p in _PROVIDERS
        ],
        "all_exhausted":    _all_quota_exhausted(),
        "blacklisted_names": list(_blacklisted),
        "dead_models":       sorted(_dead_models),
    }


def _resolve_order() -> List[Dict]:
    """
    Résout la chaîne de fallback depuis LLM_ROUTER_ORDER (.env).
    Repli sur _DEFAULT_ORDER si la variable est absente/vide.
    Un nom inconnu dans LLM_ROUTER_ORDER est ignoré avec un warning.
    """
    global _order_logged
    raw = os.getenv("LLM_ROUTER_ORDER", "").strip()
    names = [n.strip() for n in raw.split(",") if n.strip()] if raw else _DEFAULT_ORDER

    ordered: List[Dict] = []
    for name in names:
        p = _PROVIDERS_BY_NAME.get(name)
        if p is None:
            log.warning("[ROUTER] LLM_ROUTER_ORDER : provider inconnu ignoré : %s", name)
            continue
        ordered.append(p)

    if not _order_logged:
        log.info("[ROUTER] Chaîne de fallback résolue : %s", " → ".join(p["name"] for p in ordered))
        _order_logged = True

    return ordered


def _all_quota_exhausted() -> bool:
    """Retourne True si tous les providers ACTIFS (dans LLM_ROUTER_ORDER) et configurés sont blacklistés."""
    configured = [p for p in _resolve_order() if os.getenv(p["env_key"], "").strip()]
    return bool(configured) and all(p["name"] in _blacklisted for p in configured)


def _active_providers(profile: Optional[str] = None) -> List[Dict]:
    allowed = _PROFILES.get(profile) if profile else None
    return [
        p for p in _resolve_order()
        if os.getenv(p["env_key"], "").strip()
        and p["name"] not in _blacklisted
        and (allowed is None or p["name"] in allowed)
    ]


def _providers_rr(profile: Optional[str] = None) -> List[Dict]:
    """Active providers rotated so the next round-robin choice comes first."""
    global _rr_index
    active = _active_providers(profile)
    if not active:
        return []
    n = len(active)
    start = _rr_index % n
    return active[start:] + active[:start]


def _advance_rr() -> None:
    global _rr_index
    _rr_index += 1


def _is_quota_exhausted(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _QUOTA_EXHAUSTED_KEYWORDS)


def _is_model_unavailable(text: str) -> bool:
    """Erreur imputable au slug de modèle (retiré/renommé), pas au quota du provider."""
    t = text.lower()
    return any(kw in t for kw in _MODEL_UNAVAILABLE_KEYWORDS)


def _blacklist(name: str, reason: str) -> None:
    _blacklisted.add(name)
    log.warning("[ROUTER] %s blacklisté pour cette session : %s", name, reason)


def _call_one(
    p: Dict,
    model: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
    temperature: float,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Un seul appel (provider, modèle). Retourne un dict meta.
    En cas d'échec : {"success": False, "error": ..., "fatal_provider": bool}
    où fatal_provider indique que le PROVIDER entier doit être blacklisté
    (quota journalier), par opposition à un simple modèle mort.
    """
    from openai import OpenAI

    name     = p["name"]
    api_key  = os.getenv(p["env_key"], "").strip()
    base_url = os.getenv(p["base_url_env"], p["default_base"]).strip()

    try:
        client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0,
                        timeout=_LLM_REQUEST_TIMEOUT_SEC)
        t0 = time.time()
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )
        duration_ms = int((time.time() - t0) * 1000)
        content = resp.choices[0].message.content or ""
        if not content.strip():
            log.warning("[ROUTER] ✗ %s (%s) : réponse vide — modèle suivant", name, model)
            return {"success": False, "error": "Empty response", "fatal_provider": False}

        usage = getattr(resp, "usage", None)
        # finish_reason == "length" : réponse coupée par max_tokens. Sans cette
        # information l'appelant ne voit qu'un code Python invalide et croit à une
        # erreur du modèle (typiquement « unterminated string literal »), alors
        # qu'il suffit d'augmenter le budget de tokens.
        finish_reason = getattr(resp.choices[0], "finish_reason", None)
        log.info("[ROUTER] ✓ %s (%s) — %d ms%s", name, model, duration_ms,
                 " [TRONQUÉ]" if finish_reason == "length" else "")
        if finish_reason == "length":
            key = f"{name}:{model}"
            _truncation_counts[key] = _truncation_counts.get(key, 0) + 1
            if _truncation_counts[key] == _TRUNCATION_DEMOTION_THRESHOLD:
                log.warning(
                    "[ROUTER] %s rétrogradé en fin de liste — %d réponses tronquées "
                    "(modèle à raisonnement : dépense son budget avant de produire le code)",
                    key, _truncation_counts[key],
                )
        _request_counts[name] = _request_counts.get(name, 0) + 1
        return {
            "content":       content,
            "finish_reason": finish_reason,
            "truncated":     finish_reason == "length",
            "provider":    name,
            "model":       model,
            "tokens_in":   usage.prompt_tokens     if usage else 0,
            "tokens_out":  usage.completion_tokens if usage else 0,
            "duration_ms": duration_ms,
            "success":     True,
            "error":       None,
        }

    except Exception as exc:
        err = str(exc)
        el  = err.lower()
        if _is_quota_exhausted(el):
            _blacklist(name, err[:120])
            _error_counts[name] = _error_counts.get(name, 0) + 1
            return {"success": False, "error": err, "fatal_provider": True}
        if _is_model_unavailable(el):
            # Slug retiré : on écarte CE modèle, pas le provider.
            _dead_models.add(f"{name}:{model}")
            log.warning("[ROUTER] ✗ %s : modèle '%s' indisponible — écarté pour la session (%s)",
                        name, model, err[:100])
            return {"success": False, "error": err, "fatal_provider": False}
        log.warning("[ROUTER] ✗ %s (%s) : %s", name, model, err[:120])
        return {"success": False, "error": err, "fatal_provider": False}


def _route(
    messages: List[Dict[str, str]],
    max_tokens: int,
    temperature: float,
    profile: Optional[str],
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Cœur du routage, partagé par chat() et chat_with_meta().

    Parcourt les providers actifs (round-robin) et, pour chacun, ses modèles
    configurés dans l'ordre. Retourne le meta du premier appel réussi, sinon un
    meta d'échec. Lève AllProvidersExhausted si tous les quotas sont épuisés.
    """
    providers = _providers_rr(profile)
    if not providers:
        return {
            "content": "", "truncated": False, "provider": "none", "model": "none",
            "tokens_in": 0, "tokens_out": 0, "duration_ms": 0,
            "success": False,
            "error": (
                "Aucun provider LLM actif. Vérifiez LLM_ROUTER_ORDER et les clés API "
                "dans .env (DEEPSEEK_API_KEY, GOOGLE_AI_STUDIO_API_KEY, GROQ_API_KEY, "
                "NVIDIA_NIM_API_KEY, CEREBRAS_API_KEY, MISTRAL_API_KEY, "
                "HUGGINGFACE_API_KEY, TOGETHER_AI_API_KEY, OPENROUTER_API_KEY, "
                "GEMINI_API_KEY)."
            ),
        }

    last_error = ""
    for p in providers:
        for model in _models_for(p):
            log.debug("[ROUTER] → tentative : %s (%s)", p["name"], model)
            meta = _call_one(p, model, messages, max_tokens, temperature, **kwargs)
            if meta["success"]:
                _advance_rr()
                return meta
            last_error = meta.get("error", "")
            if meta.get("fatal_provider"):
                break   # quota provider épuisé : inutile d'essayer ses autres modèles

    if _all_quota_exhausted():
        names = ", ".join(p["name"] for p in _resolve_order() if os.getenv(p["env_key"], "").strip())
        raise AllProvidersExhausted(
            f"Tous les providers LLM ont épuisé leur quota journalier ({names}). "
            "L'automatisation est en pause — réessayez après minuit."
        )

    return {
        "content": "", "truncated": False, "provider": "none", "model": "none",
        "tokens_in": 0, "tokens_out": 0, "duration_ms": 0,
        "success": False, "error": last_error,
    }


def chat(
    messages: List[Dict[str, str]],
    max_tokens: int = 2000,
    temperature: float = 0.3,
    profile: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """
    Envoie les messages au premier couple (provider, modèle) disponible.
    profile : "code" | "json" | "text" | None (round-robin complet)
    Retourne le contenu texte de la réponse.
    Lève RuntimeError si tous les providers échouent.
    """
    meta = _route(messages, max_tokens, temperature, profile, **kwargs)
    if meta["success"]:
        return meta["content"]
    raise RuntimeError(
        f"Tous les providers LLM ont échoué. Dernière erreur : {meta.get('error')}"
    )


def chat_with_meta(
    messages: List[Dict[str, str]],
    max_tokens: int = 2000,
    temperature: float = 0.3,
    profile: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Comme chat() mais retourne aussi les métadonnées pour le log LLM.
    profile : "code" | "json" | "text" | None (round-robin complet)
    Retourne : {content, provider, model, tokens_in, tokens_out, duration_ms, success}
    """
    return _route(messages, max_tokens, temperature, profile)

def active_provider_names() -> List[str]:
    return [p["name"] for p in _active_providers()]
