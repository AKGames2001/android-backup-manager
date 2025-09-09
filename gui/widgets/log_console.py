# gui/widgets/log_console.py
"""
LogConsole: a simple titled panel with a read-only text view and a Clear button.

Methods:
    append(text: str): append a line of log text.
    clear(): clear the entire log view.
"""

from __future__ import annotations

from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout

class LogConsole(QGroupBox):
    """Read-only text console with a clear action for app/worker logs."""
    def __init__(self, title: str = "Activity Log"):
        super().__init__(title)
        self.view = QTextEdit(self)
        self.view.setReadOnly(True)

        self.clear_btn = QPushButton("Clear", self)

        top = QHBoxLayout()
        top.addWidget(self.clear_btn)
        top.addStretch()

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.view)
        self.setLayout(layout)

        self.clear_btn.clicked.connect(self.clear)

    def append(self, text: str) -> None:
        """Append a single line of text to the log view."""
        self.view.append(text)

    def clear(self) -> None:
        """Clear all text from the log view."""
        self.view.clear()
