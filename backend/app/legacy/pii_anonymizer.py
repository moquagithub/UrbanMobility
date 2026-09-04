"""
pii_anonymizer.py — Anonymisation des valeurs PII pour eda_analyse.py
======================================================================
Remplace load_and_anonymize() de eda_analyse.py par une version qui
anonymise également le CONTENU des cellules, pas seulement les en-têtes.

Stratégies par type de PII :
  - Identifiants (ID, numéros) → pseudonymisation SHA-256 tronquée
  - Adresses complètes         → généralisation (commune + code postal)
  - Coordonnées GPS            → arrondi à 2 décimales (~1 km)
  - Emails / téléphones        → masquage [MASKED]
  - Dates précises             → généralisation mois+année (YYYY-MM)
  - Texte libre                → suppression des entités nommées (regex)

Usage :
  from pii_anonymizer import load_and_anonymize_v2, PIIAnonymizer
  df, anon_map, value_log, err_logger = load_and_anonymize_v2("data.csv")
"""

import re
import hashlib
import logging
import os
import json
from datetime import datetime
from typing import Optional

import pandas as pd
import numpy as np


# ─────────────────────────────────────────────
# DÉTECTION DES TYPES PII
# ─────────────────────────────────────────────

# Patterns regex pour la détection automatique de PII dans les noms de colonnes
PII_COLUMN_PATTERNS = {
    "email": re.compile(
        r"(email|mail|courriel|e-mail)", re.IGNORECASE
    ),
    "phone": re.compile(
        r"(tel|phone|mobile|portable|fax|gsm|sms)", re.IGNORECASE
    ),
    "address": re.compile(
        r"(adresse|address|rue|street|avenue|boulevard|domicile|travail|lieu)", re.IGNORECASE
    ),
    "gps_lat": re.compile(
        r"(lat|latitude)", re.IGNORECASE
    ),
    "gps_lon": re.compile(
        r"(lon|long|longitude)", re.IGNORECASE
    ),
    # "name" DOIT être avant "id_direct" : "nom" est un préfixe de "numero",
    # et "^no" dans id_direct matcherait "nom" si id_direct passait en premier.
    "name": re.compile(
        r"(nom|prenom|name|firstname|lastname|surname|appelant|personne)", re.IGNORECASE
    ),
    "date_precise": re.compile(
        r"(date|heure|horaire|timestamp|time|datetime|created|updated)", re.IGNORECASE
    ),
    "free_text": re.compile(
        r"(commentaire|comment|note|texte|text|description|observation|remarque)", re.IGNORECASE
    ),
    "postcode": re.compile(
        r"(postal|postcode|zip|code.?postal|cp)", re.IGNORECASE
    ),
    "city": re.compile(
        r"(ville|city|commune|municipality|localite)", re.IGNORECASE
    ),
    # "id_direct" EN DERNIER parmi les patterns par nom : son regex ^(id|...|no)
    # est très large et doit céder la priorité aux types plus spécifiques ci-dessus.
    "id_direct": re.compile(
        r"^(id|identifiant|identifier|uuid|ref|numero|num)[\s_-]?",
        re.IGNORECASE
    ),
}

# Patterns regex pour détecter PII dans les VALEURS
EMAIL_VALUE_RE    = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_VALUE_RE    = re.compile(r"(\+?\d[\d\s\-\.\(\)]{7,}\d)")
GPS_VALUE_RE      = re.compile(r"^-?\d{1,3}\.\d{4,}$")
DATE_VALUE_RE     = re.compile(
    r"\b(\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4})\b"
)
POSTCODE_VALUE_RE = re.compile(r"\b\d{5}\b")  # codes postaux FR à 5 chiffres

# Noms propres dans texte libre : on cible les séquences Prénom Nom (2 mots maj + min)
# et on exclut les mots courants français en début de phrase.
# Le pattern exige au moins 2 tokens Majuscule+minuscules consécutifs pour réduire
# les faux positifs sur les mots en début de phrase.
NAME_ENTITY_RE = re.compile(
    r"\b([A-ZÉÈÊÀÂÙÔÎÏ][a-zéèêëàâùûüôîïç]{3,}(?:\s+[A-ZÉÈÊÀÂÙÔÎÏ][a-zéèêëàâùûüôîïç]{3,})+)\b"
)


