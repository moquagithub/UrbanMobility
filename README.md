# Mobility Reporting — Pipeline automatisé

> Catalogue Excel → LLM → MySQL + Minio → Notebooks → LaTeX → PDF  
> Génère des rapports PDF académiques sur les données de mobilité urbaine.

---

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Architecture](#2-architecture)
3. [Installation](#3-installation)
4. [Configuration `.env`](#4-configuration-env)
5. [Initialisation de la base de données](#5-initialisation-de-la-base-de-données)
6. [Démarrage rapide](#6-démarrage-rapide)
7. [Automatisation 1 — Catalogue → Problèmes → Algorithmes → Datasets](#7-automatisation-1)
8. [Automatisation 2 — Notebooks & Exécution](#8-automatisation-2)
9. [Automatisation 3 — LaTeX & PDF](#9-automatisation-3)
10. [Orchestrateur](#10-orchestrateur)
11. [Surveillance Qualité & Cohérence](#11-surveillance-qualité--cohérence)
    · [Autopilote — exécution programmée](#autopilote--exécution-programmée-sans-surveillance)
12. [Stockage Minio — Hiérarchie catalogue](#12-stockage-minio--hiérarchie-catalogue)
13. [Providers LLM](#13-providers-llm)
14. [Structure du projet](#14-structure-du-projet)
15. [Référence CLI complète](#15-référence-cli-complète)
16. [Dépannage](#16-dépannage)

---

## 1. Vue d'ensemble

Le pipeline transforme un catalogue Excel de types de données de mobilité en rapports PDF complets, entièrement générés par LLM.

**Ce que le pipeline produit pour chaque type de données (ex : *Traces GPS*) :**

- Une liste de **problèmes** liés à ce type (anomalies, biais capteur, données manquantes…)
- Pour chaque problème : plusieurs **algorithmes de détection** avec code Python validé
- Un **dataset synthétique** de test (5 000 lignes, colonnes cohérentes avec les algorithmes)
- Des **notebooks Jupyter** qui exécutent chaque algorithme sur le dataset
- Un **rapport PDF** (~20 pages) avec résultats, figures et comparatifs

Tout est stocké en double : **MySQL** (source de vérité) + **Minio** (fichiers hiérarchiques consultables).

---

## 2. Architecture

```
Catalogue Excel (data/example_catalogue.xlsx)
          │
          ▼
┌─────────────────────┐
│    AUTOMATISATION 1  │  ← Lecture + LLM → MySQL + Minio
│  S1  Catalogue       │    Catalogue Excel → data_types
│  S1b Enrichissement  │    LLM → enrichissement (domaine, sources…)
│  S2  Problèmes       │    LLM → problèmes (causes, impacts…)
│  S3  Algorithmes     │    LLM → algorithmes + code Python validé
│  S4  Datasets        │    LLM + génération → dataset synthétique 5000 lignes
└──────────┬──────────┘
           │ MySQL : problems_done → algorithms_done → datasets_done
           ▼
┌─────────────────────┐
│    AUTOMATISATION 2  │  ← Notebooks + Exécution → MySQL + Minio
│  S1  Datasets        │    Chargement datasets depuis MySQL
│  S2  Notebooks       │    Génération notebooks Jupyter (.ipynb)
│  S3  Exécution       │    nbconvert → résultats, métriques, figures
│  S4  Résultats       │    Stockage métriques (F1, précision, rappel)
│  S5  Comparaison     │    Figures comparatives tous algorithmes
└──────────┬──────────┘
           │ MySQL : notebooks_done
           ▼
┌─────────────────────┐
│    AUTOMATISATION 3  │  ← LaTeX + PDF
│  S1  Collecte        │    Lecture résultats depuis MySQL
│  S2  LaTeX           │    Génération .tex (main + chapitres)
│  S3  Compilation     │    pdflatex + auto-réparation LLM (3 rounds)
│  S4  Stockage        │    PDF → Minio
└─────────────────────┘
           │ MySQL : report_done

      orchestrator.py pilote les 3 automatisations
      MySQL est l'état partagé entre elles
      Minio stocke tous les fichiers produits
```

**Cycle d'états MySQL par type de données :**

```
pending → catalogue_done → problems_done → algorithms_done
        → datasets_done → notebooks_done → report_done
```

L'orchestrateur reprend exactement là où il s'est arrêté à chaque relance.

---

## 3. Installation

**Prérequis système :**

| Composant | Version minimale |
|-----------|-----------------|
| Python | 3.10+ |
| MySQL | 8.0+ |
| LaTeX | texlive-latex-extra |
| Minio | Toute version récente |

```bash
# LaTeX (Ubuntu/Debian)
sudo apt-get install texlive-latex-extra texlive-lang-french latexmk

# Python
git clone <repo-url> && cd mobility_pdf_generator
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

---

## 4. Configuration `.env`

Créer un fichier `.env` à la racine du projet :

```dotenv
# ── MySQL ────────────────────────────────────────────────────────────────────
DB_HOST=localhost
DB_PORT=3306
DB_NAME=urbain_automation
DB_USER=root
DB_PASSWORD=votre_mot_de_passe

# ── Minio ────────────────────────────────────────────────────────────────────
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=votre_access_key
MINIO_SECRET_KEY=votre_cle_secrete
MINIO_SECURE=false

# ── LLM — au moins 1 clé requise ────────────────────────────────────────────
# Ordre de fallback (voir section 13) — vide = ordre par défaut du routeur
LLM_ROUTER_ORDER=DeepSeek,GoogleAIStudio,Groq,NVIDIA,Cerebras,Mistral,HuggingFace,Together,OpenRouter,Gemini

GEMINI_API_KEY=AIzaSy...
GROQ_API_KEY=gsk_...
CEREBRAS_API_KEY=csk-...
MISTRAL_API_KEY=...
OPENROUTER_API_KEY=sk-or-...
DEEPSEEK_API_KEY=sk-...
GOOGLE_AI_STUDIO_API_KEY=AIzaSy...
HUGGINGFACE_API_KEY=hf_...
# NVIDIA_NIM_API_KEY et TOGETHER_AI_API_KEY également supportés (voir section 13)
```

---

## 5. Initialisation de la base de données

```bash
# 1. Créer la base MySQL
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS urbain_automation CHARACTER SET utf8mb4;"

# 2. Appliquer le schéma de base, puis les migrations dans l'ordre
mysql -u root -p urbain_automation < shared/db/schema.sql   # tables de base (v1)
python3 -m shared.db.migration_v2    # colonnes enrichissement (domaine, sources, cas d'usage…)
python3 -m shared.db.migration_v3    # datasets enrichis + notebooks + résultats (Auto2)
python3 -m shared.db.migration_v4    # table reports (PDF)
python3 -m shared.db.migration_v5    # table figures
python3 -m shared.db.migration_v6    # colonnes qualité squelettes (needs_skeleton_regen…)
python3 -m shared.db.migration_v7    # colonnes minio_key (notebooks, figures, reports, datasets)
python3 -m shared.db.migration_v8    # colonnes minio_dir (problems + algorithms) — hiérarchie catalogue
python3 -m shared.db.migration_v9    # architecture par problème (notebook/figure/rapport communs à tous les algos d'un problème)
python3 -m shared.db.migration_v10   # notebooks.n_algorithms — détection de staleness des notebooks de comparaison
python3 -m shared.db.migration_v11   # surveillance qualité (quality_issues + quality_scan_runs)

# 3. Vérifier la connexion
python3 -c "from shared.db.connection import get_connection; print('MySQL OK' if get_connection() else 'ERREUR')"
```

> **Note :** les migrations sont idempotentes — les relancer ne causera pas d'erreur.

| Migration | Tables / colonnes ajoutées |
|-----------|---------------------------|
| v1 (`schema.sql`) | `catalogue_imports`, `data_types`, `problems`, `algorithms`, `llm_calls_log`, `validation_errors`, `datasets`, `pipeline_runs`, `notebook_results` |
| v2 | Colonnes enrichissement (`domain`, `sources`, `use_cases`, `challenges`…) sur `data_types`, `problems`, `algorithms` |
| v3 | `datasets` enrichis + tables `notebooks`, `notebook_results` (Auto2) |
| v4 | `reports` |
| v5 | `figures` (PNG stockées en base : bytes + métadonnées JSON) |
| v6 | `needs_skeleton_regen`, `skeleton_regen_reason` sur `algorithms` |
| v7 | `minio_key` / `executed_minio_key` sur `notebooks`, `figures`, `reports`, `datasets` |
| v8 | `minio_dir` sur `problems`, `algorithms` |
| v9 | `notebooks.algorithm_id` nullable + `figures.problem_id` + `reports.problem_id` — 1 notebook/figure/rapport par **problème** (plus par algo) |
| v10 | `notebooks.n_algorithms` — permet de détecter qu'un notebook de comparaison est devenu obsolète |
| v11 | `quality_issues`, `quality_scan_runs` + colonnes de traçabilité qualité |

> Une migration `v9` supplémentaire (`automatisation_1 migrate-v9`) existe aussi côté Auto1 pour appliquer spécifiquement ce changement depuis sa propre CLI — voir [section 15](#15-référence-cli-complète).

---

## 6. Démarrage rapide

```bash
# Lancer le pipeline complet une fois (recommandé pour débuter)
python3 orchestrator.py --once

# Lancer uniquement pour les données GPS, avec enrichissement maximal
python3 -m automatisation_1 run --type "GPS" --enrich

# Voir l'état actuel en MySQL
python3 -m automatisation_1 status

# Vérifier que les providers LLM fonctionnent
python3 -m automatisation_1 check-llm
```

---

## 7. Automatisation 1

> Catalogue Excel → Problèmes → Algorithmes → Datasets → MySQL + Minio

### Ce qu'elle fait

Auto1 est le moteur de génération de connaissance. Pour chaque type de données du catalogue :

1. **S1 — Catalogue** : Lit le fichier Excel, extrait les types de données, les importe en MySQL. Upload `{type_id}/metadata.json` dans Minio.

2. **S1b — Enrichissement** : Appelle le LLM pour enrichir chaque type (domaine, format, sources, cas d'usage, défis). Met à jour le `metadata.json` Minio avec tous les champs.

3. **S2 — Problèmes** : Pour chaque type, appelle le LLM pour générer des problèmes (anomalies, biais, données manquantes…). En mode `--enrich`, itère jusqu'à saturation. Upload `problems/{pk}/metadata.json` dans Minio pour chaque problème.

4. **S3 — Algorithmes** : Pour chaque problème, génère des algorithmes de détection avec code Python. Valide automatiquement le squelette Python (syntaxe + `df['is_anomaly']` requis). Répare automatiquement si invalide (jusqu'à 2 tentatives). Upload `algorithms/{ak}/metadata.json` + `skeleton.py` + `explanation.txt` + `test_results.json` dans Minio.

5. **S4 — Datasets** : Génère un schéma de données cohérent avec les colonnes attendues par les algorithmes. Génère 5 000 lignes synthétiques. Stocke en table MySQL `ds_{key}` + upload `dataset/schema.json` + `dataset/data.csv` dans Minio.

### Garanties comportementales importantes

- **Saturation par type** : avec `--enrich`, Auto1 trouve le **maximum de problèmes** pour un type avant de passer au suivant.
- **Saturation par problème** : Auto1 trouve le **maximum d'algorithmes** pour le problème 1 avant de traiter le problème 2.
- **Upload après validation** : aucun fichier n'est uploadé dans Minio si la validation MySQL a échoué.
- **Résilience Minio** : un échec d'upload Minio génère un avertissement mais **ne bloque pas** le pipeline — MySQL reste la source de vérité.
- **Validation par exécution réelle (proactive)** : juste après la génération du dataset (fin de S4), chaque skeleton d'algorithme du problème est exécuté pour de vrai (sous-processus isolé, timeout) sur le dataset qui vient d'être créé — pas juste vérifié syntaxiquement. En cas d'échec ou de mauvais calibrage (0% ou >50% de lignes détectées comme anomalies), une réparation LLM est tentée immédiatement (jusqu'à 2 tentatives), avec re-validation par exécution avant tout commit MySQL/Minio. Objectif : détecter et corriger ces problèmes avant même qu'Auto2 ne génère un notebook, plutôt que d'attendre `shared.quality --watch` (qui ne les détecte qu'après un cycle Auto2 complet, beaucoup plus lent). Voir `automatisation_1/validation/execution_validator.py` (réutilise `shared/validation/skeleton_execution.py`, partagé avec la réparation réactive de `shared/quality/repair.py`). Les algos non réparables après 2 tentatives restent visibles via le monitoring qualité réactif, en filet de sécurité.

### Commandes Auto1

```bash
# Lancer pour tous les types
python3 -m automatisation_1 run

# Lancer pour un seul type (filtre sur id ou nom partiel)
python3 -m automatisation_1 run --type "GPS"
python3 -m automatisation_1 run --type "traces_gps"

# Mode enrichissement (trouver le maximum de problèmes et algorithmes)
python3 -m automatisation_1 run --type "GPS" --enrich

# Enrichissement avec paramètres personnalisés
python3 -m automatisation_1 run --enrich --max-problems 15 --max-algos 9 --patience 3

# Forcer la régénération (même si déjà en base)
python3 -m automatisation_1 run --type "GPS" --force

# Sauter des étapes
python3 -m automatisation_1 run --skip-s1 --skip-s1b   # reprendre à partir de S2
python3 -m automatisation_1 run --skip-s4               # S1→S2→S3 seulement

# Voir l'état MySQL de tous les types
python3 -m automatisation_1 status
python3 -m automatisation_1 status --type "GPS"

# Remettre un type à zéro (statut → pending)
python3 -m automatisation_1 reset --type "GPS"

# Vérifier les providers LLM
python3 -m automatisation_1 check-llm

# Appliquer la migration v8 (colonnes minio_dir)
python3 -m automatisation_1 migrate-v8

# Appliquer la migration v9 (architecture par problème : notebooks/figures/rapports)
python3 -m automatisation_1 migrate-v9
```

### Options `run` détaillées

| Option | Défaut | Description |
|--------|--------|-------------|
| `--catalogue FICHIER` | `data/example_catalogue.xlsx` | Fichier Excel source |
| `--type NOM_OU_ID` | tous | Traiter uniquement ce type |
| `--force` | non | Régénérer même si déjà en base |
| `--enrich` | non | Itérer jusqu'à saturation |
| `--max-problems N` | 12 | Max problèmes par type (mode enrich) |
| `--max-algos N` | 9 | Max algorithmes par problème (mode enrich) |
| `--patience N` | 2 | Rounds sans résultat avant saturation |
| `--skip-s1` | non | Saute la lecture du catalogue Excel |
| `--skip-s1b` | non | Saute l'enrichissement LLM des types |
| `--skip-s2` | non | Saute la génération de problèmes |
| `--skip-s3` | non | Saute la génération d'algorithmes |
| `--skip-s4` | non | Saute la génération de datasets |

---

## 8. Automatisation 2

> Datasets MySQL → Notebooks Jupyter → Exécution → Métriques → Minio

Auto2 génère et exécute un notebook Jupyter par combinaison (problème × algorithme).

```bash
# Lancer pour tous les types datasets_done
python3 -m automatisation_2 run

# Restreindre à certains types
python3 -m automatisation_2 run --types traces_gps comptages_trafic

# Limiter le nombre de notebooks exécutés (pour les tests)
python3 -m automatisation_2 run --max-notebooks 5

# Voir l'état
python3 -m automatisation_2 status
```

| Option | Description |
|--------|-------------|
| `--types ID [ID...]` | Restreindre à ces types de données |
| `--skip-s1` | Sauter génération datasets |
| `--skip-s2` | Sauter construction notebooks |
| `--skip-s3` | Sauter exécution notebooks |
| `--skip-s4` | Sauter agrégation résultats |
| `--skip-s5` | Sauter génération graphiques comparatifs |
| `--skip-s6` | Sauter notebooks standalone + LaTeX par algorithme |
| `--max-notebooks N` | Limiter les notebooks exécutés par passe |
| `--force` | Régénérer les notebooks existants |

---

## 9. Automatisation 3

> Résultats MySQL → LaTeX → PDF compilé → Minio

Auto3 génère le rapport PDF final à partir des résultats d'exécution des notebooks.

```bash
# Lancer pour tous les types notebooks_done
python3 -m automatisation_3 run

# Restreindre à certains types
python3 -m automatisation_3 run --types traces_gps

# Générer LaTeX sans compiler (debug)
python3 -m automatisation_3 run --skip-s3

# Voir l'état des PDFs générés
python3 -m automatisation_3 status
```

| Option | Description |
|--------|-------------|
| `--types ID [ID...]` | Restreindre à ces types |
| `--no-llm` | Désactiver la réparation LaTeX automatique par LLM |
| `--skip-s1` | Sauter la collecte des données depuis MySQL |
| `--skip-s2` | Sauter la génération LaTeX |
| `--skip-s3` | Générer le LaTeX sans lancer la compilation PDF |
| `--skip-s4` | Sauter le stockage MySQL du rapport |
| `--max-repairs N` | Tentatives de réparation LLM max (défaut : 3) |
| `--force` | Régénérer même si le PDF existe déjà |

---

## 10. Orchestrateur

> Pilote les 3 automatisations en séquence, gère les quotas LLM, reprend automatiquement.

```bash
# Une seule passe complète (Auto1 → Auto2 → Auto3)
python3 orchestrator.py --once

# Boucle continue (relance toutes les 120s)
python3 orchestrator.py

# Voir l'état sans rien lancer
python3 orchestrator.py --dry-run

# Avec enrichissement illimité en arrière-plan
python3 orchestrator.py --enrich

# Limiter les notebooks exécutés par passe (utile en phase de test)
python3 orchestrator.py --max-notebooks 3

# Forcer la régénération de tout
python3 orchestrator.py --force
```

| Option | Défaut | Description |
|--------|--------|-------------|
| `--once` | non | Une seule passe puis exit |
| `--interval SEC` | 120 | Secondes entre deux passes |
| `--enrich` | non | Active la phase 5 : enrichissement arrière-plan illimité après Auto1→Auto2→Auto3 |
| `--enrich-max-problems N` | 999 | (enrich) Max problèmes par type |
| `--enrich-max-algos N` | 999 | (enrich) Max algorithmes par problème |
| `--enrich-patience N` | 3 | (enrich) Rounds sans résultat avant saturation |
| `--skip-auto1` | non | Sauter Auto1 |
| `--skip-auto2` | non | Sauter Auto2 |
| `--skip-auto3` | non | Sauter Auto3 |
| `--skip-refinement` | non | Sauter la phase de raffinement |
| `--max-notebooks N` | illimité | Notebooks exécutés par passe |
| `--dry-run` | non | Afficher l'état sans agir |
| `--force` | non | Régénérer même si déjà en base |
| `--catalogue FICHIER` | `data/example_catalogue.xlsx` | Catalogue Excel |

**Gestion du quota LLM :** si tous les providers atteignent leur limite journalière, l'orchestrateur crée un fichier `QUOTA_EXHAUSTED.flag` et (en mode boucle) reprend automatiquement après minuit.

---

## 11. Surveillance Qualité & Cohérence

> Détecte les incohérences entre MySQL et Minio, valide la qualité des données et répare automatiquement via LLM.

Le module `shared/quality/` tourne en continu (ou à la demande) et surveille l'ensemble de la chaîne de données : skeletons Python, datasets, notebooks, figures, cohérence MySQL ↔ Minio (17 checks, 10+ réparations automatiques).

### Comment ça fonctionne

```
Scan (17 checks)
  │
  ├── MySQL intégrité  → skeletons manquants / SyntaxError / is_anomaly absent / imports interdits
  ├── MySQL cohérence  → problèmes sans algos / types sans problèmes
  ├── Dataset          → data_json absent / colonnes requises manquantes
  ├── Algorithmes      → erreurs d'exécution réelle / algo miscalibré (détection sur exécution réelle)
  ├── Notebooks        → status=failed / 0 figures / obsolètes / minio_key orphelin
  └── Croisé           → colonnes skeleton vs colonnes dataset / algo_count mismatch
          │
          ▼
  quality_issues (MySQL) — chaque issue : sévérité, auto_fixable, fix_result
          │
          ▼
  Réparation LLM auto (si --fix)
    • SyntaxError skeleton → LLM corrige
    • Skeleton manquant   → LLM régénère
    • is_anomaly absent   → LLM ajoute
    • Notebook failed     → reset status='generated' → S3 relancera
    • minio_key orphelin  → effacé → S2/S3 reconstruira
```

### Niveaux de sévérité

| Niveau | Exemples |
|--------|---------|
| `critical` | Skeleton vide, dataset sans données |
| `high` | SyntaxError Python, is_anomaly absent, minio_key introuvable |
| `medium` | Imports interdits, notebooks 0 figures, notebooks obsolètes |
| `low` | Décalage algo_count (notebook vs DB actuel) |

### Commandes

```bash
# Scan unique — affiche le rapport dans le terminal
python3 -m shared.quality --once

# Scan + réparations LLM automatiques
python3 -m shared.quality --fix

# Surveillance continue (scan + réparation toutes les 5 min)
python3 -m shared.quality --watch

# Restreindre à un type de données
python3 -m shared.quality --fix --type traces_gps capteurs_iot

# Rapport HTML depuis le dernier scan
python3 -m shared.quality --report --out rapport_qualite.html

# Ne lancer que certains checks
python3 -m shared.quality --only algo notebook --skip low

# Sortie JSON (pour API / monitoring)
python3 -m shared.quality --json
```

### Options CLI

| Option | Description |
|--------|-------------|
| `--once` | Scan unique (défaut) |
| `--fix` | Scan + réparations automatiques |
| `--watch` | Boucle continue |
| `--no-fix` | En mode watch, désactive les réparations |
| `--type ID [ID...]` | Restreindre aux data_type_id |
| `--only LABEL...` | Ne lancer que les checks dont le label contient ces termes |
| `--skip LABEL...` | Ignorer ces checks |
| `--interval SEC` | Intervalle entre scans watch (défaut 300s) |
| `--max-fix N` | Max réparations auto par scan (défaut 20) |
| `--report` | Générer rapport HTML/JSON depuis dernier scan |
| `--out FICHIER` | Fichier de sortie pour `--report` |
| `--scan-id UUID` | Scan spécifique à utiliser pour `--report` |
| `--json` | Sortie machine-readable JSON |
| `-v`, `--verbose` | Logs détaillés |

### Score qualité

Le rapport affiche un **score global 0-100** :

```
Score global : [████████████░░░░░░░░] 62/100
  CRITICAL : 0
  HIGH     : 3
  MEDIUM   : 8
  LOW      : 2
```

Le score décroît proportionnellement à la sévérité : −25 par issue critique, −10 par issue high, −4 medium, −1 low. Score ≥ 80 → code de retour 0 (pipeline sain), sinon 1.

### Usage programmatique

```python
from shared.quality import QualityMonitor, run_all_checks, print_report

# Scan simple
issues = run_all_checks(type_ids=["traces_gps"])
print_report(issues)

# Scan + réparations
monitor = QualityMonitor(type_ids=["traces_gps"], max_fix_per_run=10)
result = monitor.run_once(fix=True)
print(result)  # {'score': 75, 'issues': 12, 'fixed': 5, ...}

# Mode watch
monitor.watch(fix=True)  # boucle bloquante, Ctrl+C pour arrêter
```

### Tables MySQL associées

```sql
-- Toutes les issues détectées (historique complet)
SELECT * FROM quality_issues ORDER BY detected_at DESC LIMIT 20;

-- Issues non résolues, triées par sévérité
SELECT entity_type, entity_key, issue_type, severity, description
FROM quality_issues
WHERE fix_result IN ('pending', 'failed') OR fix_result IS NULL
ORDER BY FIELD(severity,'critical','high','medium','low'), detected_at DESC;

-- Historique des scans
SELECT id, started_at, finished_at, issues_found, issues_fixed, issues_failed
FROM quality_scan_runs ORDER BY started_at DESC;
```

---

### Autopilote — exécution programmée sans surveillance

`--watch` boucle indéfiniment et n'a aucune notion de « fin ». L'**autopilote**
(`shared/autopilot`) est programmable : on lui donne un objectif et une échéance,
il travaille, s'arrête seul et laisse un rapport lisible au réveil.

```bash
# Travailler toute la nuit, s'arrêter à 7h00
python3 -m shared.autopilot --until 07:00 --type traces_gps

# 8 heures maximum, arrêt anticipé si le score atteint 95
python3 -m shared.autopilot --for 8h --target-score 95 --type traces_gps

# Nuit complète, pipeline Auto1→2→3 inclus, arrêt si 3 cycles ne réparent plus rien
python3 -m shared.autopilot --until 07:00 --with-orchestrator --patience 3

# Suivre l'avancement depuis un autre terminal (pendant le run)
python3 -m shared.autopilot --status

# Vérifier le plan sans rien lancer
python3 -m shared.autopilot --until 07:00 --dry-run
```

**Conditions d'arrêt** (la première atteinte l'emporte) :

| Option | Effet |
|---|---|
| `--until HH:MM` | s'arrête à cette heure (demain si déjà passée) |
| `--for 8h30m` | s'arrête après cette durée |
| `--max-cycles N` | s'arrête après N cycles |
| `--target-score N` | s'arrête dès que le score qualité atteint N |
| `--stop-when-clean` | s'arrête dès qu'aucune issue n'est détectée |
| `--patience N` | s'arrête après N cycles consécutifs sans aucune réparation |

**Garanties pour une nuit sans surveillance :**

- une exception dans un cycle **n'arrête pas** l'autopilote (backoff exponentiel, plafonné à 1 h) ;
- quota LLM épuisé → sommeil jusqu'au reset de minuit, `reset_session()`, puis reprise ;
- `SIGTERM`/`SIGINT` → arrêt propre en fin de cycle courant, rapport écrit ;
- **verrou d'instance unique** — deux autopilotes (ou deux `--watch`) ne peuvent pas
  tourner en parallèle et se disputer les mêmes réparations ;
- état publié en continu dans `logs/autopilot_status.json` (écriture atomique) ;
- log tournant `logs/autopilot.log` (5 Mo × 5) ;
- rapport final `logs/autopilot_report.md` (trajectoire du score, bilan des réparations).

#### Programmer un lancement automatique

```bash
# systemd --user : démarre chaque nuit à 23h00, s'arrête à 07h00
python3 -m shared.autopilot --install-systemd --at 23:00 --until 07:00 --type traces_gps

# ou via cron
python3 -m shared.autopilot --install-cron --at 23:00 --until 07:00 --type traces_gps

# retirer la programmation (systemd + cron)
python3 -m shared.autopilot --uninstall-schedule
```

Pour que le timer systemd se déclenche même sans session graphique ouverte :

```bash
sudo loginctl enable-linger $USER
systemctl --user list-timers mobility-autopilot.timer   # vérifier
journalctl --user -u mobility-autopilot -f              # suivre les logs
```

---

## 12. Stockage Minio — Hiérarchie catalogue

Auto1 construit dans Minio une **arborescence hiérarchique** qui reflète exactement la structure logique des données. Elle permet d'explorer, télécharger ou inspecter n'importe quel artefact sans interroger MySQL.

### Structure du bucket `catalogue`

```
catalogue/
  {type_id}/                               ex: traces_gps/
    metadata.json                          ← type : nom, description, domaine, sources,
    │                                          cas d'usage, défis (S1 initial, S1b enrichi)
    problems/
      {problem_key}/                       ex: p1/
        metadata.json                      ← problème : titre, causes, impacts, acteurs,
        │                                      indicateurs de détection, priorité (S2)
        test_results.json                  ← résumé des algorithmes validés pour ce
        │                                      problème : nb algos, skeleton_valid (S3)
        dataset/
          schema.json                      ← colonnes, types, stats (moyenne, écart-type) (S4)
          data.csv                         ← 5 000 lignes synthétiques (S4)
        algorithms/
          {algo_key}/                      ex: alg1/
            metadata.json                  ← algorithme : principe, complexité, pseudocode,
            │                                  hyperparamètres, librairies requises (S3)
            skeleton.py                    ← code Python validé (syntaxe OK + df['is_anomaly']) (S3)
            explanation.txt                ← explication narrative : principe + cas d'usage (S3)
```

### Exemple concret pour Traces GPS (2 problèmes, 3 algos chacun)

```
catalogue/traces_gps/metadata.json
catalogue/traces_gps/problems/p1/metadata.json
catalogue/traces_gps/problems/p1/test_results.json
catalogue/traces_gps/problems/p1/dataset/schema.json
catalogue/traces_gps/problems/p1/dataset/data.csv
catalogue/traces_gps/problems/p1/algorithms/alg1/metadata.json
catalogue/traces_gps/problems/p1/algorithms/alg1/skeleton.py
catalogue/traces_gps/problems/p1/algorithms/alg1/explanation.txt
catalogue/traces_gps/problems/p1/algorithms/alg2/metadata.json
catalogue/traces_gps/problems/p1/algorithms/alg2/skeleton.py
catalogue/traces_gps/problems/p1/algorithms/alg2/explanation.txt
catalogue/traces_gps/problems/p1/algorithms/alg3/...
catalogue/traces_gps/problems/p2/metadata.json
catalogue/traces_gps/problems/p2/...
```

### Autres buckets (gérés par Auto2/Auto3)

| Bucket | Contenu | Géré par |
|--------|---------|----------|
| `catalogue` | Hiérarchie type/problème/algorithme/dataset | Auto1 |
| `notebooks` | Notebooks `.ipynb` (source + exécutés) | Auto2 |
| `figures` | Figures PNG générées par les notebooks | Auto2 |
| `reports` | PDFs finaux | Auto3 |
| `datasets` | CSV datasets (stockage plat, usage legacy) | Auto2 |

### Vérifier l'arborescence Minio

```bash
# Via le client mc (MinIO Client)
mc ls myminio/catalogue --recursive

# Via Python
python3 -c "
from shared.storage import get_storage
keys = get_storage().list_objects('catalogue', prefix='traces_gps/')
for k in keys: print(k)
"
```

### Vérifier les clés Minio en MySQL

```sql
-- Préfixes répertoires des problèmes
SELECT problem_key, minio_dir FROM problems WHERE data_type_id = 'traces_gps';

-- Préfixes répertoires des algorithmes
SELECT a.algorithm_key, a.minio_dir
FROM algorithms a
JOIN problems p ON a.problem_id = p.id
WHERE p.data_type_id = 'traces_gps';

-- Clé dataset
SELECT dataset_key, minio_key FROM datasets WHERE data_type_id = 'traces_gps';
```

---

## 13. Providers LLM

Le pipeline utilise un **routeur multi-provider** (`shared/llm/router.py`) avec fallback automatique sur 10 providers.
Si un provider épuise son quota (429) ou échoue, le suivant dans la chaîne prend le relais immédiatement ; si **tous** les quotas journaliers sont épuisés, une `AllProvidersExhausted` est levée (l'orchestrateur crée alors `QUOTA_EXHAUSTED.flag`, voir section 10).

**Ordre par défaut** (utilisé si `LLM_ROUTER_ORDER` est vide dans `.env`) :

```
DeepSeek → GoogleAIStudio → Groq → NVIDIA → Cerebras → Mistral → HuggingFace → Together → OpenRouter → Gemini
```

L'ordre est entièrement personnalisable via la variable `LLM_ROUTER_ORDER` (liste séparée par des virgules). Un provider absent de cette liste n'est **jamais** appelé, même si sa clé est présente dans `.env` — utile pour garder une clé désactivée sans la supprimer.

| Provider | Variable `.env` | Modèle par défaut |
|----------|----------------|-------------------|
| Gemini | `GEMINI_API_KEY` | `gemini-2.0-flash` |
| Groq | `GROQ_API_KEY` | `llama-3.3-70b-versatile` |
| Cerebras | `CEREBRAS_API_KEY` | `gemma-4-31b` |
| Mistral | `MISTRAL_API_KEY` | `mistral-small-latest` |
| OpenRouter | `OPENROUTER_API_KEY` | `nvidia/nemotron-3-super-120b-a12b:free`, puis `openai/gpt-oss-20b:free`, `…ultra-550b…` |
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek-chat` |
| GoogleAIStudio | `GOOGLE_AI_STUDIO_API_KEY` | `gemini-1.5-flash` |
| NVIDIA (NIM) | `NVIDIA_NIM_API_KEY` | `nvidia/nemotron-3-super-8b-instruct` |
| HuggingFace | `HUGGINGFACE_API_KEY` | `Qwen/Qwen2.5-Coder-32B-Instruct`, puis `meta-llama/Llama-3.3-70B-Instruct` |
| Together AI | `TOGETHER_AI_API_KEY` | `meta-llama/Llama-3.3-70B-Instruct-Turbo` |

Chaque provider a aussi une variable `*_BASE_URL` et `*_MODEL` optionnelle pour surcharger l'URL/modèle par défaut (ex : `GROQ_MODEL=llama-3.1-8b-instant`).

#### Liste de modèles et slugs retirés

`*_MODEL` accepte **plusieurs modèles séparés par des virgules**, essayés dans l'ordre :

```bash
OPENROUTER_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free,nvidia/nemotron-3-super-120b-a12b:free
```

C'est indispensable pour les catalogues volatils : OpenRouter retire régulièrement
des slugs `:free`, et un slug mort rendait auparavant **tout le provider muet**
(404 à chaque appel) alors que son quota était intact. Le routeur distingue
désormais deux natures d'erreur :

- **quota épuisé** → le *provider* est blacklisté pour la session ;
- **modèle indisponible/retiré** → seul ce *modèle* est écarté, et le suivant de la
  liste prend le relais (`get_session_stats()["dead_models"]` les expose).

> Les modèles réellement disponibles évoluent. Après un `AllProvidersExhausted`
> inattendu, vérifiez d'abord que les slugs configurés existent encore —
> `python3 -m automatisation_1 check-llm` teste chaque provider individuellement.

**Ordre des modèles : évitez les modèles à raisonnement en tête de liste.** Un
modèle qui produit une chaîne de pensée consomme son budget de tokens avant
d'écrire le code : `nemotron-3-ultra-550b` a vu 100 % de ses réponses tronquées à
4000 tokens, en 44-73 s par appel contre 4-15 s pour les autres. Les réécritures
de skeleton détectent la troncature (`finish_reason == "length"`) et doublent
automatiquement le budget jusqu'à 8000 tokens, mais l'appel gaspillé reste coûteux
— mieux vaut placer un modèle non raisonnant en tête.

Un timeout explicite (`LLM_REQUEST_TIMEOUT_SEC`, défaut 45s) est appliqué à chaque appel pour éviter qu'une connexion qui traîne ne bloque indéfiniment la boucle `--watch`.

```bash
# Tester tous les providers configurés
python3 -m automatisation_1 check-llm
```

---

## 14. Structure du projet

```
mobility_pdf_generator/
│
├── orchestrator.py                    ← Point d'entrée principal
│
├── automatisation_1/                  ← Catalogue → Problèmes → Algorithmes → Datasets
│   ├── cli.py                         ← Interface ligne de commande
│   ├── runner.py                      ← Orchestration des étapes S1→S4
│   ├── steps/
│   │   ├── s1_catalogue.py            ← Lecture Excel + upsert MySQL + Minio
│   │   ├── s1b_enrich_types.py        ← Enrichissement LLM + Minio
│   │   ├── s2_problems.py             ← Génération problèmes LLM + Minio
│   │   ├── s3_algorithms.py           ← Génération algos LLM + validation + Minio
│   │   └── s4_datasets.py             ← Génération datasets + MySQL table + Minio
│   ├── prompts/                        ← Prompts LLM pour chaque étape
│   └── validation/                    ← Validateurs JSON + squelettes Python
│       └── execution_validator.py     ← Validation proactive par exécution réelle (post-S4)
│
├── automatisation_2/                  ← Notebooks + Exécution
│   ├── steps/                         ← S1 datasets → S2 génération → S3 exécution…
│   └── prompts/                       ← Prompts pour génération de code notebook
│
├── automatisation_3/                  ← LaTeX + PDF
│   └── steps/                         ← S1 collecte → S2 LaTeX → S3 compile → S4 store
│
├── shared/
│   ├── db/
│   │   ├── connection.py              ← Connexion MySQL
│   │   ├── migration_v1.py … v8.py   ← Migrations (appliquer dans l'ordre)
│   │   └── repository/
│   │       ├── catalogue_repo.py      ← CRUD data_types
│   │       ├── problem_repo.py        ← CRUD problems (+ update_minio_dir)
│   │       ├── algorithm_repo.py      ← CRUD algorithms (+ update_minio_dir)
│   │       ├── dataset_repo.py        ← CRUD datasets + tables ds_{key} (+ update_minio_key)
│   │       ├── notebook_repo.py       ← CRUD notebooks
│   │       ├── figure_repo.py         ← CRUD figures
│   │       └── report_repo.py         ← CRUD reports
│   ├── runtime_lock.py                ← Verrou d'instance unique (flock) — empêche 2 watch/autopilotes
│   ├── llm/
│   │   ├── router.py                  ← Routeur multi-provider + multi-modèle + round-robin + fallback
│   │   ├── client.py                  ← call_llm_json (retry + parsing JSON)
│   │   └── health_check.py            ← Test de chaque provider ET de chaque modèle de repli
│   ├── storage/
│   │   ├── client.py                  ← StorageClient Minio (retry, checksum, batch)
│   │   ├── catalogue_storage.py       ← Uploads hiérarchiques bucket catalogue (Auto1)
│   │   ├── workspace.py               ← Context manager exécution notebooks
│   │   ├── health.py                  ← Santé MySQL + Minio
│   │   ├── sync.py                    ← Synchronisation MySQL ↔ Minio
│   │   └── __init__.py                ← API publique
│   ├── validation/
│   │   └── skeleton_execution.py      ← Exécution réelle d'un skeleton en sous-processus isolé (partagé S4 ↔ quality/repair.py)
│   ├── quality/                       ← Surveillance qualité & cohérence (v11)
│   │   ├── checks.py                  ← 17 contrôles (MySQL intégrité, Minio, croisés)
│   │   ├── repair.py                  ← 10+ réparations auto (LLM + reset statut DB)
│   │   ├── reporter.py                ← Rapport console coloré + JSON + HTML
│   │   ├── monitor.py                 ← Orchestrateur scan → réparation → base
│   │   ├── __init__.py                ← API publique
│   │   └── __main__.py                ← CLI : --once | --fix | --watch | --report
│   └── autopilot/                     ← Exécution programmée sans surveillance (nuit/week-end)
│       ├── scheduler.py               ← Cycles + conditions d'arrêt + reprise sur erreur/quota
│       ├── __init__.py                ← API publique (Autopilot, StopPlan)
│       └── __main__.py                ← CLI : --until | --for | --status | --install-systemd
│
├── data/
│   ├── example_catalogue.xlsx         ← Catalogue des types de données
│   ├── datasets/                      ← CSV générés (cache local)
│   ├── notebooks/                     ← .ipynb générés (cache local)
│   ├── figures/                       ← PNG générées (cache local)
│   └── reports/                       ← PDFs compilés (cache local)
│
├── dashboard/
│   └── streamlit_app.py               ← Interface web de suivi (optionnelle)
│
├── presentation/                      ← Pages HTML d'avancement (local uniquement, gitignored)
│
├── .env                               ← Configuration (MySQL, Minio, LLM)
├── requirements.txt
└── docker-compose.yml
```

---

## 15. Référence CLI complète

### Auto1

```bash
python3 -m automatisation_1 run          [OPTIONS]   # Lance le pipeline
python3 -m automatisation_1 status       [--type T]  # État MySQL
python3 -m automatisation_1 reset        --type T    # Remet à pending
python3 -m automatisation_1 check-llm               # Teste les providers LLM
python3 -m automatisation_1 migrate                  # Migration v6 (squelettes)
python3 -m automatisation_1 migrate-v8              # Migration v8 (minio_dir)
python3 -m automatisation_1 migrate-v9              # Migration v9 (architecture par problème)
```

### Auto2

```bash
python3 -m automatisation_2 run          [OPTIONS]   # --skip-s1..s6, --max-notebooks N, --force
python3 -m automatisation_2 status
python3 -m automatisation_2 migrate      # Migration v3
```

### Auto3

```bash
python3 -m automatisation_3 run          [OPTIONS]   # --skip-s1..s4, --max-repairs N, --no-llm, --force
python3 -m automatisation_3 status
python3 -m automatisation_3 migrate      # Migration v4
```

### Orchestrateur

```bash
python3 orchestrator.py                  # Boucle continue
python3 orchestrator.py --once           # Une passe puis exit
python3 orchestrator.py --dry-run        # État sans action
```

### Surveillance Qualité

```bash
python3 -m shared.quality                           # Scan unique (rapport console)
python3 -m shared.quality --fix                     # Scan + réparations LLM auto
python3 -m shared.quality --watch                   # Boucle continue (300s)
python3 -m shared.quality --watch --interval 600    # Boucle toutes les 10 min
python3 -m shared.quality --once --type traces_gps  # Restreindre à un type
python3 -m shared.quality --report --out r.html     # Rapport HTML depuis dernier scan
python3 -m shared.quality --json                    # Sortie JSON machine-readable
python3 -m shared.quality --only algo               # Checks skeletons uniquement
python3 -m shared.quality --skip low medium         # Ignorer les issues mineures
```

---

## 16. Dépannage

### LLM ne répond pas

```bash
python3 -m automatisation_1 check-llm
# Si tous les providers sont KO → vérifier les clés dans .env
# Si un seul KO → le fallback automatique prendra le suivant
```

### MySQL non disponible

```bash
python3 -c "
from shared.db.connection import get_connection
c = get_connection()
print('MySQL OK' if c else 'ERREUR — vérifier DB_HOST/DB_USER/DB_PASSWORD dans .env')
"
```

### Minio non disponible

```bash
python3 -c "
from shared.storage import get_storage
h = get_storage().health_check()
print('Minio OK' if h.ok else f'ERREUR : {h.error}')
print('Buckets :', h.buckets)
"
# Si KO → vérifier MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY dans .env
# Démarrer Minio si nécessaire : bash ~/start_minio.sh
```

### Colonnes minio_dir absentes (migration oubliée)

```bash
# Symptôme : OperationalError: Unknown column 'minio_dir'
python3 -m automatisation_1 migrate-v8
```

### Compilation LaTeX échoue

```bash
cd data/reports/<type_id>
pdflatex -interaction=nonstopmode main.tex
grep "^!" main.log   # lire l'erreur précise
# Auto3 répare automatiquement via LLM (jusqu'à 3 rounds)
```

### Remettre un type à zéro

```bash
# Remet le statut à 'pending' pour tout régénérer
python3 -m automatisation_1 reset --type "GPS"

# Puis relancer
python3 -m automatisation_1 run --type "GPS" --enrich --force
```

### Vérifier l'état complet d'un type

```bash
# État MySQL
python3 -m automatisation_1 status --type "GPS"

# Fichiers Minio
python3 -c "
from shared.storage import get_storage
for k in get_storage().list_objects('catalogue', prefix='traces_gps/'):
    print(k)
"

# Vérification MySQL ↔ Minio
python3 -c "
from shared.storage.sync import full_sync
report = full_sync(repair=False)
print(report)
"
```

---

## Deployment on DGX Spark

The application is **already deployed and running** on the DGX Spark. Before deploying
anything, read `docs/START_HERE.md` — it explains whether you need to deploy at all.

| Document | Purpose |
|---|---|
| **docs/START_HERE.md** | **Read first** — reuse vs redeploy decision tree |
| docs/INTEGRATION_GUIDE_EN.md | Integration behind a reverse proxy (Caddy / Authelia) |
| docs/DEPLOYMENT_GUIDE_EN.md | Full deployment from scratch |
| docs/CAPACITY_REPORT_EN.md | Concurrent users, optimisation levers, hardware needs |
| docs/FREE_OPTIONS_REPORT_EN.md | LLM provider comparison and architecture recommendation |
| docs/USER_ACCESS_GUIDE_EN.md | End-user access guide |
| docs/BENCHMARK_REPORT.pdf | Local vs cloud inference benchmark |

### Quick facts

| Metric | Value |
|---|---|
| Local LLM throughput (Llama-3.1-8B) | 14 tokens/s |
| Cloud throughput (Groq, same model) | 737 tokens/s |
| Full run (24 data types, hybrid mode) | ~10 h 30 |
| Concurrent analyses | 1 (6-8 after optimisation) |
| Hardware | NVIDIA DGX Spark, GB10, 128 GB unified, 273 GB/s |

### Configuration

Copy `.env.template` to `.env` and fill in the values. The `.env` file is never
committed — it holds API keys and passwords.

> **Docker project name**: all compose commands must use `-p versionete2026-main`,
> otherwise Docker creates empty volumes and existing data appears lost.
