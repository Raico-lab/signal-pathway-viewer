"""① データベースタブ：一覧・絞り込み・説明・CSV への書き出し（閲覧だけ。DB はサーバーの版が正で、各 PC では編集しない）。"""
import html
import time
from contextlib import contextmanager

from PyQt6.QtCore import (QAbstractTableModel, QEvent, QModelIndex, QObject, QPointF, QSortFilterProxyModel, Qt, QTimer,
                          QUrl, pyqtSignal)
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QMouseEvent
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QCompleter, QFileDialog, QHBoxLayout, QHeaderView,
                             QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSpinBox, QSplitter, QTableView,
                             QTabWidget, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                             QWidget, QWidgetAction)

from .. import conditions
from ..db import Database
from ..db import csv_io
from ..db.vocab import EFFECTS, PARALOG_SOURCE, resolve_effect, type_label
from . import help_text
from .network_tab import DetailState, WidthWatcher


# ---- 計算中の印（カーソル） ----
# 表の計算は画面のスレッドで一気に行うので、途中で印を出すことはできない。そこで操作ごとに前回かかった時間を覚えておき、
# BUSY_DELAY 秒を超えていた操作は、始める前からカーソルを「計算中」（回る印付き）にする。読み込みははじめから長いとみなす
BUSY_DELAY = 0.6
_last_seconds: dict[str, float] = {"reload": 1.0}


@contextmanager
def busy(key: str):
    show = _last_seconds.get(key, 0.0) > BUSY_DELAY
    if show:
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
    start = time.perf_counter()
    try:
        yield
    finally:
        _last_seconds[key] = time.perf_counter() - start
        if show:
            QApplication.restoreOverrideCursor()


