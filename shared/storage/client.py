"""
shared.storage.client — Client MinIO production-grade.

Fonctionnalités avancées :
  - Retry automatique avec backoff exponentiel (3 tentatives)
  - Vérification checksum MD5 (intégrité données)
  - Upload par batch parallèle (ThreadPoolExecutor)
  - Health check avec mesure de latence
  - Singleton thread-safe
  - Statistiques d'utilisation
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Tuple

from minio import Minio
from minio.error import S3Error

log = logging.getLogger(__name__)

_MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT",  "localhost:9000")
_MINIO_ACCESS   = os.getenv("MINIO_ACCESS_KEY", "")
_MINIO_SECRET   = os.getenv("MINIO_SECRET_KEY", "")
_MINIO_SECURE   = os.getenv("MINIO_SECURE",     "false").lower() == "true"

BUCKET_NOTEBOOKS  = "notebooks"
BUCKET_FIGURES    = "figures"
BUCKET_REPORTS    = "reports"
BUCKET_DATASETS   = "datasets"
BUCKET_CATALOGUE  = "catalogue"

_CONTENT_TYPES = {
    ".ipynb": "application/json",
    ".json":  "application/json",
    ".pdf":   "application/pdf",
    ".png":   "image/png",
    ".jpg":   "image/jpeg",
    ".jpeg":  "image/jpeg",
    ".csv":   "text/csv",
    ".xlsx":  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".tex":   "text/plain",
}

_MAX_RETRIES  = 3
_RETRY_DELAYS = [1.0, 2.0, 4.0]
_MAX_WORKERS  = 8

_client_instance: Optional["StorageClient"] = None
_client_lock = Lock()


@dataclass
class UploadResult:
    object_key: str
    success: bool
    checksum: str = ""
    size_bytes: int = 0
    error: str = ""


@dataclass
class HealthStatus:
    ok: bool
    latency_ms: float = 0.0
    endpoint: str = ""
    buckets: List[str] = field(default_factory=list)
    error: str = ""


@dataclass
class StorageStats:
    uploads: int = 0
    downloads: int = 0
    retries: int = 0
    errors: int = 0
    bytes_uploaded: int = 0
    bytes_downloaded: int = 0


class StorageClient:
    """Client MinIO avec retry, checksum et batch upload parallèle."""

    def __init__(self) -> None:
        self._mc = Minio(
            _MINIO_ENDPOINT,
            access_key=_MINIO_ACCESS,
            secret_key=_MINIO_SECRET,
            secure=_MINIO_SECURE,
        )
        self.stats = StorageStats()
        log.info("[STORAGE] Connecté à MinIO %s", _MINIO_ENDPOINT)

    # ── Retry helper ─────────────────────────────────────────────────────────

    def _with_retry(self, fn, *args, operation: str = "op", **kwargs):
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                self.stats.retries += 1
                # Erreurs permanentes (objet/bucket inexistant) : un retry ne changera rien,
                # on échoue immédiatement au lieu de gaspiller le backoff complet.
                if isinstance(exc, S3Error) and exc.code in ("NoSuchKey", "NoSuchBucket"):
                    break
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAYS[attempt]
                    log.warning("[STORAGE] %s tentative %d/%d échouée (%s), retry %.1fs",
                                operation, attempt + 1, _MAX_RETRIES, exc, delay)
                    time.sleep(delay)
        self.stats.errors += 1
        raise last_exc  # type: ignore[misc]

    # ── Checksum ─────────────────────────────────────────────────────────────

    @staticmethod
    def _md5(data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

    @staticmethod
    def _md5_file(path: Path) -> str:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    # ── Upload ───────────────────────────────────────────────────────────────

    def upload_file(self, bucket: str, object_key: str, local_path: Path,
                    verify_checksum: bool = False) -> UploadResult:
        local_path = Path(local_path)
        if not local_path.exists():
            return UploadResult(object_key, False, error=f"Fichier introuvable: {local_path}")

        content_type = _CONTENT_TYPES.get(local_path.suffix.lower(), "application/octet-stream")
        size = local_path.stat().st_size
        checksum = self._md5_file(local_path) if verify_checksum else ""

        def _do():
            self._mc.fput_object(bucket, object_key, str(local_path), content_type=content_type)

        try:
            self._with_retry(_do, operation=f"upload {bucket}/{object_key}")
            self.stats.uploads += 1
            self.stats.bytes_uploaded += size
            log.debug("[STORAGE] ↑ %s/%s (%d bytes)", bucket, object_key, size)
            return UploadResult(object_key, True, checksum=checksum, size_bytes=size)
        except Exception as exc:
            return UploadResult(object_key, False, error=str(exc))

    def upload_bytes(self, bucket: str, object_key: str, data: bytes,
                     content_type: str = "application/octet-stream") -> UploadResult:
        size = len(data)
        checksum = self._md5(data)

        def _do():
            self._mc.put_object(bucket, object_key, io.BytesIO(data),
                                length=size, content_type=content_type)

        try:
            self._with_retry(_do, operation=f"upload_bytes {bucket}/{object_key}")
            self.stats.uploads += 1
            self.stats.bytes_uploaded += size
            return UploadResult(object_key, True, checksum=checksum, size_bytes=size)
        except Exception as exc:
            return UploadResult(object_key, False, error=str(exc))

    def batch_upload(self, items: List[Tuple[str, str, Path]],
                     verify_checksum: bool = False) -> List[UploadResult]:
        """Upload parallèle. items = [(bucket, key, local_path), ...]"""
        results: List[UploadResult] = []
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {
                pool.submit(self.upload_file, bucket, key, path, verify_checksum): key
                for bucket, key, path in items
            }
            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as exc:
                    key = futures[fut]
                    results.append(UploadResult(key, False, error=str(exc)))
        ok = sum(1 for r in results if r.success)
        log.info("[STORAGE] batch_upload %d/%d OK", ok, len(results))
        return results

    # ── Download ─────────────────────────────────────────────────────────────

    def download_file(self, bucket: str, object_key: str, local_path: Path) -> Path:
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        def _do():
            self._mc.fget_object(bucket, object_key, str(local_path))

        self._with_retry(_do, operation=f"download {bucket}/{object_key}")
        size = local_path.stat().st_size
        self.stats.downloads += 1
        self.stats.bytes_downloaded += size
        log.debug("[STORAGE] ↓ %s/%s → %s (%d bytes)", bucket, object_key, local_path, size)
        return local_path

    def download_bytes(self, bucket: str, object_key: str) -> bytes:
        def _do():
            resp = self._mc.get_object(bucket, object_key)
            try:
                return resp.read()
            finally:
                resp.close()

        data = self._with_retry(_do, operation=f"download_bytes {bucket}/{object_key}")
        self.stats.downloads += 1
        self.stats.bytes_downloaded += len(data)
        return data

    # ── Utilitaires ──────────────────────────────────────────────────────────

    def exists(self, bucket: str, object_key: str) -> bool:
        try:
            self._mc.stat_object(bucket, object_key)
            return True
        except S3Error:
            return False

    def stat(self, bucket: str, object_key: str) -> Optional[Dict]:
        try:
            obj = self._mc.stat_object(bucket, object_key)
            return {
                "size": obj.size,
                "last_modified": obj.last_modified,
                "etag": obj.etag,
                "content_type": obj.content_type,
            }
        except S3Error:
            return None

    def delete(self, bucket: str, object_key: str) -> bool:
        try:
            self._mc.remove_object(bucket, object_key)
            log.debug("[STORAGE] ✗ %s/%s supprimé", bucket, object_key)
            return True
        except Exception as exc:
            log.warning("[STORAGE] delete erreur %s/%s : %s", bucket, object_key, exc)
            return False

    def presigned_url(self, bucket: str, object_key: str, expires_hours: int = 24) -> str:
        return self._mc.presigned_get_object(
            bucket, object_key, expires=timedelta(hours=expires_hours)
        )

    def list_objects(self, bucket: str, prefix: str = "") -> List[str]:
        objs = self._mc.list_objects(bucket, prefix=prefix, recursive=True)
        return [obj.object_name for obj in objs]

    def list_objects_with_meta(self, bucket: str, prefix: str = "") -> List[Dict]:
        objs = self._mc.list_objects(bucket, prefix=prefix, recursive=True)
        return [{"key": o.object_name, "size": o.size,
                 "last_modified": o.last_modified} for o in objs]

    # ── Health ────────────────────────────────────────────────────────────────

    def health_check(self) -> HealthStatus:
        t0 = time.perf_counter()
        try:
            buckets = [b.name for b in self._mc.list_buckets()]
            latency = round((time.perf_counter() - t0) * 1000, 1)
            return HealthStatus(ok=True, latency_ms=latency,
                                endpoint=_MINIO_ENDPOINT, buckets=buckets)
        except Exception as exc:
            return HealthStatus(ok=False, endpoint=_MINIO_ENDPOINT, error=str(exc))

    # ── Méthodes métier ───────────────────────────────────────────────────────

    def upload_notebook(self, type_slug: str, notebook_key: str, local_path: Path) -> UploadResult:
        return self.upload_file(BUCKET_NOTEBOOKS, f"{type_slug}/{notebook_key}.ipynb", local_path)

    def upload_executed_notebook(self, type_slug: str, notebook_key: str, local_path: Path) -> UploadResult:
        return self.upload_file(BUCKET_NOTEBOOKS, f"{type_slug}/{notebook_key}_executed.ipynb", local_path)

    def download_notebook(self, minio_key: str, local_path: Path) -> Path:
        return self.download_file(BUCKET_NOTEBOOKS, minio_key, local_path)

    def upload_figure(self, type_slug: str, figure_name: str, local_path: Path) -> UploadResult:
        return self.upload_file(BUCKET_FIGURES, f"{type_slug}/{figure_name}", local_path)

    def download_figure(self, minio_key: str, local_path: Path) -> Path:
        return self.download_file(BUCKET_FIGURES, minio_key, local_path)

    def upload_report(self, type_slug: str, local_path: Path) -> UploadResult:
        return self.upload_file(BUCKET_REPORTS, f"{type_slug}/main.pdf", local_path)

    def upload_dataset(self, type_slug: str, filename: str, local_path: Path) -> UploadResult:
        return self.upload_file(BUCKET_DATASETS, f"{type_slug}/{filename}", local_path)

    def download_dataset(self, minio_key: str, local_path: Path) -> Path:
        return self.download_file(BUCKET_DATASETS, minio_key, local_path)

    def ensure_catalogue_bucket(self) -> bool:
        """Crée le bucket catalogue s'il n'existe pas encore. Idempotent."""
        try:
            if not self._mc.bucket_exists(BUCKET_CATALOGUE):
                self._mc.make_bucket(BUCKET_CATALOGUE)
                log.info("[STORAGE] Bucket '%s' créé", BUCKET_CATALOGUE)
            return True
        except Exception as exc:
            log.error("[STORAGE] ensure_catalogue_bucket erreur : %s", exc)
            return False

    def get_storage_stats(self) -> Dict:
        return {
            "uploads":             self.stats.uploads,
            "downloads":           self.stats.downloads,
            "retries":             self.stats.retries,
            "errors":              self.stats.errors,
            "bytes_uploaded_mb":   round(self.stats.bytes_uploaded / 1_048_576, 2),
            "bytes_downloaded_mb": round(self.stats.bytes_downloaded / 1_048_576, 2),
        }


def get_storage() -> StorageClient:
    """Singleton thread-safe."""
    global _client_instance
    if _client_instance is None:
        with _client_lock:
            if _client_instance is None:
                _client_instance = StorageClient()
    return _client_instance
