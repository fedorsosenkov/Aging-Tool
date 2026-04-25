"""
MOS Aging Analyzer — точка входа.

Запуск:
    python main.py

Зависимости:
    pip install PyQt6 numpy
"""

import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("MOS Aging Analyzer")
    app.setApplicationVersion("2.0")
    app.setOrganizationName("AgingTool")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
