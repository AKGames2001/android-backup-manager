# core/paths.py
"""
Map device (POSIX-style) paths to local filesystem paths for backup and restore.

Design:
- Treat all device paths as POSIX using `posixpath` to avoid host-OS path semantics.
- Preserve the device's directory structure under a base folder name (e.g., "Download").
- Sanitize path components that are invalid on Windows (e.g., replace ':' with '_') when mapping to local paths.
"""

from __future__ import annotations

import os
import posixpath as pp  # device paths are POSIX-style (e.g., /sdcard/Download/file.jpg)

# ---------- Constants ----------
# Minimal, conservative character translation for Windows compatibility.
# Colon is illegal in Windows filenames and commonly appears in device-side names.
# Extend this map if more characters must be sanitized in the future.
SAFE_CHAR_MAP = {
    ":": "_",  # Windows-prohibited in filenames
}


# ---------- Helpers ----------
def _sanitize_component(name: str) -> str:
    """
    Make a single path component safe for Windows by replacing disallowed characters.

    Currently replaces ':' with '_' to avoid invalid filename errors on Windows.
    """
    out = name
    for bad, repl in SAFE_CHAR_MAP.items():
        out = out.replace(bad, repl)
    return out


def _basename_no_slash(remote_dir: str) -> str:
    """
    Return the last component of a POSIX path without trailing slash.
    """
    base = pp.basename(remote_dir.rstrip("/"))
    return _sanitize_component(base)


# ---------- Public API ----------
class PathMapper:
    """
    Convert absolute device paths to relative and local filesystem paths.

    Example:
        base_remote_dir = "/sdcard/Download"
        absolute_remote_path = "/sdcard/Download/Sub/f.txt"
        to_relative(...) -> "Sub/f.txt"
        to_local(...) -> "<dest_root>/Download/Sub/f.txt" (with safe components on Windows)
    """

    def __init__(self, source_root: str, dest_root: str):
        """
        Parameters:
            source_root: Device root (POSIX) for context (not strictly required by current methods).
            dest_root: Local destination root directory for this backup session.
        """
        self.source_root = source_root.rstrip("/") + "/"
        self.dest_root = dest_root

    def to_relative(self, absolute_remote_path: str, base_remote_dir: str) -> str:
        """
        Compute device-relative path of an absolute file under base_remote_dir (POSIX).

        Returns a relative POSIX path (e.g., "Sub/f.txt"), with any unsafe characters
        translated per SAFE_CHAR_MAP in the path segments (without altering separators).
        """
        base = base_remote_dir.rstrip("/")
        rel_posix = pp.relpath(absolute_remote_path, base)
        # Sanitize only characters that are illegal on Windows; keep POSIX separators.
        rel_safe = "/".join(_sanitize_component(part) for part in rel_posix.split("/"))
        return rel_safe.strip()

    def to_local(self, absolute_remote_path: str, base_remote_dir: str) -> str:
        """
        Map a device absolute path to a local filesystem path under dest_root.

        Layout: <dest_root>/<top_folder_name>/<relative_posix_path>
        - top_folder_name is the last segment of base_remote_dir (sanitized for Windows).
        - relative_posix_path uses the same directory structure as on the device,
          with unsafe characters in components sanitized.
        """
        rel_safe = self.to_relative(absolute_remote_path, base_remote_dir)
        top_name = _basename_no_slash(base_remote_dir)
        # Join using os.path for the local filesystem; split the POSIX rel path safely.
        local_path = os.path.join(self.dest_root, top_name, *rel_safe.split("/"))
        return local_path

    def to_local_base_dir(self, base_remote_dir: str) -> str:
        """
        Local directory corresponding to the top-level device folder under dest_root.

        Example: base_remote_dir='/sdcard/Download' -> '<dest_root>/Download'
        """
        top_name = _basename_no_slash(base_remote_dir)
        return os.path.join(self.dest_root, top_name)
