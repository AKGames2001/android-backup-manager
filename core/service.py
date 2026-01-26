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
from typing import Dict, List, Optional

from .discovery import Discovery
from .filters import Filters
from .index import UnifiedIndex
from .paths import PathMapper
from .transfer import Transfer, CopyStatus


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
            dest_root: str, 
            index: UnifiedIndex, 
            failed_csv_path: str, 
            filters_path: str,
            source_dir: str
        ) -> None:
        self.adb = adb
        self.source_dir = source_dir
        self.dest_root = dest_root
        self.index = index
        self.failed_csv_path = failed_csv_path

        # Core collaborators
        self.filters = Filters(filters_path)
        self.discovery = Discovery(self.adb)
        self.path_mapper = PathMapper(source_root=self.source_dir, dest_root=self.dest_root)
        self.transfer = Transfer(self.adb, self.index, self.path_mapper)

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

        backup_root_name = os.path.basename(self.dest_root.rstrip("\\/")) or "Unknown"
        for folder in folders:
            try:
                files = self.discovery.list_files_recursive(folder)
            except Exception:
                files = []

            for abs_file in files:
                status = self.transfer.copy_file(abs_file, folder, backup_root_name)
                if status is CopyStatus.COPIED:
                    total_copied += 1
                elif status is CopyStatus.FAILED:
                    all_failed.append(abs_file)

        self.transfer.write_failed_csv(self.failed_csv_path, all_failed)
        return {"copied_count": total_copied, "failed_count": len(all_failed)}

    # -------- Internals --------
    def _discover_filtered_folders(self) -> List[str]:
        """
        Discover top-level folders under source_dir and apply substring-based filters.
        """
        raw = self.discovery.list_dirs_top(self.source_dir)
        return self.filters.filter_folders(raw)