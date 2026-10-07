"""PyQt6.QtGui の互換（ブラウザ版）。"""
from ._core import QKeyEvent, QMouseEvent, QResizeEvent  # noqa: F401
from ._gui import (QAction, QBrush, QColor, QCursor, QDesktopServices, QFont, QFontMetrics, QGuiApplication,  # noqa: F401
                   QIcon, QKeySequence, QPalette, QShortcut)


class QPixmap:
    def __init__(self, *_a):
        pass


class QStandardItemModel:
    pass


class QTextCursor:
    pass


class QTextDocument:
    pass
