"""
Dashboard Streamlit — Mobility Reporting
Lancement : streamlit run dashboard/streamlit_app.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import streamlit as st

# ── Setup ──────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

st.set_page_config(
    page_title="Mobility Reporting",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.block-container { padding-top: 0.8rem !important; padding-bottom: 0.5rem !important; }

.provider-card {
    background: #1e293b;
    border-radius: 10px;
    padding: 10px 14px;
    margin: 5px 0;
    border-left: 5px solid #334155;
}

.stage-pill {
    text-align: center;
    background: #1e293b;
    border-radius: 10px;
    padding: 10px 6px;
    margin: 2px;
}
.stage-count { font-size: 2rem; font-weight: 800; line-height: 1.1; }
.stage-name  { font-size: 0.62rem; color: #94a3b8; margin-top: 3px; }

.log-terminal {
    background: #0d1117;
    color: #c9d1d9;
    font-family: 'JetBrains Mono', 'Consolas', monospace;
    font-size: 12px;
    padding: 14px;
    border-radius: 8px;
    border: 1px solid #21262d;
    height: 480px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-all;
    line-height: 1.5;
}

.running-badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: #052e16;
    border: 1px solid #166534;
    border-radius: 20px;
    padding: 4px 14px;
    font-size: 13px;
    color: #4ade80;
    font-weight: 600;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.3; }
}
.dot {
    width: 9px; height: 9px;
    background: #22c55e;
    border-radius: 50%;
    display: inline-block;
    animation: pulse 1.4s ease-in-out infinite;
}

.kpi-card {
    background: linear-gradient(135deg, #1e293b, #0f172a);
    border: 1px solid #334155;
    border-radius: 12px;
    padding: 16px 20px;
    text-align: center;
}
.kpi-num   { font-size: 2.4rem; font-weight: 800; line-height: 1.1; }
.kpi-label { font-size: 0.7rem; color: #64748b; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.05em; }

.section-title {
    font-size: 0.8rem;
    font-weight: 600;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 10px;
}

div[data-testid="stHorizontalBlock"] > div { gap: 6px !important; }
</style>
""", unsafe_allow_html=True)


# ── Process Manager (singleton across reruns) ──────────────────────────────────

class ProcessManager:
    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None
        self._name: str = ""
        self._cmd_str: str = ""
        self._log: deque = deque(maxlen=3000)
        self._lock = threading.Lock()
        self._start_time: Optional[float] = None

    def start(self, cmd: List[str], name: str) -> tuple[bool, str]:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return False, "Un processus est déjà en cours d'exécution."
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(PROJECT_ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env,
                )
            except Exception as exc:
                return False, str(exc)
            self._proc = proc
            self._name = name
            self._cmd_str = " ".join(cmd)
            self._start_time = time.time()
            self._log.clear()

        ts = _ts()
        self._log.append(f"[{ts}] ▶  Démarrage : {name}")
        self._log.append(f"[{ts}] $  {self._cmd_str}")

        def _reader(proc=proc):
            try:
                for raw in proc.stdout:
                    self._log.append(raw.rstrip())
            except Exception:
                pass
            proc.wait()
            self._log.append(
                f"[{_ts()}] ⏹  Terminé (code {proc.returncode})"
            )

        threading.Thread(target=_reader, daemon=True).start()
        return True, ""

    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
        self._log.append(f"[{_ts()}] ⛔  Arrêté par l'utilisateur")

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def name(self) -> str:
        return self._name

    @property
    def return_code(self) -> Optional[int]:
        with self._lock:
            return self._proc.returncode if self._proc else None

    @property
    def elapsed(self) -> str:
        if not self._start_time:
            return ""
        s = int(time.time() - self._start_time)
        h, r = divmod(s, 3600)
        m, s = divmod(r, 60)
        return f"{h}h {m:02d}m {s:02d}s" if h else f"{m:02d}m {s:02d}s"

    def get_logs(self, last_n: int = 500) -> List[str]:
        return list(self._log)[-last_n:]


