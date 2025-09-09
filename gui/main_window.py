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

from PySide6.QtCore import Qt, QThread, QSettings
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QWidget,
    QMainWindow,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QGroupBox,
    QFileDialog,
    QMessageBox,
    QTabWidget,
    QMenuBar,
)

from gui.style import BASE_STYLE
from gui.widgets.folder_list import FolderList
from gui.widgets.log_console import LogConsole
from gui.restore_widget import RestoreWidget
from gui.first_run_wizard import FirstRunWizard

from config.paths import (
    ADB_PATH,
    SOURCE_DIR,
    BASE_BACKUP_DIR,
    DEFAULT_USER,
    path_for_user_session,
    record_path_for_user,
    failed_csv_path,
    ensure_dir,
    ensure_file,
    resolve_data_path,
    write_app_config,
    restore_record_path_for_user,
)

from core.adb_client import ADBClient
from core.record import RecordStore
from core.service import BackupService
from core.discovery import Discovery
from core.filters import Filters

from gui.workers import FolderDiscoveryWorker, BackupWorker


class MainWindow(QMainWindow):
    """
    Main application window with two tabs:
      - Backup: scan and copy device folders to local destination.
      - Restore: browse the aggregated restore tree and push files back.

    Threads:
      - Discovery runs in a QThread with a FolderDiscoveryWorker.
      - Backup runs in a QThread with a BackupWorker.
    """

    def __init__(self, initial_adb_path: Optional[str] = None, initial_backup_base: Optional[str] = None, initial_default_user: Optional[str] = None):
        super().__init__()
        self.resize(1100, 720)
        self.setStyleSheet(BASE_STYLE)

        # -- Session state (prefer injected values; fall back to config) --
        self.user_name = (initial_default_user or DEFAULT_USER or "User").strip() or "User"
        self.chosen_base_dir = (initial_backup_base or BASE_BACKUP_DIR or os.getcwd()).strip() or os.getcwd()
        self.final_dest_root: Optional[str] = None
        self.record_path: Optional[str] = None
        self.failed_csv_path = failed_csv_path()
        self.current_backup_worker = None

        # -- Core services (initialized with a temporary record until a session starts) --
        adb_path = initial_adb_path or ADB_PATH
        self.adb = ADBClient(adb_path)

        temp_record_path = os.path.join(os.getcwd(), ".tmp_record.json")
        ensure_file(temp_record_path, initial={"included_folders": []})
        self.record = RecordStore(temp_record_path)

        # Prepare restore_record path (for current user context)
        rr_path = restore_record_path_for_user(self.chosen_base_dir, self.user_name) if self.user_name else None
        self.service = BackupService(
            adb=self.adb,
            source_dir=SOURCE_DIR,
            dest_root=os.getcwd(),  # replaced when a session is prepared
            record=self.record,
            failed_csv_path=self.failed_csv_path,
            filters_path=resolve_data_path("config/filters.json"),
            restore_record_path=rr_path,
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
            restore_manager=self.service.restore_manager,
            base_backup_dir=user_root,
            adb=self.adb,
            source_dir=SOURCE_DIR,
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

        self.device_status = QLabel("Device: Unknown")
        self.device_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row1.addWidget(self.device_status, 1)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        self.dest_label = QLabel(f"Destination base: {self.chosen_base_dir}")
        row2.addWidget(self.dest_label, 2)

        self.change_dest_btn = QPushButton("Change Destination...")
        row2.addWidget(self.change_dest_btn)

        self.refresh_btn = QPushButton("Refresh Device")
        row2.addWidget(self.refresh_btn)

        self.scan_btn = QPushButton("Scan Folders")
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

    # ---------- Menu actions ----------
    def _run_setup(self) -> None:
        """Launch the first-run wizard; reload config on success and notify the user."""
        settings = QSettings("AndroidBackupManager", "App")
        defaults = {"ADB_PATH": ADB_PATH, "BASE_BACKUP_DIR": BASE_BACKUP_DIR, "DEFAULT_USER": DEFAULT_USER}
        wiz = FirstRunWizard(defaults=defaults, write_config=write_app_config, qsettings=settings, parent=self)
        if wiz.exec():
            QMessageBox.information(self, "Setup", "Settings saved. Restart recommended.")

    # ---------- Wiring ----------
    def _wire_events(self) -> None:
        """Connect UI events to handlers."""
        self.refresh_btn.clicked.connect(self.refresh_device_status)
        self.scan_btn.clicked.connect(self.scan_folders)
        self.change_dest_btn.clicked.connect(self.change_destination)
        self.backup_all_btn.clicked.connect(self.backup_all)
        self.backup_selected_btn.clicked.connect(self.backup_selected)
        self.stop_btn.clicked.connect(self.abort_backup)
        self.user_input.textChanged.connect(self.on_user_changed)

    # ---------- Session preparation ----------
    def _prepare_session_paths(self) -> bool:
        """
        Resolve destination and record paths for this backup session and initialize service state.

        Creates the destination folder and record files if missing.
        """
        if not self.user_name:
            QMessageBox.warning(self, "User", "Please enter a user name.")
            return False
        if not self.chosen_base_dir:
            QMessageBox.warning(self, "Destination", "Please choose a destination base folder.")
            return False

        self.final_dest_root = path_for_user_session(self.chosen_base_dir, self.user_name, use_date=True)
        self.record_path = record_path_for_user(self.chosen_base_dir, self.user_name)

        ensure_dir(self.final_dest_root)
        ensure_file(self.record_path, initial={"included_folders": []})
        ensure_file(self.failed_csv_path, initial=None)

        # Recreate record and service with resolved paths
        self.record = RecordStore(self.record_path)
        self.service = BackupService(
            adb=self.adb,
            source_dir=SOURCE_DIR,
            dest_root=self.final_dest_root,
            record=self.record,
            failed_csv_path=self.failed_csv_path,
            filters_path=resolve_data_path("config/filters.json"),
            restore_record_path=restore_record_path_for_user(self.chosen_base_dir, self.user_name),
        )

        # Update Restore tab context to the user's base dir
        if hasattr(self, "restore_widget"):
            user_root = os.path.join(self.chosen_base_dir, self.user_name)
            self.restore_widget.set_manager(
                self.service.restore_manager,
                base_backup_dir=user_root,
                adb=self.adb,
                source_dir=SOURCE_DIR,
            )

        return True

    # ---------- UI state ----------
    def enable_ui_actions(self, enable: bool = True) -> None:
        """Enable or disable UI actions during background operations."""
        self.backup_all_btn.setEnabled(enable)
        self.backup_selected_btn.setEnabled(enable)
        self.scan_btn.setEnabled(enable)
        self.stop_btn.setEnabled(not enable)
        self.change_dest_btn.setEnabled(enable)
        self.folder_list.refresh_btn.setEnabled(enable)
        self.folder_list.select_all_btn.setEnabled(enable)
        self.folder_list.clear_btn.setEnabled(enable)

    # ---------- Event handlers ----------
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
                self.chosen_base_dir = dirs
                self.dest_label.setText(f"Destination base: {self.chosen_base_dir}")
                self.log_console.append(f"Destination base set to: {self.chosen_base_dir}")

    def scan_folders(self) -> None:
        """Start a discovery worker to list device folders and apply filters."""
        if not self.adb.is_device_connected():
            QMessageBox.warning(self, "Device", "No device connected.")
            return
        self.log_console.append("Scanning folders (with filters)...")
        self._run_discovery()

    def backup_all(self) -> None:
        """Backup all filtered device folders into the prepared session destination."""
        if not self._prepare_session_paths():
            return
        self.log_console.append(f"Starting backup for all filtered folders into: {self.final_dest_root}")
        self._run_backup(selected=None)

    def backup_selected(self) -> None:
        """Backup only the user-selected folders."""
        selected = self.folder_list.selected_folders()
        if not selected:
            QMessageBox.information(self, "No Selection", "Please select folders to back up.")
            return
        if not self._prepare_session_paths():
            return
        self.log_console.append(f"Starting backup for selected folders ({len(selected)}) into: {self.final_dest_root}")
        self._run_backup(selected=selected)

    def abort_backup(self) -> None:
        """Request current backup worker to stop and keep UI disabled until cleanup occurs."""
        if self.current_backup_worker:
            self.current_backup_worker.abort = True
            self.log_console.append("Backup stop requested. Cancelling...")
            self.enable_ui_actions(False)

    def on_user_changed(self, text: str) -> None:
        """Update user context, restore manager, and destination label."""
        self.user_name = text.strip() or DEFAULT_USER
        self.dest_label.setText(f"Destination base: {self.chosen_base_dir}")
        rrp = restore_record_path_for_user(self.chosen_base_dir, self.user_name)
        self.service.restore_record_path = rrp
        self.service.restore_manager = BackupService(
            adb=self.adb,
            source_dir=SOURCE_DIR,
            dest_root=self.service.dest_root,
            record=self.record,
            failed_csv_path=self.failed_csv_path,
            filters_path=resolve_data_path("config/filters.json"),
            restore_record_path=rrp,
        ).restore_manager

        user_root = os.path.join(self.chosen_base_dir, self.user_name)
        if hasattr(self, "restore_widget"):
            self.restore_widget.set_manager(
                self.service.restore_manager,
                base_backup_dir=user_root,
                adb=self.adb,
                source_dir=SOURCE_DIR,
            )

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

        self.disc_thread = QThread(self)
        self.disc_worker = FolderDiscoveryWorker(
            discovery=Discovery(self.adb),
            source_dir=self.service.source_dir,
            filters=Filters(resolve_data_path("config/filters.json")),
        )
        self.disc_worker.moveToThread(self.disc_thread)

        # Start worker when thread starts
        self.disc_thread.started.connect(self.disc_worker.run)

        # Connect results
        self.disc_worker.finished.connect(self._on_discovery_finished)
        self.disc_worker.error.connect(self._on_worker_error)

        # Cleanup
        self.disc_worker.finished.connect(self.disc_thread.quit)
        self.disc_worker.error.connect(self.disc_thread.quit)
        self.disc_thread.finished.connect(self.disc_worker.deleteLater)
        self.disc_thread.finished.connect(self.disc_thread.deleteLater)

        self.disc_thread.start()

    def _run_backup(self, selected: Optional[List[str]] = None) -> None:
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
    def _on_discovery_finished(self, folders: List[str], msg: str) -> None:
        """Populate the folder list and log a preview message."""
        self.folder_list.set_folders(folders)
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
        self.log_console.append(f"Backup finished. Copied: {stats['copied_count']}, Failed: {stats['failed_count']}")
        self.current_backup_worker = None
        self.bak_worker = None
        self.bak_thread = None
        self.enable_ui_actions(True)

        # Refresh restore tree to reflect updated restore_record.json
        if hasattr(self, "restore_widget"):
            self.restore_widget.refresh()
