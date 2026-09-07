"""
Contrat partagé des skeletons d'algorithmes : nom du point d'entrée, et
contraintes de l'environnement d'exécution injectées dans les prompts LLM.

Ces deux éléments doivent rester définis à UN SEUL endroit. Ils étaient
auparavant dupliqués et incohérents entre les trois composants qui les
utilisent, ce qui produisait des rejets de skeletons parfaitement valides :

  - automatisation_2/notebook_builder.py résolvait le point d'entrée avec un
    repli tolérant (première fonction définie si `run` est absent) ;
  - shared/validation/skeleton_execution.py exigeait `run` en dur et rejetait
    donc du code que le notebook aurait exécuté sans problème ;
  - automatisation_2/prompts/skeleton_adapt_prompt.py demandait carrément au LLM
    de produire `def detect(df)`, garantissant le désaccord à chaque adaptation.
"""
from __future__ import annotations

import re

# Nom du point d'entrée par convention. Les skeletons plus anciens (ou adaptés par
# S2 avant la correction du prompt) peuvent utiliser un autre nom : la résolution
# ci-dessous reste tolérante pour ne pas invalider l'existant.
ENTRY_POINT = "run"

# Budget d'exécution accordé à UN algorithme, en secondes.
#
# C'est le `timeout` passé à ExecutePreprocessor dans automatisation_2/steps/
# s3_execution.py — et nbclient l'applique PAR CELLULE, donc par algorithme, pas
# pour le notebook entier. La validation par exécution se contentait de 30 s : elle
# était dix fois plus stricte que la production et rejetait des skeletons que le
# notebook aurait exécutés sans problème (observé sur alg7, un DTW en O(n·m) sur
# 5000 lignes). Même piège que la résolution du point d'entrée : une validation
# plus sévère que la production détruit du travail valide.
EXECUTION_TIMEOUT_SEC = 300


def resolve_entry_point(skeleton: str) -> str:
    """
    Nom de la fonction d'entrée d'un skeleton.

    Préfère `run` s'il est défini où que ce soit, plutôt que la première fonction
    rencontrée : certains skeletons définissent un helper (ex. une fonction
    décorée) AVANT `run`, et prendre la première l'appelait avec le mauvais
    nombre d'arguments.

    La validation par exécution DOIT utiliser cette même résolution que le
    constructeur de notebooks — une validation plus stricte que la production
    rejette du code qui fonctionne, une validation plus laxiste laisse passer du
    code qui casse.
    """
    if not skeleton:
        return ENTRY_POINT
    if re.search(r"def\s+run\s*\(", skeleton):
        return ENTRY_POINT
    m = re.search(r"def\s+(\w+)\s*\(", skeleton)
    return m.group(1) if m else ENTRY_POINT


# Contrat d'environnement injecté dans TOUS les prompts qui font écrire ou
# réécrire un skeleton par un LLM (réparation qualité, adaptation S2, validation
# proactive S4).
#
# Le venv exécute pandas 3 / numpy 2 / scipy 1.18, alors que les modèles
# disponibles (Llama 3.3, Qwen2.5-Coder, gemma, mistral-small) ont massivement été
# entraînés sur du code pandas 1.x/2.x : ils réintroduisent spontanément des APIs
# supprimées depuis. Relevé le 2026-07-28 sur les rejets de réparation :
# `fillna(method=)`, `df.append`, buffers en lecture seule, et surtout
# l'affectation chaînée qui ne lève AUCUNE erreur mais perd l'écriture.
#
# Chaque entrée a été vérifiée par exécution réelle dans ce venv — ne pas y
# ajouter d'API « probablement supprimée » sans l'avoir testée.
ENVIRONMENT_CONTRACT = """
CONTRAINTE D'ENVIRONNEMENT — le code s'exécute sur pandas 3, numpy 2 et scipy 1.18.
Les APIs suivantes ONT ÉTÉ SUPPRIMÉES et lèvent une erreur. N'en utilise AUCUNE :
- `df.fillna(method="ffill"/"bfill")` → utilise `df.ffill()` / `df.bfill()`
- `df.append(...)` → utilise `pd.concat([df, autre], ignore_index=True)`
- `df.applymap(f)` → utilise `df.map(f)`
- `serie.iteritems()` → utilise `serie.items()`
- `errors="ignore"` dans `pd.to_numeric` / `pd.to_datetime` → utilise `errors="coerce"`
- `np.float`, `np.int`, `np.bool`, `np.object` → utilise `float`, `int`, `bool`, `object`
- `np.NaN`, `np.NAN`, `np.Inf` → utilise `np.nan`, `np.inf`
- `np.in1d` → `np.isin` ; `np.trapz` → `np.trapezoid` ; `np.product` → `np.prod`
- `scipy.signal.cwt` / `ricker` / `morlet` → utilise `scipy.signal.stft` ou
  `scipy.ndimage.gaussian_filter1d(x, sigma, order=2)`
- `scipy.integrate.simps` / `trapz` → `simpson` / `trapezoid`

PIÈGE SILENCIEUX LE PLUS GRAVE (aucune erreur levée, résultat faux) : sous le
Copy-on-Write de pandas 3, l'affectation chaînée est PERDUE sans avertissement.
  df["is_anomaly"][mask] = 1        # ← ne modifie RIEN, la colonne reste à 0
  df[df.x > 3]["is_anomaly"] = 1    # ← ne modifie RIEN non plus
Écris TOUJOURS en une seule indexation :
  df.loc[mask, "is_anomaly"] = 1
  df["is_anomaly"] = mask.astype(int)
Un algorithme qui utilise l'affectation chaînée s'exécute sans erreur mais retourne
0 anomalie, et sera rejeté pour ça.

SECOND PIÈGE pandas 3 : `df["col"].values`, `df["col"].to_numpy()` et `df.values`
renvoient des tableaux EN LECTURE SEULE. Toute écriture en place dessus, ou tout
passage à une routine Cython/scipy qui exige un buffer inscriptible, échoue avec
« buffer source array is read-only ». Si tu dois modifier le tableau ou le passer
à une telle routine, copie-le explicitement : `df["col"].to_numpy(copy=True)`
(`np.ascontiguousarray` ne suffit PAS, il renvoie le même tableau non inscriptible).

SIGNATURE IMPOSÉE : le point d'entrée doit être exactement `def run(df):` — un seul
paramètre. Si l'algorithme a besoin d'une structure annexe (graphe, matrice de
transition, couche de référence, topologie…), construis-la À L'INTÉRIEUR de `run`
à partir de `df` ; ne l'ajoute pas en second paramètre, l'appelant ne le fournira pas.
"""
