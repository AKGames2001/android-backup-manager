# core/record.py
"""
Persistent record of files already included in backups.

Design:
- Store a single set of normalized relative paths under the JSON key "included_folders".
- Keep I/O simple and robust: UTF-8 JSON with deterministic ordering, and safe writes via temp-file + os.replace.
- Provide small, clear methods (contains, add) used by the transfer flow, plus optional helpers (add_all, remove, clear).
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Dict, Iterable, List, Set

# ---------- Constants ----------
RECORD_KEY = "included_folders"  # JSON key persisted on disk
ENCODING = "utf-8"               # UTF-8 is the recommended default for JSON text [RFC 8259]
EMPTY_RECORD: Dict[str, List[str]] = {RECORD_KEY: []}


# ---------- Helpers ----------
def _norm_rel_path(p: str) -> str:
    """
    Normalize a relative path string for consistent storage:
    - Trim whitespace
    - Use forward slashes
    - Remove leading './' and trailing slashes
    """
    s = (p or "").strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    s = s.rstrip("/")
    return s


def _ensure_parent_dir(path: str) -> None:
    """Create the parent directory for 'path' if missing."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _atomic_write_json(path: str, data: Dict) -> None:
    """
    Safely write JSON to 'path' by writing to a temp file in the same directory and replacing the target.
    This ensures a complete file is always visible and reduces the risk of corruption on crashes.  # noqa: E501
    """
    _ensure_parent_dir(path)
    dir_ = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix=".tmp_record_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding=ENCODING) as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        # Replace target atomically on same filesystem
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


# ---------- Public API ----------
class RecordStore:
    """
    JSON-backed set of relative paths that have already been included in backups.

    File format:
      {
        "included_folders": [
          "Download/file.txt",
          "Pictures/Camera/img001.jpg"
        ]
      }
    """

    def __init__(self, record_path: str):
        """
        Initialize the store and load existing entries (or create an empty file if missing).
        """
        self.record_path = record_path
        self._folders: Set[str] = set()
        self._load()

    def _load(self) -> None:
        """
        Load the JSON record from disk, tolerating a missing or malformed file by falling back to empty.
        """
        if not os.path.exists(self.record_path):
            _atomic_write_json(self.record_path, EMPTY_RECORD)
            self._folders = set()
            return

        try:
            with open(self.record_path, "r", encoding=ENCODING) as f:
                data = json.load(f)
            items = data.get(RECORD_KEY, []) if isinstance(data, dict) else []
            # Normalize and deduplicate
            self._folders = {p for p in (_norm_rel_path(x) for x in items) if p}
        except Exception:
            # On any error, reset to empty to avoid crashing the app flow
            self._folders = set()

    def _save(self) -> None:
        """
        Persist current state to disk with deterministic ordering and safe replacement.
        """
        data = {RECORD_KEY: sorted(self._folders)}
        _atomic_write_json(self.record_path, data)

    @property
    def included(self) -> Set[str]:
        """
        Return a copy of the current set of recorded relative paths.
        """
        return set(self._folders)

    def contains(self, rel_path: str) -> bool:
        """
        Return True if rel_path (normalized) is already recorded.
        """
        return _norm_rel_path(rel_path) in self._folders

    def add(self, rel_path: str) -> None:
        """
        Add a single relative path to the record and save if it was not present.
        """
        p = _norm_rel_path(rel_path)
        if not p or p in self._folders:
            return
        self._folders.add(p)
        self._save()

    # -------- Optional conveniences (not required by current callers) --------
    def add_all(self, rel_paths: Iterable[str]) -> int:
        """
        Add multiple relative paths and save once; returns the number of new items added.
        """
        before = len(self._folders)
        for rel in rel_paths:
            p = _norm_rel_path(rel)
            if p:
                self._folders.add(p)
        if len(self._folders) != before:
            self._save()
        return len(self._folders) - before

    def remove(self, rel_path: str) -> bool:
        """
        Remove a single relative path if present and save; returns True if removed.
        """
        p = _norm_rel_path(rel_path)
        if p in self._folders:
            self._folders.remove(p)
            self._save()
            return True
        return False

    def clear(self) -> None:
        """
        Clear all recorded entries and save an empty record.
        """
        if self._folders:
            self._folders.clear()
            self._save()
