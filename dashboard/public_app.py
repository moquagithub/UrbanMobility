"""
Façade publique — Mobimix
==========================
Seule application exposée à l'extérieur. Elle ne permet QUE :
  1. déposer un catalogue Excel (validé avant traitement)
  2. suivre l'avancement du traitement
  3. télécharger les rapports PDF produits

Elle n'expose ni le LLM, ni MySQL, ni MinIO : tout passe par elle.

Lancement :
    streamlit run dashboard/public_app.py --server.port 8501 --server.address 0.0.0.0
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

# ── Chemins (dans le volume ./data monté, donc persistants) ─────────────────
DATA_DIR = Path(os.getenv("MOBIMIX_DATA_DIR", "/app/data"))
UPLOAD_DIR = DATA_DIR / "uploads"
JOBS_FILE = DATA_DIR / "jobs.json"
LOCK_FILE = DATA_DIR / "job.lock"
LOG_DIR = DATA_DIR / "joblogs"

for d in (UPLOAD_DIR, LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ── Garde-fous (protègent le GPU partagé) ──────────────────────────────────
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "5"))
MAX_ROWS = int(os.getenv("MAX_ROWS", "5"))          # ~35 min par ligne !
MIN_PER_ROW = 40                                     # estimation affichée

# Colonnes reconnues (aligné sur automatisation_1/steps/s1_catalogue.py)
NAME_ALIASES = {"nom", "name", "type", "type de donnée", "type de donnee",
                "libellé", "libelle", "intitulé", "intitule"}

st.set_page_config(page_title="Mobimix — Générateur de rapports",
                   page_icon="📊", layout="centered")


# ══════════════════════════════════════════════════════════════════════════
#  Authentification (simple mais réelle)
# ══════════════════════════════════════════════════════════════════════════
def _check_auth() -> bool:
    expected = os.getenv("APP_PASSWORD", "")
    if not expected:
        st.error("APP_PASSWORD n'est pas défini côté serveur. "
                 "L'application refuse de démarrer sans mot de passe.")
        st.stop()

    if st.session_state.get("authed"):
        return True

    st.title("📊 Mobimix")
    st.caption("Génération automatique de rapports d'analyse de données de mobilité")
    pwd = st.text_input("Mot de passe d'accès", type="password")
    who = st.text_input("Votre nom ou email (pour tracer les demandes)")
    if st.button("Se connecter", type="primary"):
        if hashlib.sha256(pwd.encode()).hexdigest() == hashlib.sha256(expected.encode()).hexdigest() and who.strip():
            st.session_state["authed"] = True
            st.session_state["user"] = who.strip()
            st.rerun()
        else:
            st.error("Mot de passe incorrect, ou nom manquant.")
    st.stop()


# ══════════════════════════════════════════════════════════════════════════
#  Persistance des tâches (fichier JSON dans le volume)
# ══════════════════════════════════════════════════════════════════════════
def load_jobs() -> List[Dict]:
    if not JOBS_FILE.exists():
        return []
    try:
        return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_jobs(jobs: List[Dict]) -> None:
    JOBS_FILE.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")


def update_job(job_id: str, **fields) -> None:
    jobs = load_jobs()
    for j in jobs:
        if j["id"] == job_id:
            j.update(fields)
    save_jobs(jobs)


def a_job_is_running() -> bool:
    """Une seule tâche à la fois : le GPU est partagé."""
    if not LOCK_FILE.exists():
        return False
    # Verrou périmé (> 24 h) => on le considère mort
    age_h = (datetime.now().timestamp() - LOCK_FILE.stat().st_mtime) / 3600
    if age_h > 24:
        LOCK_FILE.unlink(missing_ok=True)
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════
#  Validation du fichier déposé (AVANT de lancer 40 min de calcul)
# ══════════════════════════════════════════════════════════════════════════
def validate_excel(file) -> tuple[Optional[pd.DataFrame], List[str], List[str]]:
    """Retourne (dataframe, erreurs, avertissements)."""
    errors: List[str] = []
    warns: List[str] = []

    size_mb = file.size / (1024 * 1024)
    if size_mb > MAX_FILE_MB:
        errors.append(f"Fichier trop volumineux ({size_mb:.1f} Mo, maximum {MAX_FILE_MB} Mo).")
        return None, errors, warns

    try:
        df = pd.read_excel(file, engine="openpyxl")
    except Exception as exc:
        errors.append(f"Fichier Excel illisible : {exc}")
        return None, errors, warns

    if df.empty:
        errors.append("Le fichier ne contient aucune ligne de données.")
        return None, errors, warns

    # Colonne « nom » obligatoire (recherche par alias, insensible à la casse)
    cols_norm = {str(c).strip().lower(): c for c in df.columns}
    name_col = next((cols_norm[a] for a in NAME_ALIASES if a in cols_norm), None)
    if name_col is None:
        errors.append(
            "Aucune colonne de nom trouvée. Le fichier doit contenir une colonne "
            f"nommée par exemple « nom », « type » ou « type de donnée ». "
            f"Colonnes détectées : {', '.join(map(str, df.columns))}"
        )
        return None, errors, warns

    df = df[df[name_col].notna() & (df[name_col].astype(str).str.strip() != "")]
    if df.empty:
        errors.append(f"La colonne « {name_col} » est vide sur toutes les lignes.")
        return None, errors, warns

    if len(df) > MAX_ROWS:
        errors.append(
            f"{len(df)} lignes détectées, maximum autorisé : {MAX_ROWS}. "
            f"Chaque ligne demande ~{MIN_PER_ROW} min de calcul GPU. "
            "Merci de scinder votre catalogue."
        )
        return None, errors, warns

    dups = df[name_col].astype(str).str.strip().duplicated().sum()
    if dups:
        warns.append(f"{dups} nom(s) en double — ils seront fusionnés.")

    if len(df.columns) < 3:
        warns.append("Peu de colonnes descriptives : les rapports seront moins riches. "
                     "Ajoutez description, caractéristiques, sources…")

    return df, errors, warns


# ══════════════════════════════════════════════════════════════════════════
#  Lancement du traitement (en arrière-plan)
# ══════════════════════════════════════════════════════════════════════════
def run_pipeline(job_id: str, xlsx_path: Path) -> None:
    log_path = LOG_DIR / f"{job_id}.log"
    LOCK_FILE.write_text(job_id, encoding="utf-8")
    update_job(job_id, status="running", started_at=datetime.now().isoformat())
    try:
        with log_path.open("w", encoding="utf-8") as log:
            proc = subprocess.run(
                [sys.executable, "orchestrator.py",
                 "--catalogue", str(xlsx_path), "--once"],
                cwd="/app", stdout=log, stderr=subprocess.STDOUT, timeout=24 * 3600,
            )
        update_job(job_id,
                   status="done" if proc.returncode == 0 else "error",
                   finished_at=datetime.now().isoformat(),
                   returncode=proc.returncode)
    except Exception as exc:
        update_job(job_id, status="error", error=str(exc),
                   finished_at=datetime.now().isoformat())
    finally:
        LOCK_FILE.unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════════════
#  Accès aux rapports (via MinIO, jamais exposé directement)
# ══════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=30)
def list_reports() -> List[Dict]:
    try:
        from shared.storage.client import get_storage
        s = get_storage()
        out = []
        for b in s._mc.list_buckets():
            for o in s._mc.list_objects(b.name, recursive=True):
                if o.object_name.endswith(".pdf"):
                    out.append({"bucket": b.name, "key": o.object_name,
                                "size": o.size, "date": o.last_modified})
        return sorted(out, key=lambda x: x["key"])
    except Exception as exc:
        st.warning(f"Stockage indisponible : {exc}")
        return []


def fetch_report(bucket: str, key: str) -> Optional[bytes]:
    try:
        from shared.storage.client import get_storage
        return get_storage()._mc.get_object(bucket, key).read()
    except Exception as exc:
        st.error(f"Téléchargement impossible : {exc}")
        return None


@st.cache_data(ttl=10)
def pipeline_progress() -> Dict[str, int]:
    """Avancement lu en base — l'utilisateur ne voit que des compteurs."""
    try:
        from shared.db.connection import get_connection
        conn = get_connection()
        if not conn:
            return {}
        with conn.cursor() as cur:
            cur.execute("SELECT processing_status, COUNT(*) FROM data_types "
                        "GROUP BY processing_status")
            return {r[0]: r[1] for r in cur.fetchall()}
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════════
#  Interface
# ══════════════════════════════════════════════════════════════════════════
_check_auth()

