# gui/app.py
"""
Application bootstrap for the GUI layer.

Responsibilities:
- Initialize QSettings with stable organization/app identifiers.
- Run the first-run setup wizard if required, writing app_config.json.
- Reload runtime configuration after successful setup.
- Construct and return the MainWindow with injected, explicit values.
"""

from __future__ import annotations

import importlib
from PySide6.QtCore import QSettings

import config.paths as cfg
from gui.main_window import MainWindow
from gui.first_run_wizard import FirstRunWizard
from config.paths import needs_first_run, write_app_config


# ---------- Constants ----------
ORG_NAME = "AndroidBackupManager"
APP_NAME = "App"


# ---------- Public API ----------
def create_app() -> MainWindow:
    """
    Create and return the main window.

    Steps:
      1) Initialize QSettings.
      2) If a first run is needed, present the setup wizard; on success, reload config.
      3) Instantiate MainWindow using the (possibly updated) configuration values.
    """
    # 1) App-scoped settings repository
    settings = QSettings(ORG_NAME, APP_NAME)

    # 2) First-run setup (writes app_config.json via write_app_config)
    if needs_first_run():
        defaults = {
            "ADB_PATH": cfg.ADB_PATH,
            "BASE_BACKUP_DIR": cfg.BASE_BACKUP_DIR,
            "DEFAULT_USER": cfg.DEFAULT_USER,
        }
        wiz = FirstRunWizard(defaults=defaults, write_config=write_app_config, qsettings=settings)
        if wiz.exec():  # Accepted
            # Reload only after a successful write to ensure fresh values
            importlib.reload(cfg)
            # Optionally, settings.sync() is already invoked by the wizard when accepted

    # 3) Create the main window with explicit, current config values
    window = MainWindow(
        initial_adb_path=cfg.ADB_PATH,
        initial_backup_base=cfg.BASE_BACKUP_DIR,
        initial_default_user=cfg.DEFAULT_USER,
    )
    window.setWindowTitle("Android Backup Manager")
    return window
