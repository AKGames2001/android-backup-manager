# gui/restore_widget.py

import os
import mimetypes

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem, QPushButton,
    QMessageBox, QAbstractItemView, QGroupBox, QComboBox, QLabel, QProgressBar,
    QStackedWidget, QSplitter, QFormLayout, QScrollArea
)

from core.index import UnifiedIndex
from gui.widgets.log_console import LogConsole


class DetailsPanel(QWidget):
    """Right-pane 'Details' view for a single selected file."""
    rootChanged = Signal(str)  # emits chosen root

    def __init__(self, parent=None):
        super().__init__(parent)

        self._current_relpath = None
        outer = QVBoxLayout(self)

        group = QGroupBox("Details", self)
        form = QFormLayout(group)

        self.name_lbl = QLabel("-")
        self.type_lbl = QLabel("-")
        self.size_lbl = QLabel("-")
        self.path_lbl = QLabel("-")

        self.version_combo = QComboBox()
        self.version_combo.currentTextChanged.connect(self._onrootchanged)

        self.versions_lbl = QLabel("-")  # optional compact summary

        for w in (self.name_lbl, self.type_lbl, self.size_lbl, self.path_lbl, self.versions_lbl):
            w.setTextInteractionFlags(Qt.TextSelectableByMouse)

        form.addRow("Name:", self.name_lbl)
        form.addRow("Type:", self.type_lbl)
        form.addRow("Size:", self.size_lbl)
        form.addRow("Selected backup:", self.version_combo)
        form.addRow("Available:", self.versions_lbl)
        form.addRow("Device path:", self.path_lbl)

        group.setLayout(form)

        scroller = QScrollArea(self)
        scroller.setWidget(group)
        scroller.setWidgetResizable(True)
        outer.addWidget(scroller)

    def _onrootchanged(self, root: str) -> None:
        if self._current_relpath:
            self.rootChanged.emit(root)

    def clear(self):
        self._current_relpath = None
        self.name_lbl.setText("-")
        self.type_lbl.setText("-")
        self.size_lbl.setText("-")
        self.path_lbl.setText("-")
        self.versions_lbl.setText("-")
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        self.version_combo.blockSignals(False)

    def set_from_item(
        self,
        rel_path: str,
        roots: list,
        selected_root: str,
        base_backup_dir: str,
        local_rel: str,
    ):
        self._current_relpath = rel_path
        self.path_lbl.setText(rel_path)

        name = rel_path.split("/")[-1] if rel_path else "-"
        self.name_lbl.setText(name)

        mime, _ = mimetypes.guess_type(name)
        self.type_lbl.setText(mime or "unknown")
        roots_sorted = sorted([r for r in roots if r], reverse=True)
        self.versions_lbl.setText(", ".join(roots_sorted) if roots_sorted else "-")

        # Populate combo (newest first)
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        self.version_combo.addItems(roots_sorted)
        idx = self.version_combo.findText(selected_root)
        if idx >= 0:
            self.version_combo.setCurrentIndex(idx)
        elif roots_sorted:
            self.version_combo.setCurrentIndex(0)
        self.version_combo.blockSignals(False)

        # Size from selected root + local_rel
        try:
            if selected_root:
                lp = os.path.join(base_backup_dir, selected_root, (local_rel or rel_path).replace("/", os.sep))
                size = os.path.getsize(lp)
                for unit in ["B", "KB", "MB", "GB", "TB"]:
                    if size < 1024.0:
                        self.size_lbl.setText(f"{size:,.2f} {unit}")
                        break
                    size /= 1024.0
        except Exception:
            self.size_lbl.setText("-")