st.title("📊 Mobimix")
st.caption(f"Connecté en tant que **{st.session_state['user']}**")

tab_new, tab_jobs, tab_reports = st.tabs(
    ["📤 Nouvelle demande", "⏳ Mes traitements", "📄 Rapports"])

# ── Onglet 1 : dépôt ───────────────────────────────────────────────────────
with tab_new:
    st.subheader("Déposer un catalogue")
    st.markdown(
        f"Fichier Excel (`.xlsx`) — une ligne par type de donnée, "
        f"**{MAX_ROWS} lignes maximum**. Une colonne de nom est obligatoire "
        "(« nom », « type », « type de donnée »…). Les colonnes description, "
        "caractéristiques et sources améliorent la qualité des rapports."
    )

    up = st.file_uploader("Catalogue", type=["xlsx"], label_visibility="collapsed")

    if up is not None:
        df, errors, warns = validate_excel(up)

        for e in errors:
            st.error(e)
        for w in warns:
            st.warning(w)

        if df is not None and not errors:
            st.success(f"Fichier valide — **{len(df)} type(s)** détecté(s).")
            st.dataframe(df.head(10), use_container_width=True)

            eta = len(df) * MIN_PER_ROW
            st.info(f"⏱️ Durée estimée : **~{eta} minutes** "
                    f"({eta // 60}h{eta % 60:02d}). Le traitement se poursuit même "
                    "si vous fermez cette page.")

            if a_job_is_running():
                st.warning("Un traitement est déjà en cours sur le serveur. "
                           "Réessayez lorsqu'il sera terminé (onglet « Mes traitements »).")
            elif st.button("🚀 Lancer le traitement", type="primary"):
                job_id = uuid.uuid4().hex[:8]
                dest = UPLOAD_DIR / f"{job_id}.xlsx"
                up.seek(0)
                dest.write_bytes(up.read())

                jobs = load_jobs()
                jobs.insert(0, {
                    "id": job_id, "user": st.session_state["user"],
                    "filename": up.name, "rows": len(df),
                    "status": "queued", "created_at": datetime.now().isoformat(),
                })
                save_jobs(jobs)

                threading.Thread(target=run_pipeline, args=(job_id, dest),
                                 daemon=True).start()
                st.success(f"Traitement lancé — référence **{job_id}**. "
                           "Suivez l'avancement dans « Mes traitements ».")
                st.balloons()

