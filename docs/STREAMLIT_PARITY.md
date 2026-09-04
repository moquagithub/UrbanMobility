# Correspondance avec les apps Streamlit d'origine

Ce document fait le lien, page par page, entre les deux applications Streamlit d'origine (`app_eda.py`, `app_ml.py`) et leur équivalent dans la nouvelle architecture (endpoint(s) backend + page frontend). Légende :

- ✅ **Parité complète** — même donnée, même fonctionnalité, présentée différemment (Plotly.js au lieu de composants Streamlit).
- ⚠️ **Parité partielle** — l'essentiel est là, un aspect secondaire diffère ou manque.
- 🔄 **Changé par choix délibéré** — comportement différent assumé et justifié (voir la colonne Notes).
- ❌ **Non porté** — fonctionnalité de l'app Streamlit absente de la nouvelle architecture à ce jour.

## `app_eda.py` — Analyse exploratoire de données

| Page Streamlit | Endpoint(s) backend | Page frontend | Statut | Notes |
|---|---|---|---|---|
| Vue d'ensemble | `GET /datasets/{id}/overview` | `/datasets/[id]/overview` | ✅ | |
| Valeurs manquantes | `GET /datasets/{id}/missing` | `/datasets/[id]/missing` | ✅ | |
| Distributions | `GET /datasets/{id}/distributions[/numeric\|categorical/{col}]` | `/datasets/[id]/distributions` | ✅ | |
| Normalité des variables | `GET /datasets/{id}/normality[/{col}]` | `/datasets/[id]/normality` | ✅ | |
| Corrélations | `GET /datasets/{id}/correlation` | `/datasets/[id]/correlation` | ✅ | |
| Importance des variables | `GET /datasets/{id}/importance` | `/datasets/[id]/importance` | ✅ | |
| Analyse Quiz | `GET /datasets/{id}/quiz` | — | ❌ | Endpoint livré en DQE-7 ; la page frontend a été explicitement exclue du périmètre DQE-9 (liste de pages du ticket ne la mentionnait pas). L'API est prête, l'écran reste à construire. |
| Exploration par variable | `GET /datasets/{id}/variables[/{col}]` | `/datasets/[id]/explorer` | ✅ | |
| Recommandations | `GET /datasets/{id}/recommendations`, `POST .../apply` | `/datasets/[id]/recommendations` | ✅ | Transformation crée un **nouveau** dataset plutôt que de muter l'original — voir 🔄 ci-dessous. |
| Journal des erreurs | `GET /datasets/{id}/errors` | `/datasets/[id]/errors` | ✅ | |
| Télécharger les rapports | `GET /datasets/{id}/reports/detailed`, `GET .../reports/synthesis` | Bouton sur la page Vue d'ensemble (détaillé) + bouton sur Recommandations après application (synthèse) | ✅ | Retourne un ZIP (LaTeX + figures PNG), pas un PDF compilé — pas de `pdflatex` dans la stack ; décompresser puis compiler soi-même (ou Overleaf), comme dans l'app d'origine. |

**🔄 Changement délibéré — transformations non destructives.** Dans `app_eda.py`, "Appliquer les transformations" mute la session Streamlit en place (un seul utilisateur, un seul dataset actif). L'API expose plusieurs datasets en parallèle (multi-utilisateurs) : `POST /recommendations/apply` crée donc un nouveau `dataset_id`, l'original reste intact et consultable. Voir `app/services/recommendations_service.py`.

## `app_ml.py` — Clustering

| Page Streamlit | Endpoint(s) backend | Page frontend | Statut | Notes |
|---|---|---|---|---|
| Chargement des données | — | — | 🔄 | app_ml.py a son propre uploader, indépendant de app_eda.py. La nouvelle architecture réutilise directement un dataset déjà chargé via le module EDA (`dataset_id` existant) — pas de second pipeline d'upload dupliqué. Voir `app/services/ml_service.py`. |
| Qualité des données | `GET /datasets/{id}/ml/data-quality` | `/datasets/[id]/ml` | ✅ | |
| Recommandation d'algorithmes | `GET /datasets/{id}/ml/algorithms` | `/datasets/[id]/ml` | ✅ | |
| Plan d'encodage des catégorielles | `GET /datasets/{id}/ml/encoding-plan` | `/datasets/[id]/ml` (indices affichés en regard de chaque case à cocher catégorielle) | ⚠️ | Pas d'écran dédié — les recommandations sont intégrées au sélecteur de colonnes plutôt qu'affichées séparément. |
| Configuration (colonnes, algorithme, paramètres) | — | `/datasets/[id]/ml` | ✅ | 5 algorithmes (K-Means, DBSCAN, CAH, GMM, Mean-Shift) avec formulaire de paramètres dédié par algorithme. |
| Aide au choix de k (elbow / BIC-AIC) | `POST /datasets/{id}/ml/k-selection` | `/datasets/[id]/ml` (section repliable) | ✅ | |
| Dendrogramme | `POST /datasets/{id}/ml/dendrogram` | `/datasets/[id]/ml` (section repliable, CAH uniquement) | ✅ | Rendu à partir des données brutes (`icoord`/`dcoord`), pas d'image PNG. |
| Lancement du clustering | `POST /datasets/{id}/ml/clustering` (asynchrone) | `/datasets/[id]/ml` → redirection `/datasets/[id]/ml/[jobId]` | ✅ | Traitement en tâche de fond (`BackgroundTasks`) avec suivi de statut — non bloquant, contrairement au calcul synchrone de l'app Streamlit. |
| Résultats (métriques, PCA/t-SNE, profils, silhouette) | Inclus dans le résultat du job ci-dessus | `/datasets/[id]/ml/[jobId]` | ✅ | |
| Interprétation automatique | Inclus dans le résultat du job | `/datasets/[id]/ml/[jobId]` | ✅ | |
| Export (CSV labellisé, rapport) | — | — | ❌ | Non porté : pas d'endpoint de téléchargement du dataset avec labels de cluster ajoutés. Les résultats (labels, statistiques par cluster) sont consultables à l'écran mais pas exportables en CSV à ce jour. |

## Fonctionnalités transverses

| Fonctionnalité | Streamlit | Nouvelle architecture |
|---|---|---|
| Authentification | Aucune (exécution locale mono-utilisateur) | Aucune — accès direct à l'application |
| Cloisonnement des données | Aucun (un seul utilisateur à la fois) | Aucun — tout dataset enregistré est consultable via son identifiant |
| Anonymisation PII | `pii_anonymizer.py` | Identique, code non modifié |
| Persistance entre sessions | CSV/état en mémoire Streamlit, perdu à la fermeture | Datasets persistés sur disque (`dataset_store/`), retrouvables via "Mes datasets" après reconnexion |

## Résumé

Sur les 11 pages de `app_eda.py` : 10 ont une parité complète, 1 (Analyse Quiz) a son API prête mais pas d'écran. Sur les 6 sections de `app_ml.py` : 4 ont une parité complète, 1 (plan d'encodage) est fusionnée dans un autre écran, 1 (export) n'est pas portée. Le chargement des données diffère par choix délibéré (dataset partagé entre modules EDA et ML plutôt que deux pipelines d'upload séparés).