class RestoreWidget(QWidget):
    """
    Enhanced Restore tab:
    - Left: file tree
    - Right: QStackedWidget [Details | Logs]
    - Top: user selector and context info
    - Bottom: progress bar + Restore + Stop
    """
    # Signal to request switching user at the app level (MainWindow can recreate manager and call set_manager)
    userChanged = Signal(str)

    def __init__(self, index: UnifiedIndex, base_backup_dir: str, adb=None, source_dir: str = "sdcard", parent=None):
        super().__init__(parent)

        self.index = index
        self.base_backup_dir = base_backup_dir
        self.adb = adb
        self.source_dir = source_dir.rstrip("/")

        self._thread = None
        self._worker = None

        # device_rel -> chosen root
        self.selected_root_for = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # Header: user selector and context
        root.addWidget(self._build_header())

        # Middle: splitter with tree (left) and stacked (right)
        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setChildrenCollapsible(False)

        # Left: file tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Latest / Versions"])
        self.tree.setColumnWidth(0, 420)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        splitter.addWidget(self.tree)

        # Right: stacked panel (Details | Logs)
        self.right_stack = QStackedWidget()
        # Page 0: Details
        self.details_panel = DetailsPanel()
        # Page 1: Logs
        self.log_console = LogConsole("Restore Log")
        self.right_stack.addWidget(self.details_panel)
        self.right_stack.addWidget(self.log_console)

        splitter.addWidget(self.right_stack)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        # Footer: actions + progress
        root.addWidget(self._build_footer())

        # Wire events
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.details_panel.rootChanged.connect(self._on_details_root_changed)

        # Initial load
        self._populate_user_combo()
        self.refresh()

    def _build_header(self):
        box = QGroupBox("Restore Context", self)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(8, 8, 8, 8)

        lay.addWidget(QLabel("User:"))
        self.user_combo = QComboBox()
        self.user_combo.currentTextChanged.connect(self._on_user_combo_changed)
        lay.addWidget(self.user_combo, 1)

        self.base_dir_lbl = QLabel(f"Base: {os.path.dirname(self.base_backup_dir) or '-'}")
        self.base_dir_lbl.setToolTip(self.base_dir_lbl.text())
        lay.addWidget(self.base_dir_lbl, 2)

        self.source_lbl = QLabel(f"Device root: {self.source_dir or '-'}")
        self.source_lbl.setToolTip(self.source_lbl.text())
        lay.addWidget(self.source_lbl, 1)

        return box

    def _build_footer(self):
        box = QGroupBox("Actions", self)
        lay = QVBoxLayout(box)

        # Buttons
        row = QHBoxLayout()
        self.restore_btn = QPushButton("Restore Selected")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        
        row.addWidget(self.restore_btn)
        row.addStretch()
        row.addWidget(self.stop_btn)

        # Progress
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        lay.addLayout(row)
        lay.addWidget(self.progress)

        # Wire
        self.restore_btn.clicked.connect(self._on_restore)
        self.stop_btn.clicked.connect(self._on_stop)
        return box

    def _populate_user_combo(self):
        """List users from parent directory of base_backup_dir and select current."""
        users_root = os.path.dirname(self.base_backup_dir.rstrip("\\/"))
        self.user_combo.blockSignals(True)
        self.user_combo.clear()
        try:
            entries = sorted(
                d for d in os.listdir(users_root)
                if os.path.isdir(os.path.join(users_root, d))
            )
        except Exception:
            entries = []
        self.user_combo.addItems(entries)

        current_user = os.path.basename(self.base_backup_dir.rstrip("\\/"))
        idx = self.user_combo.findText(current_user)
        self.user_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.user_combo.blockSignals(False)

    @Slot(str)
    def _on_user_combo_changed(self, user_name: str):
        """Emit a request to change user; MainWindow should reload manager and call set_manager."""
        self.userChanged.emit(user_name)

    def set_manager(self, index: UnifiedIndex, base_backup_dir=None, adb=None, source_dir=None):
        """Update context and reload tree."""
        self.index = index
        if base_backup_dir:
            self.base_backup_dir = base_backup_dir
            self.base_dir_lbl.setText(f"Base: {os.path.dirname(self.base_backup_dir) or '-'}")
        # Re-sync combo selection if external change happened
        self._populate_user_combo()

        if adb:
            self.adb = adb
        if source_dir:
            self.source_dir = source_dir.rstrip("/")
            self.source_lbl.setText(f"Device root: {self.source_dir or '-'}")

        self.selected_root_for = {}
        self.refresh()

    def refresh(self):
        """Rebuild the file tree from restore_manager."""
        self.tree.clear()
        data = self.index.get_tree() if self.index else {}
        self._insert_tree(self.tree.invisibleRootItem(), data)
        self.tree.expandAll()
        # Show details page by default (idle/selection state)
        self.right_stack.setCurrentWidget(self.details_panel)

    def _insert_tree(self, parent_item, subtree: dict):
        for name, val in sorted(subtree.items()):
            if isinstance(val, dict):
                # Directory node
                item = QTreeWidgetItem(parent_item, [name, ""])
                flags = item.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate
                item.setFlags(flags)
                item.setCheckState(0, Qt.Unchecked)
                self._insert_tree(item, val)
            else:
                roots = list(val) if isinstance(val, list) else []
                latest = roots[-1] if roots else ""
                label = f"Latest: {latest} ({len(roots)} versions)" if latest else "-"
                item = QTreeWidgetItem(parent_item, [name, label])
                flags = item.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
                item.setFlags(flags)
                item.setCheckState(0, Qt.Unchecked)

    def reconstruct_relpath(self, fileitem) -> str:
        parts = []
        cur = fileitem
        while cur and cur.parent():
            parts.append(cur.text(0))
            cur = cur.parent()
        parts.reverse()
        return "/".join(parts)

    def _gather_checked_relpaths(self) -> list[str]:
        """
        Returns device-relative paths for all effectively checked leaf files.
        """
        result: list[str] = []
        root = self.tree.invisibleRootItem()

        def walk(node, parts, inherited_checked: bool):
            for i in range(node.childCount()):
                child = node.child(i)
                name = child.text(0)
                has_children = child.childCount() > 0
                state = child.checkState(0)

                # Folder node
                if has_children:
                    # Effective checked if this node is Checked or inherited from parent
                    child_effective_checked = inherited_checked or (state == Qt.Checked)
                    walk(child, parts + [name], child_effective_checked)
                else:
                    if (state == Qt.Checked) or inherited_checked:
                        rel = "/".join(parts + [name])
                        result.append(rel)

        walk(root, [], False)
        return result

    def _collect_selected_relpaths(self) -> list[str]:
        out: list[str] = []
        for it in self.tree.selectedItems():
            if it.childCount() > 0:
                continue
            out.append(self.reconstruct_relpath(it))
        return out

    def _build_restore_items(self, rel_paths: list[str]) -> list[tuple]:
        """
        Build items: (device_rel, chosen_root, local_rel) per file.
        chosen_root defaults to latest if not explicitly selected.
        """
        items: list[tuple] = []
        for rel in rel_paths:
            roots = self.index.roots_for(rel) if self.index else []
            latest = roots[-1] if roots else None
            chosen = self.selected_root_for.get(rel) or latest
            if not chosen:
                continue
            local_rel = self.index.local_rel_for(rel, chosen) if self.index else None
            items.append((rel, chosen, local_rel or rel))
        return items

    @Slot()
    def _on_tree_selection_changed(self):
        """Update details panel when selection changes; prefer single-file details."""
        items = self.tree.selectedItems()
        if not items:
            self.details_panel.clear()
            self.right_stack.setCurrentWidget(self.details_panel)
            return

        # If multiple selected, show a summary
        file_items = [it for it in items if it.childCount() == 0]
        if len(file_items) != 1:
            self.details_panel.name_lbl.setText(f"{len(file_items)} files selected")
            self.details_panel.type_lbl.setText("-")
            self.details_panel.size_lbl.setText("-")
            self.details_panel.versions_lbl.setText("-")
            self.details_panel.path_lbl.setText("-")
            self.details_panel.version_combo.blockSignals(True)
            self.details_panel.version_combo.clear()
            self.details_panel.version_combo.blockSignals(False)
            self.right_stack.setCurrentWidget(self.details_panel)
            return

        it = file_items[0]
        rel = self.reconstruct_relpath(it)
        roots = self.index.roots_for(rel) if self.index else []
        chosen = self.selected_root_for.get(rel) or (roots[-1] if roots else "")
        local_rel = self.index.local_rel_for(rel, chosen) if (self.index and chosen) else rel

        self.details_panel.set_from_item(
            rel_path=rel,
            roots=roots,
            selected_root=chosen,
            base_backup_dir=self.base_backup_dir,
            local_rel=local_rel or rel,
        )
        self.right_stack.setCurrentWidget(self.details_panel)

    @Slot(str)
    def _on_details_root_changed(self, root: str):
        """
        User chose a specific version in the Details dropdown.
        Persist it for this file so Restore Selected uses per-file roots.
        """
        items = self.tree.selectedItems()
        file_items = [it for it in items if it.childCount() == 0]
        if len(file_items) != 1:
            return
        rel = self.reconstruct_relpath(file_items[0])
        if root:
            self.selected_root_for[rel] = root

        # Refresh details (size/path based on selected root)
        roots = self.index.roots_for(rel) if self.index else []
        local_rel = self.index.local_rel_for(rel, root) if (self.index and root) else rel
        self.details_panel.set_from_item(rel, roots, root, self.base_backup_dir, local_rel or rel)
    @Slot()
    def _on_restore(self):
        rel_paths = self._gather_checked_relpaths()
        if not rel_paths:
            rel_paths = self._collect_selected_relpaths()

        if not rel_paths:
            QMessageBox.information(self, "Restore", "Select one or more files to restore.")
            return

        if self.adb is None or not self.source_dir or not self.base_backup_dir:
            QMessageBox.warning(self, "Restore", "Restore context is not initialized.")
            return

        # Threaded restore using existing RestoreWorker
        items = self._build_restore_items(rel_paths)
        if not items:
            QMessageBox.warning(self, "Restore", "No valid backup versions found for the selected files.")
            return

        from gui.workers import RestoreWorker
        self._thread = QThread(self)
        self._worker = RestoreWorker(adb=self.adb, base_backup_dir=self.base_backup_dir, source_dir=self.source_dir, items=items)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.log.connect(self._on_log)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        # UI state
        self.restore_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress.setRange(0, 0)  # indeterminate until first progress
        self.right_stack.setCurrentWidget(self.log_console)

        self._thread.start()

    @Slot()
    def _on_stop(self):
        if self._worker:
            self._worker.abort = True
            self._append_log("Restore: stop requested.")

    @Slot(int, int)
    def _on_progress(self, current, total):
        if total <= 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            pct = int((current / total) * 100) if total else 0
            self.progress.setValue(pct)

    @Slot(str)
    def _on_log(self, message: str):
        self._append_log(message)

    def _append_log(self, msg: str):
        try:
            self.log_console.append(msg)
        except Exception:
            pass

    @Slot(dict)
    def _on_finished(self, stats: dict):
        self.restore_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        QMessageBox.information(
            self,
            "Restore",
            f"Restore finished. Restored: {stats.get('restored_count', 0)}, Failed: {stats.get('failed_count', 0)}",
        )
        
        # Return to details page after completion
        self.right_stack.setCurrentWidget(self.details_panel)

    @Slot(str)
    def _on_error(self, err: str):
        self.restore_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        QMessageBox.critical(self, "Restore", err)
        self.right_stack.setCurrentWidget(self.details_panel)