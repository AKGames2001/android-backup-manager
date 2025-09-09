# gui/workers.py
"""
Background workers for discovery, backup, and restore.

Design:
- Worker-objects subclass QObject and are moved to QThread by the UI layer.
- Progress and logging are emitted via Qt signals to keep the GUI responsive.
- Each worker exposes a single run() entry point and reports results or errors.
"""

from __future__ import annotations

import os
from typing import Dict, List, Tuple
from PySide6.QtCore import QObject, Signal, Slot
from core.transfer import CopyStatus


class FolderDiscoveryWorker(QObject):
    """
    Worker that lists top-level device folders and applies filters.

    Emits:
      - finished(folders: list[str], message: str)
      - error(message: str)
    """
    finished = Signal(list, str)
    error = Signal(str)

    def __init__(self, discovery, source_dir: str, filters):
        super().__init__()
        self.discovery = discovery
        self.source_dir = source_dir
        self.filters = filters

    @Slot()
    def run(self) -> None:
        """Execute discovery and emit results or error."""
        try:
            raw = self.discovery.list_dirs_top(self.source_dir)
            if not raw:
                self.finished.emit([], "No folders found on device.")
                return
            allowed = self.filters.filter_folders(raw)
            preview = ", ".join([f.split("/")[-1] for f in allowed[:8]])
            msg = f"Found {len(allowed)} folder(s) after filters. Sample: [{preview}]"
            self.finished.emit(allowed, msg)
        except Exception as e:
            self.error.emit(f"Discovery failed: {e}")


class BackupWorker(QObject):
    """
    Worker that performs the backup process over one or more folders.

    Emits:
      - progress(current: int, total: int)
      - log(message: str)
      - finished(stats: dict)
      - error(message: str)

    Notes:
      - Only successful new copies are counted as 'copied'.
      - Failures are accumulated and written once to CSV at the end.
      - Aborts stop both inner file loops and outer folder loops cleanly.
    """
    progress = Signal(int, int)
    log = Signal(str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, service, folders: List[str] | None = None):
        super().__init__()
        self.service = service
        self.folders = folders
        self.abort = False

    @Slot()
    def run(self) -> None:
        """Entry point; run selected-only or full-filtered backup."""
        try:
            if self.folders is not None:
                stats = self._run_selected_only(self.folders)
            else:
                stats = self._run_all()
            self.finished.emit(stats)
        except Exception as e:
            self.error.emit(f"Backup failed: {e}")

    def _run_all(self) -> Dict[str, int]:
        """Backup all filtered folders discovered under service.source_dir."""
        from core.discovery import Discovery

        if not self.service.adb.is_device_connected():
            raise RuntimeError("No device connected.")

        discovery = Discovery(self.service.adb)
        folders = self.service.discovery.list_dirs_top(self.service.source_dir)
        allowed = self.service.filters.filter_folders(folders)

        # Pre-scan to compute total files for progress
        total_files = 0
        per_folder_files: Dict[str, List[str]] = {}
        for folder in allowed:
            try:
                files = discovery.list_files_recursive(folder)
            except Exception as e:
                self.log.emit(f"Skipping folder (list error): {folder} -> {e}")
                files = []
            per_folder_files[folder] = files
            total_files += len(files)

        return self._copy_folders(per_folder_files, total_files)

    def _run_selected_only(self, selected_folders: List[str]) -> Dict[str, int]:
        """Backup only the user-selected folders."""
        from core.discovery import Discovery

        if not self.service.adb.is_device_connected():
            raise RuntimeError("No device connected.")

        discovery = Discovery(self.service.adb)

        total_files = 0
        per_folder_files: Dict[str, List[str]] = {}
        for folder in selected_folders:
            try:
                files = discovery.list_files_recursive(folder)
            except Exception as e:
                self.log.emit(f"Skipping folder (list error): {folder} -> {e}")
                files = []
            per_folder_files[folder] = files
            total_files += len(files)

        return self._copy_folders(per_folder_files, total_files)

    def _copy_folders(self, per_folder_files: Dict[str, List[str]], total_files: int) -> Dict[str, int]:
        """
        Copy files for the provided folder->files map and update restore record once.

        Returns {'copied_count': int, 'failed_count': int}.
        """
        processed = 0
        total_copied = 0
        all_failed: List[str] = []
        session_rel_paths: set[str] = set()

        backup_root_name = os.path.basename(self.service.dest_root.rstrip("/\\") or "")

        for folder, files in per_folder_files.items():
            if self.abort:
                self.log.emit("Backup aborted by user.")
                break

            base_name = os.path.basename(folder.rstrip("/"))
            for f in files:
                if self.abort:
                    self.log.emit("Backup aborted by user.")
                    break

                status = self.service.transfer.copy_file(f, folder)
                processed += 1
                self.progress.emit(processed, total_files)

                if status is CopyStatus.COPIED:
                    # Compute device-relative path under top folder
                    rel = os.path.relpath(f, folder).replace("\\", "/")
                    rel_dev = f"{base_name}/{rel}".strip("/")
                    session_rel_paths.add(rel_dev)
                    total_copied += 1
                    self.log.emit(f"Copied: {f}")
                elif status is CopyStatus.SKIPPED:
                    self.log.emit(f"Skipped (already in record): {f}")
                else:
                    all_failed.append(f)
                    self.log.emit(f"Failed: {f}")

            if self.abort:
                break

        # Persist failed entries once
        self.service.transfer.write_failed_csv(self.service.failed_csv_path, all_failed)

        # Update restore record once per session
        if session_rel_paths and backup_root_name:
            self.service.restore_manager.add_or_update_root(
                root_name=backup_root_name,
                files=sorted(session_rel_paths),
                description=f"Backup on {backup_root_name}",
            )

        return {"copied_count": total_copied, "failed_count": len(all_failed)}


