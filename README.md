# Data Quiz EDA Platform — Enterprise DGXspark Deployment Guide

> **Zero-Authorization Architecture**  
> High-performance Exploratory Data Analysis & Machine Learning suite containerized and production-ready for **NVIDIA DGXspark** systems and local development.

---

## 1. Overview & Architecture

The **Data Quiz EDA Platform** is a full-stack analytical web application designed for fast data profiling, quality auditing, statistical validation, and unsupervised machine learning clustering on tabular and geospatial-derived datasets.

```
                               ┌──────────────────────────────────────────────┐
                               │       Client Browser / Corporate Network     │
                               │          http://<dgx-host>:8080             │
                               └──────────────────────┬───────────────────────┘
                                                      │
                                                      ▼
                                ┌───────────────────────────────────────────┐
                                │          Nginx Reverse Proxy              │
                                │           Port 8080 (Single Origin)       │
                                └───────┬───────────────────────────┬───────┘
                                        │                           │
                   /api/*, /docs,       │                           │  /* (All frontend assets)
                   /openapi.json        ▼                           ▼
                        ┌────────────────────────┐         ┌────────────────────────┐
                        │ FastAPI Backend        │         │ Next.js 14 Frontend    │
                        │ Port 8000 (Internal)   │         │ Port 3000 (Internal)   │
                        │ Python 3.12, Uvicorn   │         │ Node 22 Standalone     │
                        │ Pandas / Scikit / SciPy│         │ React 18 / Plotly.js   │
                        └───────────┬────────────┘         └────────────────────────┘
                                    │
                        ┌───────────▼────────────┐
                        │ Persistent Docker Vol. │
                        │  - uploaded_files      │
                        │  - anonymization_keys  │
                        │  - dataset_store       │
                        └────────────────────────┘
```

### Key Capabilities
- **Fast Tabular Ingestion**: Multi-format CSV ingestion with automatic delimiter/encoding detection.
- **Automated PII Anonymization**: SHA-256 salted hashing for emails, phone numbers, and identifying strings before storage.
- **13 Statistical Profiling Modules**: Overview, missing data matrix, distribution fits, D'Agostino-Pearson & Shapiro normality tests, Pearson/Spearman correlation matrices, Random Forest feature importance, variable drilldown, automated cleaning recommendations, and data quality diagnostics.
- **Unsupervised ML Clustering Engine**: K-Means, DBSCAN, Agglomerative Hierarchical (CAH), Gaussian Mixture Models (GMM), and Mean-Shift clustering with PCA/t-SNE 2D/3D projections and silhouette evaluation metrics.
- **Exportable LaTeX Reports**: Instant compilation of LaTeX summary reports with embedded statistical graphs.

---

## 2. Zero-Authorization Model

This deployment is built **strictly without any authorization or authentication layer**:
- **No Identity Provider**: No Keycloak, OAuth2, or external IdP dependency.
- **No User Database**: No accounts, passwords, or session tokens required (`users.db` and auth tables are removed).
- **Public Endpoints**: Every API route under `/api/v1/**` and the Next.js UI are openly accessible.
- **Dataset Access**: Datasets are accessed directly via their unique hash-based `dataset_id`.

> ⚠️ **CRITICAL SECURITY NOTE FOR DGX DEPLOYMENT**  
> Because the application does not enforce credentials or tenant isolation, **do not expose this service directly to the public Internet with sensitive data**.  
> It is designed to be hosted on your **internal DGX corporate network**, behind an **authenticating perimeter proxy**, within a **secure VPN**, or bound to `127.0.0.1` accessed via SSH tunnels.

---

## 3. Directory Structure

