# Marker file for Python package
"""
core package public API.
"""

from __future__ import annotations

# Public imports (re-exported symbols)
from .adb_client import ADBClient
from .discovery import Discovery
from .filters import Filters
from .paths import PathMapper
from .record import RecordStore
from .restore_manager import RestoreManager
from .transfer import Transfer, CopyStatus
from .service import BackupService

# Package metadata (PEP 440 compliant)
__version__ = "0.1.0"

# Explicit public API for consumers: from core import ADBClient, BackupService, ...
__all__ = [
    "ADBClient",
    "Discovery",
    "Filters",
    "PathMapper",
    "RecordStore",
    "RestoreManager",
    "Transfer",
    "CopyStatus",
    "BackupService",
    "__version__",
]


def make_backup_service(
    adb_path: str,
    source_dir: str,
    dest_root: str,
    record_path: str,
    failed_csv_path: str,
    filters_path: str,
    restore_record_path: str | None = None,
) -> BackupService:
    """
    Convenience factory to build a ready-to-run BackupService.

    Parameters:
        adb_path: Absolute path to adb executable.
        source_dir: Device root (e.g., '/sdcard/').
        dest_root: Local destination root for this backup session (e.g., 'D:/Mobile-Backup/Aditya/2025-09-09').
        record_path: Path to record.json (per user, shared across sessions).
        failed_csv_path: Path to a CSV file where failed transfers are logged.
        filters_path: Path to filters.json controlling excluded folders.
        restore_record_path: Optional path to restore_record.json; if None, BackupService computes a default.

    Returns:
        BackupService instance wired with ADBClient, RecordStore, and paths.

    Notes:
        - Keeps the GUI and config concerns out of core; callers decide paths and persistence.
        - ADB connectivity is checked by the service when run() is called.
    """
    adb = ADBClient(adb_path)
    record = RecordStore(record_path)
    return BackupService(
        adb=adb,
        source_dir=source_dir,
        dest_root=dest_root,
        record=record,
        failed_csv_path=failed_csv_path,
        filters_path=filters_path,
        restore_record_path=restore_record_path,
    )
