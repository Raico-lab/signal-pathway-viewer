"""PyQt6.QtWidgets の互換（ブラウザ版）。"""
from ._gui import QAction, QShortcut  # noqa: F401
from ._items import (QAbstractItemView, QCompleter, QHeaderView, QListView, QListWidget, QListWidgetItem,  # noqa: F401
                     QTableView, QTreeView, QTreeWidget, QTreeWidgetItem)
from ._widgets import (QAbstractButton, QAbstractScrollArea, QBoxLayout, QButtonGroup, QCheckBox, QComboBox,  # noqa: F401
                       QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLayout, QLayoutItem, QLineEdit,
                       QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QRubberBand, QScrollArea, QSizeGrip,
                       QSizePolicy, QSlider, QSpacerItem, QSpinBox, QSplitter, QStackedWidget, QTabBar, QTabWidget,
                       QTextBrowser, QTextEdit, QToolButton, QVBoxLayout, QWidget)
from ._windows import (QApplication, QColorDialog, QDialog, QDialogButtonBox, QFileDialog, QInputDialog,  # noqa: F401
                       QMainWindow, QMenu, QMenuBar, QMessageBox, QStatusBar, QWidgetAction)


class QStyle:
    class StandardPixmap:
        SP_ArrowBack = 0


class QToolBar(QWidget):
    pass


class QStyledItemDelegate:
    """項目の描き方（ブラウザ版では使わない。灰色の項目のチェックを薄くするのは一覧の側で行う）。"""
    def __init__(self, parent=None):
        self._parent = parent

    def initStyleOption(self, *_a):
        pass


class QStyleFactory:
    @staticmethod
    def create(*_a):
        return None
