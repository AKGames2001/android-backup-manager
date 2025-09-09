# core/restore_manager.py
"""
Manage restore metadata that maps backup roots to the files they contain.

File format (restore_record.json):
{
  "roots": {
    "2025-09-09": {
      "description": "Backup on 2025-09-09",
      "files": [
        "Download/file1.jpg",
        "Pictures/Camera/img001.jpg"
      ]
    }
  }
}

Design:
- Normalize all relative paths (forward slashes, no leading './', no trailing slash).
- Deduplicate per-root file lists and save in deterministic order for stable diffs.
- Persist using UTF‑8 JSON and atomic replacement to avoid partial writes.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Dict, List, Set, Tuple

# ---------- Constants ----------
ENCODING = "utf-8"
ROOTS_KEY = "roots"
FILES_KEY = "files"
DESC_KEY = "description"

EMPTY_RECORD: Dict[str, dict] = {ROOTS_KEY: {}}


# ---------- Helpers ----------
def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if not parent:
        raise ValueError(f"Invalid restore_record_path (no parent dir): {path}")
    os.makedirs(parent, exist_ok=True)


def _norm_rel_path(p: str) -> str:
    """Normalize a relative path for consistent storage."""
    s = (p or "").strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s.rstrip("/")


def _norm_root_name(name: str) -> str:
    """Normalize a root name (e.g., date folder) for consistent map keys."""
    return (name or "").strip().replace("\\", "/").strip("/")


def _atomic_write_json(path: str, data: Dict) -> None:
    """Write JSON atomically using a temp file then os.replace in the same directory."""
    _ensure_parent_dir(path)
    dir_ = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix=".tmp_restore_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding=ENCODING) as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


# ---------- Public API ----------
class RestoreManager:
    """
    Manage mapping of backup roots to lists of device-relative file paths.

    Each root entry is a dict:
      {"description": str, "files": List[str]}
    """

    def __init__(self, restore_record_path: str):
        """
        Initialize from a JSON file path; creates an empty structure if the file is missing.
        """
        self.restore_record_path = restore_record_path
        self.roots: Dict[str, Dict[str, List[str]]] = {}
        self.load()

    def load(self) -> None:
        """
        Load JSON record; on missing or malformed files, create/keep an empty structure.
        """
        if not os.path.exists(self.restore_record_path):
            # Create an empty record to make later saves straightforward
            _atomic_write_json(self.restore_record_path, EMPTY_RECORD)
            self.roots = {}
            return
        try:
            with open(self.restore_record_path, "r", encoding=ENCODING) as f:
                data = json.load(f)
            raw = data.get(ROOTS_KEY, {}) if isinstance(data, dict) else {}
        except Exception:
            raw = {}

        # Normalize and deduplicate per-root
        norm_roots: Dict[str, Dict[str, List[str]]] = {}
        for root_name, info in raw.items():
            rname = _norm_root_name(root_name)
            if not rname or not isinstance(info, dict):
                continue
            desc = info.get(DESC_KEY) or rname
            files_raw = info.get(FILES_KEY, [])
            files_set: Set[str] = {p for p in (_norm_rel_path(x) for x in files_raw) if p}
            norm_roots[rname] = {
                DESC_KEY: desc,
                FILES_KEY: sorted(files_set),
            }
        self.roots = norm_roots

    def save(self) -> None:
        """
        Persist current state with deterministic ordering and atomic replacement.
        """
        out: Dict[str, Dict] = {ROOTS_KEY: {}}
        for root in sorted(self.roots.keys()):
            info = self.roots[root]
            out[ROOTS_KEY][root] = {
                DESC_KEY: info.get(DESC_KEY) or root,
                FILES_KEY: sorted({p for p in (_norm_rel_path(x) for x in info.get(FILES_KEY, [])) if p}),
            }
        _atomic_write_json(self.restore_record_path, out)

    def add_root(self, root_name: str, description: str | None = None, files: List[str] | None = None) -> None:
        """
        Create or replace a root entry with the provided metadata and files.
        """
        rname = _norm_root_name(root_name)
        if not rname:
            return
        files = files or []
        files_set: Set[str] = {p for p in (_norm_rel_path(x) for x in files) if p}
        self.roots[rname] = {
            DESC_KEY: (description or rname),
            FILES_KEY: sorted(files_set),
        }
        self.save()

    def add_or_update_root(self, root_name: str, description: str | None = None, files: List[str] | None = None) -> None:
        """
        Merge unique files into an existing root or create a new root if missing.
        """
        rname = _norm_root_name(root_name)
        if not rname:
            return
        files = files or []
        add_set: Set[str] = {p for p in (_norm_rel_path(x) for x in files) if p}
        if rname in self.roots:
            cur = self.roots[rname]
            cur_set: Set[str] = set(cur.get(FILES_KEY, []))
            cur_set.update(add_set)
            cur[FILES_KEY] = sorted(cur_set)
            if description:
                cur[DESC_KEY] = description
        else:
            self.roots[rname] = {
                DESC_KEY: (description or rname),
                FILES_KEY: sorted(add_set),
            }
        self.save()

    def remove_root(self, root_name: str) -> bool:
        """
        Remove a root entirely; returns True if it existed.
        """
        rname = _norm_root_name(root_name)
        if rname in self.roots:
            del self.roots[rname]
            self.save()
            return True
        return False

    def get_all_files_tree(self) -> Dict:
        """
        Build a nested dict tree of all files across roots.

        Example output:
        {
          "WhatsApp": {
            "Media": {
              "Photos": {
                "img1.jpg": ["2023-08-15", "2025-09-09"]
              }
            }
          }
        }
        """
        tree: Dict = {}

        def add_path(node: Dict, parts: List[str], root_name: str) -> None:
            if not parts:
                return
            head = parts[0]
            if len(parts) == 1:
                node.setdefault(head, [])
                if root_name not in node[head]:
                    node[head].append(root_name)
                return
            node.setdefault(head, {})
            add_path(node[head], parts[1:], root_name)

        for root, info in self.roots.items():
            for rel in info.get(FILES_KEY, []):
                rel_norm = _norm_rel_path(rel)
                if not rel_norm:
                    continue
                add_path(tree, rel_norm.split("/"), root)
        return tree

    # Convenience accessors
    def list_roots(self) -> List[Tuple[str, str]]:
        """
        Return a list of (root_name, description) sorted by root_name.
        """
        return [(r, self.roots[r].get(DESC_KEY) or r) for r in sorted(self.roots.keys())]

    def files_for_root(self, root_name: str) -> List[str]:
        """
        Return the sorted list of files for a given root (or an empty list).
        """
        rname = _norm_root_name(root_name)
        info = self.roots.get(rname, {})
        return list(info.get(FILES_KEY, []))