class RestoreWorker(QObject):
    """
    Worker that restores selected files from local backups back to the device.

    Items format:
      List[Tuple[str, List[str]]] where each entry is (rel_path, available_roots).
      rel_path examples: "Download/file.txt", "Pictures/Camera/img.jpg".
      available_roots are backup root names (e.g., "2025-09-06").

    Emits:
      - progress(current: int, total: int)
      - log(message: str)
      - finished(stats: dict)
      - error(message: str)
    """
    progress = Signal(int, int)
    log = Signal(str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, adb, base_backup_dir: str, source_dir: str, items: List[Tuple[str, List[str]]]):
        super().__init__()
        self.adb = adb
        self.base_backup_dir = base_backup_dir
        self.source_dir = source_dir.rstrip("/")
        self.items = items
        self.abort = False

    @Slot()
    def run(self) -> None:
        """Execute restore operations for queued items, respecting abort requests."""
        try:
            total = len(self.items)
            done = 0
            copied = 0
            failed: List[str] = []

            self.log.emit(f"Starting restore of {total} items")

            for rel_path, roots in self.items:
                if self.abort:
                    self.log.emit("Restore aborted by user.")
                    break

                preferred_root = sorted(roots)[-1] if roots else ""
                if not preferred_root:
                    failed.append(rel_path)
                    self.log.emit(f"No backup root for: {rel_path}")
                    done += 1
                    self.progress.emit(done, total)
                    continue

                # Build local path on PC
                local_path = os.path.join(self.base_backup_dir, preferred_root, rel_path.replace("/", os.sep))
                if not os.path.exists(local_path):
                    failed.append(rel_path)
                    self.log.emit(f"Local file missing: {local_path}")
                    done += 1
                    self.progress.emit(done, total)
                    continue

                # Build remote path on device and ensure parent directory exists
                remote_path = f"{self.source_dir}/{rel_path}"
                parent_dir = remote_path.rsplit("/", 1) if "/" in remote_path else self.source_dir
                parent_dir = parent_dir[0] if isinstance(parent_dir, list) else parent_dir
                try:
                    self.adb.ensure_remote_dir(parent_dir)
                    proc = self.adb.push(local_path, remote_path)
                    if getattr(proc, "returncode", 1) == 0:
                        copied += 1
                        self.log.emit(f"Restored: {rel_path} -> {preferred_root}")
                    else:
                        failed.append(rel_path)
                        self.log.emit(f"Failed restore: {rel_path}. Stderr: {(proc.stderr or '').strip()}")
                except Exception as e:
                    failed.append(rel_path)
                    self.log.emit(f"Failed restore: {rel_path}. Error: {e}")

                done += 1
                self.progress.emit(done, total)

            self.finished.emit({"restored_count": copied, "failed_count": len(failed)})
        except Exception as e:
            self.error.emit(f"Restore failed: {e}")