class RowsModel(QAbstractTableModel):
    def __init__(self, headers: list[str]):
        super().__init__()
        self.headers = headers
        self.rows: list[list] = []
        self.ids: list[int] = []
        self.colors: dict[tuple[int, int], str] = {}
        self._sort: tuple[int, Qt.SortOrder] | None = None   # 今の並べ替え（行を入れ直したときにもう一度かける）

    def set_rows(self, ids, rows, colors=None):
        self.beginResetModel()
        self.ids, self.rows, self.colors = list(ids), rows, colors or {}
        if self._sort:
            self._reorder(*self._sort)
        self.endResetModel()

    @staticmethod
    def _key(value):
        """並べ替えの鍵: 数は数として、文字は大文字・小文字を区別せずに。空は最後（昇順のとき）。"""
        if value is None or value == "":
            return (2, 0, "")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (0, value, "")
        return (1, 0, str(value).lower())

    def _reorder(self, column: int, order: Qt.SortOrder) -> list[int]:
        """行を並べ替え、元の行番号の並び（新しい行 → 元の行）を返す。"""
        perm = sorted(range(len(self.rows)), key=lambda r: self._key(self.rows[r][column]),
                      reverse=order == Qt.SortOrder.DescendingOrder)
        self.rows = [self.rows[r] for r in perm]
        self.ids = [self.ids[r] for r in perm]
        new_of = {old: new for new, old in enumerate(perm)}
        self.colors = {(new_of[r], c): v for (r, c), v in self.colors.items()}
        return perm

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        """Python で 1 回だけ並べ替える（QSortFilterProxyModel に任せると、比べるたびに Python を呼んで遅い）。"""
        if column < 0 or not self.rows:
            self._sort = (column, order) if column >= 0 else None
            return
        self._sort = (column, order)
        with busy(f"sort:{id(self)}:{column}"):
            self.layoutAboutToBeChanged.emit()
            old = self.persistentIndexList()
            perm = self._reorder(column, order)
            new_of = {o: n for n, o in enumerate(perm)}
            self.changePersistentIndexList(old, [self.index(new_of[i.row()], i.column()) for i in old])
            self.layoutChanged.emit()

    def rowCount(self, parent=QModelIndex()):
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        value = self.rows[index.row()][index.column()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return "" if value is None else str(value)
        if role == Qt.ItemDataRole.UserRole:  # 並べ替え用の生の値
            return value
        if role == Qt.ItemDataRole.BackgroundRole and (index.row(), index.column()) in self.colors:
            return QBrush(QColor(self.colors[(index.row(), index.column())]))
        if role == Qt.ItemDataRole.ForegroundRole and (index.row(), index.column()) in self.colors:
            # 色を塗ったマスの文字は、地の明るさで黒か白に（暗い画面の白い文字が明るい地に乗らないように）
            c = QColor(self.colors[(index.row(), index.column())])
            light = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue() > 140
            return QBrush(QColor("#000000" if light else "#ffffff"))
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return None


class PassOutsideClick(QObject):
    """プルダウン（QCompleter の一覧・QMenu）が開いているときに、その外を押したら、プルダウンを閉じてから、押した所の
    部品にもう一度クリックを届ける。macOS では、外の押下はプルダウンを閉じるだけで下の部品に届かず、
    「入力中に『条件』を押しても開かない」などになるため。"""

    def __init__(self, owner: QWidget, popup):
        """owner: この仕組みを持つ部品（消えるまで働く）。popup: プルダウンを返す関数（QCompleter は一覧を作り直すことがある）。"""
        super().__init__(owner)
        self._popup = popup
        # QCompleter は一覧に自分の目印を付けて押下を先に処理するので、アプリ全体で先に見る（開いているときだけ働く）
        QApplication.instance().installEventFilter(self)

    @property
    def popup(self) -> QWidget:
        return self._popup()

    def eventFilter(self, obj, event):
        if (event.type() == event.Type.MouseButtonPress and QApplication.activePopupWidget() is not None
                and QApplication.activePopupWidget() is self.popup):
            pos = event.globalPosition().toPoint()
            if not self.popup.rect().contains(self.popup.mapFromGlobal(pos)):
                self.popup.hide()
                target = QApplication.widgetAt(pos)
                # プルダウンを開いた部品そのもの（「条件」のボタン）を押したときは、閉じるだけ
                opener = self.popup.parentWidget()
                if (target is not None and target.window() is not self.popup.window()
                        and not (isinstance(self.popup, QMenu) and opener is not None
                                 and (target is opener or opener.isAncestorOf(target)))):
                    QTimer.singleShot(0, lambda: self._click(target, pos))
                return True
        return super().eventFilter(obj, event)

    @staticmethod
    def _click(target: QWidget, pos) -> None:
        # プルダウン付きのボタン（「条件」）は、押して離すと開く前に戻ってしまうので、直接開く
        button = target if isinstance(target, QPushButton) else None
        if button is not None and button.menu() is not None:
            button.showMenu()
            return
        local = QPointF(target.mapFromGlobal(pos))
        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            QApplication.sendEvent(target, QMouseEvent(kind, local, QPointF(pos), Qt.MouseButton.LeftButton,
                                                       Qt.MouseButton.LeftButton if kind == QEvent.Type.MouseButtonPress
                                                       else Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


class GenePicker(QLineEdit):
    """遺伝子名の入力窓。打つと名前を含む候補がプルダウンで出て、選んだ（または候補どおりに打って Enter した）
    名前を chosen にする。打ち直して選んだ名前と違ったら chosen は空に戻る（絞り込まない）。"""
    chosenChanged = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.chosen = ""
        self.names: dict[str, str] = {}   # 大文字の名前 → 表示の名前
        self.setClearButtonEnabled(True)
        self.setPlaceholderText("名前で絞り込む")
        self.setFixedWidth(140)
        completer = QCompleter([])
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setMaxVisibleItems(15)
        completer.activated[str].connect(self._choose)
        self.setCompleter(completer)
        self._pass_click = PassOutsideClick(self, lambda: self.completer().popup())
        self.returnPressed.connect(lambda: self._choose(self.text()))
        self.textChanged.connect(self._edited)

    def set_names(self, names: list[str]) -> None:
        self.names = {n.upper(): n for n in names}
        self.completer().model().setStringList(names)
        if self.chosen and self.chosen.upper() not in self.names:
            self._set("")

    def _choose(self, text: str) -> None:
        name = self.names.get(text.strip().upper())
        if name:
            if self.text() != name:
                self.setText(name)
            self._set(name)

    def mousePressEvent(self, event):
        # 押したら候補を出す（打たなくても選べるように。空なら全部、文字があればそれを含む名前）
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and not self.completer().popup().isVisible():
            QTimer.singleShot(0, self._show_candidates)

    def _show_candidates(self) -> None:
        completer = self.completer()
        completer.setCompletionPrefix("" if self.text() == self.chosen else self.text())
        completer.complete()

    def _edited(self, text: str) -> None:
        if self.chosen and text != self.chosen:
            self._set("")

    def _set(self, name: str) -> None:
        if name != self.chosen:
            self.chosen = name
            self.chosenChanged.emit()


class ConditionPicker(QPushButton):
    """制御関係の「条件」の絞り込み: 押すとプルダウンが開き、条件の群（大分類）が並ぶ。群を押すとその小分類が開き、
    チェックは群でも小分類でも付けられる（群のチェックで中の小分類をまとめて切り替え）。最初はすべてチェック（絞り込まない）。
    チェックした条件を 1 つでも持つ関係だけを残す（すべて外せば 0 件）。"""
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setText("条件")
        self.setMinimumWidth(220)   # 「条件: 浸透圧ストレス ほか 12」などが切れないように
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumSize(340, 420)
        self.tree.itemChanged.connect(self._on_changed)
        self.tree.itemClicked.connect(self._on_clicked)
        menu = QMenu(self)
        action = QWidgetAction(menu)
        action.setDefaultWidget(self.tree)
        menu.addAction(action)
        self.setMenu(menu)
        self._pass_click = PassOutsideClick(self, self.menu)
        self.keys: set[str] = set()       # 並んでいる条件
        self.off: set[str] = set()        # チェックを外した条件（DB を読み直しても残す。新しく出た条件はチェック済み）
        self.selected: set[str] = set()

    def set_all(self, on: bool) -> None:
        """「すべて選択」「すべて解除」。"""
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self._on_changed()

    def is_filtering(self) -> bool:
        return bool(self.off)

    def set_conditions(self, present: set[str]) -> None:
        """DB の関係に付いている条件だけを群ごとに並べ直す（選んでいたものは残す）。"""
        groups: dict[str, list] = {}
        for c in conditions.load():
            if c.key in present:
                groups.setdefault(c.group, []).append((c.key, c.label))
        self.tree.blockSignals(True)
        self.tree.clear()
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
        for group, items in groups.items():
            head = QTreeWidgetItem([group])
            head.setFlags(flags | Qt.ItemFlag.ItemIsAutoTristate)
            for key, label in items:
                item = QTreeWidgetItem([label])
                item.setFlags(flags)
                item.setData(0, Qt.ItemDataRole.UserRole, key)
                item.setCheckState(0, Qt.CheckState.Unchecked if key in self.off else Qt.CheckState.Checked)
                head.addChild(item)
            self.tree.addTopLevelItem(head)
        none = QTreeWidgetItem([conditions.NONE_LABEL])
        none.setFlags(flags)
        none.setData(0, Qt.ItemDataRole.UserRole, conditions.NONE_KEY)
        none.setCheckState(0, Qt.CheckState.Unchecked if conditions.NONE_KEY in self.off else Qt.CheckState.Checked)
        self.tree.addTopLevelItem(none)
        self.tree.blockSignals(False)
        self._on_changed()

    def _on_clicked(self, item, _column) -> None:
        if item.childCount():
            item.setExpanded(not item.isExpanded())

    def _on_changed(self, item=None, _column=0) -> None:
        if item is not None and item.childCount() and item.checkState(0) == Qt.CheckState.Checked:
            item.setExpanded(True)   # 群にチェックしたら中の小分類を見せる
        keys, selected = set(), set()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for item in [top.child(j) for j in range(top.childCount())] or [top]:
                key = item.data(0, Qt.ItemDataRole.UserRole)
                keys.add(key)
                if item.checkState(0) == Qt.CheckState.Checked:
                    selected.add(key)
        names = conditions.labels()
        first = sorted(names.get(k, k) for k in selected)[:1]
        self.setText("条件: すべて" if selected == keys else "条件: なし" if not selected else
                     f"条件: {first[0]}" + (f" ほか {len(selected) - 1}" if len(selected) > 1 else ""))
        off = keys - selected
        if (off, selected) != (self.off, self.selected):
            self.keys, self.off, self.selected = keys, off, selected
            self.changed.emit()


class IdFilterProxy(QSortFilterProxyModel):
    """検索ボックスの文字に加えて、allowed（行の id の集合）に入る行だけを残す（None なら絞り込まない）。"""

    def __init__(self):
        super().__init__()
        self.allowed: set[int] | None = None

    def set_allowed(self, allowed: set[int] | None) -> None:
        self.allowed = allowed
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        return self.allowed is None or self.sourceModel().ids[row] in self.allowed

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        # 並べ替えは元の表（RowsModel.sort）で行い、ここでは元の並びのまま使う
        self.sourceModel().sort(column, order)


class DeselectTable(QTableView):
    """行のない所をクリックすると選択を外す表。"""

    def mousePressEvent(self, event):
        if not self.indexAt(event.position().toPoint()).isValid():
            self.clearSelection()
        super().mousePressEvent(event)


class TablePage(QWidget):
    """件数・操作ボタン＋絞り込み＋表の 1 ページ。"""

    def __init__(self, headers: list[str], parent=None):
        super().__init__(parent)
        self.model = RowsModel(headers)
        self.proxy = IdFilterProxy()
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.count = QLabel()
        self.table = DeselectTable()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.help_button = QPushButton("Help")
        # 1 行に: 左に表ごとの絞り込み（DatabaseTab が中身を置く）、右端に件数と Help
        self.filter_row = QHBoxLayout()
        top = QHBoxLayout()
        top.addLayout(self.filter_row)
        top.addStretch(1)
        top.addWidget(self.count)
        top.addWidget(self.help_button)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table)
        self.proxy.rowsInserted.connect(self._update_count)
        self.proxy.rowsRemoved.connect(self._update_count)   # 絞り込みで行が減ったとき
        self.proxy.modelReset.connect(self._update_count)
        self.proxy.layoutChanged.connect(self._update_count)

    def _update_count(self, *_):
        self.count.setText(f"{self.proxy.rowCount()} / {self.model.rowCount()} 件")

    def shown_ids(self) -> set[int]:
        """絞り込みで残っている行の id。"""
        return {self.model.ids[self.proxy.mapToSource(self.proxy.index(r, 0)).row()] for r in range(self.proxy.rowCount())}

    def selected_ids(self) -> list[int]:
        rows = {self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()}
        return [self.model.ids[r] for r in sorted(rows)]

    def set_rows(self, ids, rows, colors=None):
        self.model.set_rows(ids, rows, colors)
        self.table.resizeColumnsToContents()
        for c in range(self.model.columnCount()):
            if self.table.columnWidth(c) > 320:
                self.table.setColumnWidth(c, 320)


class DatabaseTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.db: Database | None = None
        self.proteins_page = TablePage(
            ["遺伝子名", "Systematic 名", "ORF の確度（SGD）", "機能説明"])
        self.interactions_page = TablePage(
            ["上流", "下流", "種類", "作用の向き", "条件", "根拠・文献"])
        self.categories_page = TablePage(["名前", "複合体", "色", "所属遺伝子数", "所属遺伝子", "出典"])

        self._build_filters()
        self._proteins, self._interactions, self._categories = [], [], []
        self._cat_members: dict[int, list[str]] = {}   # カテゴリ → 所属する遺伝子名（手作業の区分と Complex Portal・パラログ）

        self.tabs = QTabWidget()
        self.tabs.addTab(self.proteins_page, "遺伝子")
        self.tabs.addTab(self.interactions_page, "制御関係")
        self.tabs.addTab(self.categories_page, "カテゴリ")

        self.export_button = QPushButton("表示中の表を CSV エクスポート…")
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.export_button)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.tabs)
        left_layout.addLayout(bottom)

        # 右側の説明欄: 最初は空。表の行を選ぶとその遺伝子・制御関係・カテゴリの説明（各表の右上の Help で使い方に戻る）
        self.network = None   # 説明欄の中身を地図の説明欄と同じ関数で作るのに使う（MainWindow がつなぐ。set_network）
        self.help_view = QTextBrowser()
        self.help_view.setMinimumWidth(240)
        self.help_view.setOpenLinks(False)
        self.help_view.anchorClicked.connect(lambda url: self._open_link(url.toString()))
        WidthWatcher(self.help_view).changed.connect(
            lambda: self._current and self._current[0] == "node" and self._refresh_detail())
        self.detail_state = DetailState()   # 説明欄の開閉（一覧・条件の測定）。中身は地図の説明欄と共通
        self._current = None   # 説明欄に出している (種類, キー)。空・使い方を出しているときは None
        # 説明欄の履歴（◀ ▶。地図の説明欄と同じ）。空は (None, None)、使い方は ("usage", None) として入れる
        self._hist: list[tuple] = [(None, None)]
        self._hist_pos = 0
        self._hist_nav = False
        nav = QHBoxLayout()
        nav.setContentsMargins(0, 0, 0, 0)
        self.detail_back, self.detail_forward = QPushButton("◀"), QPushButton("▶")
        for button, step, tip in ((self.detail_back, -1, "前に表示した説明に戻る"),
                                  (self.detail_forward, 1, "次に表示した説明に進む")):
            button.setFixedWidth(34)
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _=False, s=step: self._detail_go(s))
            nav.addWidget(button)
        nav.addStretch(1)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addLayout(nav)
        right_layout.addWidget(self.help_view, 1)
        # 一番下の Help（地図の説明欄と同じ）: 出している説明の見方を、説明欄の新しいページとして出す（◀ で戻れる）
        detail_help = QPushButton("Help")
        detail_help.setToolTip("この説明欄の見方を表示します（◀ で戻れます）")
        detail_help.clicked.connect(self.show_detail_help)
        right_layout.addWidget(detail_help)
        self._update_nav()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([900, 300])
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        p, i, c = self.proteins_page, self.interactions_page, self.categories_page
        for page, kind in ((p, "node"), (i, "edge"), (c, "category")):
            page.help_button.clicked.connect(self.show_help)
            page.table.selectionModel().selectionChanged.connect(
                lambda *_a, page=page, kind=kind: self._show_row(page, kind))
        self.export_button.clicked.connect(self.export_csv)
        # カテゴリの表は CSV に書き出せないので、そのタブではボタンを押せなくする
        self.tabs.currentChanged.connect(lambda i: self.export_button.setEnabled(i != 2))

    # ---- 絞り込み ----
    def _build_filters(self) -> None:
        """遺伝子: 候補から選んだ名前と、そこから上流・下流へ何段までを残すか。制御関係: 上流・下流の名前と、左の条件タブの選択。"""
        def steps():
            spin = QSpinBox()
            spin.setRange(0, 3)
            spin.setValue(1)
            return spin

        self.gene_name, self.gene_up, self.gene_down = GenePicker(), steps(), steps()
        row = self.proteins_page.filter_row
        for label, w in (("遺伝子", self.gene_name), ("上流", self.gene_up), ("下流", self.gene_down)):
            row.addWidget(QLabel(label))
            row.addWidget(w)
        self.gene_exact = QCheckBox("一致")   # チェック中は選んだ遺伝子だけ（上流・下流の段数は使わない）
        row.addWidget(self.gene_exact)
        self.gene_name.chosenChanged.connect(self._filter_proteins)
        self.gene_up.valueChanged.connect(self._filter_proteins)
        self.gene_down.valueChanged.connect(self._filter_proteins)
        self.gene_exact.toggled.connect(self._on_gene_exact)

        self.rel_source, self.rel_target = GenePicker(), GenePicker()
        self.rel_conditions = ConditionPicker()
        row = self.interactions_page.filter_row
        for label, w in (("上流", self.rel_source), ("下流", self.rel_target)):
            row.addWidget(QLabel(label))
            row.addWidget(w)
        row.addWidget(self.rel_conditions)
        for text, on in (("すべて解除", False), ("すべて選択", True)):
            button = QPushButton(text)
            button.setToolTip(f"条件を{text}")
            button.clicked.connect(lambda _=False, on=on: self.rel_conditions.set_all(on))
            row.addWidget(button)
        self.rel_source.chosenChanged.connect(self._filter_interactions)
        self.rel_target.chosenChanged.connect(self._filter_interactions)
        self.rel_conditions.changed.connect(self._filter_interactions)

        # カテゴリ自体の名前（複合体・パラログの組の名前）でも絞り込む。打つたびに、名前にその文字を含むものだけを残す
        self.cat_name = QLineEdit()
        self.cat_name.setPlaceholderText("名前で絞り込む")
        self.cat_name.setClearButtonEnabled(True)
        self.cat_name.setFixedWidth(180)
        self.cat_gene = GenePicker()
        row = self.categories_page.filter_row
        row.addWidget(self.cat_name)
        row.addWidget(QLabel("遺伝子"))
        row.addWidget(self.cat_gene)
        self.cat_name.textChanged.connect(self._filter_categories)
        self.cat_gene.chosenChanged.connect(self._filter_categories)

    def _filter_categories(self, *args) -> None:
        with busy("_filter_categories"):
            self._filter_categories_now(*args)

    @staticmethod
    def _name_key(text: str) -> str:
        """名前の照らし合わせ用: 大文字・小文字と、ハイフン・下線と空白の違いを区別しない。"""
        return text.lower().replace("-", " ").replace("_", " ")

    def _filter_categories_now(self, *_) -> None:
        """カテゴリ: 名前に、打った語（空白で区切ったもの）をすべて含み、選んだ遺伝子が所属するカテゴリ
        （複合体・パラログの組）だけを残す。"""
        gene, words = self.cat_gene.chosen, self._name_key(self.cat_name.text()).split()
        if not gene and not words:
            self.categories_page.proxy.set_allowed(None)
            return
        self.categories_page.proxy.set_allowed(
            {c.id for c in self._categories
             if all(w in self._name_key(c.name) for w in words)
             and (not gene or gene in self._cat_members.get(c.id, []))})

    def _on_gene_exact(self, on: bool) -> None:
        self.gene_up.setEnabled(not on)
        self.gene_down.setEnabled(not on)
        self._filter_proteins()

    def _find_gene(self, name: str):
        return next((p for p in self._proteins if p.gene_name == name), None)

    def _filter_proteins(self, *args) -> None:
        with busy("_filter_proteins"):
            self._filter_proteins_now(*args)

    def _filter_proteins_now(self, *_) -> None:
        start = self._find_gene(self.gene_name.chosen) if self.gene_name.chosen else None
        if start is None:
            self.proteins_page.proxy.set_allowed(None)
            return
        keep = {start.id}
        if self.gene_exact.isChecked():
            self.proteins_page.proxy.set_allowed(keep)
            return
        ups, downs = {}, {}
        for it in self._interactions:
            ups.setdefault(it.target_id, set()).add(it.source_id)
            downs.setdefault(it.source_id, set()).add(it.target_id)
        for adj, n in ((ups, self.gene_up.value()), (downs, self.gene_down.value())):
            front = {start.id}
            for _ in range(n):
                front = {m for g in front for m in adj.get(g, ())} - keep
                keep |= front
        self.proteins_page.proxy.set_allowed(keep)

    def _filter_interactions(self, *args) -> None:
        with busy("_filter_interactions"):
            self._filter_interactions_now(*args)

    def _filter_interactions_now(self, *_) -> None:
        tests = []
        for box, side in ((self.rel_source, "source_id"), (self.rel_target, "target_id")):
            if box.chosen:
                gene = self._find_gene(box.chosen)
                gid = gene.id if gene else None
                tests.append(lambda it, side=side, gid=gid: getattr(it, side) == gid)
        chosen = self.rel_conditions.selected
        if self.rel_conditions.is_filtering():
            tests.append(lambda it: any(k in chosen for k in conditions.split(it.conditions) or [conditions.NONE_KEY]))
        self.interactions_page.proxy.set_allowed(
            {it.id for it in self._interactions if all(t(it) for t in tests)} if tests else None)

    # ---- 右側の説明欄 ----
    def _show_row(self, page: "TablePage", kind: str) -> None:
        """選んだ行の説明を出す（選択が外れたら空に戻す）。"""
        rows = page.table.selectionModel().selectedRows()
        if not rows:
            self.show_blank()
            return
        current = page.table.currentIndex()
        index = current if current.isValid() and page.table.selectionModel().isRowSelected(current.row()) else rows[0]
        self.show_detail(kind, page.model.ids[page.proxy.mapToSource(index).row()])

    def mousePressEvent(self, event):
        # 表の外の何もない所をクリックしたら、表の選択を外す（入力窓・ボタン・説明欄のクリックはここに来ない）
        for page in (self.proteins_page, self.interactions_page, self.categories_page):
            page.table.clearSelection()
        super().mousePressEvent(event)

    def show_blank(self) -> None:
        self._current = None
        self.help_view.setHtml("")
        self._push_hist(None, None)

    def show_help(self) -> None:
        self._current = None
        self.help_view.setHtml(help_text.DATABASE)
        self._push_hist("usage", None)

    def show_detail_help(self) -> None:
        """説明欄の一番下の Help: 今の説明の見方を新しいページとして出す。何も出していなければ表の使い方。"""
        kind, key = self._current or (None, None)
        if kind is None:
            self.show_help()
            return
        if kind == "help":
            return
        if kind == "category":
            c = next((x for x in self._categories if x.id == key), None)
            topic = "paralog" if c is not None and c.source == PARALOG_SOURCE else "complex"
        else:
            topic = kind
        self.show_detail("help", topic)

    def show_detail(self, kind: str, key) -> None:
        if kind == "help":
            self._current = (kind, key)
            self.help_view.setHtml(help_text.detail_help(key))
            self._push_hist(kind, key)
            return
        if (kind, key) != self._current:
            self.detail_state.reset()   # 別のものを開いたら、一覧を閉じる（地図の説明欄と同じ）
        try:
            text = {"node": self._gene_html, "edge": self._edge_html, "category": self._category_html}[kind](key)
        except (KeyError, StopIteration, AttributeError):
            return
        self._current = (kind, key)
        self.help_view.setHtml(text)
        self._push_hist(kind, key)

    # ---- 説明欄の履歴（◀ ▶） ----
    HISTORY_MAX = 50

    def _push_hist(self, kind, key) -> None:
        if self._hist_nav or self._hist[self._hist_pos] == (kind, key):
            return
        del self._hist[self._hist_pos + 1:]
        self._hist.append((kind, key))
        del self._hist[:-self.HISTORY_MAX]
        self._hist_pos = len(self._hist) - 1
        self._update_nav()

    def _update_nav(self) -> None:
        self.detail_back.setEnabled(self._hist_pos > 0)
        self.detail_forward.setEnabled(self._hist_pos < len(self._hist) - 1)

    def _detail_go(self, step: int) -> None:
        pos = self._hist_pos + step
        if not 0 <= pos < len(self._hist):
            return
        self._hist_pos = pos
        kind, key = self._hist[pos]
        self._hist_nav = True
        try:
            if kind is None:
                self.show_blank()
            elif kind == "usage":
                self.show_help()
            else:
                self.show_detail(kind, key)
        finally:
            self._hist_nav = False
        self._update_nav()

    def _refresh_detail(self) -> None:
        """説明欄を出し直す（スクロール位置は保つ）。"""
        if self._current is None:
            return
        bar = self.help_view.verticalScrollBar()
        pos = bar.value()
        self.show_detail(*self._current)
        bar.setValue(pos)

    def _desc_html(self, text: str) -> str:
        """英語の説明（原文のまま）。"""
        return f"<p style='margin-bottom:2px'>{html.escape(text)}</p>" if text else ""

    def set_network(self, network) -> None:
        """地図のタブとつなぐ: 説明欄の中身を地図と同じ関数で作る。"""
        self.network = network

    def _open_link(self, target: str) -> None:
        if target.startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(target))
            return
        if self.network and self.network.toggle_detail(self.detail_state, target):
            self._refresh_detail()   # 一覧・条件の測定の開閉（スクロール位置は保つ）
            return
        kind, _, key = target.partition(":")
        if kind in ("node", "edge"):
            self.show_detail(kind, int(key))
        elif kind == "cpx":
            # 遺伝子の説明の「所属する複合体」: その複合体（カテゴリ）の説明を出す
            cat = next((c for c in self._categories if c.ref == key and c.is_complex), None)
            if cat:
                self.show_detail("category", cat.id)
            else:
                QDesktopServices.openUrl(QUrl(f"https://www.ebi.ac.uk/complexportal/complex/{key}"))
        elif kind == "paralog":
            cat = next((c for c in self._categories if c.name == key), None)
            if cat:
                self.show_detail("category", cat.id)

    def _gene_html(self, pid: int) -> str:
        self.detail_state.width = self.help_view.viewport().width()   # 条件名をこの欄の幅で折り返す
        return self.network.render_html("node", pid, self.detail_state)   # 地図の説明欄と同じ

    def _edge_html(self, iid: int) -> str:
        return self.network.render_html("edge", iid, self.detail_state)   # 地図の説明欄と同じ

    def _category_html(self, cid: int) -> str:
        c = next(x for x in self._categories if x.id == cid)
        genes = set(self._cat_members.get(cid, []))
        members = sorted((p for p in self._proteins if p.gene_name in genes), key=lambda p: p.gene_name)
        items = "".join(f"<li><a href='node:{p.id}'>{html.escape(p.gene_name)}</a></li>" for p in members)
        if self.network and self.network.model and c.name in self.network.model.categories and \
                (c.source == PARALOG_SOURCE or c.name in self.network.model.all_complexes()):
            return self.network.render_html("complex", c.name, self.detail_state)   # 地図の説明欄と同じ
        if c.source:
            # Complex Portal の複合体: 出典へのリンク・構成・説明
            link = (f"<a href='https://www.ebi.ac.uk/complexportal/complex/{html.escape(c.ref)}'>"
                    f"{html.escape(c.source)} {html.escape(c.ref)}</a>")
            return (f"<h2 style='margin-bottom:2px'>{html.escape(c.name)}</h2><p>{link}</p>"
                    f"<h4>所属する遺伝子 ({len(members)})</h4>"
                    + (f"<ul style='margin-top:0'>{items}</ul>" if items else "<span style='color:#888'>なし</span>")
                    + (f"<h4 style='margin-bottom:2px'>説明</h4>{self._desc_html(c.description)}" if c.description else ""))
        swatch = f"<span style='background:{html.escape(c.color or '')}'>&nbsp;&nbsp;&nbsp;&nbsp;</span> {html.escape(c.color or '')}"
        return (f"<h2 style='margin-bottom:2px'>{html.escape(c.name)}{' 複合体' if c.is_complex and not c.name.endswith('複合体') else ''}</h2>"
                f"<p>色: {swatch}</p><h4>所属する遺伝子 ({len(members)})</h4>"
                + (f"<ul style='margin-top:0'>{items}</ul>" if items else "<span style='color:#888'>なし</span>")
                + (self._complex_about(c.name, {p.gene_name.upper() for p in members}) if c.is_complex else ""))

    def _complex_about(self, name: str, genes: set[str]) -> str:
        """複合体の説明: Complex Portal で構成の重なる複合体（名前・構成・説明。リンク付き）。
        構成の半分以上が重なるもの（またはカテゴリの遺伝子をすべて含むもの）を、重なりの多い順に 3 つまで。"""
        if not self.network or not genes:
            return ""
        scored = []
        for ac, title, members, desc in self.network.portal_complexes():
            shared = len(genes & members)
            if shared and (shared / len(genes | members) >= 0.5 or genes <= members):
                scored.append((shared / len(genes | members), ac, title, members, desc))
        scored.sort(key=lambda x: -x[0])
        parts, seen = [], set()
        for _score, ac, title, members, desc in scored[:3]:
            if desc in seen:
                desc = ""   # 型違い（TORC1 の TOR1 型・TOR2 型など）で同じ説明は 1 度だけ出す
            seen.add(desc)
            parts.append(f"<p style='margin-bottom:2px'><a href='https://www.ebi.ac.uk/complexportal/complex/{ac}'>"
                         f"{html.escape(title)}</a> <span style='color:#888'>({'・'.join(sorted(members))})</span></p>"
                         + self._desc_html(desc))
        return ("<h4 style='margin-bottom:2px'>複合体の説明（Complex Portal）</h4>" + "".join(parts)) if parts else ""

    # ---- 表示 ----
    def set_database(self, db: Database) -> None:
        self.db = db
        self.reload()

    def reload(self, *args) -> None:
        # 表は、このタブが見えているときだけ作る（隠れているときは印だけ付け、開いたときに作る。起動を速くするため）
        if not self.isVisible():
            self._stale = True
            return
        self._stale = False
        with busy("reload"):
            self._reload_now()

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_stale", False):
            self.reload()

    def _reload_now(self) -> None:
        proteins = self.db.proteins()
        interactions = self.db.interactions()
        categories = self.db.categories()
        self._proteins, self._interactions = proteins, interactions
        names = sorted({p.gene_name for p in proteins})
        for box in (self.gene_name, self.rel_source, self.rel_target):
            box.set_names(names)
        cond_labels = conditions.labels()
        self.rel_conditions.set_conditions({k for it in interactions for k in conditions.split(it.conditions)})
        self.proteins_page.set_rows(
            [p.id for p in proteins],
            [[p.gene_name, p.standard_name, p.protein_properties, p.description] for p in proteins])
        self.interactions_page.set_rows(
            [it.id for it in interactions],
            [[it.source.gene_name, it.target.gene_name, type_label(it.interaction_type),
              EFFECTS[resolve_effect(it.interaction_type, it.effect)] + ("" if it.effect else "・自動"),
              "・".join(cond_labels.get(k, k) for k in conditions.split(it.conditions)),
              it.evidence] for it in interactions])
        # 所属: 手作業の区分（Upstream など）は遺伝子の complex_category、Complex Portal・パラログは CategoryMember（複数に入れる）
        by_frame: dict[str, list[str]] = {}
        for p in proteins:
            if p.complex_category:
                by_frame.setdefault(p.complex_category, []).append(p.gene_name)
        gene_names = {p.id: p.gene_name for p in proteins}
        extra = self.db.category_members()
        self._cat_members = {c.id: sorted(by_frame.get(c.name, []) if not c.source else
                                          [gene_names[g] for g in extra.get(c.id, []) if g in gene_names])
                             for c in categories}
        self._categories = categories
        self.cat_gene.set_names(sorted({g for genes in self._cat_members.values() for g in genes}))
        self.categories_page.set_rows(
            [c.id for c in categories],
            [[c.name, "✔" if c.is_complex else "", c.color, len(self._cat_members[c.id]),
              "・".join(self._cat_members[c.id]), c.source or "手作業"] for c in categories],
            {(r, 2): c.color for r, c in enumerate(categories)})
        self._filter_proteins()
        self._filter_interactions()
        self._filter_categories()

    # ---- CSV ----
    def export_csv(self) -> None:
        index = self.tabs.currentIndex()
        if index == 2:   # カテゴリのタブではボタンを押せない
            return
        page = self.proteins_page if index == 0 else self.interactions_page
        ids = page.shown_ids() if page.proxy.rowCount() < page.model.rowCount() else None
        name = "proteins.csv" if index == 0 else "interactions.csv"
        path, _ = QFileDialog.getSaveFileName(self, "CSV にエクスポート", name, "CSV (*.csv)")
        if not path:
            return
        try:
            (csv_io.export_proteins if index == 0 else csv_io.export_interactions)(self.db, path, ids)
        except OSError as e:
            QMessageBox.critical(self, "エラー", f"書き出せませんでした。\n{e}")
            return
        QMessageBox.information(self, "エクスポート完了", f"{page.proxy.rowCount()} 件を {path} に保存しました。")
