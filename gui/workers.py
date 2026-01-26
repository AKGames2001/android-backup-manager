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

from core.discovery import Discovery
from core.transfer import CopyStatus


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

    def __init__(self, service, folders: List = None) -> None:
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

    def _run_selected_only(self, selected) -> Dict[str, int]:
        """
        Backup only the user-selected items.

        selected format:
        - legacy: List[str] of folders
        - new: List[Tuple[path, is_dir]]
        """

        if not self.service.adb.is_device_connected():
            raise RuntimeError("No device connected.")

        discovery = Discovery(self.service.adb)
        total_files = 0
        per_folder_files: Dict[str, List[str]] = {}

        # selected may be:
        # - legacy: List[str] folders
        # - new: List[Tuple[path, isdir]]
        items: List[Tuple[str, bool]]
        if selected and isinstance(selected[0], (list, tuple)) and len(selected[0]) >= 2:
            items = [(p, bool(is_dir)) for (p, is_dir, *_) in selected]  # tolerate extra fields
        else:
            # Backwards compatibility: treat as folders only
            items = [(p, True) for p in (selected or [])]

        source_root = self.service.source_dir.rstrip("/")
        root_prefix = source_root

        # Collect all individual files that need to be copied
        all_files: List[str] = []
        for path, is_dir in items:
            if is_dir:
                try:
                    files = discovery.list_files_recursive(path)
                except Exception as e:
                    self.log.emit(f"Skipping folder (list error): {path} -> {e}")
                    files = []
                all_files.extend(files)
            else:
                # Single file path (absolute on device)
                all_files.append(path)

        # Deduplicate
        all_files = sorted(set(all_files))

        # Group by first-level folder under source_root (top name),
        # so that transfer.copy_file(f, folder_base) and restore_record
        # paths stay consistent with existing logic.
        for f in all_files:
            if not f.startswith(root_prefix):
                # Outside configured root; skip
                self.log.emit(f"Skipping path outside source root: {f}")
                continue
            rel_from_root = f[len(root_prefix):].lstrip("/")
            top = rel_from_root.split("/", 1)[0] if rel_from_root else ""
            if not top:
                continue
            base_dir = f"{source_root}/{top}"
            per_folder_files.setdefault(base_dir, []).append(f)

        for files in per_folder_files.values():
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
        backup_root_name = os.path.basename(self.service.dest_root.rstrip("\\/")) or "Unknown"

        for folder, files in per_folder_files.items():
            if self.abort:
                self.log.emit("Backup aborted by user.")
                break

            for f in files:
                if self.abort:
                    self.log.emit("Backup aborted by user.")
                    break

                status = self.service.transfer.copy_file(f, folder, backup_root_name)

                processed += 1
                self.progress.emit(processed, total_files)

                if status is CopyStatus.COPIED:
                    total_copied += 1
                    self.log.emit(f"Copied: {f}")
                elif status is CopyStatus.SKIPPED:
                    self.log.emit(f"Skipped (already indexed): {f}")
                else:
                    all_failed.append(f)
                    self.log.emit(f"Failed: {f}")

            if self.abort:
                break

        # Persist failed entries once
        self.service.transfer.write_failed_csv(self.service.failed_csv_path, all_failed)
        return {"copied_count": total_copied, "failed_count": len(all_failed)}


class RestoreWorker(QObject):
    """
    Items format: List[Tuple[device_rel, chosen_root, local_rel]]
    - device_rel: e.g. "Download/a.txt"
    - chosen_root: e.g. "2025-12-31"
    - local_rel: relative path under that root folder (Windows-safe if needed)
    """
    progress = Signal(int, int)
    log = Signal(str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, adb, base_backup_dir: str, source_dir: str, items: List[Tuple[str, str, str]]) -> None:
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
            restored = 0
            failed: List[str] = []

            self.log.emit(f"Starting restore of {total} items")

            for device_rel, root, local_rel in self.items:
                if self.abort:
                    self.log.emit("Restore aborted by user.")
                    break

                root = (root or "").strip()
                if not root:
                    failed.append(device_rel)
                    self.log.emit(f"No root selected for: {device_rel}")
                    done += 1
                    self.progress.emit(done, total)
                    continue
                
                # Build local path on PC
                local_path = os.path.join(
                    self.base_backup_dir,
                    root,
                    (local_rel or device_rel).replace("/", os.sep),
                )
                if not os.path.exists(local_path):
                    failed.append(device_rel)
                    self.log.emit(f"Local file missing: {local_path}")
                    done += 1
                    self.progress.emit(done, total)
                    continue

                remote_path = f"{self.source_dir}/{device_rel}".replace("\\", "/")
                parent_dir = remote_path.rsplit("/", 1)[0] if "/" in remote_path else self.source_dir

                try:
                    self.adb.ensure_remote_dir(parent_dir)
                    proc = self.adb.push(local_path, remote_path)
                    if getattr(proc, "returncode", 1) == 0:
                        restored += 1
                        self.log.emit(f"Restored: {device_rel} (from {root})")
                    else:
                        failed.append(device_rel)
                        self.log.emit(f"Failed restore: {device_rel}. Stderr: {(proc.stderr or '').strip()}")
                except Exception as e:
                    failed.append(device_rel)
                    self.log.emit(f"Failed restore: {device_rel}. Error: {e}")

                done += 1
                self.progress.emit(done, total)

            self.finished.emit({"restored_count": restored, "failed_count": len(failed)})
        except Exception as e:
            self.error.emit(f"Restore failed: {e}")


class FullTreeDiscoveryWorker(QObject):
    """
    Build a full nested tree from device files for allowed top-level folders.

    Emits:
      finished(tree: dict, msg: str)
      error(msg: str)
    """
    finished = Signal(dict, str)
    error = Signal(str)

    def __init__(self, discovery: Discovery, source_dir: str, filters) -> None:
        super().__init__()
        self.discovery = discovery
        self.source_dir = source_dir
        self.filters = filters

    @Slot()
    def run(self) -> None:
        try:
            raw = self.discovery.list_dirs_top(self.source_dir)
            allowed = self.filters.filter_folders(raw)

            # Build a nested dict:
            # { "Download": { "Sub": { "file.txt": {"__file__": "/sdcard/Download/Sub/file.txt"} } } }
            tree: dict = {}

            for top_dir in allowed:
                topdir_norm = top_dir.rstrip("/")
                top_name = topdir_norm.split("/")[-1] or topdir_norm

                # Top folder node
                top_node = tree.setdefault(top_name, {})
                top_node["__dir__"] = topdir_norm

                files = self.discovery.list_files_recursive(topdir_norm)
                prefix = topdir_norm.rstrip("/")

                for abs_path in files:
                    # abspath: e.g. sdcard/Download/a/b.txt
                    rel = abs_path[len(prefix):].lstrip("/") if abs_path.startswith(prefix) else abs_path
                    parts = [p for p in rel.split("/") if p]
                    if not parts:
                        continue

                    node = top_node
                    cur_abs_dir = topdir_norm

                    for idx, part in enumerate(parts):
                        is_last = idx == (len(parts) - 1)

                        if is_last:
                            node[part] = {"__file__": abs_path}
                        else:
                            cur_abs_dir = f"{cur_abs_dir}/{part}".rstrip("/")
                            child = node.get(part)
                            if not isinstance(child, dict):
                                child = {}
                                node[part] = child
                            child.setdefault("__dir__", cur_abs_dir)
                            node = child

            msg = f"Loaded full tree for {len(allowed)} top folder(s)."
            self.finished.emit(tree, msg)

        except Exception as e:
            self.error.emit(f"Full discovery failed: {e}")