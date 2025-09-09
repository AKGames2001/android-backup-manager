# gui/style.py
"""
Application-wide Qt Style Sheet (QSS) and a small helper to apply it.

Notes:
- Qt Style Sheets use flat CSS2-like selectors; nesting is not supported.
- Subcontrols must be targeted explicitly (e.g., QProgressBar::chunk).
- Apply the style once at app or window level to avoid partial overrides.
"""

from __future__ import annotations

# Base dark theme with concise, valid QSS selectors
BASE_STYLE = """
/* -------- Base palette -------- */
QWidget {
  background-color: #111418;
  color: #E6E6E6;
  font-family: 'Segoe UI', 'Inter', Arial, sans-serif;
  font-size: 12pt;
}

/* -------- Inputs and views -------- */
QLineEdit,
QComboBox,
QTextEdit,
QListWidget,
QTreeWidget,
QTableWidget {
  background-color: #161A20;
  border: 1px solid #2A2F37;
  border-radius: 8px;
  padding: 8px;
  selection-background-color: #2E7DFF;
  selection-color: #FFFFFF;
}

/* -------- Buttons -------- */
QPushButton {
  background-color: #1E222A;
  border: 1px solid #2A2F37;
  border-radius: 8px;
  padding: 10px 14px;
  color: #E6E6E6;
}
QPushButton:hover { background-color: #232833; }
QPushButton:pressed { background-color: #2A2F37; }
QPushButton:disabled { color: #7A8088; background-color: #1A1E25; }

/* -------- Progress bar -------- */
QProgressBar {
  border: 1px solid #2A2F37;
  border-radius: 8px;
  text-align: center;
  background: #161A20;
  height: 16px;
  color: #E6E6E6;
}
QProgressBar::chunk {
  background-color: #2E7DFF;
  border-radius: 6px;
}

/* -------- Group boxes -------- */
QGroupBox {
  border: 1px solid #2A2F37;
  border-radius: 10px;
  margin-top: 16px;
  padding: 10px;
}
QGroupBox::title {
  subcontrol-origin: margin;
  left: 10px;
  padding: 0 4px;
  color: #9AA4B2;
}

/* -------- Scrollbars (vertical) -------- */
QScrollBar:vertical {
  background: #161A20;
  width: 12px;
  margin: 0px;
}
QScrollBar::handle:vertical {
  background: #2A2F37;
  min-height: 20px;
  border-radius: 6px;
}
QScrollBar::handle:vertical:hover {
  background: #2E7DFF;
}
"""

def apply_base_style(target) -> None:
    """
    Apply BASE_STYLE to a QApplication or QWidget (or any object exposing setStyleSheet).

    Example:
        app.setStyleSheet(BASE_STYLE)   # or apply_base_style(app)
        window.setStyleSheet(BASE_STYLE)
    """
    try:
        target.setStyleSheet(BASE_STYLE)
    except Exception:
        # Best-effort application; ignore if target lacks setStyleSheet
        pass
