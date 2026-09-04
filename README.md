# 🚲 BicycleLane — Opportunity Detection

> **Detection of opportunities to build new bicycle lanes in any city.**
>
> BicycleLane analyses a city's existing cycle network together with its full road network and surfaces ranked opportunities for infrastructure investment. Deployable as an interactive web app on NVIDIA DGX Spark.

---

## Table of Contents

- [Overview](#overview)
- [Detectors](#detectors)
- [Architecture](#architecture)
- [Quick Start — Local](#quick-start--local)
- [Deployment — NVIDIA DGX Spark](#deployment--nvidia-dgx-spark)
- [Configuration & Environment Variables](#configuration--environment-variables)
- [API Reference](#api-reference)
- [Web Explorer](#web-explorer)
- [Programmatic Use](#programmatic-use)
- [Project Structure](#project-structure)
- [Development](#development)

---

## Overview

BicycleLane v1 ships six detectors that identify different types of cycling infrastructure opportunities:

| Code | Name | Description |
|------|------|-------------|
| **D1** | Missing Links | Join two distinct cycle-network components across a short, road-feasible gap |
| **D2** | Route Continuity | Fill uncovered gaps along a named street corridor that already has partial cycle provision |
| **D4** | Coverage / Density | Built city zones statistically under-served (Getis-Ord Gi* cold-spots) |
| **D5** | Demand–Supply Mismatch | Grid zones with high demand (weighted OSM POIs) but low cycling supply |
| **D9** | Intermodality / Stations | Streets to equip near public-transport hubs whose catchment lacks cycle provision |
| **D11** | Generators / Schools | Streets to equip near schools, universities, and hospitals with unsafe cycle access |

Each detector returns a ranked **GeoDataFrame** of `Opportunity` objects (highest `score` first).

---

## Architecture

```
opportunity-detection/
├── app/                    # FastAPI web app (BL-17 MVP)
│   ├── main.py             # API routes + static file serving
│   ├── jobs.py             # Background job management
│   ├── pipeline.py         # City analysis pipeline (all detectors)
│   └── static/             # Leaflet frontend (HTML/CSS/JS)
├── src/bicyclelane/        # Core Python library
│   ├── detectors/          # D1, D2, D4, D5, D9, D11 implementations
│   ├── osm.py              # Cached OSM cycle-network extraction
│   ├── roads.py            # Cached OSM road-network extraction
│   ├── demand.py           # POI demand weighting + INSEE population
│   ├── places.py           # Transit hubs, schools, hospitals
│   ├── grid.py             # Regular grid + supply per cell
│   ├── crs.py              # Metric CRS reprojection
│   ├── mapping.py          # Folium map export
│   └── schema.py           # Opportunity dataclass
├── tests/                  # Offline synthetic-graph test suite
├── Dockerfile              # DGX Spark–ready container image
├── docker-compose.yml      # GPU-enabled compose deployment
├── pyproject.toml          # Package definition (src layout)
└── requirements.txt        # Python dependencies
```

---

## Quick Start — Local

### Prerequisites
- Python 3.12+ (Anaconda/Miniconda recommended)

### 1. Create a virtual environment

```bash
cd opportunity-detection
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
pip install -e .
```

### 3. Run the web app

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8080
# → Open http://127.0.0.1:8080
```

### 4. Or run the CLI demo

```bash
# Live OSM extraction (requires internet):
python demo.py "Aix-en-Provence, France"

# Generate a standalone HTML map:
python make_maps.py "Marseille, France"
# → outputs/marseille_opportunities.html

# Offline tests (no internet required):
python -m pytest -q
```

---

## Deployment — NVIDIA DGX Spark

The service is containerised and ready to deploy on **NVIDIA DGX Spark** (Grace Blackwell GB10).

### Prerequisites on DGX Spark

```bash
# Verify Docker and NVIDIA Container Toolkit
docker run --rm --gpus all nvcr.io/nvidia/pytorch:25.11-py3 nvidia-smi
```

> **Note:** The NVIDIA Container Toolkit is pre-installed on DGX Spark systems. If you see `permission denied`, add your user to the docker group:
> ```bash
> sudo usermod -aG docker $USER && newgrp docker
> ```

### Option A — Docker Compose (recommended)

```bash
# 1. Clone the repo
git clone https://github.com/moquagithub/UrbanMobility.git
cd UrbanMobility
git checkout bicycle_lane
cd opportunity-detection

# 2. Build and start (GPU-enabled, auto-restart)
docker compose up --build -d

# 3. Check logs
docker compose logs -f bicyclelane

# 4. Open the web explorer
# → http://<DGX-IP>:8080
```

To stop:

```bash
docker compose down
```

### Option B — Plain Docker

```bash
# Build
docker build -t bicyclelane:latest .

# Run with GPU access
docker run --rm \
  --gpus all \
  -p 8080:8080 \
  -v $(pwd)/outputs:/app/outputs \
  -v $(pwd)/cache:/app/.cache \
  bicyclelane:latest
```

### Persistent Data

| Path (inside container) | Host mount (via compose) | Purpose |
|-------------------------|--------------------------|---------|
| `/app/outputs` | `./outputs` | Generated HTML maps |
| `/app/.cache` | `./cache` | OSM network cache (avoids repeated downloads) |

---

## Configuration & Environment Variables

Set these in `docker-compose.yml` or as shell exports before running locally.

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Bind address for uvicorn |
| `PORT` | `8080` | HTTP port |
| `OMP_NUM_THREADS` | `8` | OpenMP thread limit (Grace CPU tuning) |
| `BICYCLELANE_INSEE` | _(none)_ | Path to INSEE gridded population file — adds real population weighting to D5 demand |
| `BICYCLELANE_RIDERSHIP` | _(none)_ | Path to SNCF ridership CSV — sharpens D9 hub priorities |
| `BICYCLELANE_ENROLMENT` | _(none)_ | Path to Éducation nationale enrolment CSV — sharpens D11 generator priorities |

---

## API Reference

### `GET /api/detectors`
Returns the list of available detectors with metadata.

```json
[{"code": "D1", "name": "Missing links", "description": "..."}]
```

### `POST /api/analyze?city=<name>&bbox=<west,south,east,north>`
Submits a city analysis as a background job.

```bash
curl -X POST "http://localhost:8080/api/analyze?city=Aix-en-Provence%2C+France"
# → {"job_id": "abc123", "status": "running"}
```

Optional `bbox` restricts analysis to an area of interest (much faster for large cities):
```bash
curl -X POST "http://localhost:8080/api/analyze?city=Paris%2C+France&bbox=2.33,48.85,2.37,48.87"
```

### `GET /api/jobs/{job_id}`
Poll job progress.

```json
{"status": "done", "result": {...}}
```

### `GET /api/opportunities?city=<name>`
Returns cached analysis payload (call after job reports `"done"`).

---

## Web Explorer

The interactive Leaflet-based web UI lets you:

- 🔍 **Search any city** — type a place name and click **Analyser**
- 🗺️ **Draw an area of interest** — two clicks restrict the analysis to a bounding box
- 🎛️ **Toggle detectors** — enable/disable any of the six criteria (D1/D2/D4/D5/D9/D11)
- ⚖️ **Weight detectors** — 0–1 sliders re-rank opportunities client-side instantly
- 🔵 **Zone → segment drill-down** — click a D5 zone to reveal candidate lane segments inside it
- 💾 **Export** — download results as **GeoJSON** or **CSV** (with WKT geometry)
- 🌍 **Internationalised** — switch between French 🇫🇷, English 🇬🇧, and Spanish 🇪🇸 live

---

## Programmatic Use

```python
from bicyclelane.detectors import detect_missing_links, detect_continuity_gaps
from bicyclelane.osm import get_cycle_graph
from bicyclelane.roads import get_road_graph
from bicyclelane.crs import project_graph
import osmnx as ox

place = "Aix-en-Provence, France"
cycle_g = project_graph(get_cycle_graph(place))   # local metric CRS
road_g  = project_graph(get_road_graph(place))

# D1 — Missing links
d1 = detect_missing_links(cycle_g, road_g, eps=300, kappa=1.5, city=place)

# D2 — Route continuity
road_gdf  = ox.graph_to_gdfs(road_g, nodes=False)
cycle_gdf = ox.graph_to_gdfs(cycle_g, nodes=False)
d2 = detect_continuity_gaps(cycle_gdf, road_gdf, d_min=30, d_max=500, city=place)

print(d1.head())   # GeoDataFrame, highest score first
```

---

## Development

```bash
# Install with dev extras
pip install -e ".[dev]"

# Run offline test suite (no internet required)
python -m pytest -q

# Run with auto-reload during development
uvicorn app.main:app --reload --port 8080
```

### Optional extras

```bash
# DEM/elevation support (for relief detector BL-23)
pip install -e ".[dem]"   # installs rasterio
# Activate via: export BICYCLELANE_DEM=/path/to/dem.tif
```

### Reference environment

| Package | Version |
|---------|---------|
| Python | 3.12+ |
| osmnx | ≥ 2.0 |
| geopandas | ≥ 1.0 |
| shapely | ≥ 2.0 |
| networkx | ≥ 3.0 |
| scipy | ≥ 1.10 |
| scikit-learn | ≥ 1.3 |
| fastapi | ≥ 0.110 |
| uvicorn | ≥ 0.29 |

---

## License

Proprietary — BicycleLane team.
