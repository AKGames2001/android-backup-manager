from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional


ENCODING = "utf-8"
SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _atomic_write_json(path: str, data: dict) -> None:
    _ensure_parent_dir(path)
    d = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=d, prefix=".tmpindex_", suffix=".json")
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


def norm_rel_path(p: str) -> str:
    s = (p or "").strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s.rstrip("/")


def norm_root_name(name: str) -> str:
    return (name or "").strip().replace("\\", "/").strip("/")


@dataclass(frozen=True)
class FileVersion:
    root: str
    backup_ts: str
    local_rel: str
    remote_mtime: Optional[int] = None
    remote_size: Optional[int] = None


class UnifiedIndex:
    """
    Unified store replacing:
    - RecordStore (skip logic)
    - RestoreManager (roots->files mapping)

    Primary key: device-relative path (POSIX style), e.g. "Download/a.txt"
    """

    def __init__(
        self,
        index_path: str,
        device_root: str = "sdcard",
        migrate_record_path: Optional[str] = None,
        migrate_restore_record_path: Optional[str] = None,
    ) -> None:
        self.index_path = index_path
        self.device_root = (device_root or "sdcard").strip().rstrip("/")

        self._files: Dict[str, Dict] = {}  # relpath -> {"versions": [dict], "latest": dict|None}
        self._load_or_init()

        if migrate_record_path or migrate_restore_record_path:
            changed = self._migrate_from_legacy(migrate_record_path, migrate_restore_record_path)
            if changed:
                self.save()

    # ---------- Persistence ----------

    def _load_or_init(self) -> None:
        if not os.path.exists(self.index_path):
            self._files = {}
            self.save()
            return

        try:
            with open(self.index_path, "r", encoding=ENCODING) as f:
                data = json.load(f) if f else {}
        except Exception:
            data = {}

        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            self._files = {}
            self.save()
            return

        raw_files = data.get("files", {})
        self._files = raw_files if isinstance(raw_files, dict) else {}

    def save(self) -> None:
        out = {
            "schema_version": SCHEMA_VERSION,
            "device_root": self.device_root,
            "files": self._files,
        }
        _atomic_write_json(self.index_path, out)

    # ---------- Legacy migration ----------

    def _migrate_from_legacy(
        self,
        record_path: Optional[str],
        restore_record_path: Optional[str],
    ) -> bool:
        changed = False

        # record.json: {"includedfolders": [...]}
        if record_path and os.path.exists(record_path):
            try:
                with open(record_path, "r", encoding=ENCODING) as f:
                    data = json.load(f)
                included = data.get("includedfolders", []) if isinstance(data, dict) else []
                if isinstance(included, list):
                    for p in included:
                        rel = norm_rel_path(str(p))
                        if not rel:
                            continue
                        if rel not in self._files:
                            self._files[rel] = {"versions": [], "latest": None}
                            changed = True
            except Exception:
                pass

        # restore_record.json: {"roots": {"2025-12-31": {"files": [...]}}}
        if restore_record_path and os.path.exists(restore_record_path):
            try:
                with open(restore_record_path, "r", encoding=ENCODING) as f:
                    data = json.load(f)
                roots = data.get("roots", {}) if isinstance(data, dict) else {}
                if isinstance(roots, dict):
                    for root_name, info in roots.items():
                        r = norm_root_name(str(root_name))
                        if not r or not isinstance(info, dict):
                            continue
                        files = info.get("files", [])
                        if not isinstance(files, list):
                            continue
                        for p in files:
                            rel = norm_rel_path(str(p))
                            if not rel:
                                continue
                            self.note_backup(
                                relpath=rel,
                                root=r,
                                backup_ts=None,         # unknown from legacy
                                local_rel=rel,          # best-effort for legacy
                                remote_mtime=None,
                                remote_size=None,
                                save=False,
                            )
                            changed = True
            except Exception:
                pass

        return changed

    # ---------- Query helpers ----------

    def has_path(self, relpath: str) -> bool:
        rel = norm_rel_path(relpath)
        return bool(rel) and rel in self._files and (
            bool(self._files[rel].get("versions")) or self._files[rel].get("latest") is not None
        )

    def roots_for(self, relpath: str) -> List[str]:
        rel = norm_rel_path(relpath)
        info = self._files.get(rel) or {}
        versions = info.get("versions") or []
        roots: List[str] = []
        for v in versions:
            if isinstance(v, dict):
                r = norm_root_name(v.get("root", ""))
                if r:
                    roots.append(r)
        # Also include latest root if present but versions list is empty (edge cases)
        latest = info.get("latest")
        if isinstance(latest, dict):
            r = norm_root_name(latest.get("root", ""))
            if r:
                roots.append(r)
        return sorted(set(roots))

    def latest_root_for(self, relpath: str) -> Optional[str]:
        rel = norm_rel_path(relpath)
        info = self._files.get(rel) or {}
        latest = info.get("latest")
        if isinstance(latest, dict):
            r = norm_root_name(latest.get("root", ""))
            return r or None
        roots = self.roots_for(rel)
        return roots[-1] if roots else None

    def local_rel_for(self, relpath: str, root: str) -> Optional[str]:
        rel = norm_rel_path(relpath)
        r = norm_root_name(root)
        info = self._files.get(rel) or {}
        versions = info.get("versions") or []
        if isinstance(versions, list):
            for v in versions:
                if not isinstance(v, dict):
                    continue
                if norm_root_name(v.get("root", "")) == r:
                    lr = norm_rel_path(v.get("local_rel", ""))
                    return lr or None
        latest = info.get("latest")
        if isinstance(latest, dict) and norm_root_name(latest.get("root", "")) == r:
            lr = norm_rel_path(latest.get("local_rel", ""))
            return lr or None
        return None

    def get_tree(self) -> Dict:
        """
        Nested dict: directory nodes are dict; leaf nodes are list[str] roots.
        Compatible with existing RestoreWidget insert logic.
        """
        tree: Dict = {}
        for rel in sorted(self._files.keys()):
            reln = norm_rel_path(rel)
            if not reln:
                continue
            roots = self.roots_for(reln)
            if not roots:
                roots = ["Unknown"]

            parts = [p for p in reln.split("/") if p]
            if not parts:
                continue

            node = tree
            for i, part in enumerate(parts):
                is_last = i == (len(parts) - 1)
                if is_last:
                    node.setdefault(part, roots)
                else:
                    node = node.setdefault(part, {})
        return tree

    # ---------- Update API ----------

    def note_backup(
        self,
        relpath: str,
        root: str,
        backup_ts: Optional[str] = None,
        local_rel: Optional[str] = None,
        remote_mtime: Optional[int] = None,
        remote_size: Optional[int] = None,
        save: bool = True,
    ) -> None:
        rel = norm_rel_path(relpath)
        r = norm_root_name(root)
        if not rel or not r:
            return

        v = FileVersion(
            root=r,
            backup_ts=backup_ts or _now_iso(),
            local_rel=norm_rel_path(local_rel or rel),
            remote_mtime=remote_mtime,
            remote_size=remote_size,
        )

        info = self._files.setdefault(rel, {"versions": [], "latest": None})
        versions = info.get("versions")
        if not isinstance(versions, list):
            versions = []
            info["versions"] = versions

        # Dedup by (root, local_rel)
        exists = any(
            isinstance(x, dict)
            and norm_root_name(x.get("root", "")) == v.root
            and norm_rel_path(x.get("local_rel", "")) == v.local_rel
            for x in versions
        )
        if not exists:
            versions.append({
                "root": v.root,
                "backup_ts": v.backup_ts,
                "local_rel": v.local_rel,
                "remote_mtime": v.remote_mtime,
                "remote_size": v.remote_size,
            })

        latest = info.get("latest")
        latest_root = None
        if isinstance(latest, dict):
            latest_root = norm_root_name(latest.get("root", "")) or None

        # Prefer lexicographically max root (works for YYYY-MM-DD)
        if latest_root is None or v.root >= latest_root:
            info["latest"] = {
                "root": v.root,
                "backup_ts": v.backup_ts,
                "local_rel": v.local_rel,
                "remote_mtime": v.remote_mtime,
                "remote_size": v.remote_size,
            }

        if save:
            self.save()
