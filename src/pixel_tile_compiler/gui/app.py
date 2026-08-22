"""GUI process entry point."""

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def run() -> None:
    """Start the Qt event loop."""
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    application.exec()
