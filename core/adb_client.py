# core/adb_client.py
"""
ADB client wrapper used for running adb, shelling into the device, and transferring files.
"""

from __future__ import annotations
import os
import shlex
import subprocess
from typing import Optional

class ADBClient:
    def __init__(self, adb_path: str):
        """
        Initialize the client with an absolute path to adb.exe (or adb on *nix).   
        """
        self.adb_path = adb_path

    def _run(self, args, capture: bool = True, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        """
        Run adb with list-form args and return CompletedProcess; shell() wraps this to raise on errors.   
        """
        if isinstance(args, str):
            args = shlex.split(args)
        cmd = [self.adb_path] + list(args)

        # Hide console windows on Windows.
        creationflags = 0
        startupinfo = None
        if os.name == "nt":
            creationflags |= subprocess.CREATE_NO_WINDOW   
            startupinfo = subprocess.STARTUPINFO()         
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW   

        return subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            shell=False,
            timeout=timeout,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )

    def shell(self, shell_cmd, timeout: int = 10) -> str:
        """
        Run `adb shell ...` and return stripped stdout; raise RuntimeError on nonzero exit or timeout.   
        """
        shell_args = shlex.split(shell_cmd) if isinstance(shell_cmd, str) else list(shell_cmd)
        try:
            proc = self._run(["shell"] + shell_args, capture=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"ADB shell timed out: {' '.join(shell_args)}") from e
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()
            raise RuntimeError(f"ADB shell failed ({proc.returncode}). Stderr: {stderr or ''}. Stdout: {stdout or ''}")
        return (proc.stdout or "").strip()

    def is_device_connected(self) -> bool:
        """
        Return True only when at least one device is listed as 'device' (not unauthorized/offline).   
        """
        proc = self._run(["devices"])
        out = (proc.stdout or "") + (proc.stderr or "")
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        return any(line.endswith("\tdevice") for line in lines[1:]) 

    def pull(self, remote: str, local: str) -> subprocess.CompletedProcess:
        """
        Pull a single file from the device to a local absolute path, creating parent dirs first.   
        """
        os.makedirs(os.path.dirname(local), exist_ok=True)
        return self._run(["pull", remote, local])

    def pull_dir(self, remote_dir: str, local_dir: str) -> subprocess.CompletedProcess:
        """
        Pull a directory tree from the device to a local directory, creating it if necessary.   
        """
        os.makedirs(local_dir, exist_ok=True)
        return self._run(["pull", remote_dir, local_dir])

    def push(self, local: str, remote: str) -> subprocess.CompletedProcess:
        """
        Push a single local file to the device; ensure_remote_dir(...) should be called first.   
        """
        return self._run(["push", local, remote], capture=True)

    def ensure_remote_dir(self, remote_dir: str, timeout: int = 10) -> None:
        """
        Create the remote directory (and parents) using toybox/busybox mkdir -p if not present.   
        """
        if not isinstance(remote_dir, str):
            raise TypeError(f"remote_dir must be str, got {type(remote_dir).__name__}")
        remote_dir = remote_dir.strip()
        if not remote_dir:
            return
        self.shell(f'mkdir -p "{remote_dir}"', timeout=timeout)
