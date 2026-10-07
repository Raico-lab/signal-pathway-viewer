"""PyQt6.QtCore の互換（ブラウザ版）。"""
from ._core import (BoundSignal, QAbstractItemModel, QAbstractListModel, QAbstractTableModel, QCoreApplication,  # noqa: F401
                    QEvent, QItemSelectionModel, QKeyEvent, QLibraryInfo, QMimeData, QModelIndex, QMouseEvent, QObject,
                    QPersistentModelIndex, QPoint, QPointF, QRect, QRegularExpression, QResizeEvent, QSize, QSizeF,
                    QSortFilterProxyModel, QStringListModel, QThread, QTimer, QUrl, Qt, pyqtProperty, pyqtSignal,
                    pyqtSlot)

PYQT_VERSION_STR = "6.web"
QT_VERSION_STR = "6.web"


class QItemSelection(list):
    pass