# ── Onglet 2 : suivi ───────────────────────────────────────────────────────
with tab_jobs:
    st.subheader("Traitements")
    if st.button("🔄 Actualiser"):
        st.cache_data.clear()
        st.rerun()

    jobs = [j for j in load_jobs() if j["user"] == st.session_state["user"]]
    if not jobs:
        st.info("Aucune demande pour l'instant.")
    else:
        icons = {"queued": "⏸️ En attente", "running": "⚙️ En cours",
                 "done": "✅ Terminé", "error": "❌ Erreur"}
        for j in jobs[:20]:
            with st.container(border=True):
                c1, c2, c3 = st.columns([3, 2, 2])
                c1.markdown(f"**{j['filename']}**  \n`{j['id']}`")
                c2.markdown(f"{j['rows']} type(s)  \n{j['created_at'][:16].replace('T', ' ')}")
                c3.markdown(icons.get(j["status"], j["status"]))

    prog = pipeline_progress()
    if prog:
        st.markdown("##### Avancement global du pipeline")
        labels = {"pending": "En attente", "catalogue_done": "Catalogue lu",
                  "problems_done": "Problèmes identifiés",
                  "algorithms_done": "Algorithmes générés",
                  "datasets_done": "Jeux de données créés",
                  "notebooks_done": "Analyses exécutées",
                  "report_done": "Rapport disponible"}
        cols = st.columns(min(len(prog), 4))
        for i, (k, v) in enumerate(prog.items()):
            cols[i % len(cols)].metric(labels.get(k, k), v)

# ── Onglet 3 : rapports ────────────────────────────────────────────────────
with tab_reports:
    st.subheader("Rapports disponibles")
    reports = list_reports()

    if not reports:
        st.info("Aucun rapport disponible pour le moment.")
    else:
        st.caption(f"{len(reports)} rapport(s)")
        q = st.text_input("Filtrer", placeholder="ex. traces_gps")
        shown = [r for r in reports if q.lower() in r["key"].lower()] if q else reports

        for r in shown[:60]:
            parts = r["key"].split("/")
            nice = parts[0].replace("_", " ").title()
            sub = "/".join(parts[1:])
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"**{nice}**  \n<small>{sub} — {r['size'] // 1024} Ko</small>",
                        unsafe_allow_html=True)
            if c2.button("Télécharger", key=r["key"]):
                data = fetch_report(r["bucket"], r["key"])
                if data:
                    c2.download_button("💾 Enregistrer", data,
                                       file_name=f"{parts[0]}_{parts[-2]}.pdf",
                                       mime="application/pdf",
                                       key=f"dl_{r['key']}")