```
C:\Users\thous\Documents\Aix projects\Transportation_proj\EDA/
├── docker-compose.yml        # Multi-container orchestration (Nginx, FastAPI, Next.js)
├── .env.example              # Environment variables template
├── .env                      # Active runtime environment file
├── deploy_dgx.sh             # 1-command deployment script for DGX Linux
├── start_docker.bat          # 1-click Docker launcher for Windows
├── run_local.bat             # 1-click native launcher for Windows (Python + Node)
├── ARCHITECTURE.md           # In-depth architectural technical specification
├── README.md                 # This deployment guide
│
├── nginx/
│   └── nginx.conf            # Single-origin reverse proxy configuration (8080)
│
├── backend/
│   ├── Dockerfile            # Python 3.12-slim production container
│   ├── requirements.txt      # FastAPI, NumPy, Pandas, Scikit-learn, SciPy, Matplotlib
│   ├── pyproject.toml        # Ruff linter & build configuration
│   ├── pytest.ini            # Pytest test suite configuration
│   ├── app/                  # FastAPI routers, core logic, ML job store
│   └── tests/                # 51 unit & integration tests
│
├── frontend/
│   ├── Dockerfile            # Multi-stage Node 22-slim Next.js standalone runner
│   ├── package.json          # Dependencies (Next.js 14, React 18, Plotly.js, Tailwind)
│   ├── app/                  # App Router pages (9 analysis pages + ML clustering)
│   ├── components/           # UI widgets, layout guards, Plotly chart wrappers
│   └── lib/                  # Relative URL API client
│
└── systemd/                  # Native Linux DGX templates (if not using Docker)
    ├── eda-backend.service   # Systemd unit for FastAPI
    ├── eda-frontend.service  # Systemd unit for Next.js
    └── nginx-site.conf       # Nginx host reverse proxy configuration
```

---

## 4. Deployment on DGXspark Server

### Method A: Docker Compose (Recommended)

Docker Compose provides an isolated, reproducible container environment on your DGX server with zero dependency conflicts.

#### 1. Transfer Project to DGX
Transfer this folder to your DGX server via `rsync` or `scp`:
```bash
rsync -avz --exclude '.git' --exclude 'node_modules' --exclude '.venv' \
  "/path/to/EDA" dgxuser@<dgx-spark-ip>:/home/dgxuser/
```

#### 2. Configure Environment (`.env`)
On the DGX server:
```bash
cd /home/dgxuser/EDA
cp .env.example .env
```
Default parameters in `.env`:
```ini
# Port exposed by Nginx reverse proxy on the DGX host
NGINX_PORT=8080

# CORS origins (default * since Nginx handles single-origin proxying)
CORS_ALLOWED_ORIGINS=*

# Salt used for PII anonymization
EDA_ANON_SALT=dgx_production_salt_2026
```

#### 3. Run the Automated Deployment Script
```bash
chmod +x deploy_dgx.sh
./deploy_dgx.sh
```
Or start manually via Docker Compose:
```bash
docker compose up --build -d
```

#### 4. Verify Service Health
```bash
# Check container status
docker compose ps

# Check backend health
curl -s http://localhost:8080/api/v1/health
# Expected output: {"status":"healthy"}
```

Access the application in your browser:
- **Web UI**: `http://<dgx-spark-ip>:8080`
- **Interactive API Docs (Swagger)**: `http://<dgx-spark-ip>:8080/docs`

---

### Method B: Native DGX Linux Deployment (Systemd + Host Nginx)

If you prefer running directly on DGX bare-metal (e.g. to share system CUDA drivers or Spark Python environments):

#### 1. Backend Setup (FastAPI)
```bash
cd /home/dgxuser/EDA/backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

#### 2. Frontend Setup (Next.js)
```bash
cd /home/dgxuser/EDA/frontend
npm ci
# Build production standalone bundle
NEXT_PUBLIC_API_URL="" npm run build
```

#### 3. Configure Systemd Services
Copy the provided unit files:
```bash
sudo cp /home/dgxuser/EDA/systemd/eda-backend.service /etc/systemd/system/
sudo cp /home/dgxuser/EDA/systemd/eda-frontend.service /etc/systemd/system/

# Adjust user and working directory paths if different from /opt/eda
sudo nano /etc/systemd/system/eda-backend.service
sudo nano /etc/systemd/system/eda-frontend.service

sudo systemctl daemon-reload
sudo systemctl enable --now eda-backend eda-frontend
```

#### 4. Configure Host Nginx & SSL (Certbot)
Copy the Nginx configuration snippet:
```bash
sudo cp /home/dgxuser/EDA/systemd/nginx-site.conf /etc/nginx/sites-available/eda.conf
sudo ln -s /etc/nginx/sites-available/eda.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```
To enable HTTPS using Let's Encrypt:
```bash
sudo certbot --nginx -d eda.yourdomain.com
```

---

## 5. Local Windows Execution

You can run the application directly from PowerShell on your local workstation:

### Option 1: One-Click Local Script (No Docker required)
From PowerShell:
```powershell
PS C:\Users\thous\Documents\Aix projects\Transportation_proj\EDA> .\run_local.bat
```
This script will:
1. Verify Python 3.10+ and Node.js 18+ are installed.
2. Initialize `backend/.venv` and install `requirements.txt`.
3. Install frontend `npm` packages if not present.
4. Launch FastAPI on `http://localhost:8000` and Next.js on `http://localhost:3000`.
5. Automatically open `http://localhost:3000` in your default browser.

