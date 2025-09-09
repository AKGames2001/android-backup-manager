# core/service.py
"""
High-level backup orchestration:
- Discovers folders on device (optionally filtered).
- Copies files via ADB into the local destination.
- Tracks copied files in a persistent record to support incremental runs.
- Updates restore_record.json with the session's relative device paths.
"""

from __future__ import annotations

import os
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .discovery import Discovery
from .filters import Filters
from .paths import PathMapper
from .transfer import Transfer, CopyStatus
from .restore_manager import RestoreManager


# ---------- Helpers ----------
def _norm_rel_device_path(abs_file: str, base_dir: str) -> str:
    """
    Build a device-relative path like 'Download/sub/file.jpg' from absolute and base.
    Normalizes separators to forward slashes.
    """
    rel = os.path.relpath(abs_file, base_dir)
    return rel.replace("\\", "/").strip()


def _basename_no_slash(path: str) -> str:
    """Return the last segment of a path without trailing slashes."""
    return os.path.basename(path.rstrip("/"))


# ---------- Public API ----------
class BackupService:
    """
    Orchestrates a backup session from an Android device to a local destination.

    Dependencies:
        - adb: object exposing is_device_connected(), pull(), etc. (core.adb_client.ADBClient)
        - record: core.record.RecordStore instance
        - failed_csv_path: CSV file path to write failed transfers
        - filters_path: JSON file path for folder exclusion rules

    Behavior:
        - If folders is None, the service discovers top-level folders under source_dir and filters them.
        - Returns a dict with {'copied_count': int, 'failed_count': int}.
    """

    def __init__(
        self,
        adb,
        source_dir: str,
        dest_root: str,
        record,
        failed_csv_path: str,
        filters_path: str,
        restore_record_path: Optional[str] = None,
    ):
        self.adb = adb
        self.source_dir = source_dir
        self.dest_root = dest_root
        self.record = record
        self.failed_csv_path = failed_csv_path

        # Core collaborators
        self.filters = Filters(filters_path)
        self.discovery = Discovery(self.adb)
        self.path_mapper = PathMapper(source_root=source_dir, dest_root=dest_root)
        self.transfer = Transfer(self.adb, self.record, self.path_mapper)

        # Restore record lives one level above the dated session folder:
        # e.g. D:\Mobile-Backup\Aditya\restore_record.json
        user_root = os.path.dirname(self.dest_root.rstrip("\\/"))
        default_restore_record = os.path.join(user_root, "restore_record.json")

        candidate = restore_record_path or default_restore_record
        if not os.path.dirname(candidate):
            candidate = default_restore_record

        self.restore_record_path = candidate
        self.restore_manager = RestoreManager(self.restore_record_path)

    def run(self, folders: Optional[List[str]] = None) -> Dict[str, int]:
        """
        Execute the backup process.

        Steps:
            1) Ensure device connectivity.
            2) Determine target folders (filtered discovery if not provided).
            3) Copy files, updating the record for new copies.
            4) Write failed paths to CSV.
            5) Update restore_record.json with this session's relative device paths.

        Returns:
            {'copied_count': <int new files copied>, 'failed_count': <int failures>}
        """
        if not self.adb.is_device_connected():
            raise RuntimeError("No device connected. Please connect your Android device and try again.")

        # 1) Determine folders
        folders = folders if folders is not None else self._discover_filtered_folders()

        total_copied = 0
        all_failed: List[str] = []
        # Collect session's device-relative paths for this backup root:
        # Each rel path should include the top folder name (e.g., "Download/file.txt")
        session_rel_paths: Set[str] = set()

        # 2) Copy per folder
        for folder in folders:
            try:
                files = self.discovery.list_files_recursive(folder)
            except Exception:
                files = []

            base_name = _basename_no_slash(folder)
            copied_here = 0
            failed_here: List[str] = []

            for abs_file in files:
                status = self.transfer.copy_file(abs_file, folder)

                if status is CopyStatus.COPIED:
                    rel = _norm_rel_device_path(abs_file, folder)
                    rel_dev = f"{base_name}/{rel}".strip("/")
                    session_rel_paths.add(rel_dev)
                    copied_here += 1
                elif status is CopyStatus.FAILED:
                    failed_here.append(abs_file)
                elif status is CopyStatus.SKIPPED:
                    # Already in record; do not add to restore_record for this session
                    pass

            total_copied += copied_here
            all_failed.extend(failed_here)

        # 3) Persist failed entries once
        self.transfer.write_failed_csv(self.failed_csv_path, all_failed)

        # 4) Update restore record with this session's files
        backup_root_name = os.path.basename(self.dest_root.rstrip("/\\"))
        if session_rel_paths and backup_root_name:
            self.restore_manager.add_or_update_root(
                root_name=backup_root_name,
                files=sorted(session_rel_paths),
                description=f"Backup on {backup_root_name}",
            )

        return {"copied_count": total_copied, "failed_count": len(all_failed)}

    # -------- Internals --------
    def _discover_filtered_folders(self) -> List[str]:
        """
        Discover top-level folders under source_dir and apply substring-based filters.
        """
        raw = self.discovery.list_dirs_top(self.source_dir)
        return self.filters.filter_folders(raw)