# ─────────────────────────────────────────────
# CLASSE PRINCIPALE
# ─────────────────────────────────────────────

class PIIAnonymizer:
    """
    Détecte et anonymise les données personnelles (PII) dans un DataFrame.

    Attributs publics après fit() :
        column_types   : dict {col_anon -> type_pii détecté}
        value_log      : dict {col_anon -> {méthode, nb_valeurs_traitées}}
        pseudonym_table: dict {valeur_originale -> pseudonyme}  ← CONFIDENTIEL
    """

    def __init__(self, salt: str = "eda_anon_2025", log_path: Optional[str] = None):
        """
        Args:
            salt      : sel cryptographique pour la pseudonymisation (à conserver secret).
            log_path  : chemin du fichier JSON de log de stratégie (None = pas de fichier).
        """
        self.salt = salt
        self.log_path = log_path
        self.column_types: dict[str, str] = {}
        self.value_log: dict[str, dict] = {}
        self.pseudonym_table: dict[str, str] = {}   # ← NE PAS COMMITTER

    # ── Détection du type PII d'une colonne ──────────────────────────────────

    def _detect_pii_type(self, col_original: str, col_anon: str, series: pd.Series) -> str:
        """
        Détermine le type PII d'une colonne en combinant :
          1. Le nom de colonne original (patterns regex)
          2. Un échantillon de valeurs (heuristiques sur le contenu)
        Retourne : 'email' | 'phone' | 'address' | 'gps_lat' | 'gps_lon' |
                   'id_direct' | 'name' | 'date_precise' | 'free_text' |
                   'postcode' | 'city' | 'numeric_id' | 'none'
        """
        col_lower = col_original.lower()

        # 1. Correspondance par nom de colonne
        for pii_type, pattern in PII_COLUMN_PATTERNS.items():
            if pattern.search(col_lower):
                return pii_type

        # 2. Heuristiques sur les valeurs (échantillon 100 premières non-null)
        sample = series.dropna().astype(str).head(100)
        if sample.empty:
            return "none"

        # Détecter emails dans les valeurs
        if sample.apply(lambda v: bool(EMAIL_VALUE_RE.search(v))).mean() > 0.3:
            return "email"

        # Détecter téléphones
        if sample.apply(lambda v: bool(PHONE_VALUE_RE.search(v))).mean() > 0.3:
            return "phone"

        # Détecter coordonnées GPS (valeurs numériques avec 4+ décimales significatives)
        if pd.api.types.is_numeric_dtype(series):
            vals = series.dropna()
            if not vals.empty and len(vals) >= 2:
                # Exiger que les valeurs aient au moins 4 décimales (pas juste 12.5)
                has_many_decimals = vals.apply(
                    lambda v: len(str(float(v)).rstrip('0').split('.')[-1]) >= 4
                ).mean() > 0.7
                if has_many_decimals:
                    if vals.between(-90, 90).all():
                        return "gps_lat"
                    if vals.between(-180, 180).all():
                        return "gps_lon"

        # Détecter codes postaux (5 chiffres)
        if sample.apply(lambda v: bool(POSTCODE_VALUE_RE.fullmatch(v.strip()))).mean() > 0.5:
            return "postcode"

        # Détecter identifiants numériques (tous uniques, numériques, et assez de lignes
        # pour que l'unicité soit significative — évite les faux positifs sur petits datasets)
        if pd.api.types.is_numeric_dtype(series):
            if len(series) >= 20 and series.nunique() / max(len(series), 1) > 0.95:
                return "numeric_id"

        return "none"

    # ── Méthodes d'anonymisation par type ────────────────────────────────────

    def _pseudonymize(self, value: str) -> str:
        """Hash SHA-256 tronqué à 10 chars. Déterministe (même salt → même pseudo)."""
        if pd.isna(value) or str(value).strip() == "":
            return value
        key = f"{self.salt}::{str(value)}"
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:10].upper()
        pseudo = f"ID_{h}"
        self.pseudonym_table[str(value)] = pseudo
        return pseudo

    def _mask(self, value) -> str:
        """Remplace la valeur par [MASKED]."""
        if pd.isna(value):
            return value
        return "[MASKED]"

    def _generalize_address(self, value) -> str:
        """
        Extrait le code postal et la commune d'une adresse.
        Retourne 'CP XXXXX' si code postal trouvé, sinon '[ADDR_MASKED]'.
        """
        if pd.isna(value):
            return value
        s = str(value)
        # Chercher code postal FR (5 chiffres)
        m = POSTCODE_VALUE_RE.search(s)
        if m:
            cp = m.group(0)
            # Chercher une ville après le code postal
            rest = s[m.end():].strip().split()[0:2] if m.end() < len(s) else []
            city_part = " ".join(rest) if rest else ""
            return f"CP {cp}" + (f" {city_part}" if city_part else "")
        return "[ADDR_MASKED]"

    def _round_gps(self, value, decimals: int = 2):
        """Arrondit une coordonnée GPS à `decimals` décimales (~1 km à 2 déc.)."""
        if pd.isna(value):
            return value
        try:
            return round(float(value), decimals)
        except (ValueError, TypeError):
            return value

    def _generalize_date(self, value) -> str:
        """
        Généralise une date précise en YYYY-MM (supprime le jour et l'heure).
        Essaie plusieurs formats courants du plus précis au moins précis.
        """
        if pd.isna(value):
            return value
        s = str(value).strip()
        # Ordre important : formats longs en premier pour éviter les faux-positifs
        for fmt in (
            "%Y-%m-%d %H:%M:%S",  # 2024-03-15 08:32:11
            "%Y-%m-%d %H:%M",     # 2024-03-15 08:32
            "%d/%m/%Y %H:%M:%S",  # 15/03/2024 08:32:11
            "%d/%m/%Y %H:%M",     # 15/03/2024 08:32
            "%Y-%m-%d",           # 2024-03-15
            "%d/%m/%Y",           # 15/03/2024
            "%d-%m-%Y",           # 15-03-2024
            "%Y/%m/%d",           # 2024/03/15
            "%m/%d/%Y",           # 03/15/2024  (format US)
        ):
            try:
                # strptime doit consommer TOUTE la chaîne (pas de troncature)
                dt = datetime.strptime(s, fmt)
                return dt.strftime("%Y-%m")
            except ValueError:
                continue
        # Fallback regex : extraire YYYY-MM ou DD/MM/YYYY depuis une chaîne mixte
        m = re.search(r"(\d{4})[-/](\d{2})[-/]\d{2}", s)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
        m = re.search(r"(\d{2})[/-](\d{2})[/-](\d{4})", s)
        if m:
            return f"{m.group(3)}-{m.group(2)}"
        return "[DATE_MASKED]"

    def _anonymize_free_text(self, value) -> str:
        """
        Supprime les entités PII d'un texte libre :
          - emails → [EMAIL]
          - téléphones → [PHONE]
          - noms propres probables → [NAME]
          - codes postaux → [CP]
        """
        if pd.isna(value):
            return value
        s = str(value)
        s = EMAIL_VALUE_RE.sub("[EMAIL]", s)
        s = PHONE_VALUE_RE.sub("[PHONE]", s)
        s = POSTCODE_VALUE_RE.sub("[CP]", s)
        # Noms propres heuristique (prudent : seulement si isolés)
        s = NAME_ENTITY_RE.sub(
            lambda m: "[NAME]" if len(m.group(0)) > 3 else m.group(0), s
        )
        return s

    # ── Application par colonne ───────────────────────────────────────────────

    def _anonymize_column(
        self, series: pd.Series, pii_type: str, col_anon: str
    ) -> pd.Series:
        """Applique la méthode d'anonymisation adaptée au type PII."""
        n_before = series.notna().sum()

        if pii_type in ("email", "phone"):
            result = series.apply(self._mask)
            method = "masquage [MASKED]"

        elif pii_type in ("id_direct", "name", "numeric_id"):
            result = series.apply(
                lambda v: self._pseudonymize(str(v)) if not pd.isna(v) else v
            )
            method = "pseudonymisation SHA-256"

        elif pii_type == "address":
            result = series.apply(self._generalize_address)
            method = "généralisation → code postal"

        elif pii_type in ("gps_lat", "gps_lon"):
            result = series.apply(self._round_gps)
            method = "arrondi GPS à 2 décimales (~1 km)"

        elif pii_type == "date_precise":
            result = series.apply(self._generalize_date)
            method = "généralisation → YYYY-MM"

        elif pii_type == "free_text":
            result = series.apply(self._anonymize_free_text)
            method = "suppression entités nommées (regex)"

        elif pii_type in ("postcode", "city"):
            # Code postal et ville : on garde — déjà agrégés, non réidentifiants seuls
            result = series.copy()
            method = "conservé (agrégé, non réidentifiant seul)"

        else:  # "none"
            result = series.copy()
            method = "aucun traitement (pas de PII détecté)"

        n_after = result.notna().sum()
        self.value_log[col_anon] = {
            "pii_type":      pii_type,
            "methode":       method,
            "nb_valeurs":    int(n_before),
            "nb_traitees":   int(n_before - (result == series).sum())
                              if pii_type != "none" else 0,
        }
        return result

    # ── Interface principale ──────────────────────────────────────────────────

    def fit_transform(
        self,
        df: pd.DataFrame,
        anon_map: dict,
        forced_types: Optional[dict] = None,
    ) -> pd.DataFrame:
        """
        Détecte les PII et anonymise toutes les colonnes du DataFrame.

        Args:
            df          : DataFrame avec colonnes déjà renommées VAR_XX.
            anon_map    : {nom_original -> VAR_XX} pour la détection par nom.
            forced_types: dict optionnel {VAR_XX -> type_pii} pour forcer
                          manuellement le type d'une colonne (surcharge détection auto).
        Returns:
            DataFrame anonymisé (copie).
        """
        reverse_map = {v: k for k, v in anon_map.items()}
        df_out = df.copy()
        forced_types = forced_types or {}

        print("\n  🔍 Détection et anonymisation des PII dans les valeurs...")
        detected = {}

        for col_anon in df.columns:
            col_original = reverse_map.get(col_anon, col_anon)

            # Type forcé manuellement ?
            if col_anon in forced_types:
                pii_type = forced_types[col_anon]
            else:
                pii_type = self._detect_pii_type(col_original, col_anon, df[col_anon])

            self.column_types[col_anon] = pii_type
            detected[col_anon] = pii_type

            if pii_type != "none":
                df_out[col_anon] = self._anonymize_column(df[col_anon], pii_type, col_anon)
                print(f"    ✅ {col_anon} ({col_original[:30]}) → {pii_type} : {self.value_log[col_anon]['methode']}")
            else:
                self.value_log[col_anon] = {
                    "pii_type": "none",
                    "methode": "aucun traitement",
                    "nb_valeurs": int(df[col_anon].notna().sum()),
                    "nb_traitees": 0,
                }

        n_pii = sum(1 for t in detected.values() if t != "none")
        print(f"\n  📊 {n_pii} colonne(s) avec PII traitée(s) sur {len(df.columns)} au total.")

        # Écrire le log de stratégie
        if self.log_path:
            self._write_strategy_log()

        return df_out

    def _write_strategy_log(self):
        """Sauvegarde la stratégie d'anonymisation en JSON (lisible, auditable)."""
        os.makedirs(os.path.dirname(os.path.abspath(self.log_path)), exist_ok=True)
        log_data = {
            "date": datetime.now().isoformat(),
            "colonnes": self.value_log,
            "resume": {
                "total_colonnes": len(self.value_log),
                "colonnes_pii": sum(1 for v in self.value_log.values() if v["pii_type"] != "none"),
                "valeurs_traitees": sum(v["nb_traitees"] for v in self.value_log.values()),
            },
        }
        with open(self.log_path, "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
        print(f"  📋 Stratégie d'anonymisation écrite : {self.log_path}")

    def save_pseudonym_table(self, path: str):
        """
        Sauvegarde la table pseudonyme → valeur originale.
        ⚠️  CONFIDENTIEL — à exclure de Git (.gitignore) !
        """
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.pseudonym_table, f, ensure_ascii=False, indent=2)
        print(f"  🔑 Table de pseudonymisation sauvegardée : {path}")
        print(f"     ⚠️  Ce fichier est CONFIDENTIEL — ajoutez-le à .gitignore !")

    def to_markdown_doc(self, anon_map: dict) -> str:
        """
        Génère la documentation Markdown de la stratégie d'anonymisation.
        Peut être incluse dans le rapport ou le README du projet.
        """
        reverse_map = {v: k for k, v in anon_map.items()}
        lines = [
            "# Stratégie d'Anonymisation — Documentation",
            "",
            f"**Date** : {datetime.now().strftime('%d/%m/%Y %H:%M')}  ",
            f"**Colonnes analysées** : {len(self.value_log)}  ",
            f"**Colonnes PII traitées** : {sum(1 for v in self.value_log.values() if v['pii_type'] != 'none')}",
            "",
            "## Méthodes appliquées par colonne",
            "",
            "| Variable anonymisée | Nom original | Type PII | Méthode | Valeurs traitées |",
            "|---|---|---|---|---|",
        ]
        for col_anon, info in self.value_log.items():
            col_orig = reverse_map.get(col_anon, col_anon)
            lines.append(
                f"| `{col_anon}` | `{col_orig}` | {info['pii_type']} "
                f"| {info['methode']} | {info['nb_traitees']} |"
            )
        lines += [
            "",
            "## Méthodes détaillées",
            "",
            "| Type PII | Méthode | Utilité analytique préservée |",
            "|---|---|---|",
            "| `id_direct`, `name`, `numeric_id` | Pseudonymisation SHA-256 (10 chars) | Liaison inter-tables via pseudonyme |",
            "| `email`, `phone` | Masquage → `[MASKED]` | Non applicable |",
            "| `address` | Généralisation → code postal | Analyse géographique agrégée |",
            "| `gps_lat`, `gps_lon` | Arrondi 2 décimales (~1 km) | Clustering spatial |",
            "| `date_precise` | Généralisation → YYYY-MM | Analyse temporelle mensuelle |",
            "| `free_text` | Suppression entités nommées (regex) | Contenu sémantique non-PII |",
            "| `postcode`, `city` | Conservé | Agrégé, non réidentifiant seul |",
            "",
            "## Table de pseudonymisation",
            "",
            "> ⚠️ La table de correspondance pseudonyme ↔ valeur originale est stockée",
            "> dans `anonymization_keys/pseudonym_table.json`.",
            "> **Ce fichier est confidentiel et doit être exclu de Git** (`.gitignore`).",
            "",
            "## Critères de validation RGPD",
            "",
            "- ☑ Aucune adresse complète dans les CSV transformés",
            "- ☑ Aucun email / téléphone en clair",
            "- ☑ Coordonnées GPS arrondies à ~1 km",
            "- ☑ Identifiants pseudonymisés (non réversibles sans la clé)",
            "- ☑ Dates généralisées au mois (pas de timestamp précis)",
        ]
        return "\n".join(lines)


