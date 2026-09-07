# Mobimix Deployment — Complete Guide

Standalone guide to deploy the Mobimix pipeline and its web interface on the
**DGX Spark**. Follow the sections in order; each step includes a verification.

> **What you will get**: a pipeline that automatically generates data-quality analysis
> reports for urban mobility data using a locally hosted LLM on GPU, plus a web
> interface where users can upload a file and download the resulting PDFs — without
> exposing the LLM, the database, or the object storage.

---

## Table of contents

1. [What you need](#1-what-you-need)
2. [Understanding the architecture](#2-understanding-the-architecture)
3. [Pre-flight checks](#3-pre-flight-checks)
4. [Installation](#4-installation)
5. [Starting the infrastructure](#5-starting-the-infrastructure)
6. [Deploying the web interface](#6-deploying-the-web-interface)
7. [Final verification](#7-final-verification)
8. [Day-to-day operation](#8-day-to-day-operation)
9. [Troubleshooting](#9-troubleshooting)
10. [Known issues and limitations](#10-known-issues-and-limitations)

---

## 1. What you need

### Provided files

| File | Destination | Purpose |
|---|---|---|
| `docker-compose.yml` | project root | MySQL + MinIO + migrations + pipeline |
| `docker-compose.spark.yml` | project root | local LLM server (vLLM on GPU) |
| `docker-compose.public.yml` | project root | public web interface |
| `public_app.py` | `dashboard/` | web interface source code |
| `run_migrations.py` | project root | applies SQL migrations |
| `benchmark_compare.py` | project root | *(optional)* performance comparison |
| `env.spark.example` | project root, rename to `.env` | configuration |

The application code itself comes from the `MobimixEte2026` Git repository.

### Required access

- SSH account on `dgxspark2.joyria.net` with a key
- membership in the `docker` group (no `sudo` needed)
- a HuggingFace read token (free — to download the model)
- *(optional)* a Groq API key (free) to speed things up via the cloud

---

## 2. Understanding the architecture

Five containers. **Only one is exposed.**

| Container | Exposed? | Role |
|---|---|---|
| `mysql` | no | pipeline state, measurements |
| `minio` | no | stores produced files (PDFs, notebooks, datasets) |
| `vllm` | **no** | hosts the LLM on GPU |
| `pipeline` | no | orchestrator (run on demand) |
| `webapp` | **yes** (localhost only) | interface: upload, tracking, download |

End users can do exactly three things: upload a validated Excel file, track progress,
and download a PDF. The LLM and the database are unreachable from outside.

**The pipeline in 6 stages**: Excel catalogue → problems → Python algorithms →
synthetic datasets → executed notebooks → LaTeX PDF report.

---

## 3. Pre-flight checks

Log in and verify. **Do not proceed unless everything passes.**

```bash
ssh -i <your_key> <your_login>@dgxspark2.joyria.net

# System
cat /etc/os-release          # Ubuntu 24.04, aarch64
groups | grep -q docker && echo "docker group OK" || echo "PROBLEM"
df -h /                      # at least 100 GB free

# Docker
docker --version             # >= 24
docker compose version       # v2.x (with a space)

# GPU visible INSIDE a container — the critical check
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi

# Network
curl -sS -o /dev/null -w "%{http_code}\n" https://huggingface.co   # 200
```

If the GPU command fails with `could not select device driver`, the NVIDIA Container
Toolkit is missing — installing it requires admin rights, so ask the machine
administrator.

### Ports already in use on this machine

The Spark is **shared**. These ports are taken by other services: `8000`, `8001`,
`8080`, `9000`, `9001`, `3001`, `3002`, `11434`, `5432`.

The provided files therefore use: **8100** (vLLM), **9100/9101** (MinIO),
**3306** (MySQL), **8501** (web interface). Confirm they are free:

```bash
ss -tln | grep -E '8100|9100|9101|3306|8501'   # should print nothing
```

> ⚠️ **Two vLLM servers owned by `root` are already running** (Qwen3-14B and an
> embedding model), using ~50 GB of memory. Do not touch them. About 60–70 GB remain
> out of the 128 GB unified memory.

---

## 4. Installation

### 4.1 Get the code

```bash
cd ~
git clone <repository_url> MobimixEte2026-main
cd MobimixEte2026-main
```

*(or unzip the provided archive)*

### 4.2 Copy the provided files

```bash
# From your workstation, to the Spark:
scp -i <key> docker-compose.yml docker-compose.spark.yml docker-compose.public.yml \
    run_migrations.py benchmark_compare.py env.spark.example \
    <login>@dgxspark2.joyria.net:/home/<login>/MobimixEte2026-main/

scp -i <key> public_app.py \
    <login>@dgxspark2.joyria.net:/home/<login>/MobimixEte2026-main/dashboard/
```

Verify on the Spark:

```bash
cd ~/MobimixEte2026-main
ls -1 docker-compose.yml docker-compose.spark.yml docker-compose.public.yml \
      run_migrations.py dashboard/public_app.py
```

### 4.3 Two mandatory fixes

These bugs exist in the repository and **prevent reports from being produced**.
Apply them before the first startup.

**a) Missing Python dependency** — the LLM generates code that imports `statsmodels`:

```bash
grep -q statsmodels requirements.txt || echo "statsmodels>=0.14.0" >> requirements.txt
tail -2 requirements.txt
```

**b) Missing LaTeX fonts** — otherwise no PDF compilation succeeds:

```bash
cp Dockerfile Dockerfile.bak
grep -q texlive-fonts-extra Dockerfile || sed -i 's|    texlive-fonts-recommended \\|    texlive-fonts-recommended \\\n    texlive-fonts-extra \\\n    lmodern \\|' Dockerfile
sed -n '28,40p' Dockerfile   # should show texlive-fonts-extra and lmodern
```

> Two more fixes are recommended but less critical — see §10.

### 4.4 Configure `.env`

```bash
cp env.spark.example .env
nano .env
```

Fill in:

```dotenv
# Database
DB_HOST=mysql                    # Docker service name, NOT localhost
DB_PORT=3306
DB_NAME=urbain_automation
DB_USER=root
DB_PASSWORD=<strong_password>

# Object storage
MINIO_ENDPOINT=minio:9000        # internal container-to-container address
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=<at_least_8_chars>
MINIO_SECURE=false

# HuggingFace (model download)
HF_TOKEN=hf_xxxxxxxxxxxx

# LLM — cloud first, falls back to the local GPU when the quota runs out
LLM_ROUTER_ORDER=Groq,NVIDIA
GROQ_API_KEY=gsk_xxxxxxxxxxxx
GROQ_MODEL=llama-3.1-8b-instant
NVIDIA_NIM_API_KEY=dummy
NVIDIA_NIM_BASE_URL=http://vllm:8000/v1
NVIDIA_NIM_MODEL=NousResearch/Meta-Llama-3.1-8B-Instruct
LLM_REQUEST_TIMEOUT_SEC=600

# Web interface password
APP_PASSWORD=<access_password>
```

**Common pitfalls:**

- `MINIO_SECRET_KEY` shorter than 8 characters → MinIO refuses to start, with no clear message
- `NVIDIA_NIM_API_KEY` empty → the router silently ignores the local LLM; set it to `dummy`
- `DB_HOST=localhost` → the pipeline will never find the database
- `!`, `$`, or backtick characters in passwords → shell interpretation problems
- `LLM_REQUEST_TIMEOUT_SEC`: the code default is 45 s, far too short locally (~90 s per call)

Check for duplicate keys:

```bash
grep -vE '^\s*#|^\s*$' .env | grep -oE '^[A-Z_]+=' | sort | uniq -d
# should print nothing
```

### 4.5 LLM model

The configured model is `NousResearch/Meta-Llama-3.1-8B-Instruct` — a freely accessible
mirror of the Meta weights. **Do not use `meta-llama/...`**: that repository is gated
and returns a 403 error.

Size: ~15 GB, downloaded once and cached in `~/.cache/huggingface`.

---

## 5. Starting the infrastructure

Create the alias (must be recreated in every new SSH session):

```bash
cd ~/MobimixEte2026-main
alias dcx='docker compose -p versionete2026-main -f docker-compose.yml -f docker-compose.spark.yml'
set -a && source .env && set +a
```

> The `-p versionete2026-main` flag sets the Docker project name. **It must stay
> identical everywhere**, otherwise Docker creates empty volumes and the data appears
> to vanish. To make it permanent: `echo "alias dcx='...'" >> ~/.bashrc`

Startup:

```bash
dcx build
dcx up -d mysql minio
sleep 20
dcx run --rm migrate
dcx up -d vllm
dcx logs -f vllm          # Ctrl+C to stop watching
```

> **The first vLLM startup takes 20–40 minutes** (download + loading into memory).
> Wait for the `Application startup complete` line. Later startups take ~5 minutes
> thanks to the cache.

Verification:

```bash
curl -s http://localhost:8100/health && echo " OK"
curl -s http://localhost:8100/v1/models | python3 -m json.tool | head -8
dcx run --rm --entrypoint python pipeline -m automatisation_1 check-llm
```

`check-llm` should list both Groq **and** NVIDIA as operational.

Throughput measurement (reference: **~14 tokens/s**):

```bash
curl -s http://localhost:8100/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"NousResearch/Meta-Llama-3.1-8B-Instruct","messages":[{"role":"user","content":"Explain the Kalman filter in 200 words."}],"max_tokens":400}' \
  -w "\n== %{time_total}s ==\n" -o /tmp/r.json
python3 -c "import json;d=json.load(open('/tmp/r.json'));print('tokens:',d['usage']['completion_tokens'])"
```

If throughput is below 5 tok/s, the machine is probably saturated by other processes —
check with `nvidia-smi` and `free -h`.

---

## 6. Deploying the web interface

```bash
alias dcp='docker compose -p versionete2026-main -f docker-compose.yml -f docker-compose.spark.yml -f docker-compose.public.yml'

dcp build webapp
dcp up -d webapp
sleep 20
dcp ps
```

**Mandatory** verification:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8501/_stcore/health
# expected: 200

docker ps --filter name=webapp --format "{{.Ports}}"
# expected: 127.0.0.1:8501->8501/tcp
```

> 🚨 If the second command shows `0.0.0.0:8501`, **stop immediately**
> (`dcp stop webapp`): the app would be reachable from the internet with no protection.
>
> Note: Streamlit prints an "External URL" with the public IP in its logs. That is
> misleading — the Docker port mapping actually restricts access to localhost. Trust
> `docker ps`.

### User access

No port is opened: access goes through an **SSH tunnel**. Each user runs:

```bash
ssh -i <their_key> -L 8501:localhost:8501 <their_login>@dgxspark2.joyria.net
```

then opens `http://localhost:8501` and authenticates with `APP_PASSWORD`.

For access without an SSH account you would need something like a Cloudflare Tunnel —
and **approval from the machine administrator**, since the Spark is shared.

---

## 7. Final verification

```bash
# All services up?
dcp ps

# Database reachable and populated?
MID=$(docker compose -p versionete2026-main ps -q mysql)
docker exec -i $MID mysql -u root -p"$DB_PASSWORD" urbain_automation \
  -e "SELECT processing_status, COUNT(*) FROM data_types GROUP BY processing_status;"

# Reports available?
dcp run --rm --entrypoint python webapp -c "
from shared.storage.client import get_storage
s = get_storage()
n = sum(1 for b in s._mc.list_buckets() for o in s._mc.list_objects(b.name, recursive=True) if o.object_name.endswith('.pdf'))
print(n, 'PDF reports')
"
```

Full functional test: open a tunnel, log into the interface, download a PDF, upload a
test Excel file (without starting the processing).

---

## 8. Day-to-day operation

```bash
# Aliases — recreate in every session
alias dcx='docker compose -p versionete2026-main -f docker-compose.yml -f docker-compose.spark.yml'
alias dcp='docker compose -p versionete2026-main -f docker-compose.yml -f docker-compose.spark.yml -f docker-compose.public.yml'

dcp ps                    # status
dcp logs -f webapp        # web interface logs
dcx logs -f vllm          # LLM logs
dcp restart webapp        # restart the interface
dcp stop webapp           # CUT ACCESS immediately
dcx stop                  # stop everything (data preserved)
```

### Running a job from the command line

```bash
tmux new -s run           # persistent session — survives disconnection
cd ~/MobimixEte2026-main
dcx run --rm pipeline --catalogue /app/data/example_catalogue.xlsx --once
# Ctrl+B then D to detach; tmux attach -t run to come back
```

> Never press `Ctrl+C`: it stops the job.

### Data persistence

| Command | Data |
|---|---|
| `stop`, `down`, `up --build` | **preserved** |
| `down -v` | **ERASED** (database + storage) |

The model cache lives in `~/.cache/huggingface` (bind mount), so it survives even
`down -v`.

### Changing the interface password

```bash
sed -i '/^APP_PASSWORD=/d' .env
echo 'APP_PASSWORD=NewPassword' >> .env
dcp up -d --force-recreate webapp
```

---

## 9. Troubleshooting

### Infrastructure

| Symptom | Cause | Fix |
|---|---|---|
| `port is already allocated` | containers from another project running | check `-p versionete2026-main` everywhere |
| Empty volumes, data "gone" | different Docker project name | always use the same `-p` |
| MinIO restart loop | `MINIO_SECRET_KEY` < 8 characters | make it longer |
| Tables missing | pre-existing volume, `schema.sql` not replayed | `down -v` then restart |
| `Unknown column 'minio_dir'` | migrations v7–v9 not applied | use `run_migrations.py` |

### vLLM

| Symptom | Cause | Fix |
|---|---|---|
| `exec format error` | x86_64 image on ARM64 | keep `nvcr.io/nvidia/vllm` |
| `403` / `gated repo` | Meta repository is gated | use the `NousResearch/...` mirror |
| `CUDA out of memory` | shared memory saturated | lower `--gpu-memory-utilization` |
| Throughput < 5 tok/s | machine loaded by other users | `nvidia-smi`, `free -h` |

### Pipeline

| Symptom | Cause | Fix |
|---|---|---|
| `No active LLM provider` | `NVIDIA_NIM_API_KEY` empty | set it to `dummy` |
| Repeated timeouts | 45 s default | `LLM_REQUEST_TIMEOUT_SEC=600` |
| `429 Too Many Requests` | free Groq quota exhausted | normal — falls back to local; resets at midnight |
| `ModuleNotFoundError` in a notebook | missing dependency | add it to `requirements.txt`, then `dcx build` |
| LaTeX compilation fails | missing font or corrupted `.tex` | see §10 |

### Normal log noise

These messages are **not errors**:

- `Invalid JSON → JSON repaired`: the model wraps its JSON, a repair layer recovers it
- `Invalid skeleton (attempt 1/3)`: generated code is validated by execution, then auto-corrected

---

## 10. Known issues and limitations

### Known bugs in the repository

**1. `notebook_repo.list_for_type()` — blocking INNER JOIN.**
The query uses `JOIN algorithms`, which discards every comparison notebook (they have
no `algorithm_id`). As a result no data type ever reaches `notebooks_done` and no PDF
is produced.

*Fix* — in `shared/db/repository/notebook_repo.py`, switch both joins to `LEFT JOIN`:

```bash
sed -i 's|                    JOIN problems p ON n.problem_id = p.id|                    LEFT JOIN problems p ON n.problem_id = p.id|; s|                    JOIN algorithms a ON n.algorithm_id = a.id|                    LEFT JOIN algorithms a ON n.algorithm_id = a.id|' shared/db/repository/notebook_repo.py
```

**2. `_repair_with_llm()` corrupts LaTeX files.**
When compilation fails, this function replaces the **entire** `.tex` file with the raw
LLM response. With an 8B model it hallucinates commands (e.g. `\input{binhex}`) and
makes the file permanently uncompilable — 4 attempts at ~100 s each, for nothing.

*Fix* — disable the repair in `automatisation_3/latex_compiler.py` by inserting
`    return False` just before the line
`from shared.llm.router import chat_with_meta` (inside `_repair_with_llm`):

```bash
N=$(grep -n "from shared.llm.router import chat_with_meta" automatisation_3/latex_compiler.py | head -1 | cut -d: -f1)
sed -i "${N}i\\    return False  # DISABLED: LLM repair was corrupting the .tex files" automatisation_3/latex_compiler.py
python3 -m py_compile automatisation_3/latex_compiler.py && echo "SYNTAX OK"
```

*Measured gain*: compilation from 302 s (failing) → **1.5 s (succeeding)**.

**3. Metrics not propagated.** Notebooks execute correctly but their results never
reach `notebook_results`: the F1 / precision / recall tables in the reports stay empty.
**Unresolved** — fix still needed.

**4. Files written to ephemeral paths.** Several stages write to `/tmp/` or
`data/reports/`, which disappear with the container. An interrupted run cannot resume
cleanly. The MinIO fallback fails (`'MinIOStorage' object has no attribute
'download_notebook'`). **Workaround**: run each job in a single uninterrupted pass.

### Performance limits

| Measurement | Value |
|---|---|
| Local LLM throughput | ~14 tokens/s |
| Cloud throughput (Groq) | ~737 tokens/s |
| Time per data type | 25–40 min (depending on cloud availability) |
| Full run, 24 types | ~10 h 30 in hybrid mode |
| LLM calls per type | ~13 |

The slowness is **structural**: generation is capped by memory bandwidth (273 GB/s on
this machine). It cannot be fixed through configuration.

### Interface limits

- **one job at a time** (lock): no queue
- 5 rows and 5 MB maximum per uploaded file
- reports are visible to every logged-in user
- single shared password, no individual accounts

### Machine context

The DGX Spark is **shared**. Two `root` services run permanently on it (~50 GB of
memory). Never stop them. Notify the administrator before exposing anything to the
network.
