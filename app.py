from __future__ import annotations

import sys

import pyqtgraph as pg
from PySide6 import QtWidgets

from spectrum_app import MainWindow


def main() -> int:
    pg.setConfigOptions(imageAxisOrder="row-major", antialias=False)
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