# ─────────────────────────────────────────────
# FONCTION DE REMPLACEMENT load_and_anonymize()
# ─────────────────────────────────────────────

def load_and_anonymize_v2(
    csv_path: str,
    forced_pii_types: Optional[dict] = None,
    salt: str = "eda_anon_2025",
    keys_dir: Optional[str] = None,
):
    """
    Remplace load_and_anonymize() de eda_analyse.py.
    Anonymise à la fois les noms de colonnes ET le contenu des cellules.

    Args:
        csv_path        : chemin vers le fichier CSV source.
        forced_pii_types: dict optionnel {nom_original -> type_pii} pour forcer
                          manuellement le type d'une colonne.
                          Exemple : {"ID_ENQUETE": "id_direct", "ADRESSE_DOM": "address"}
        salt            : sel cryptographique (changer en production !).
        keys_dir        : répertoire pour sauvegarder la table de pseudonymisation.
                          None = pas de sauvegarde.

    Returns:
        df_anon    : DataFrame anonymisé (colonnes + valeurs)
        anon_map   : dict {nom_original -> VAR_XX}
        anonymizer : instance PIIAnonymizer (contient value_log, column_types)
        err_logger : instance ErrorLogger (compatible avec eda_analyse.py)
    """
    # Import des dépendances de eda_analyse.py
    import csv as csv_module
    import warnings
    from eda_analyse import ErrorLogger  # type: ignore

    warnings.filterwarnings("ignore")

    error_logger = ErrorLogger(csv_path)

    if not os.path.exists(csv_path):
        raise ValueError(f"Fichier introuvable : {csv_path}")

    # ── Détection du séparateur ──────────────────────────────────────────────
    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        sample = f.read(65536)
    if not sample.strip():
        raise ValueError("Le fichier CSV est vide.")

    candidates = [";", ",", "\t", "|"]
    lines_sample = [l for l in sample.splitlines() if l.strip()]
    ref_line = lines_sample[1] if len(lines_sample) > 1 else lines_sample[0]
    counts = {c: ref_line.count(c) for c in candidates}
    sep = max(counts, key=counts.get)
    if counts[sep] == 0:
        try:
            dialect = csv_module.Sniffer().sniff(sample[:4096], delimiters=";,\t|")
            sep = dialect.delimiter
        except csv_module.Error:
            sep = ","
    print(f"  📌 Séparateur détecté : '{sep}'")

    # ── Chargement ───────────────────────────────────────────────────────────
    try:
        df = pd.read_csv(
            csv_path, sep=sep, encoding="utf-8", encoding_errors="replace",
            on_bad_lines="warn", low_memory=False,
        )
    except TypeError:
        df = pd.read_csv(
            csv_path, sep=sep, encoding="utf-8",
            error_bad_lines=False, warn_bad_lines=True, low_memory=False,
        )

    if df.empty:
        raise ValueError(f"Aucune ligne valide chargée (sep='{sep}').")

    total_data_lines = max(len(lines_sample) - 1, 1)
    skipped = max(total_data_lines - len(df), 0)
    error_logger.finalize(total_data_lines, skipped)
    print(f"  ✅ {len(df)} lignes × {len(df.columns)} colonnes chargées")

    # ── Anonymisation des noms de colonnes ───────────────────────────────────
    original_cols = list(df.columns)
    anon_map = {col: f"VAR_{i+1:02d}" for i, col in enumerate(original_cols)}
    df.rename(columns=anon_map, inplace=True)

    # ── Conversion des forced_pii_types (noms originaux → VAR_XX) ────────────
    forced_anon = {}
    if forced_pii_types:
        for orig_name, pii_type in forced_pii_types.items():
            if orig_name in anon_map:
                forced_anon[anon_map[orig_name]] = pii_type
            else:
                print(f"  ⚠️  Colonne forcée introuvable : '{orig_name}' (ignorée)")

    # ── Anonymisation des valeurs ─────────────────────────────────────────────
    log_path = None
    if keys_dir:
        os.makedirs(keys_dir, exist_ok=True)
        log_path = os.path.join(keys_dir, "anonymization_strategy.json")

    anonymizer = PIIAnonymizer(salt=salt, log_path=log_path)
    df_anon = anonymizer.fit_transform(df, anon_map, forced_types=forced_anon)

    # ── Sauvegarde table de pseudonymisation ─────────────────────────────────
    if keys_dir and anonymizer.pseudonym_table:
        pseudo_path = os.path.join(keys_dir, "pseudonym_table.json")
        anonymizer.save_pseudonym_table(pseudo_path)

    # ── Patch de compatibilité Windows : ajouter close() sur ErrorLogger ─────
    # ErrorLogger (défini dans eda_analyse.py) ne possède pas de méthode close().
    # Sur Windows, le FileHandler de logging garde logfile-errors.log verrouillé
    # tant qu'il n'est pas explicitement fermé, ce qui empêche shutil.rmtree
    # (utilisé par TemporaryDirectory et les tests) de nettoyer le dossier.
    # On injecte close() dynamiquement pour ne pas modifier eda_analyse.py.
    if not hasattr(error_logger, "close"):
        def _close_logger(self=error_logger):
            for handler in list(self.logger.handlers):
                try:
                    handler.close()
                except Exception:
                    pass
                self.logger.removeHandler(handler)
        import types
        error_logger.close = types.MethodType(lambda self: _close_logger(), error_logger)

    return df_anon, anon_map, anonymizer, error_logger