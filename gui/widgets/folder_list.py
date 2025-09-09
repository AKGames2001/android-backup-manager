# gui/widgets/folder_list.py
"""
FolderList: a titled list of device folders with multi-selection and quick actions.

Signals:
    selectionChanged() -> emitted whenever the list selection changes.

Public attributes (consumed by MainWindow):
    - refresh_btn: QPushButton to trigger re-scan
    - select_all_btn: QPushButton to select all list items
    - clear_btn: QPushButton to clear the current selection
"""

from __future__ import annotations

import os
from typing import Iterable, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QVBoxLayout,
    QListWidget,
    QListWidgetItem,
    QHBoxLayout,
    QPushButton,
    QAbstractItemView,
)

class FolderList(QGroupBox):
    """A titled list widget for device folders with multi-select and convenience buttons."""
    selectionChanged = Signal()

    def __init__(self, title: str = "Device Folders"):
        super().__init__(title)

        # List with multi-selection behavior
        self.list = QListWidget(self)
        self.list.setSelectionMode(QAbstractItemView.MultiSelection)

        # Action buttons
        self.refresh_btn = QPushButton("Refresh", self)
        self.select_all_btn = QPushButton("Select All", self)
        self.clear_btn = QPushButton("Clear Selection", self)

        # Top row of actions
        top = QHBoxLayout()
        top.addWidget(self.refresh_btn)
        top.addWidget(self.select_all_btn)
        top.addWidget(self.clear_btn)
        top.addStretch()

        # Main layout
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.list)
        self.setLayout(layout)

        # Wire actions
        self.select_all_btn.clicked.connect(self.select_all)
        self.clear_btn.clicked.connect(self.clear_selection)
        self.list.itemSelectionChanged.connect(self.selectionChanged.emit)

    # ---------- Public API ----------
    def set_folders(self, folders: Iterable[str]) -> None:
        """Populate the list with display names, preserving the full path in UserRole."""
        self.list.clear()
        for f in folders:
            display_name = os.path.basename((f or "").rstrip("/"))
            item = QListWidgetItem(display_name)
            item.setData(Qt.UserRole, f)
            item.setFlags(item.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self.list.addItem(item)

    def selected_folders(self) -> List[str]:
        """Return the full paths stored in UserRole for all selected items."""
        return [i.data(Qt.UserRole) for i in self.list.selectedItems()]

    def select_all(self) -> None:
        """Select all items."""
        self.list.selectAll()

    def clear_selection(self) -> None:
        """Clear any current selection."""
        self.list.clearSelection()
