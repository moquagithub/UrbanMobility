# Mobimix — Handover Package

**Read this file first.** It tells you what you actually need to do, which is probably
less than you expect.

---

## The key fact

**The application is already deployed and running on the DGX Spark.** Docker on that
machine is not partitioned per user — any member of the `docker` group sees and can use
every container. That includes the Mobimix stack.

So the first question is not *how do I deploy this*, but *do I need to deploy it at all*.

Check what is already running:

```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "versionete|NAMES"
```

You should see four containers: `webapp`, `mysql`, `minio`, `vllm`. If they are up, the
stack is live.

---

## Decision: reuse or redeploy?

### ✅ Scenario A — Reuse the existing deployment (recommended)

**Choose this if**: you want to expose Mobimix through your portal, with users sharing
the same data and reports.

**What you deploy**: nothing. You route your reverse proxy to the running webapp.

**Effort**: routing configuration + an authentication decision.

**Read**: `INTEGRATION_GUIDE_EN.md`

This is almost certainly what you want. It costs no additional memory and keeps a single
source of data.

---

### ⚠️ Scenario B — Deploy your own instance

**Choose this only if**: you need strict isolation — separate database, separate
reports, separate users.

**Serious constraint**: the DGX is shared and already loaded (~69 GB of 119 GB in use).
The Mobimix LLM server alone occupies ~15 GB. **Do not start a second vLLM instance.**
Point your deployment at the existing one instead:

```dotenv
NVIDIA_NIM_BASE_URL=http://172.17.0.1:8100/v1
```

You would also need to change every published port (3306, 9100, 9101, 8501 are taken)
and use a different Docker project name.

**Read**: `DEPLOYMENT_GUIDE_EN.md`

---

## What is in this package

| File | Purpose |
|---|---|
| **INTEGRATION_GUIDE_EN.md** | **Scenario A** — routing, authentication, constraints |
| DEPLOYMENT_GUIDE_EN.md | Scenario B — full deployment from scratch |
| CAPACITY_REPORT_EN.md | How many concurrent users, optimisation levers |
| FREE_OPTIONS_REPORT_EN.md | LLM provider comparison and architecture recommendation |
| USER_ACCESS_GUIDE_EN.md | End-user guide, to forward to your users |
| BENCHMARK_REPORT.pdf | Local vs cloud inference benchmark |
| `docker-compose*.yml` | Infrastructure definitions |
| `dashboard/public_app.py` | Web interface source |
| `run_migrations.py` | Database migrations |
| `benchmark_compare.py` | Performance measurement tool |

Full source code: **https://github.com/SpOOfy0/Mobimix**

---

## Not included: the `.env` file

It holds API keys and passwords, so it is deliberately absent from both this package and
the repository. Ask Youness for the values through a private channel.

---

## Three things to know before you start

**One analysis at a time.** Generating one data type takes 25 to 40 minutes on the GPU.
This is a hardware limit (memory bandwidth), not a configuration issue. Viewing and
downloading reports, however, supports dozens of concurrent users.

**Report metrics tables are empty.** F1, precision and recall columns show dashes. The
notebooks execute correctly but their results are not propagated. Structure, analysis
and code are complete. A fix is pending — worth mentioning to your users so they do not
discover it themselves.

**Never run `docker compose down -v`.** It erases the database and all generated
reports. `stop`, `down` and `up --build` are all safe.

---

## Quick verification

```bash
# Is the interface responding?
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8501/_stcore/health   # expect 200

# Is the LLM server up?
curl -s http://localhost:8100/v1/models | python3 -m json.tool | head -5

# How many reports exist?
docker exec -i $(docker compose -p versionete2026-main ps -q mysql) \
  mysql -u root -p"$DB_PASSWORD" urbain_automation -e "SELECT COUNT(*) FROM reports;"
```

If the first command returns 200, the application is live and you can go straight to
`INTEGRATION_GUIDE_EN.md`.
