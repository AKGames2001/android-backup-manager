# gui/main_window.py
"""
Main application window.

Responsibilities:
- Display a tabbed UI with Backup and Restore tabs.
- Manage device discovery and backup operations in background threads.
- Persist incremental backup state and update restore metadata.
- Offer quick access to a setup wizard and destination selection.
"""

from __future__ import annotations

import os
from typing import Optional, List, Dict

from PySide6.QtCore import Qt, QThread, QSettings, QSignalBlocker
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QWidget, QMainWindow, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QProgressBar, QGroupBox, QFileDialog, QMessageBox, QTabWidget, QMenuBar
)

from .first_run_wizard import FirstRunWizard
from gui.restore_widget import RestoreWidget
from gui.style import BASE_STYLE
from gui.widgets.folder_list import FolderList, ROLE_PATH, ROLE_IS_DIR, ROLE_CHILDREN_LOADED
from gui.widgets.log_console import LogConsole
from gui.workers import BackupWorker

from config.paths import (
    ADB_PATH, SOURCE_DIR, BASE_BACKUP_DIR, DEFAULT_USER,
    path_for_user_session, record_path_for_user, failed_csv_path_for_user,
    ensure_dir, resolve_data_path, write_app_config, restore_record_path_for_user,
    index_path_for_user,
)

from core.adb_client import ADBClient
from core.index import UnifiedIndex
from core.service import BackupService
from core.discovery import Discovery
from core.filters import Filters


