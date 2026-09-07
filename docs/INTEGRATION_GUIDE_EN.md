# Mobimix — Integration Guide for the Portal

**For**: the engineer integrating Mobimix into the multi-app portal (Caddy + Authelia).

**Summary**: the application is **already deployed and running** on the DGX Spark. It is
not something you need to build or install. What is needed is to route your reverse
proxy to it, and to decide how authentication should be handled.

---

## 1. What this application is

Mobimix generates data-quality analysis reports for urban mobility data. A user uploads
an Excel catalogue describing data types (GPS traces, traffic loops, ticketing, GTFS)
and receives PDF reports containing identified quality issues, Python detection
algorithms, synthetic test datasets, and a comparative summary.

The content is produced by a **language model running locally on the DGX GPU** — no data
leaves the machine. This is a deliberate design constraint: mobility data can be
re-identifiable.

---

## 2. Current deployment state

Everything is already running. **Nothing needs to be deployed from scratch.**

| Container | Role | Exposure |
|---|---|---|
| `versionete2026-main-webapp-1` | web interface (Streamlit) | `127.0.0.1:8501` |
| `versionete2026-main-mysql-1` | pipeline state | `0.0.0.0:3306` |
| `versionete2026-main-minio-1` | file storage (PDFs) | `9100` / `9101` |
| `versionete2026-main-vllm-1` | **LLM server on GPU** | `127.0.0.1:8100` |

> ⚠️ **Docker project name**: `versionete2026-main` (historical, kept so existing volumes
> are not lost). Every `docker compose` command must include
> `-p versionete2026-main`, otherwise Docker creates empty volumes and the data appears
> to vanish.

Working directory on the machine: `~/MobimixEte2026-main` (owner: `yzaitouni`).

Useful commands:

```bash
cd ~/MobimixEte2026-main
alias dcp='docker compose -p versionete2026-main -f docker-compose.yml -f docker-compose.spark.yml -f docker-compose.public.yml'

dcp ps                 # status
dcp logs -f webapp     # interface logs
dcp restart webapp     # restart
dcp stop webapp        # cut access immediately
```

---

## 3. The integration point — read this first

The application currently listens on **`127.0.0.1:8501`** (host loopback only). This was
a deliberate security choice: no port is exposed to the internet, and access goes
through an SSH tunnel.

**This will not work as-is behind Caddy.** From inside the `edge-caddy` container,
`127.0.0.1` refers to the Caddy container itself, not the host. Your other apps
(`mobimix`, `tableau`, `helloworld`, `mobility-data`) expose `8080/tcp` without a host
port binding, which means Caddy reaches them over a **shared Docker network**.

Two options to bridge this:

### Option A — attach the webapp to your Caddy network (recommended)

Cleanest and consistent with your existing apps. Add the external network to the webapp
service in `docker-compose.public.yml`:

```yaml
services:
  webapp:
    networks:
      - default
      - <your_caddy_network>

networks:
  <your_caddy_network>:
    external: true
```

Caddy can then reach it at `http://versionete2026-main-webapp-1:8501`. The
`127.0.0.1:8501` port binding can stay (useful for local debugging) or be removed.

### Option B — route through the host gateway

If you prefer not to touch the compose file, Caddy can reach the host via
`host.docker.internal` (requires `extra_hosts: - "host.docker.internal:host-gateway"`
on the Caddy service). Less clean, but no change to the Mobimix side.

**Note**: there is already a container named `mobimix` on your side (port 8080). Watch
out for naming collisions in your Caddy configuration.

---

## 4. Authentication — a decision is needed

The application has its **own password authentication** (`APP_PASSWORD` in `.env`),
built for SSH-tunnel access. Behind Authelia, this becomes redundant.

Three possible approaches:

| Approach | Consequence |
|---|---|
| Keep both | double login — poor UX, but zero code change |
| Authelia only | requires disabling the app's login screen (small code change in `dashboard/public_app.py`) |
| App password only | bypasses your portal's access-level model — not recommended |

**Recommendation**: Authelia only, so Mobimix follows the same access-level model as
your other apps. The change is small — the `_check_auth()` function in
`public_app.py` can be short-circuited when a trusted header is present.

One caveat: the app asks for a **name/email** on login, used to attribute jobs to users
(stored in `data/jobs.json`). If Authelia takes over authentication, that identity
should be forwarded via a header (e.g. `Remote-User`) so job attribution keeps working.

