# core/filters.py
"""
Folder filtering rules loaded from a JSON config.
"""

from __future__ import annotations

import json
from typing import Iterable, List, Tuple

# ---------- Constants ----------
EXCLUDED_KEY = "excluded_folders"
DEFAULT_EXCLUDED: Tuple[str, ...] = () 


# ---------- Helpers ----------
def _load_filters_file(path: str) -> Tuple[str, ...]:
    """
    Load the filters JSON file and return a tuple of excluded folder substrings.
    The file is expected to contain: {"excluded_folders": ["Android", ".SLOGAN", ...]}.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        # On any read/parse error, default to no exclusions.
        return DEFAULT_EXCLUDED
    raw = list(data.get(EXCLUDED_KEY, [])) if isinstance(data, dict) else []
    # Deduplicate while preserving order.
    seen = set()
    out: List[str] = []
    for s in raw:
        if isinstance(s, str) and s not in seen:
            seen.add(s)
            out.append(s)
    return tuple(out)


def _norm_folder_path(p: str) -> str:
    """
    Normalize a device folder path for matching:
    - Use forward slashes
    - Strip trailing slashes
    """
    return (p or "").replace("\\", "/").rstrip("/")


# ---------- Public API ----------
class Filters:
    """
    Substring-based folder exclusion loaded from a JSON file.

    Example JSON:
      { "excluded_folders": ["Android", ".SLOGAN"] }

    Semantics:
    - A folder is allowed if none of the excluded substrings occur in its normalized path.
    """

    def __init__(self, filters_path: str):
        """
        Initialize from a JSON file path and capture excluded substrings as a tuple.
        """
        self.excluded: Tuple[str, ...] = _load_filters_file(filters_path)

    def allow_folder(self, folder_path: str) -> bool:
        """
        Return True if the folder_path does NOT contain any excluded substring.

        Matching uses simple substring membership for each configured token,
        after normalizing the candidate path to forward slashes without a trailing slash.
        """
        candidate = _norm_folder_path(folder_path)
        return not any(ex in candidate for ex in self.excluded)

    def filter_folders(self, folders: Iterable[str]) -> List[str]:
        """
        Return only the folders that pass allow_folder, preserving input order.
        """
        return [f for f in folders if self.allow_folder(f)]