class MainWindow(QMainWindow):
    """
    Main application window with two tabs:
      - Backup: scan and copy device folders to local destination.
      - Restore: browse the aggregated restore tree and push files back.

    Threads:
      - Discovery runs in a QThread with a FolderDiscoveryWorker.
      - Backup runs in a QThread with a BackupWorker.
    """
    def __init__(
        self,
        initial_adb_path: Optional[str] = None,
        initial_backup_base: Optional[str] = None,
        initial_default_user: Optional[str] = None,
    ):
        super().__init__()

        self.resize(1100, 720)
        self.setStyleSheet(BASE_STYLE)

        self.user_name = initial_default_user or DEFAULT_USER or "User"
        self.chosen_base_dir = initial_backup_base or BASE_BACKUP_DIR or os.getcwd()
        self.final_dest_root: Optional[str] = None

        self.failed_csv_path = failed_csv_path_for_user(self.chosen_base_dir, self.user_name)

        self.current_backup_worker = None
        self._committed_user_name = self.user_name
        self._user_pending = False

        # -- Core services (initialized with a temporary record until a session starts) --
        adb_path = initial_adb_path or ADB_PATH
        self.adb = ADBClient(adb_path)

        # Temp index until a session starts (keeps UI stable)
        local_app_data = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
        temp_root = os.path.join(local_app_data, "AndroidBackupManager", "cache")
        ensure_dir(temp_root)
        temp_index_path = os.path.join(temp_root, ".tmpindex.json")
        self.index = UnifiedIndex(temp_index_path, device_root=SOURCE_DIR)

        # Service uses placeholder destroot until session is prepared
        self.service = BackupService(
            adb=self.adb,
            source_dir=SOURCE_DIR,
            dest_root=os.getcwd(),
            index=self.index,
            failed_csv_path=self.failed_csv_path,
            filters_path=resolve_data_path("config/filters.json"),
        )

        # -- UI: Tabs, header, center widgets, footer --
        self._build_core_widgets()
        self._build_tabs()
        self._wire_events()
        self._build_menu()

        # Initial device status
        self.refresh_device_status()

    # ---------- UI construction ----------
    def _build_core_widgets(self) -> None:
        """Build top header, central lists/log, and footer action area."""
        self.header_box = self._build_header()
        self.folder_list = FolderList("Device Folders")
        self.log_console = LogConsole("Activity Log")
        self.footer_widget = self._build_footer()

    def _build_tabs(self) -> None:
        """Create Backup and Restore tabs and set them as the central widget."""
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Backup tab
        backup_tab = QWidget()
        v = QVBoxLayout(backup_tab)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(14)
        v.addWidget(self.header_box)

        h = QHBoxLayout()
        h.addWidget(self.folder_list, 1)
        h.addWidget(self.log_console, 1)
        v.addLayout(h)

        v.addWidget(self.footer_widget)
        self.tabs.addTab(backup_tab, "Backup")

        # Restore tab (bound to current service restore manager)
        user_root = os.path.join(self.chosen_base_dir, self.user_name)
        self.restore_widget = RestoreWidget(
            index=self.index,
            base_backup_dir=user_root, 
            adb=self.adb, 
            source_dir=SOURCE_DIR
        )
        self.tabs.addTab(self.restore_widget, "Restore")

    def _build_menu(self) -> None:
        """Create a simple Tools menu with Setup action."""
        menubar = self.menuBar() if hasattr(self, "menuBar") else QMenuBar(self)
        if not hasattr(self, "menuBar"):
            self.setMenuBar(menubar)

        tools_menu = menubar.addMenu("Tools")
        act_setup = QAction("Run Setup...", self)
        act_setup.triggered.connect(self._run_setup)
        tools_menu.addAction(act_setup)

    def _build_header(self) -> QGroupBox:
        """Build the session header with user, device status, destination, and actions."""
        box = QGroupBox("Session")
        lay = QVBoxLayout(box)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("User"))
        self.user_input = QLineEdit(self.user_name)
        self.user_input.setPlaceholderText("Enter user name")
        row1.addWidget(self.user_input, 1)

        self.user_apply_btn = QPushButton("✓")
        self.user_cancel_btn = QPushButton("✕")
        self.user_apply_btn.setFixedWidth(36)
        self.user_cancel_btn.setFixedWidth(36)
        self.user_apply_btn.setEnabled(False)
        self.user_cancel_btn.setEnabled(False)
        self.user_apply_btn.setVisible(False)
        self.user_cancel_btn.setVisible(False)

        row1.addWidget(self.user_apply_btn)
        row1.addWidget(self.user_cancel_btn)


        self.device_status = QLabel("Device: Unknown")
        self.device_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row1.addWidget(self.device_status, 1)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        self.dest_label = QLabel(f"Destination base: {self.chosen_base_dir}")
        row2.addWidget(self.dest_label, 2)

        self.change_dest_btn = QPushButton("Change Destination...")
        self.refresh_btn = QPushButton("Refresh Device")
        self.scan_btn = QPushButton("Scan Folders")

        row2.addWidget(self.change_dest_btn)
        row2.addWidget(self.refresh_btn)
        row2.addWidget(self.scan_btn)
        lay.addLayout(row2)

        return box

    def _build_footer(self) -> QGroupBox:
        """Build the footer with backup buttons and a progress bar."""
        box = QGroupBox("Actions")
        lay = QVBoxLayout(box)

        actions = QHBoxLayout()
        self.backup_all_btn = QPushButton("Backup All (Filtered)")
        self.backup_selected_btn = QPushButton("Backup Selected")
        self.stop_btn = QPushButton("Stop")

        actions.addWidget(self.backup_all_btn)
        actions.addWidget(self.backup_selected_btn)
        actions.addStretch()
        actions.addWidget(self.stop_btn)

        self.progress = QProgressBar()
        self.progress.setMinimum(0)
        self.progress.setMaximum(100)
        self.progress.setValue(0)

        lay.addLayout(actions)
        lay.addWidget(self.progress)
        return box

    # ---------- Wiring ----------
    def _wire_events(self) -> None:
        self.refresh_btn.clicked.connect(self.refresh_device_status)
        self.scan_btn.clicked.connect(self.scan_folders)
        self.change_dest_btn.clicked.connect(self.change_destination)
        self.backup_all_btn.clicked.connect(self.backup_all)
        self.backup_selected_btn.clicked.connect(self.backup_selected)
        self.stop_btn.clicked.connect(self.abort_backup)

        self.user_input.textEdited.connect(self.on_user_edited)
        self.user_apply_btn.clicked.connect(self.apply_user_change)
        self.user_cancel_btn.clicked.connect(self.cancel_user_change)

        self.restore_widget.userChanged.connect(self.on_restore_user_changed)

    # ---------- Menu actions ----------
    def _run_setup(self) -> None:
        """Launch the first-run wizard; reload config on success and notify the user."""
        settings = QSettings("AndroidBackupManager", "App")
        defaults = {"ADB_PATH": ADB_PATH, "BASE_BACKUP_DIR": BASE_BACKUP_DIR, "DEFAULT_USER": DEFAULT_USER}
        wiz = FirstRunWizard(defaults=defaults, write_config=write_app_config, qsettings=settings, parent=self)
        if wiz.exec():
            QMessageBox.information(self, "Setup", "Settings saved. Restart recommended.")

    def refresh_device_status(self) -> None:
        """Probe adb for device connectivity and update the header label."""
        try:
            connected = self.adb.is_device_connected()
        except Exception as e:
            connected = False
            self.log_console.append(f"ADB error: {e}")
        self.device_status.setText(f"Device: {'Connected' if connected else 'Not Connected'}")
        self.log_console.append("Checked device connectivity.")

    def change_destination(self) -> None:
        """Prompt for a new base destination directory and update the label."""
        dlg = QFileDialog(self)
        dlg.setFileMode(QFileDialog.Directory)
        dlg.setOption(QFileDialog.ShowDirsOnly, True)
        if dlg.exec():
            dirs = dlg.selectedFiles()
            if dirs:
                self.chosen_base_dir = dirs[0]
                self.dest_label.setText(f"Destination base: {self.chosen_base_dir}")
                self.log_console.append(f"Destination base set to: {self.chosen_base_dir}")

    def scan_folders(self) -> None:
        """Start a discovery worker to list device folders and apply filters."""
        if not self.adb.is_device_connected():
            QMessageBox.warning(self, "Device", "No device connected.")
            return
        self.log_console.append("Scanning folders with filters...")
        self._run_discovery()

    def prepare_session_paths(self) -> bool:
        if not self.user_name:
            QMessageBox.warning(self, "User", "Please enter a user name.")
            return False
        if not self.chosen_base_dir:
            QMessageBox.warning(self, "Destination", "Please choose a destination base folder.")
            return False

        self.final_dest_root = path_for_user_session(self.chosen_base_dir, self.user_name, use_date=True)
        ensure_dir(self.final_dest_root)

        # Legacy files for migration
        legacy_record = record_path_for_user(self.chosen_base_dir, self.user_name)
        legacy_restore = restore_record_path_for_user(self.chosen_base_dir, self.user_name)
        # Unified index path
        idx_path = index_path_for_user(self.chosen_base_dir, self.user_name)
        self.index = UnifiedIndex(
            idx_path,
            device_root=SOURCE_DIR,
            migrate_record_path=legacy_record,
            migrate_restore_record_path=legacy_restore,
        )

        self.failed_csv_path = failed_csv_path_for_user(self.chosen_base_dir, self.user_name)

        self.service = BackupService(
            adb=self.adb,
            source_dir=SOURCE_DIR,
            dest_root=self.final_dest_root,
            index=self.index,
            failed_csv_path=self.failed_csv_path,
            filters_path=resolve_data_path("config/filters.json"),
        )

        user_root = os.path.join(self.chosen_base_dir, self.user_name)
        self.restore_widget.set_manager(
            index=self.index, 
            base_backup_dir=user_root, 
            adb=self.adb, 
            source_dir=SOURCE_DIR
        )
        return True

    def backup_all(self) -> None:
        """Backup all filtered device folders into the prepared session destination."""
        if not self.prepare_session_paths():
            return
        self.log_console.append(f"Starting backup (all filtered) into: {self.final_dest_root}")
        self._run_backup(None)

    def backup_selected(self) -> None:
        """Backup only the user-selected folders/files."""
        selected = self.folder_list.checked_items()  # [(path, is_dir)]
        if not selected:
            QMessageBox.information(self, "No Selection", "Please select folders or files to back up.")
            return

        if not self.prepare_session_paths():
            return
        self.log_console.append(f"Starting backup ({len(selected)} items) into: {self.final_dest_root}")
        self._run_backup(selected)

    def abort_backup(self) -> None:
        """Request current backup worker to stop and keep UI disabled until cleanup occurs."""
        if self.current_backup_worker:
            self.current_backup_worker.abort = True
            self.log_console.append("Backup stop requested. Cancelling...")
            self.enable_ui_actions(False)

    def enable_ui_actions(self, enable: bool = True) -> None:
        self.backup_all_btn.setEnabled(enable)
        self.backup_selected_btn.setEnabled(enable)
        self.scan_btn.setEnabled(enable)
        self.stop_btn.setEnabled(not enable)
        self.change_dest_btn.setEnabled(enable)
    
    # ---------- User change ----------
    def set_user_pending(self, pending: bool) -> None:
        self.user_pending = pending
        self.user_apply_btn.setVisible(pending)
        self.user_cancel_btn.setVisible(pending)
        self.user_apply_btn.setEnabled(pending)
        self.user_cancel_btn.setEnabled(pending)
    def on_user_edited(self, text: str) -> None:
        current = (self.user_input.text() or "").strip() or DEFAULT_USER
        self.set_user_pending(current != self._committed_user_name)

    def apply_user_change(self) -> None:
        new_user = (self.user_input.text() or "").strip() or DEFAULT_USER
        if new_user == self._committed_user_name:
            self.set_user_pending(False)
            self.user_input.clearFocus()
            return

        # Commit once
        self.user_name = new_user
        self._committed_user_name = new_user
        self.failed_csv_path = failed_csv_path_for_user(self.chosen_base_dir, self.user_name)
        self.set_user_pending(False)

        # Refresh restore context immediately
        user_root = os.path.join(self.chosen_base_dir, self.user_name)
        idx_path = index_path_for_user(self.chosen_base_dir, self.user_name)
        legacy_record = record_path_for_user(self.chosen_base_dir, self.user_name)
        legacy_restore = restore_record_path_for_user(self.chosen_base_dir, self.user_name)

        self.index = UnifiedIndex(
            idx_path,
            device_root=SOURCE_DIR,
            migrate_record_path=legacy_record,
            migrate_restore_record_path=legacy_restore,
        )

        self.service = BackupService(
            adb=self.adb,
            source_dir=SOURCE_DIR,
            dest_root=self.service.dest_root,
            index=self.index,
            failed_csv_path=self.failed_csv_path,
            filters_path=resolve_data_path("config/filters.json"),
        )
        self.restore_widget.set_manager(
            index=self.index, 
            base_backup_dir=user_root, 
            adb=self.adb, 
            source_dir=SOURCE_DIR
        )

    def cancel_user_change(self) -> None:
        # Revert UI to committed value without retriggering edit logic
        with QSignalBlocker(self.user_input):
            self.user_input.setText(self._committed_user_name)
        self.set_user_pending(False)
        self.user_input.clearFocus()

    def on_restore_user_changed(self, username: str) -> None:
        # Mirror selection if user changes via restore tab dropdown
        if not username:
            return
        with QSignalBlocker(self.user_input):
            self.user_input.setText(username)
        self.username = username
        self.committedusername = username
        self.set_user_pending(False)
        self.apply_user_change()

    # ---------- Threads ----------
    def _run_discovery(self) -> None:
        """Launch a discovery worker in a QThread; update the list when done."""
        # Stop a prior discovery thread, if any
        if hasattr(self, "disc_thread") and self.disc_thread is not None:
            try:
                self.disc_thread.quit()
                self.disc_thread.wait(1000)
            except Exception:
                pass

        # NOTE: itemExpanded/DirEntriesWorker is no longer needed for Backup tree
        # once the whole tree is built upfront. [file:68]

        from gui.workers import FullTreeDiscoveryWorker  # or import at top of file

        self.disc_thread = QThread(self)
        self.disc_worker = FullTreeDiscoveryWorker(
            discovery=Discovery(self.adb),
            source_dir=self.service.source_dir,
            filters=Filters(resolve_data_path("config/filters.json")),
        )
        self.disc_worker.moveToThread(self.disc_thread)

        # Start worker when thread starts
        self.disc_thread.started.connect(self.disc_worker.run)

        # Connect results (tree instead of folders)
        self.disc_worker.finished.connect(self._on_discovery_finished_full_tree)
        self.disc_worker.error.connect(self._on_worker_error)

        # Cleanup
        self.disc_worker.finished.connect(self.disc_thread.quit)
        self.disc_worker.error.connect(self.disc_thread.quit)
        self.disc_thread.finished.connect(self.disc_worker.deleteLater)
        self.disc_thread.finished.connect(self.disc_thread.deleteLater)

        self.disc_thread.start()

    def _run_backup(self, selected: Optional[List] = None) -> None:
        """Launch a backup worker in a QThread; update UI on progress and completion."""
        if not self.adb.is_device_connected():
            QMessageBox.warning(self, "Device", "No device connected.")
            return

        # Stop a prior backup thread, if any
        if hasattr(self, "bak_thread") and self.bak_thread is not None:
            try:
                self.bak_thread.quit()
                self.bak_thread.wait(1000)
            except Exception:
                pass

        self.current_backup_worker = None
        self.bak_worker = None
        self.bak_thread = None

        self.progress.setValue(0)

        self.bak_thread = QThread(self)
        self.bak_worker = BackupWorker(service=self.service, folders=selected)
        self.current_backup_worker = self.bak_worker
        self.bak_worker.moveToThread(self.bak_thread)

        # Start
        self.bak_thread.started.connect(self.bak_worker.run)

        # Signals
        self.bak_worker.progress.connect(self._on_progress)
        self.bak_worker.log.connect(self.log_console.append)
        self.bak_worker.finished.connect(self._on_backup_finished)
        self.bak_worker.error.connect(self._on_worker_error)

        # Cleanup
        self.bak_worker.finished.connect(self.bak_thread.quit)
        self.bak_worker.error.connect(self.bak_thread.quit)
        self.bak_thread.finished.connect(self.bak_worker.deleteLater)
        self.bak_thread.finished.connect(self.bak_thread.deleteLater)

        self.bak_thread.start()
        self.enable_ui_actions(False)

    # ---------- Slots ----------
    def _on_discovery_finished_full_tree(self, tree: dict, msg: str) -> None:
        # FolderList must implement set_full_tree(tree)
        self.folder_list.set_full_tree(tree)
        self.log_console.append(msg)

    def _on_worker_error(self, err: str) -> None:
        """Show an error dialog and re-enable UI after worker failure."""
        QMessageBox.critical(self, "Error", err)
        self.log_console.append(f"Error: {err}")
        self.enable_ui_actions(True)

    def _on_progress(self, current: int, total: int) -> None:
        """Update progress bar for determinate or indeterminate states."""
        if total <= 0:
            self.progress.setRange(0, 0)  # indeterminate
        else:
            self.progress.setRange(0, 100)
            pct = int((current / total) * 100) if total else 0
            self.progress.setValue(pct)

    def _on_backup_finished(self, stats: Dict[str, int]) -> None:
        """Finalize UI state and refresh restore tab after a successful backup."""
        self.progress.setValue(100)
        self.log_console.append(f"Backup finished. Copied: {stats.get('copied_count', 0)}, Failed: {stats.get('failed_count', 0)}")
        self.current_backup_worker = None
        self.bak_worker = None
        self.bak_thread = None
        self.enable_ui_actions(True)

        # Refresh restore tree to reflect updated restore_record.json
        if hasattr(self, "restore_widget"):
            self.restore_widget.refresh()