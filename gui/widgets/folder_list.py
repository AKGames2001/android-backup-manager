from __future__ import annotations

import os
from typing import Iterable, List, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox, QVBoxLayout, QTreeWidget, QTreeWidgetItem,
    QHBoxLayout, QPushButton,
)

ROLE_PATH = Qt.UserRole
ROLE_IS_DIR = Qt.UserRole + 1
ROLE_CHILDREN_LOADED = Qt.UserRole + 2


class FolderList(QGroupBox):
    """
    Hierarchical browser of device storage.

    - Top-level entries are device folders (after filters).
    - Folders are tri-state checkable; files are checkable.
    - Checking a folder conceptually selects everything; user can still uncheck
      specific subfolders/files.
    """
    selectionChanged = Signal()

    def __init__(self, title: str = "Device Browser"):
        super().__init__(title)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Name", "Type"])
        self.tree.setUniformRowHeights(True)

        self.refresh_btn = QPushButton("Refresh", self)
        self.select_all_btn = QPushButton("Select All", self)
        self.clear_btn = QPushButton("Clear Selection", self)

        top = QHBoxLayout()
        top.addWidget(self.refresh_btn)
        top.addWidget(self.select_all_btn)
        top.addWidget(self.clear_btn)
        top.addStretch()

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.tree)
        self.setLayout(layout)

        self.select_all_btn.clicked.connect(self.select_all)
        self.clear_btn.clicked.connect(self.clear_selection)

        self._block_item_changed = False
        self.tree.itemChanged.connect(self._on_item_changed)

        self._last_user_item = None
        self.tree.itemPressed.connect(self._on_item_pressed)


    # ---------- Population ----------

    def set_roots(self, root_dirs: Iterable[str]) -> None:
        """
        Populate initial root folders (absolute device paths).
        Children are loaded lazily on expand.
        """
        self.tree.clear()
        for p in root_dirs:
            name = os.path.basename((p or "").rstrip("/")) or p
            item = QTreeWidgetItem([name, "Folder"])
            item.setData(0, ROLE_PATH, p)
            item.setData(0, ROLE_IS_DIR, True)
            item.setData(0, ROLE_CHILDREN_LOADED, False)

            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            item.setCheckState(0, Qt.Unchecked)

            # Dummy child to show the expand arrow
            item.addChild(QTreeWidgetItem(["Loading...", ""]))
            self.tree.addTopLevelItem(item)

    def set_full_tree(self, tree: dict) -> None:
        self.tree.clear()
        self._block_item_changed = True
        try:
            root = self.tree.invisibleRootItem()

            def add_nodes(parent_item, subtree: dict):
                # subtree has optional "__dir__" marker (folder abs path)
                # leaf files have {"__file__": abs_path}
                for name, val in sorted(subtree.items()):
                    if name.startswith("__"):
                        continue

                    # file leaf
                    if isinstance(val, dict) and "__file__" in val:
                        item = QTreeWidgetItem(parent_item, [name, "File"])
                        item.setData(0, ROLE_PATH, val["__file__"])
                        item.setData(0, ROLE_IS_DIR, False)
                        item.setData(0, ROLE_CHILDREN_LOADED, True)
                        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                        item.setCheckState(0, Qt.Unchecked)
                        continue

                    # folder node
                    abs_dir = val.get("__dir__", "")
                    item = QTreeWidgetItem(parent_item, [name, "Folder"])
                    item.setData(0, ROLE_PATH, abs_dir)
                    item.setData(0, ROLE_IS_DIR, True)
                    item.setData(0, ROLE_CHILDREN_LOADED, True)

                    item.setFlags(
                        item.flags()
                        | Qt.ItemIsUserCheckable
                        | Qt.ItemIsAutoTristate
                    )
                    item.setCheckState(0, Qt.Unchecked)

                    add_nodes(item, val)

            add_nodes(root, tree)

            self.tree.collapseAll()  # children exist but are hidden until expand
        finally:
            self._block_item_changed = False

    def mark_children_loaded(self, parent_item: QTreeWidgetItem,
                             entries: List[Tuple[str, bool]]) -> None:
        """
        Replace the parent's dummy children with actual entries.
        Child check state is initialised from parent's current state.
        """
        parent_state = parent_item.checkState(0)
        parent_item.takeChildren()

        for path, is_dir in entries:
            name = os.path.basename(path.rstrip("/")) or path
            typ = "Folder" if is_dir else "File"
            child = QTreeWidgetItem([name, typ])

            child.setData(0, ROLE_PATH, path)
            child.setData(0, ROLE_IS_DIR, bool(is_dir))
            child.setData(0, ROLE_CHILDREN_LOADED, (not is_dir))

            flags = child.flags() | Qt.ItemIsUserCheckable
            if is_dir:
                flags |= Qt.ItemIsAutoTristate
                # Dummy child for lazy-load arrow
                child.addChild(QTreeWidgetItem(["Loading...", ""]))
            child.setFlags(flags)

            # Inherit parent's state (Checked/Unchecked/PartiallyChecked)
            child.setCheckState(0, parent_state)

            parent_item.addChild(child)

        parent_item.setData(0, ROLE_CHILDREN_LOADED, True)

    # ---------- Selection API ----------

    def checked_items(self) -> List[Tuple[str, bool]]:
        """
        Return [(path, is_dir)] for all selected items.

        Semantics:
        - If a folder is explicitly Checked, it is returned as (path, True),
          and the backup worker will back up everything under it recursively.
        - If the user unchecks some children under a checked folder, those
          children will not appear here (their check state is Unchecked),
          so they are effectively excluded.
        - Files that are directly Checked are returned as (path, False).
        """
        results: List[Tuple[str, bool]] = []

        def walk(node: QTreeWidgetItem):
            for i in range(node.childCount()):
                ch = node.child(i)
                path = ch.data(0, ROLE_PATH)
                if not path:
                    continue
                is_dir = bool(ch.data(0, ROLE_IS_DIR))
                state = ch.checkState(0)

                # Explicitly checked folders: we record them and still walk
                # children so explicit unchecks are honoured in the UI, but
                # the worker will treat the folder as "all included unless
                # file is also checked somewhere else".
                if is_dir and state == Qt.Checked:
                    results.append((path, True))
                    # Still descend so partially-checked overrides can be seen
                    walk(ch)
                elif not is_dir and state == Qt.Checked:
                    results.append((path, False))
                else:
                    # For Unchecked / PartiallyChecked dirs, just recurse.
                    walk(ch)

        root = self.tree.invisibleRootItem()
        walk(root)
        return results

    def select_all(self) -> None:
        self._set_all_check_state(Qt.Checked)

    def clear_selection(self) -> None:
        self._set_all_check_state(Qt.Unchecked)

    def _set_all_check_state(self, state: Qt.CheckState) -> None:
        self._block_item_changed = True
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            root.child(i).setCheckState(0, state)
        self._block_item_changed = False
        self.selectionChanged.emit()

    # ---------- Internal propagation ----------

    def _on_item_pressed(self, item, _col):
        self._last_user_item = item

    def _on_item_changed(self, item: QTreeWidgetItem, _col: int) -> None:
        if self._block_item_changed:
            return

        # Only propagate if the user directly toggled this item
        if item is not self._last_user_item:
            self.selectionChanged.emit()
            return

        self._last_user_item = None

        # Propagate to children (now that this was an intentional folder toggle)
        if bool(item.data(0, ROLE_IS_DIR)):
            self._block_item_changed = True
            try:
                state = item.checkState(0)
                for i in range(item.childCount()):
                    item.child(i).setCheckState(0, state)
            finally:
                self._block_item_changed = False

        self.selectionChanged.emit()
