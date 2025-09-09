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
from enum import Enum
from typing import Dict, List

from tqdm import tqdm  # progress bar for long folder copies


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

    def __init__(self, adb, record, path_mapper):
        self.adb = adb
        self.record = record
        self.path_mapper = path_mapper

    def copy_file(self, remote_file: str, base_remote_dir: str) -> CopyStatus:
        """
        Copy a single file from the device.

        Behavior:
        - Compute the device-relative key from base_remote_dir.
        - If it's already in the record, return SKIPPED.
        - Else adb.pull(...) to the mapped local path; on success, add to record and return COPIED.
        - If pull fails, return FAILED.
        """
        rel = self.path_mapper.to_relative(remote_file, base_remote_dir)
        if self.record.contains(rel):
            return CopyStatus.SKIPPED  # do not count as copied and do not add to restore_record

        local_path = self.path_mapper.to_local(remote_file, base_remote_dir)
        try:
            proc = self.adb.pull(remote_file, local_path)
        except Exception:
            return CopyStatus.FAILED

        if getattr(proc, "returncode", 1) == 0:
            # Record only when the copy succeeded
            self.record.add(rel)
            return CopyStatus.COPIED
        return CopyStatus.FAILED

    def copy_folder_recursive(self, remote_dir: str) -> Dict[str, List[str]]:
        """
        Copy all files recursively under remote_dir using Discovery.

        Returns:
            {"failed": [<remote file>...], "copied": <int count_of_copied_files>}
        Notes:
            - Only counts successful new copies as 'copied'.
            - Skipped files (already in record) are excluded from 'copied' and 'failed'.
        """
        from .discovery import Discovery

        discovery = Discovery(self.adb)
        try:
            files = discovery.list_files_recursive(remote_dir)
        except Exception:
            files = []

        failed: List[str] = []
        copied_count = 0

        for f in tqdm(files, desc=f"Copying from {os.path.basename(remote_dir.rstrip('/'))}"):
            status = self.copy_file(f, remote_dir)
            if status is CopyStatus.COPIED:
                copied_count += 1
            elif status is CopyStatus.FAILED:
                failed.append(f)
            # SKIPPED -> do nothing

        return {"failed": failed, "copied": copied_count}

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