---

## 5. Resource constraints — important

These are not tunable settings; they follow from the hardware.

**One analysis at a time.** The application enforces a lock. Generating one data type
takes **25 to 40 minutes** on the GPU. This is not a bug: throughput is capped by memory
bandwidth (273 GB/s on this machine → ~14 tokens/s).

**Shared GPU.** The DGX also runs `pmai-vllm-chat`, `pmai-vllm-embed` and
`pmai-bench-vllm-second`, which occupy roughly 50 GB of the 128 GB unified memory. The
Mobimix vLLM instance is configured with `--gpu-memory-utilization 0.25` to coexist.

**Upload limits.** 5 MB and 5 rows per file, enforced in `docker-compose.public.yml`
(`MAX_FILE_MB`, `MAX_ROWS`). Lower `MAX_ROWS` to 2 if several users are expected — it
divides the monopolisation time by 2.5.

**Capacity**: viewing and downloading reports supports 20-50 concurrent users without
issue. Only *launching an analysis* is constrained.

---

## 6. What not to do

| Action | Consequence |
|---|---|
| `docker compose down -v` | **erases the database and all generated reports** |
| Omitting `-p versionete2026-main` | Docker uses empty volumes, data appears lost |
| Stopping `pmai-*` containers | those belong to system administration, not this project |
| Exposing port 8100 (vLLM) | an open inference endpoint would be abused within hours |
| Exposing MySQL or MinIO | the web interface is the only intended access path |

The stop/start cycle is safe: `stop`, `down` and `up --build` all preserve data. Only
`down -v` destroys it.

---

## 7. Current state and known limitations

**What works**: the full pipeline has completed on 24 data types, producing 72 PDF
reports available through the interface. Upload, validation, job tracking and download
all function.

**Known limitations**, in order of visibility:

1. **Metrics tables are empty** in the reports (F1, precision, recall). Notebooks
   execute correctly but their results are not propagated to `notebook_results`.
   Structure, analysis and code are complete — only the numerical results are missing.
   Fix pending.
2. **No job queue.** If a second user submits while a job runs, they get "a job is
   already running" and must retry manually. A queue is the most valuable next
   improvement (~1 day of work).
3. **Reports are visible to all logged-in users.** No per-user partitioning. Relevant if
   your portal's access levels imply data separation.
4. **Interrupted runs cannot resume cleanly** — several pipeline stages write to
   ephemeral container paths. Run jobs in a single uninterrupted pass.

---

## 8. Configuration reference

The `.env` file is **not in the repository** (it contains API keys and passwords). Ask
Youness for the values. Key entries:

```dotenv
DB_HOST=mysql                 # Docker service name, not localhost
MINIO_ENDPOINT=minio:9000     # internal container-to-container
APP_PASSWORD=<...>            # web interface password
LLM_ROUTER_ORDER=Groq,NVIDIA  # cloud first, local fallback
NVIDIA_NIM_BASE_URL=http://vllm:8000/v1
NVIDIA_NIM_API_KEY=dummy      # must be non-empty, value ignored by vLLM
LLM_REQUEST_TIMEOUT_SEC=600   # code default is 45 s — far too short locally
```

> **Privacy rule**: `LLM_ROUTER_ORDER=Groq,NVIDIA` sends part of the workload to a free
> cloud tier. Free tiers are funded by user prompts. For **real user data**, switch to
> `LLM_ROUTER_ORDER=NVIDIA` (local only). This switch should be explicit, not left to
> configuration drift.

---

## 9. Repository

`https://github.com/SpOOfy0/Mobimix` — contains the pipeline, Docker configuration, the
web interface (`dashboard/public_app.py`) and the deployment guide.

The original pipeline was developed by the intern on the `MobimixEte2026` repository;
this repository adds the DGX deployment, the web interface and four blocking bug fixes.

---

## 10. Suggested first steps

1. Get the `.env` values from Youness and confirm the app responds:
   `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8501/_stcore/health` → 200
2. Decide on the authentication approach (§4) — this drives whether a code change is needed
3. Connect the webapp to the Caddy network (§3, Option A)
4. Test end to end through the portal: login → report list → download
5. Adjust `MAX_ROWS` to match expected usage

Do **not** launch a full analysis during testing: it occupies the GPU for 25-40 minutes
and blocks other users. Validating upload and download is sufficient.
