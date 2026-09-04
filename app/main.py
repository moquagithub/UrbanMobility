"""FastAPI app for the BicycleLane v1 explorer (BL-17 MVP).

Run from the ``code/`` directory:

    uvicorn app.main:app --reload

then open http://localhost:8000
"""

from __future__ import annotations

import sys
from pathlib import Path

# src layout: make the bicyclelane package importable without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import jobs
from app.pipeline import DETECTORS, run_city

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="BicycleLane — opportunity explorer", version="0.1.0")


@app.get("/api/detectors")
def detectors() -> list[dict]:
    return DETECTORS


def _parse_bbox(s: str | None) -> tuple | None:
    """Parse a "west,south,east,north" (EPSG:4326) string into a tuple, or None."""
    if not s:
        return None
    try:
        parts = [float(x) for x in s.split(",")]
    except ValueError:
        return None
    return tuple(parts) if len(parts) == 4 else None


@app.post("/api/analyze")
def analyze(
    city: str = Query(..., min_length=2),
    bbox: str | None = Query(None, description="west,south,east,north (EPSG:4326)"),
) -> dict:
    """Kick off a (slow) analysis in the background; return a job id to poll.
    When ``bbox`` is given the analysis is restricted to that area of interest."""
    job_id = jobs.submit(run_city, city, bbox=_parse_bbox(bbox))
    return {"job_id": job_id, "status": "running"}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    state = jobs.status(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return state


@app.get("/api/opportunities")
def opportunities(
    city: str = Query(..., min_length=2),
    bbox: str | None = Query(None),
) -> JSONResponse:
    """Return the (cached) analysis payload — call after the job reports done."""
    try:
        return JSONResponse(run_city(city, bbox=_parse_bbox(bbox)))
    except Exception as exc:  # geocoding / empty network / network errors
        raise HTTPException(
            status_code=400,
            detail=f"Could not analyse {city!r}: {type(exc).__name__}: {exc}",
        )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
