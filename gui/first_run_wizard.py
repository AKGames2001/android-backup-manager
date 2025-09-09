# gui/first_run_wizard.py
"""
First-run setup wizard.

Pages:
- ADBPage: Pick a valid adb executable (probed using `adb version` with timeout).
- BackupPage: Pick a writable base backup directory.
- UserPage: Enter the default user name.

Design notes:
- Each page reimplements isComplete() and emits completeChanged when state changes so QWizard enables/disables Next/Finish appropriately.
- QFileDialog static helpers are used for straightforward file/folder selection across platforms.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QFormLayout, QLineEdit, QPushButton,
    QLabel, QFileDialog, QWidget, QHBoxLayout, QMessageBox, QGroupBox,
)

from gui.style import BASE_STYLE


# ---------- Constants ----------
ADB_HINT = "Select a valid adb.exe; it should respond to `adb version`."
FOLDER_HINT = "Choose a writable folder (permissions required)."
USER_HINT = "Enter a non-empty user name."
ADB_PROBE_TIMEOUT_SECS = 5


# ---------- Data model ----------
@dataclass
class FirstRunResult:
    adb_path: str
    backup_base: str
    default_user: str


# ---------- Helpers ----------
def _file_exists(path: str) -> bool:
    """True if path points to an existing file."""
    try:
        return bool(path) and os.path.isfile(path)
    except Exception:
        return False


def _dir_writable(path: str) -> bool:
    """True if path exists or can be created and written to."""
    try:
        if not path:
            return False
        os.makedirs(path, exist_ok=True)
        test_file = os.path.join(path, ".perm_test.tmp")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(test_file)
        return True
    except Exception:
        return False


def _probe_adb_version(adb_path: str, timeout: int = ADB_PROBE_TIMEOUT_SECS) -> str:
    """
    Return stdout/stderr from `adb version` on success; empty string otherwise.

    Uses subprocess.run with a short timeout to avoid UI hangs and surfaces failures safely.
    """
    if not _file_exists(adb_path):
        return ""
    try:
        proc = subprocess.run(
            [adb_path, "version"], capture_output=True, text=True, timeout=timeout, shell=False
        )
        if proc.returncode == 0:
            out = (proc.stdout or proc.stderr or "").strip()
            return out
    except Exception:
        pass
    return ""


class PathPicker(QWidget):
    """
    Small composite widget with a QLineEdit and a Browse button.

    - If is_dir is True, opens a folder picker; otherwise, opens a file picker for executables.
    """
    def __init__(self, is_dir: bool, parent=None):
        super().__init__(parent)
        self.is_dir = is_dir
        self.edit = QLineEdit(self)
        self.browse = QPushButton("Browse...", self)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.browse)
        self.browse.clicked.connect(self._on_browse)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:
        self.edit.setText(value or "")

    def _on_browse(self) -> None:
        if self.is_dir:
            d = QFileDialog.getExistingDirectory(self, "Choose folder")
            if d:
                self.setText(d)
        else:
            # Keep a simple executable filter on Windows; allow all files otherwise.
            f, _ = QFileDialog.getOpenFileName(
                self, "Choose file", "", "Executables (*.exe);;All Files (*)"
            )
            if f:
                self.setText(f)


# ---------- Wizard Pages ----------
class ADBPage(QWizardPage):
    """Collect and validate the adb executable path."""
    def __init__(self, initial: str = "", parent=None):
        super().__init__(parent)
        self.setTitle("ADB tool")
        self._status = QLabel("")
        self._status.setObjectName("status_lbl")
        self._status.setWordWrap(True)

        self.picker = PathPicker(is_dir=False)
        self.picker.setText(initial)
        self.picker.edit.textChanged.connect(self._on_changed)

        form = QFormLayout()
        form.setFormAlignment(Qt.AlignTop)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setContentsMargins(8, 8, 8, 8)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.addRow("adb.exe:", self.picker)

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)
        v.addLayout(form)
        v.addWidget(self._status)

        # Initial evaluation
        self._on_changed()

    def _on_changed(self, _=None) -> None:
        # Notify QWizard to reevaluate button enablement
        self.completeChanged.emit()  # required when overriding isComplete
        self._paint_status()

    def _paint_status(self) -> None:
        path = self.picker.text()
        ok = _file_exists(path) and bool(_probe_adb_version(path))
        self._status.setText("Validated." if ok else ADB_HINT)
        self._status.setProperty("state", "ok" if ok else "error")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def isComplete(self) -> bool:
        path = self.picker.text()
        return _file_exists(path) and bool(_probe_adb_version(path))

    def validatePage(self) -> bool:
        return self.isComplete()

    def value(self) -> str:
        return self.picker.text()


class BackupPage(QWizardPage):
    """Collect and validate the base backup directory."""
    def __init__(self, initial: str = "", parent=None):
        super().__init__(parent)
        self.setTitle("Backup folder")

        self._status = QLabel("")
        self._status.setObjectName("status_lbl")
        self._status.setWordWrap(True)

        self.picker = PathPicker(is_dir=True)
        self.picker.setText(initial)
        self.picker.edit.textChanged.connect(lambda _: self._on_changed())

        form = QFormLayout()
        form.setFormAlignment(Qt.AlignTop)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setContentsMargins(8, 8, 8, 8)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.addRow("Base backup folder:", self.picker)

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)
        v.addLayout(form)
        v.addWidget(self._status)

        self._on_changed()

    def _on_changed(self) -> None:
        self.completeChanged.emit()
        self._paint_status()

    def _paint_status(self) -> None:
        p = self.picker.text()
        ok = _dir_writable(p)
        self._status.setText("Folder is writable." if ok else FOLDER_HINT)
        self._status.setProperty("state", "ok" if ok else "error")
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def isComplete(self) -> bool:
        return _dir_writable(self.picker.text())

    def validatePage(self) -> bool:
        return self.isComplete()

    def value(self) -> str:
        return self.picker.text()


class UserPage(QWizardPage):
    """Collect the default user name."""
    def __init__(self, initial: str = "", parent=None):
        super().__init__(parent)
        self.setTitle("Default user")

        self.user_edit = QLineEdit(initial)
        self.user_edit.setPlaceholderText("e.g., Aditya")
        self.user_edit.textChanged.connect(lambda _: self._on_changed())

        form = QFormLayout()
        form.setFormAlignment(Qt.AlignTop)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setContentsMargins(8, 8, 8, 8)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.addRow("Default user:", self.user_edit)

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)
        v.addLayout(form)

    def _on_changed(self) -> None:
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return bool((self.user_edit.text() or "").strip())

    def validatePage(self) -> bool:
        if not self.isComplete():
            QMessageBox.warning(self, "User", USER_HINT)
            return False
        return True

    def value(self) -> str:
        return (self.user_edit.text() or "").strip()


# ---------- Wizard ----------
class FirstRunWizard(QWizard):
    """
    First-time setup wizard.

    Accept handler writes app_config.json via a callback and stores a few values to QSettings.
    """
    def __init__(
        self,
        defaults: Dict[str, str],
        write_config: Callable[[Dict[str, Any]], str],
        qsettings: Optional[QSettings] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("First-time setup")

        # Theme (reuse base app style; apply minimal extra hints)
        self.setStyleSheet(
            BASE_STYLE + 
            """
            QWizard { background-color: #111418; }
            QWizardPage { background-color: #111418; }
            QLabel#status_lbl[state="ok"] { color: #7CD992; }
            QLabel#status_lbl[state="error"] { color: #FF6B6B; }
            """
        )

        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.HaveHelpButton, False)

        self._write_config = write_config
        self._qsettings = qsettings

        self.page_adb = ADBPage(defaults.get("ADB_PATH", ""))
        self.page_backup = BackupPage(defaults.get("BASE_BACKUP_DIR", ""))
        self.page_user = UserPage(defaults.get("DEFAULT_USER", ""))

        self.addPage(self.page_adb)
        self.addPage(self.page_backup)
        self.addPage(self.page_user)

        self.setButtonText(QWizard.NextButton, "Next")
        self.setButtonText(QWizard.BackButton, "Back")
        self.setButtonText(QWizard.FinishButton, "Finish")
        self.setButtonText(QWizard.CancelButton, "Cancel")

    def accept(self) -> None:
        """Persist configuration on Finish and close the wizard."""
        result = FirstRunResult(
            adb_path=self.page_adb.value(),
            backup_base=self.page_backup.value(),
            default_user=self.page_user.value(),
        )

        cfg = {
            "ADB_PATH": result.adb_path,
            "SOURCE_DIR": "/sdcard/",
            "BASE_BACKUP_DIR": result.backup_base,
            "DEFAULT_USER": result.default_user,
        }

        try:
            self._write_config(cfg)
            if self._qsettings is not None:
                self._qsettings.setValue("wizard_completed", True)
                self._qsettings.setValue("last_adb_path", result.adb_path)
                self._qsettings.setValue("last_backup_base", result.backup_base)
                self._qsettings.sync()
        except Exception as e:
            QMessageBox.critical(self, "Save error", f"Failed to save configuration:\n{e}")
            return

        super().accept()
