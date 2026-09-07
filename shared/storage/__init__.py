"""
shared.storage — Couche de stockage objet MinIO pour le pipeline Mobility PDF.

Architecture :
  MySQL  → métadonnées (IDs, statuts, clés MinIO, timestamps)
  MinIO  → contenu fichiers (notebooks, figures, PDFs, datasets)
  Local  → cache temporaire pendant l'exécution uniquement

Point d'entrée :
    from shared.storage import get_storage
    from shared.storage import check_infrastructure
    from shared.storage import NotebookWorkspace, temp_figures_dir
    from shared.storage import full_sync
"""
from shared.storage.client import (
    StorageClient,
    UploadResult,
    HealthStatus,
    StorageStats,
    get_storage,
    BUCKET_NOTEBOOKS,
    BUCKET_FIGURES,
    BUCKET_REPORTS,
    BUCKET_DATASETS,
    BUCKET_CATALOGUE,
)
from shared.storage.workspace import NotebookWorkspace, temp_figures_dir
from shared.storage.health import check_infrastructure, InfrastructureHealth, ComponentHealth
from shared.storage.sync import full_sync, SyncReport
from shared.storage.catalogue_storage import (
    catalogue_path,
    problem_dir_prefix,
    algorithm_dir_prefix,
    upload_type_metadata,
    upload_type_description,
    upload_problem_metadata,
    upload_problem_description,
    upload_algorithm_metadata,
    upload_algorithm_skeleton,
    upload_algorithm_explanation,
    upload_test_results,
    upload_dataset_schema,
    upload_dataset_csv,
    upload_dataset_description,
    # Auto2 / Auto3
    upload_notebook,
    upload_comparison_figure,
    upload_report_pdf,
)

__all__ = [
    "StorageClient", "UploadResult", "HealthStatus", "StorageStats", "get_storage",
    "BUCKET_NOTEBOOKS", "BUCKET_FIGURES", "BUCKET_REPORTS", "BUCKET_DATASETS", "BUCKET_CATALOGUE",
    "NotebookWorkspace", "temp_figures_dir",
    "check_infrastructure", "InfrastructureHealth", "ComponentHealth",
    "full_sync", "SyncReport",
    # catalogue hierarchy
    "catalogue_path", "problem_dir_prefix", "algorithm_dir_prefix",
    "upload_type_metadata", "upload_type_description",
    "upload_problem_metadata", "upload_problem_description",
    "upload_algorithm_metadata", "upload_algorithm_skeleton", "upload_algorithm_explanation",
    "upload_test_results", "upload_dataset_schema", "upload_dataset_csv", "upload_dataset_description",
    "upload_notebook", "upload_comparison_figure", "upload_report_pdf",
]