@st.cache_resource
def get_pm() -> ProcessManager:
    return ProcessManager()


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ── Data fetchers ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=6)
def fetch_state():
    try:
        from shared.db.connection import get_connection
        state: Dict[str, int] = {}
        totals = {k: 0 for k in ("types", "problems", "algorithms", "datasets", "notebooks", "reports")}
        conn = get_connection()
        if not conn:
            return state, totals
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT processing_status, COUNT(*) FROM data_types GROUP BY processing_status"
                )
                for st_val, n in cur.fetchall():
                    k = st_val or "pending"
                    state[k] = n
                    totals["types"] += n
                for table, key in [
                    ("problems", "problems"),
                    ("algorithms", "algorithms"),
                    ("datasets", "datasets"),
                    ("notebooks", "notebooks"),
                ]:
                    try:
                        cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                        totals[key] = cur.fetchone()[0] or 0
                    except Exception:
                        pass
        finally:
            conn.close()
        return state, totals
    except Exception:
        return {}, {k: 0 for k in ("types", "problems", "algorithms", "datasets", "notebooks", "reports")}


def fetch_quota() -> Dict:
    try:
        from shared.llm.router import get_session_stats
        return get_session_stats()
    except Exception as exc:
        return {"providers": [], "all_exhausted": False, "error": str(exc)}


# ── Pipeline stages definition ─────────────────────────────────────────────────

STAGES = [
    ("pending",         "En attente",   "#64748b"),
    ("catalogue_done",  "Catalogue ✓",  "#3b82f6"),
    ("problems_done",   "Problèmes ✓",  "#8b5cf6"),
    ("algorithms_done", "Algos ✓",      "#f59e0b"),
    ("datasets_done",   "Datasets ✓",   "#06b6d4"),
    ("notebooks_done",  "Notebooks ✓",  "#10b981"),
    ("report_done",     "Rapports ✓",   "#22c55e"),
]


# ── Command builder ────────────────────────────────────────────────────────────

def build_cmd(automation: str, opts: Dict) -> List[str]:
    py = sys.executable
    if automation == "Auto 1":
        cmd = [py, "-m", "automatisation_1.cli"]
        if opts.get("force"):       cmd.append("--force")
        if opts.get("skip_s1"):     cmd.append("--skip-s1")
        if opts.get("skip_s2"):     cmd.append("--skip-s2")
        if opts.get("skip_s3"):     cmd.append("--skip-s3")
        if opts.get("skip_s4"):     cmd.append("--skip-s4")
        if opts.get("type_filter"): cmd += ["--type", opts["type_filter"]]
        mp = opts.get("max_problems", 0)
        ma = opts.get("max_algos", 0)
        if mp and mp > 0: cmd += ["--max-problems", str(mp)]
        if ma and ma > 0: cmd += ["--max-algos",    str(ma)]
    elif automation == "Auto 2":
        cmd = [py, "-m", "automatisation_2.cli"]
        if opts.get("force"):   cmd.append("--force")
        if opts.get("skip_s1"): cmd.append("--skip-s1")
        if opts.get("skip_s2"): cmd.append("--skip-s2")
        if opts.get("skip_s3"): cmd.append("--skip-s3")
        if opts.get("skip_s4"): cmd.append("--skip-s4")
        if opts.get("skip_s5"): cmd.append("--skip-s5")
    elif automation == "Auto 3":
        cmd = [py, "-m", "automatisation_3.cli"]
        if opts.get("force"): cmd.append("--force")
    elif automation == "Orchestrateur":
        cmd = [py, "orchestrator.py"]
        if opts.get("once"):            cmd.append("--once")
        if opts.get("enrich"):          cmd.append("--enrich")
        if opts.get("skip_auto1"):      cmd.append("--skip-auto1")
        if opts.get("skip_auto2"):      cmd.append("--skip-auto2")
        if opts.get("skip_auto3"):      cmd.append("--skip-auto3")
        if opts.get("skip_refinement"): cmd.append("--skip-refinement")
        if opts.get("dry_run"):         cmd.append("--dry-run")
        cmd += ["--interval", str(opts.get("interval", 120))]
    else:
        return []
    return cmd


