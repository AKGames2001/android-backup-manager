"""
Application entry point.

Creates the QApplication, builds the main window using gui.app.create_app(),
and starts the Qt event loop.
"""

import sys
from PySide6.QtWidgets import QApplication

from gui.app import create_app


def main() -> int:
    """Create the app and run the main event loop; return exit code."""
    app = QApplication(sys.argv)
    window = create_app()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
