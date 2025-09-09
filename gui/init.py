"""
GUI package public API.

Exports high-level widgets and helpers for constructing and styling the application UI:
- MainWindow (primary app window)
- FirstRunWizard (initial setup flow)
- RestoreWidget (restore tab UI)
- FolderList, LogConsole (reusable sub-widgets)
- BASE_STYLE (consistent app-wide stylesheet)
"""

from __future__ import annotations

# Re-export selected UI components for convenient imports
from .main_window import MainWindow            # Primary window [MW]
from .first_run_wizard import FirstRunWizard   # First-run setup wizard [FRW]
from .restore_widget import RestoreWidget      # Restore UI (tab/panel) [RW]
from .widgets.folder_list import FolderList    # Left-side folder list UI [FL]
from .widgets.log_console import LogConsole    # Log display panel [LC]
from .style import BASE_STYLE                  # Global stylesheet [STY]

# Optional package metadata
__version__ = "0.1.0"

# Public API surface (wildcard imports and readable discovery)
__all__ = [
    "MainWindow",
    "FirstRunWizard",
    "RestoreWidget",
    "FolderList",
    "LogConsole",
    "BASE_STYLE",
    "__version__",
]