# ── HTML helpers ───────────────────────────────────────────────────────────────

def _kpi_html(num: int, label: str, color: str = "#60a5fa") -> str:
    return f"""
    <div class="kpi-card">
        <div class="kpi-num" style="color:{color}">{num:,}</div>
        <div class="kpi-label">{label}</div>
    </div>"""


def _stage_pill_html(count: int, label: str, color: str) -> str:
    active = count > 0
    c = color if active else "#334155"
    tc = color if active else "#475569"
    return f"""
    <div class="stage-pill" style="border-top: 3px solid {c};">
        <div class="stage-count" style="color:{tc}">{count}</div>
        <div class="stage-name">{label}</div>
    </div>"""


def _provider_card_html(p: Dict) -> str:
    if not p["configured"]:
        color, icon, text = "#475569", "○", "Non configuré"
    elif p["blacklisted"]:
        color, icon, text = "#ef4444", "✕", "Quota épuisé"
    else:
        color, icon, text = "#22c55e", "✓", "Disponible"
    reqs = p.get("requests", 0)
    errs = p.get("errors", 0)
    model = p.get("model", "")
    return f"""
    <div class="provider-card" style="border-left-color:{color}; margin-bottom:8px;">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
                <span style="font-size:15px;color:{color};font-weight:700;">{icon}</span>
                <strong style="color:{color};margin-left:6px;">{p['name']}</strong>
            </div>
            <div style="font-size:10px;color:#475569;text-align:right">
                {reqs} req &nbsp;·&nbsp; {errs} err
            </div>
        </div>
        <div style="font-size:11px;color:#475569;padding-left:22px;">{model}</div>
        <div style="font-size:11px;color:{color};padding-left:22px;margin-top:2px;">{text}</div>
    </div>"""


def _colorize_log(lines: List[str]) -> str:
    import html as _html
    result = []
    for line in lines:
        safe = _html.escape(line)   # échappe < > & pour ne pas casser le HTML
        ll = line.lower()
        if any(x in ll for x in ("error", "erreur", "traceback", "exception")):
            result.append(f'<span style="color:#f87171">{safe}</span>')
        elif any(x in ll for x in ("warning", "warn", "⚠")):
            result.append(f'<span style="color:#fbbf24">{safe}</span>')
        elif any(x in line for x in ("✓", "✅", "⏹", "Terminé", "terminé", "OK", "ok =")):
            result.append(f'<span style="color:#4ade80">{safe}</span>')
        elif any(x in line for x in ("▶", "Démarrage", "$ ")):
            result.append(f'<span style="color:#60a5fa">{safe}</span>')
        elif "[INFO]" in line or "INFO" in line:
            result.append(f'<span style="color:#94a3b8">{safe}</span>')
        elif "⛔" in line or "CRITICAL" in line:
            result.append(f'<span style="color:#fb923c;font-weight:bold">{safe}</span>')
        else:
            result.append(f'<span style="color:#cbd5e1">{safe}</span>')
    return "<br>".join(result)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    try:
        _render()
    except Exception as _exc:
        import traceback as _tb
        st.error(f"❌ Erreur dashboard : {_exc}")
        with st.expander("Détails de l'erreur"):
            st.code(_tb.format_exc())