### Option 2: Docker Desktop
From PowerShell:
```powershell
PS C:\Users\thous\Documents\Aix projects\Transportation_proj\EDA> .\start_docker.bat
```
Or directly:
```powershell
docker compose up --build -d
```
Then navigate to `http://localhost:8080`.

---

## 6. Pairing with DGX Spark / RAPIDS Pipelines

When executing large-scale geospatial or mobility transformations using **Apache Spark & NVIDIA RAPIDS** on the DGX:
1. **Export Aggregations to Parquet or CSV**: Have your PySpark pipeline write consolidated feature tables:
   ```python
   # Inside your DGX PySpark script:
   df_features.coalesce(1).write.mode("overwrite").option("header", "true").csv("/data/exports/model_input.csv")
   ```
2. **Direct Upload into EDA**:
   - Through the Web UI at `http://<dgx-spark-ip>:8080` (drag-and-drop the CSV).
   - Or programmatically via `curl`:
     ```bash
     curl -X POST "http://localhost:8080/api/v1/datasets/upload" \
          -F "file=@/data/exports/model_input.csv"
     ```
3. **Increasing Upload Limits for Big Data**:
   - The default file upload size is set to **55 MB**.
   - To ingest larger datasets (e.g. 500 MB+), adjust:
     - `nginx/nginx.conf`: `client_max_body_size 500M;`
     - `backend/app/core/config.py`: `MAX_UPLOAD_SIZE_MB = 500`

---

## 7. API Reference Summary

| Category | Endpoint | Method | Description |
| :--- | :--- | :--- | :--- |
| **System** | `/api/v1/health` | `GET` | Healthcheck (returns `{"status":"healthy"}`) |
| **Datasets** | `/api/v1/datasets/upload` | `POST` | Ingests CSV, applies PII hashing, returns `dataset_id` |
| **Datasets** | `/api/v1/datasets/` | `GET` | Lists all stored datasets |
| **Profiling** | `/api/v1/datasets/{id}/overview` | `GET` | Dimensions, memory usage, inferred column data types |
| **Profiling** | `/api/v1/datasets/{id}/missing` | `GET` | Missing value rates, null patterns, completeness score |
| **Profiling** | `/api/v1/datasets/{id}/distributions`| `GET` | Skewness, kurtosis, quartiles, histogram bins |
| **Profiling** | `/api/v1/datasets/{id}/normality` | `GET` | D'Agostino-Pearson & Shapiro-Wilk statistical tests |
| **Profiling** | `/api/v1/datasets/{id}/correlation` | `GET` | Pearson & Spearman correlation matrices |
| **Profiling** | `/api/v1/datasets/{id}/importance` | `GET` | Feature importance ranking via Random Forest |
| **Profiling** | `/api/v1/datasets/{id}/explorer` | `GET` | Univariate distribution and value counts drilldown |
| **Profiling** | `/api/v1/datasets/{id}/recommendations`| `GET` | Imputation and feature transformation suggestions |
| **Profiling** | `/api/v1/datasets/{id}/errors` | `GET` | Anomalies, outliers, and schema validation errors |
| **Reports** | `/api/v1/datasets/{id}/reports` | `GET` | Compiles synthesis and LaTeX summary report |
| **ML Engine** | `/api/v1/ml/jobs` | `POST` | Submits asynchronous clustering task |
| **ML Engine** | `/api/v1/ml/jobs/{job_id}` | `GET` | Polls clustering status, metrics, and PCA projections |

Interactive Swagger documentation is available at `/docs`.

---

## 8. Operations & Maintenance

### Checking Logs
```bash
# View all combined logs
docker compose logs -f

# View backend FastAPI logs only
docker compose logs -f backend

# View frontend Next.js logs only
docker compose logs -f frontend
```

### Restarting & Updating
```bash
# Restart without rebuilding
docker compose restart

# Rebuild after code updates
docker compose up --build -d
```

### Stopping Services
```bash
docker compose down
```
*(Data in volumes `backend_uploads`, `backend_keys`, and `backend_datasets` will persist across restarts.)*

### Running Automated Test Suite
```bash
# Backend pytest suite (51 tests)
cd backend
pytest tests/ -v
```

---

## 9. License & Attribution
- Built with **FastAPI**, **Next.js 14**, **Tailwind CSS**, and **Plotly.js**.
- Configured for open access on internal enterprise DGX computing clusters.
