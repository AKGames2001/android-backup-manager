# core/discovery.py
"""
Directory and file discovery on Android storage via ADB.
"""

from __future__ import annotations
from typing import List, Tuple

# Tunable timeouts (seconds)
TOP_DIRS_TIMEOUT = 8
FIND_TIMEOUT = 20
LS_RECURSIVE_TIMEOUT = 25


class Discovery:
    """High-level directory and file discovery via ADB."""

    def __init__(self, adb_client):
        """
        adb_client: instance exposing .shell(cmd: str|list, timeout: int) -> str; raises on failure.
        """
        self.adb = adb_client 
        
    def list_dirs_top(self, source_dir: str) -> List[str]:
        """
        List top-level directories immediately under `source_dir` using a portable `ls -1p` approach.

        Returns absolute paths like `/sdcard/Download` (no trailing slash), sorted, unique.
        """
        base = source_dir.rstrip("/")
        out = self.adb.shell(f'ls -1p "{base}"', timeout=TOP_DIRS_TIMEOUT)
        if not out:
            return []
        dirs = []
        seen = set()
        for line in out.splitlines():
            name = line.strip()
            if not name or not name.endswith("/"):
                continue
            path = f"{base}/{name[:-1]}" 
            if path not in seen:
                seen.add(path)
                dirs.append(path)
        dirs.sort()
        return dirs
    
    def list_entries(self, dir_path: str) -> List[Tuple[str, bool]]:
        """
        List immediate children of dir_path.
        Returns: [(absolute_path, is_dir), ...] sorted with folders first.
        """
        base = dir_path.rstrip("/")
        out = self.adb.shell(f'ls -1p "{base}"', timeout=TOP_DIRS_TIMEOUT)
        if not out:
            return []

        entries: List[Tuple[str, bool]] = []
        for raw in out.splitlines():
            name = raw.strip()
            if not name:
                continue
            is_dir = name.endswith("/")
            clean = name[:-1] if is_dir else name
            full = f"{base}/{clean}"
            entries.append((full, is_dir))

        # Folders first, then files, alphabetical
        entries.sort(key=lambda t: (not t[1], t[0].lower()))
        return entries

    def list_files_recursive(self, base_dir: str) -> List[str]:
        """
        Recursively list all files under `base_dir`.

        Order of attempts (first success wins):
          1) toybox `find` (standard on modern Android)
          2) busybox `find` (if present)
          3) ls -R parsing (last resort; heuristic)

        Returns absolute paths like `/sdcard/Download/file.txt`, sorted and unique.
        """
        base = base_dir.rstrip("/")
        # 1) Standard / toybox find
        try:
            return self._run_find(["find", base, "-type", "f"], timeout=FIND_TIMEOUT)
        except Exception:
            pass

        # 2) Busybox find (if available)
        try:
            return self._run_find(["busybox", "find", base, "-type", "f"], timeout=FIND_TIMEOUT)
        except Exception:
            pass

        # 3) ls -R parse (heaviest fallback)
        out = self.adb.shell(f'ls -1R "{base}"', timeout=LS_RECURSIVE_TIMEOUT)
        return self._parse_ls_recursive(base, out)

    def _run_find(self, argv: List[str], timeout: int) -> List[str]:
        """
        Execute a find-like command and return sorted, unique file paths.

        argv examples:
          - ["find", "/sdcard/Download", "-type", "f"]
          - ["busybox", "find", "/sdcard/Download", "-type", "f"]
        """
        out = self.adb.shell(argv, timeout=timeout)
        files = [l.strip() for l in out.splitlines() if l.strip()]
        files = sorted(set(files))
        return files

    @staticmethod
    def _parse_ls_recursive(base_dir: str, ls_out: str) -> List[str]:
        """
        Parse `ls -R` output to collect file paths under `base_dir`.

        Notes:
          - This is locale/variant dependent and used only as a last resort.
          - Recognizes directory headers of form `/path:` and lines ending with `/` as subdirs.
        """
        files: List[str] = []
        current_dir = base_dir.rstrip("/")
        for raw in ls_out.splitlines():
            line = raw.rstrip()
            if not line:
                continue
            if line.endswith(":"):
                current_dir = line[:-1]
                continue
            if line.endswith("/"):
                continue
            files.append(f"{current_dir.rstrip('/')}/{line}")
        files = sorted(set(files))
        return files
