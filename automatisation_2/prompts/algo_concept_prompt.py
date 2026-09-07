"""Prompt LLM pour générer l'explication mathématique d'un algorithme en LaTeX."""
from __future__ import annotations


def build_prompt(
    algo_name: str,
    category: str,
    description: str,
    prob_title: str,
    type_name: str,
) -> tuple[str, str]:
    system = (
        "Tu es un expert en mathématiques et en algorithmique appliquée à la mobilité urbaine. "
        "Génère un document LaTeX complet et rigoureux expliquant un algorithme. "
        "Réponds UNIQUEMENT avec du code LaTeX valide, sans balise markdown, sans explication autour. "
        "Le document doit compiler avec pdflatex sans package exotique (utilise amsmath, amssymb, algorithm2e, geometry, hyperref). "
        "Langue : français."
    )
    user = f"""Génère un document LaTeX expliquant l'algorithme suivant :

Nom         : {algo_name}
Catégorie   : {category}
Description : {description}
Problème    : {prob_title}
Domaine     : {type_name}

Le document doit contenir exactement ces sections :
1. \\section{{Concept mathématique}} — intuition et définition formelle
2. \\section{{Formulation}} — équations et notations mathématiques précises
3. \\section{{Pseudo-code}} — algorithme étape par étape (environnement algorithm2e)
4. \\section{{Complexité}} — complexité temporelle et spatiale en notation O(...)
5. \\section{{Application au domaine}} — comment cet algorithme s'applique à "{type_name}"
6. \\section{{Avantages et limites}} — liste avantages / inconvénients

Commence directement par \\documentclass{{article}} sans aucun texte avant.
"""
    return system, user
