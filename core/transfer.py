# core/transfer.py
"""
File transfer utilities for pulling files from the device and tracking outcomes.

Design:
- copy_file(): pull one file and record it when successfully copied.
- copy_folder_recursive(): convenience helper to copy an entire folder using Discovery (optional).
- write_failed_csv(): persist the list of failures with a single column, overwriting per session.

Notes:
- Uses CopyStatus enum for explicit result states to avoid ambiguous truthiness checks.
"""

from __future__ import annotations

import csv
import os
import posixpath as pp
from enum import Enum
from typing import List, Dict, Optional

from core.index import UnifiedIndex, norm_rel_path


class CopyStatus(Enum):
    """Result of a copy attempt for a single file."""
    COPIED = "copied"
    SKIPPED = "skipped"
    FAILED = "failed"


class Transfer:
    """
    High-level file transfer operations using:
      - adb: core.adb_client.ADBClient (pull)
      - record: core.record.RecordStore (deduplicate via membership)
      - path_mapper: core.paths.PathMapper (map device -> local path)
    """
    def __init__(self, adb, index: UnifiedIndex, path_mapper) -> None:
        self.adb = adb
        self.index = index
        self.path_mapper = path_mapper

    @staticmethod
    def _device_rel(remote_file: str, base_remote_dir: str) -> str:
        base = (base_remote_dir or "").rstrip("/")
        top = pp.basename(base)
        rel = pp.relpath(remote_file, base)
        rel = rel.lstrip("./").strip("/")
        if not top:
            return norm_rel_path(rel)
        return norm_rel_path(f"{top}/{rel}" if rel else top)

    def copy_file(self, remote_file: str, base_remote_dir: str, backup_root: str) -> CopyStatus:
        """
        Copy a single file from the device into the current session destroot.
        - Skip if index already has device-relative path.
        - On success, record a version entry for this backup root with local_rel.
        """
        device_rel = self._device_rel(remote_file, base_remote_dir)
        if self.index.has_path(device_rel):
            return CopyStatus.SKIPPED

        local_path = self.path_mapper.to_local(remote_file, base_remote_dir)
        try:
            proc = self.adb.pull(remote_file, local_path)
        except Exception:
            return CopyStatus.FAILED

        if getattr(proc, "returncode", 1) != 0:
            return CopyStatus.FAILED

        # local_rel must be relative to the backup root folder (self.path_mapper.dest_root)
        try:
            local_rel = os.path.relpath(local_path, self.path_mapper.dest_root).replace("\\", "/")
        except Exception:
            local_rel = device_rel

        self.index.note_backup(
            relpath=device_rel,
            root=str(backup_root),
            local_rel=local_rel,
            remote_mtime=None,
            remote_size=None,
            save=True,
        )
        return CopyStatus.COPIED

    @staticmethod
    def write_failed_csv(failed_csv_path: str, failed_items: List[str]) -> None:
        """
        Write failed remote file paths to a CSV with one column 'Failed paths'.

        The file is overwritten per call to reflect the latest session state.
        """
        os.makedirs(os.path.dirname(failed_csv_path), exist_ok=True)
        # Open with newline='' to avoid extra blank lines on Windows.
        with open(failed_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(["Failed paths"])
            for item in failed_items:
                writer.writerow([item])
