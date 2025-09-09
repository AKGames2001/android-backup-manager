"""
Public API for gui.widgets.
Re-exports common sub-widgets used across the GUI.
"""

from __future__ import annotations

from .folder_list import FolderList
from .log_console import LogConsole

__all__ = ["FolderList", "LogConsole"]
