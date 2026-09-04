"""Minimal in-process async job registry for the web MVP.

An analysis (OSM extraction + detectors) takes tens of seconds, which would
block the HTTP request. Instead the API submits the work to a background thread
and returns a job id the client polls. This is deliberately lightweight (single
process, in-memory) — a real deployment would use Redis + a task queue, but this
removes the blocking-request problem without extra infrastructure.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Callable, Optional

_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def submit(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
    """Run ``fn(*args, **kwargs)`` in a background thread; return a job id."""
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[job_id] = {"status": "running", "error": None}

    def _run() -> None:
        try:
            fn(*args, **kwargs)
            with _LOCK:
                _JOBS[job_id]["status"] = "done"
        except Exception as exc:  # surfaced to the client via the job status
            with _LOCK:
                _JOBS[job_id]["status"] = "error"
                _JOBS[job_id]["error"] = f"{type(exc).__name__}: {exc}"

    threading.Thread(target=_run, daemon=True).start()
    return job_id


def status(job_id: str) -> Optional[dict[str, Any]]:
    """Return a copy of the job record, or None if the id is unknown."""
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None