def _render():
    pm = get_pm()
    state, totals = fetch_state()
    quota = fetch_quota()

    # ── SIDEBAR ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown(
            "<div style='font-size:1.3rem;font-weight:800;letter-spacing:-0.5px'>"
            "🚀 Mobility Reporting</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div style='font-size:11px;color:#64748b'>{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</div>",
            unsafe_allow_html=True,
        )
        st.divider()

        # ── Running status ────────────────────────────────────────────────────
        if pm.is_running:
            st.markdown(
                f'<div class="running-badge"><span class="dot"></span>'
                f'{pm.name} &nbsp;·&nbsp; {pm.elapsed}</div>',
                unsafe_allow_html=True,
            )
            if st.button("⏹  ARRÊTER", use_container_width=True, type="primary"):
                pm.stop()
                time.sleep(0.3)
                st.rerun()
        else:
            rc = pm.return_code
            if rc is not None:
                ok = rc == 0
                icon = "✅" if ok else "❌"
                color = "#22c55e" if ok else "#ef4444"
                st.markdown(
                    f"<div style='font-size:13px;color:{color}'>{icon} Terminé "
                    f"(code {rc}) — {pm.name}</div>",
                    unsafe_allow_html=True,
                )

        st.divider()

        # ── LLM Providers ─────────────────────────────────────────────────────
        st.markdown(
            "<div class='section-title'>Providers LLM — Quota</div>",
            unsafe_allow_html=True,
        )

        if quota.get("all_exhausted"):
            st.error("⚠️ **TOUS LES QUOTAS ÉPUISÉS**  \nPause jusqu'au reset minuit.")

        providers = quota.get("providers", [])
        if providers:
            st.markdown(
                "".join(_provider_card_html(p) for p in providers),
                unsafe_allow_html=True,
            )
        else:
            st.warning(quota.get("error", "Impossible de charger le statut LLM"))

        if st.button("🔄 Réinitialiser session", use_container_width=True,
                     help="Efface la blacklist quota (après reset minuit)"):
            try:
                from shared.llm.router import reset_session
                reset_session()
                fetch_state.clear()
                st.success("Session réinitialisée !")
                time.sleep(0.5)
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

        st.divider()

        # ── Auto-refresh ──────────────────────────────────────────────────────
        auto_ref = st.toggle(
            "🔄 Auto-refresh",
            value=pm.is_running,
            key="auto_ref_toggle",
        )
        if auto_ref:
            interval_str = st.selectbox(
                "Intervalle", ["2s", "5s", "10s", "30s"],
                index=1, label_visibility="collapsed",
            )
            _refresh_secs = int(interval_str.rstrip("s"))
        else:
            _refresh_secs = 5

    # ── TABS ──────────────────────────────────────────────────────────────────
    tab_ov, tab_ctrl, tab_logs = st.tabs(["📊  Vue d'ensemble", "🚀  Contrôle", "📋  Logs"])

    # ───────────────────────────────────────────────────────────────────────────
    # TAB 1 — VUE D'ENSEMBLE
    # ───────────────────────────────────────────────────────────────────────────
    with tab_ov:
        # KPI row
        st.markdown("<div class='section-title'>Statistiques globales</div>", unsafe_allow_html=True)

        kpi_data = [
            (totals["types"],       "Types de données", "#60a5fa"),
            (totals["problems"],    "Problèmes",         "#a78bfa"),
            (totals["algorithms"],  "Algorithmes",       "#f59e0b"),
            (totals["datasets"],    "Datasets",          "#06b6d4"),
            (totals["notebooks"],   "Notebooks",         "#10b981"),
            (state.get("report_done", 0), "Rapports PDF", "#22c55e"),
        ]
        kpi_cols = st.columns(6, gap="small")
        for col, (num, label, color) in zip(kpi_cols, kpi_data):
            with col:
                st.markdown(_kpi_html(num, label, color), unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # Pipeline flow
        st.markdown("<div class='section-title'>Pipeline — Statut par étape</div>", unsafe_allow_html=True)
        stage_cols = st.columns(len(STAGES), gap="small")
        for col, (key, label, color) in zip(stage_cols, STAGES):
            with col:
                st.markdown(_stage_pill_html(state.get(key, 0), label, color), unsafe_allow_html=True)

        total_types = max(totals["types"], 1)
        done = state.get("report_done", 0)
        progress_pct = done / total_types
        st.markdown("<br>", unsafe_allow_html=True)
        st.progress(
            progress_pct,
            text=f"Complétion globale : **{done} / {total_types}** types terminés ({progress_pct*100:.0f}%)",
        )

        # Bar chart of pipeline distribution
        if totals["types"] > 0:
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown("<div class='section-title'>Répartition par statut</div>", unsafe_allow_html=True)
            import pandas as pd
            df = pd.DataFrame(
                {label: [state.get(key, 0)] for key, label, _ in STAGES},
                index=["Types"],
            )
            st.bar_chart(df.T, height=220)
        else:
            st.info(
                "⚠️  Aucune donnée en base de données.  \n"
                "Lancez **Auto 1** dans l'onglet Contrôle pour commencer."
            )

    # ───────────────────────────────────────────────────────────────────────────
    # TAB 2 — CONTRÔLE
    # ───────────────────────────────────────────────────────────────────────────
    with tab_ctrl:
        if pm.is_running:
            st.warning(
                f"⚠️  **{pm.name}** est en cours d'exécution.  "
                f"Arrêtez-le depuis la barre latérale avant d'en lancer un autre."
            )

        st.markdown("<div class='section-title'>Sélection de l'automation</div>", unsafe_allow_html=True)
        automation = st.radio(
            "Automation",
            ["Auto 1", "Auto 2", "Auto 3", "Orchestrateur"],
            horizontal=True,
            label_visibility="collapsed",
        )

        st.divider()
        opts: Dict = {}

        # ── Options par automation ─────────────────────────────────────────
        if automation == "Auto 1":
            st.markdown(
                "**Auto 1** — Catalogue Excel → Problèmes → Algorithmes → Datasets (MySQL)"
            )
            c1, c2, c3 = st.columns([1, 1, 1], gap="large")
            with c1:
                st.markdown("**Étapes**")
                opts["skip_s1"] = st.checkbox("Ignorer S1 — Catalogue", value=True,
                                               help="S1 est fait au lancement de l'orchestrateur")
                opts["skip_s2"] = st.checkbox("Ignorer S2 — Problèmes", value=False)
                opts["skip_s3"] = st.checkbox("Ignorer S3 — Algorithmes", value=False)
                opts["skip_s4"] = st.checkbox("Ignorer S4 — Datasets", value=False)
            with c2:
                st.markdown("**Limites**")
                opts["max_problems"] = st.number_input(
                    "Max problèmes / type", min_value=0, value=0,
                    help="0 = sans limite"
                )
                opts["max_algos"] = st.number_input(
                    "Max algos / problème", min_value=0, value=0,
                    help="0 = sans limite"
                )
            with c3:
                st.markdown("**Filtre**")
                tf = st.text_input("Filtrer par type ID", placeholder="ex: gtfs_001")
                opts["type_filter"] = tf.strip()
                opts["force"] = st.checkbox("Forcer la régénération", value=False)

        elif automation == "Auto 2":
            st.markdown(
                "**Auto 2** — Notebooks Jupyter + exécution + figures de comparaison"
            )
            c1, c2 = st.columns([1, 1], gap="large")
            with c1:
                st.markdown("**Étapes**")
                opts["skip_s1"] = st.checkbox("Ignorer S1 — Chargement datasets", value=False)
                opts["skip_s2"] = st.checkbox("Ignorer S2 — Build notebooks", value=False)
                opts["skip_s3"] = st.checkbox("Ignorer S3 — Exécution notebooks", value=False)
                opts["skip_s4"] = st.checkbox("Ignorer S4 — Extraction résultats", value=False)
                opts["skip_s5"] = st.checkbox("Ignorer S5 — Figures comparaison", value=False)
            with c2:
                opts["force"] = st.checkbox("Forcer la régénération", value=False)

        elif automation == "Auto 3":
            st.markdown(
                "**Auto 3** — Génération des rapports PDF (compilation LaTeX)"
            )
            opts["force"] = st.checkbox("Forcer la régénération", value=False)

        elif automation == "Orchestrateur":
            st.markdown(
                "**Orchestrateur** — Pipeline complet Auto 1 → Auto 2 → Auto 3 + enrichissement"
            )
            c1, c2, c3 = st.columns([1, 1, 1], gap="large")
            with c1:
                st.markdown("**Mode**")
                opts["once"] = st.checkbox("Mode --once (une seule passe)", value=True)
                opts["enrich"] = st.checkbox("Enrichissement illimité (--enrich)", value=False)
                opts["dry_run"] = st.checkbox("Dry-run (afficher sans agir)", value=False)
            with c2:
                st.markdown("**Étapes à ignorer**")
                opts["skip_auto1"]      = st.checkbox("Skip Auto 1", value=False)
                opts["skip_auto2"]      = st.checkbox("Skip Auto 2", value=False)
                opts["skip_auto3"]      = st.checkbox("Skip Auto 3", value=False)
                opts["skip_refinement"] = st.checkbox("Skip Raffinement", value=False)
            with c3:
                st.markdown("**Intervalle boucle**")
                opts["interval"] = st.number_input(
                    "Secondes entre deux passes", min_value=30, value=120, step=10
                )

        st.divider()

        # ── Commande prévisualisée ─────────────────────────────────────────
        cmd = build_cmd(automation, opts)
        st.markdown("<div class='section-title'>Commande générée</div>", unsafe_allow_html=True)
        st.code(" ".join(cmd) if cmd else "# (aucune commande)", language="bash")

        # ── Boutons run / stop ─────────────────────────────────────────────
        col_run, col_stop, _ = st.columns([1, 1, 3])
        with col_run:
            if st.button(
                "▶  LANCER", type="primary", use_container_width=True,
                disabled=pm.is_running or not cmd,
            ):
                ok, err = pm.start(cmd, automation)
                if ok:
                    st.success(f"✅ {automation} démarré !")
                    time.sleep(0.4)
                    st.rerun()
                else:
                    st.error(f"Erreur : {err}")
        with col_stop:
            if st.button(
                "⏹  ARRÊTER", use_container_width=True,
                disabled=not pm.is_running,
            ):
                pm.stop()
                time.sleep(0.3)
                st.rerun()

    # ───────────────────────────────────────────────────────────────────────────
    # TAB 3 — LOGS
    # ───────────────────────────────────────────────────────────────────────────
    with tab_logs:
        header_c1, header_c2, header_c3 = st.columns([3, 1, 1])
        with header_c1:
            if pm.is_running:
                st.markdown(
                    f'<div class="running-badge"><span class="dot"></span>'
                    f'{pm.name} &nbsp;·&nbsp; ⏱ {pm.elapsed}</div>',
                    unsafe_allow_html=True,
                )
            else:
                rc = pm.return_code
                if rc is not None:
                    color = "#22c55e" if rc == 0 else "#ef4444"
                    st.markdown(
                        f"<span style='color:{color};font-size:13px'>"
                        f"{'✅' if rc == 0 else '❌'} Terminé (code {rc}) — {pm.name}"
                        f"</span>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        "<span style='color:#475569;font-size:13px'>⏹ Aucun processus</span>",
                        unsafe_allow_html=True,
                    )

        with header_c2:
            n_lines = st.selectbox(
                "Lignes", [100, 300, 500, 1000], index=1,
                label_visibility="collapsed",
            )
        with header_c3:
            if st.button("🗑️ Effacer", use_container_width=True):
                pm._log.clear()
                st.rerun()

        logs = pm.get_logs(last_n=n_lines)
        colored = _colorize_log(logs) if logs else "<span style='color:#475569'>(Aucun log — lancez une automation)</span>"

        st.markdown(
            f'<div class="log-terminal">{colored}</div>',
            unsafe_allow_html=True,
        )

    # ── Auto-refresh ──────────────────────────────────────────────────────────
    if pm.is_running or auto_ref:
        secs = 2 if pm.is_running else _refresh_secs
        time.sleep(secs)
        st.rerun()


import streamlit.runtime as _st_runtime

if _st_runtime.exists():
    # Contexte Streamlit — rendre l'interface
    main()
else:
    # Lancé avec `python3 dashboard/streamlit_app.py` → bootstrap automatique
    import subprocess
    print("Lancement du dashboard sur http://localhost:8501 ...")
    print("Ouvrez votre navigateur sur : http://localhost:8501")
    sys.exit(subprocess.call([
        sys.executable, "-m", "streamlit", "run", __file__,
        "--server.port", "8501",
        "--browser.gatherUsageStats", "false",
    ] + sys.argv[1:]))
