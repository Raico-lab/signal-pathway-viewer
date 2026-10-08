"""一覧（QListWidget）・木（QTreeWidget）・表（QTableView）・入力の候補（QCompleter）。"""
import enum
import weakref

import js

from . import _dom
from ._core import (QModelIndex, QObject, QPoint, QItemSelectionModel, QStringListModel, Qt, pyqtSignal)
from ._gui import QBrush, QColor, QFont, QFontMetrics
from ._widgets import QAbstractScrollArea, QSizePolicy, QWidget, _px, _visibility_changed

ROLE = Qt.ItemDataRole


class QAbstractItemView(QAbstractScrollArea):
    _accepts_mouse = True

    def setItemDelegate(self, *_a):
        pass

    class SelectionMode(enum.IntEnum):
        NoSelection = 0
        SingleSelection = 1
        MultiSelection = 2
        ExtendedSelection = 3
        ContiguousSelection = 4

    class SelectionBehavior(enum.IntEnum):
        SelectItems = 0
        SelectRows = 1
        SelectColumns = 2

    class EditTrigger(enum.IntFlag):
        NoEditTriggers = 0
        CurrentChanged = 1
        DoubleClicked = 2
        SelectedClicked = 4
        EditKeyPressed = 8
        AnyKeyPressed = 16
        AllEditTriggers = 31

    class ScrollHint(enum.IntEnum):
        EnsureVisible = 0
        PositionAtTop = 1
        PositionAtBottom = 2
        PositionAtCenter = 3

    class ScrollMode(enum.IntEnum):
        ScrollPerItem = 0
        ScrollPerPixel = 1

    def setSelectionMode(self, mode):
        self._selection_mode = mode

    def selectionMode(self):
        return getattr(self, "_selection_mode", QAbstractItemView.SelectionMode.SingleSelection)

    def setSelectionBehavior(self, *_a):
        pass

    def setEditTriggers(self, *_a):
        pass

    def setAlternatingRowColors(self, on):
        self._el.classList.toggle("alternate", bool(on))

    def setUniformItemSizes(self, *_a):
        pass

    def setUniformRowHeights(self, *_a):
        pass

    def setVerticalScrollMode(self, *_a):
        pass

    def setHorizontalScrollMode(self, *_a):
        pass

    def setTextElideMode(self, *_a):
        pass

    def setWordWrap(self, *_a):
        pass

    def setIconSize(self, *_a):
        pass

    def setDragEnabled(self, *_a):
        pass

    def setMouseTracking(self, *_a):
        pass

    def _extend_mode(self) -> bool:
        return self.selectionMode() in (QAbstractItemView.SelectionMode.ExtendedSelection,
                                        QAbstractItemView.SelectionMode.MultiSelection)


# ================= 一覧 =================
class QListWidgetItem:
    def __init__(self, text="", parent=None, *_a):
        if not isinstance(text, str):
            text, parent = "", text
        self._text = text
        self._data: dict = {}
        self._flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDragEnabled
        self._check = None
        self._fg = None
        self._bg = None
        self._tooltip = ""
        self._hidden = False
        self._bold = False
        self._list = None
        self._el = None
        if parent is not None:
            parent.addItem(self)

    def listWidget(self):
        return self._list() if self._list else None

    def _changed(self):
        lw = self.listWidget()
        if lw is not None:
            lw._render_item(self)
            lw.itemChanged.emit(self)

    def text(self):
        return self._text

    def setText(self, text):
        self._text = str(text)
        self._changed()

    def data(self, role):
        if role in (ROLE.DisplayRole, ROLE.EditRole):
            return self._text
        if role == ROLE.CheckStateRole:
            return self._check
        if role == ROLE.ToolTipRole:
            return self._tooltip
        return self._data.get(int(role))

    def setData(self, role, value):
        if role in (ROLE.DisplayRole, ROLE.EditRole):
            self._text = str(value)
        elif role == ROLE.CheckStateRole:
            self._check = value
        else:
            self._data[int(role)] = value
        self._changed()

    def flags(self):
        return self._flags

    def setFlags(self, flags):
        self._flags = flags
        self._changed()

    def checkState(self):
        return self._check if self._check is not None else Qt.CheckState.Unchecked

    def setCheckState(self, state):
        self._check = Qt.CheckState(int(state))
        self._changed()

    def setForeground(self, color):
        self._fg = color.color() if isinstance(color, QBrush) else color
        self._changed()

    def foreground(self):
        return QBrush(self._fg) if self._fg else QBrush()

    def setBackground(self, color):
        self._bg = color.color() if isinstance(color, QBrush) else color
        self._changed()

    def setToolTip(self, text):
        self._tooltip = text or ""
        self._changed()

    def toolTip(self):
        return self._tooltip

    def setHidden(self, on):
        self._hidden = bool(on)
        lw = self.listWidget()
        if lw is not None:
            lw._render_item(self)

    def isHidden(self):
        return self._hidden

    def setSelected(self, on):
        lw = self.listWidget()
        if lw is not None:
            lw._set_selected(self, on)

    def isSelected(self):
        lw = self.listWidget()
        return lw is not None and self in lw._selected

    def setFont(self, font):
        self._bold = font.bold()
        self._changed()

    def font(self):
        f = QFont()
        f.setBold(self._bold)
        return f

    def setSizeHint(self, *_a):
        pass

    def setTextAlignment(self, *_a):
        pass

    def setIcon(self, *_a):
        pass


class QListWidget(QAbstractItemView):
    itemChanged = pyqtSignal(object)
    itemClicked = pyqtSignal(object)
    itemDoubleClicked = pyqtSignal(object)
    itemActivated = pyqtSignal(object)
    itemPressed = pyqtSignal(object)
    currentItemChanged = pyqtSignal(object, object)
    currentRowChanged = pyqtSignal(int)
    currentTextChanged = pyqtSignal(str)
    itemSelectionChanged = pyqtSignal()

    def __init__(self, parent=None):
        self._items: list = []
        self._current = None
        self._selected: list = []
        super().__init__(parent)

    def _build(self):
        QAbstractItemView._build(self)
        self._el.classList.add("qlist")
        self._rows = _dom.el("div", "qlist-rows")
        self._view.appendChild(self._rows)
        self._by_key: dict = {}
        self._next_key = 0
        # 行ごとにつながず、一覧の要素でまとめて受ける（行を作り直しても受け口が増えない）
        _dom.listen(self, self._rows, "mousedown", lambda ev: self._dispatch(ev, self._on_press))
        _dom.listen(self, self._rows, "click", lambda ev: self._dispatch(ev, self._on_click))
        _dom.listen(self, self._rows, "dblclick", lambda ev: self._dispatch(ev, self._on_dblclick))

    def _dispatch(self, ev, handler):
        e = ev.target
        while not _dom.absent(e) and e != self._rows:
            # dataset に k がない要素（行の中の文字の要素など）は、Pyodide では読むと例外になるので getattr で読む
            k = getattr(e.dataset, "k", None) if hasattr(e, "dataset") else None
            if not _dom.absent(k) and str(k) != "":
                item = self._by_key.get(int(k))
                if item is not None:
                    handler(ev, item)
                return
            e = e.parentElement

    # ---- 項目 ----
    def addItem(self, item):
        self.insertItem(len(self._items), item)

    def addItems(self, texts):
        for t in texts:
            self.addItem(t)

    def insertItem(self, row, item):
        if isinstance(item, str):
            item = QListWidgetItem(item)
        item._list = weakref.ref(self)
        self._next_key += 1
        item._key = self._next_key
        self._by_key[item._key] = item
        row = min(max(0, row), len(self._items))
        self._items.insert(row, item)
        item._el = None
        self._render_item(item)
        before = self._items[row + 1]._el if row + 1 < len(self._items) else None
        self._rows.insertBefore(item._el, before)

    def _render_item(self, item):
        e = item._el
        if e is None:
            e = item._el = _dom.el("div", "q-item")
            e.dataset.k = str(item._key)
        e.innerHTML = ""
        enabled = bool(int(item._flags) & int(Qt.ItemFlag.ItemIsEnabled))
        if item._check is not None and int(item._flags) & int(Qt.ItemFlag.ItemIsUserCheckable):
            box = _dom.el("input")
            box.type = "checkbox"
            box.checked = item._check == Qt.CheckState.Checked
            box.disabled = not enabled
            box.className = "q-item-check"
            e.appendChild(box)
        e.appendChild(_dom.el("span", "q-item-text", item._text))
        e.classList.toggle("disabled", not enabled)
        e.classList.toggle("selected", item in self._selected)
        e.classList.toggle("current", item is self._current)
        e.style.color = item._fg.name() if item._fg else ""
        e.style.background = item._bg.name() if item._bg else ""
        e.style.fontWeight = "bold" if item._bold else ""
        e.style.display = "none" if item._hidden else ""
        if item._tooltip:
            e.title = item._tooltip
        else:
            e.removeAttribute("title")

    def _on_press(self, ev, item):
        if not int(item._flags) & int(Qt.ItemFlag.ItemIsEnabled):
            return
        if str(ev.target.tagName).upper() == "INPUT":
            return
        self.itemPressed.emit(item)
        if self._extend_mode() and (ev.metaKey or ev.ctrlKey):
            self._set_selected(item, item not in self._selected)
            self._set_current(item, select=False)
        elif self._extend_mode() and ev.shiftKey and self._current in self._items and item is not self._current:
            # Shift: 今の項目から押した項目までを選ぶ（Qt の ExtendedSelection と同じ）。今の項目は動かさない
            ev.preventDefault()   # 文字の範囲選択にしない
            a, b = sorted((self._items.index(self._current), self._items.index(item)))
            old = list(self._selected)
            self._selected = [it for it in self._items[a:b + 1] if not it._hidden
                              and int(it._flags) & int(Qt.ItemFlag.ItemIsSelectable)]
            for it in old + self._selected:
                if it._el is not None:
                    it._el.classList.toggle("selected", it in self._selected)
            if old != self._selected:
                self.itemSelectionChanged.emit()
        else:
            self._set_current(item)

    def _on_click(self, ev, item):
        if not int(item._flags) & int(Qt.ItemFlag.ItemIsEnabled):
            return
        if str(ev.target.tagName).upper() == "INPUT":
            item.setCheckState(Qt.CheckState.Checked if ev.target.checked else Qt.CheckState.Unchecked)
            return
        self.itemClicked.emit(item)

    def _on_dblclick(self, ev, item):
        if int(item._flags) & int(Qt.ItemFlag.ItemIsEnabled):
            self.itemDoubleClicked.emit(item)
            self.itemActivated.emit(item)

    def _set_selected(self, item, on):
        if on and item not in self._selected:
            self._selected.append(item)
        elif not on and item in self._selected:
            self._selected.remove(item)
        else:
            return
        if item._el is not None:
            item._el.classList.toggle("selected", on)
        self.itemSelectionChanged.emit()

    def _set_current(self, item, select=True):
        prev = self._current
        if select:
            old = list(self._selected)
            self._selected = [item] if item is not None else []
            for it in old + self._selected:
                if it._el is not None:
                    it._el.classList.toggle("selected", it in self._selected)
            if old != self._selected:
                self.itemSelectionChanged.emit()
        if item is prev:
            return
        self._current = item
        for it in (prev, item):
            if it is not None and it._el is not None:
                it._el.classList.toggle("current", it is self._current)
        if item is not None and item._el is not None:
            item._el.scrollIntoView(_block_nearest())
        self.currentItemChanged.emit(item, prev)
        self.currentRowChanged.emit(self.row(item) if item is not None else -1)
        self.currentTextChanged.emit(item.text() if item is not None else "")

    def item(self, row):
        return self._items[row] if 0 <= row < len(self._items) else None

    def count(self):
        return len(self._items)

    def row(self, item):
        return self._items.index(item) if item in self._items else -1

    def takeItem(self, row):
        if not 0 <= row < len(self._items):
            return None
        item = self._items.pop(row)
        self._by_key.pop(getattr(item, "_key", None), None)
        if item._el is not None:
            item._el.remove()
        item._list = None
        if item in self._selected:
            self._selected.remove(item)
        if item is self._current:
            self._current = None
            self.currentItemChanged.emit(None, item)
        return item

    def clear(self):
        prev = self._current
        had_sel = bool(self._selected)
        for it in self._items:
            it._list = None
        self._items = []
        self._selected = []
        self._current = None
        self._rows.innerHTML = ""
        self._by_key = {}
        if prev is not None:
            self.currentItemChanged.emit(None, prev)
            self.currentRowChanged.emit(-1)
        if had_sel:
            self.itemSelectionChanged.emit()

    def currentItem(self):
        return self._current

    def currentRow(self):
        return self.row(self._current) if self._current is not None else -1

    def setCurrentItem(self, item, *_a):
        self._set_current(item)

    def setCurrentRow(self, row, *_a):
        self._set_current(self.item(row))

    def selectedItems(self):
        return [it for it in self._items if it in self._selected]

    def clearSelection(self):
        if self._selected:
            old, self._selected = self._selected, []
            for it in old:
                if it._el is not None:
                    it._el.classList.remove("selected")
            self.itemSelectionChanged.emit()

    def findItems(self, text, flags=None):
        return [it for it in self._items if it._text == text]

    def itemAt(self, *args):
        p = args[0] if len(args) == 1 else QPoint(*args)
        x, y, _, _ = _dom.rect(self._view)
        target = _dom.document.elementFromPoint(x + p.x(), y + p.y())
        while not _dom.absent(target) and target != self._view:
            for it in self._items:
                if it._el is not None and it._el == target:
                    return it
            target = target.parentElement
        return None

    def visualItemRect(self, item):
        from ._core import QRect
        if item is None or item._el is None:
            return QRect()
        x, y, w, h = _dom.rect(item._el)
        vx, vy, _, _ = _dom.rect(self._view)
        return QRect(round(x - vx), round(y - vy), round(w), round(h))

    def scrollToItem(self, item, *_a):
        if item is not None and item._el is not None:
            item._el.scrollIntoView(_block_nearest())

    def sizeHintForRow(self, row) -> int:
        it = self.item(row) or (self._items[0] if self._items else None)
        if it is not None and it._el is not None and it._el.offsetHeight:
            return int(it._el.offsetHeight)
        return 20

    def sizeHintForColumn(self, col) -> int:
        fm = QFontMetrics()
        return max((fm.horizontalAdvance(it._text) for it in self._items), default=0) + 12

    def setSortingEnabled(self, *_a):
        pass

    def sortItems(self, *_a):
        pass

    def setSpacing(self, *_a):
        pass

    def openPersistentEditor(self, *_a):
        pass


def _block_nearest():
    opts = js.Object.new()
    opts.block = "nearest"
    return opts


# ================= 木 =================
class QTreeWidgetItem:
    def __init__(self, *args):
        self._texts: list = [""]
        self._data: dict = {}      # (列, 役割) → 値
        self._children: list = []
        self._parent = None
        self._tree = None
        self._expanded = False
        self._hidden = False
        self._flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDragEnabled
        self._check: dict = {}
        self._tooltips: dict = {}
        self._fg: dict = {}
        self._bold: dict = {}
        parent = None
        for a in args:
            if isinstance(a, (list, tuple)):
                self._texts = [str(t) for t in a] or [""]
            elif isinstance(a, (QTreeWidgetItem, QTreeWidget)):
                parent = a
        if isinstance(parent, QTreeWidgetItem):
            parent.addChild(self)
        elif isinstance(parent, QTreeWidget):
            parent.addTopLevelItem(self)

    def treeWidget(self):
        if self._tree is not None:
            return self._tree()
        p = self._parent
        while p is not None:
            if p._tree is not None:
                return p._tree()
            p = p._parent
        return None

    def _changed(self, column=0, emit=True):
        tree = self.treeWidget()
        if tree is not None:
            tree._dirty()
            if emit:
                tree.itemChanged.emit(self, column)

    # ---- 文字・値 ----
    def text(self, col=0):
        return self._texts[col] if col < len(self._texts) else ""

    def setText(self, col, text):
        while len(self._texts) <= col:
            self._texts.append("")
        self._texts[col] = str(text)
        self._changed(col)

    def data(self, col, role):
        if role in (ROLE.DisplayRole, ROLE.EditRole):
            return self.text(col)
        if role == ROLE.CheckStateRole:
            return self.checkState(col)
        if role == ROLE.ToolTipRole:
            return self._tooltips.get(col, "")
        return self._data.get((col, int(role)))

    def setData(self, col, role, value):
        if role in (ROLE.DisplayRole, ROLE.EditRole):
            self.setText(col, value)
            return
        if role == ROLE.CheckStateRole:
            self.setCheckState(col, value)
            return
        if role == ROLE.ForegroundRole:   # None でふだんの色に戻す
            if value is None:
                self._fg.pop(col, None)
            else:
                self._fg[col] = value.color() if isinstance(value, QBrush) else QColor(value)
            self._changed(col)
            return
        self._data[(col, int(role))] = value
        self._changed(col)

    def setToolTip(self, col, text):
        self._tooltips[col] = text or ""
        self._changed(col, emit=False)

    def toolTip(self, col):
        return self._tooltips.get(col, "")

    def setForeground(self, col, brush):
        self._fg[col] = brush.color() if isinstance(brush, QBrush) else QColor(brush)
        self._changed(col)

    def setFont(self, col, font):
        self._bold[col] = font.bold()
        self._changed(col)

    def font(self, col):
        f = QFont()
        f.setBold(self._bold.get(col, False))
        return f

    def setBackground(self, *_a):
        pass

    def setIcon(self, *_a):
        pass

    def setSizeHint(self, *_a):
        pass

    def setTextAlignment(self, *_a):
        pass

    def setFirstColumnSpanned(self, *_a):
        pass

    def setChildIndicatorPolicy(self, *_a):
        pass

    def flags(self):
        return self._flags

    def setFlags(self, flags):
        self._flags = flags
        self._changed(0, emit=False)

    def setDisabled(self, on):
        if on:
            self._flags &= ~Qt.ItemFlag.ItemIsEnabled
        else:
            self._flags |= Qt.ItemFlag.ItemIsEnabled
        self._changed(0, emit=False)

    def isDisabled(self):
        return not int(self._flags) & int(Qt.ItemFlag.ItemIsEnabled)

    # ---- チェック ----
    def _auto_tristate(self) -> bool:
        return bool(int(self._flags) & int(Qt.ItemFlag.ItemIsAutoTristate)) and bool(self._children)

    def checkState(self, col=0):
        if self._auto_tristate():
            states = [c.checkState(col) for c in self._children]
            if all(s == Qt.CheckState.Checked for s in states):
                return Qt.CheckState.Checked
            if all(s == Qt.CheckState.Unchecked for s in states):
                return Qt.CheckState.Unchecked
            return Qt.CheckState.PartiallyChecked
        return self._check.get(col, Qt.CheckState.Unchecked)

    def _has_check(self, col=0) -> bool:
        return col in self._check or self._auto_tristate()

    def setCheckState(self, col, state):
        state = Qt.CheckState(int(state))
        if self._auto_tristate() and state != Qt.CheckState.PartiallyChecked:
            for c in self._children:
                c._set_check_quiet(col, state)
        self._check[col] = state
        tree = self.treeWidget()
        if tree is not None:
            tree._dirty()
            tree.itemChanged.emit(self, col)
            p = self._parent
            while p is not None and p._auto_tristate():
                tree.itemChanged.emit(p, col)
                p = p._parent

    def _set_check_quiet(self, col, state):
        if self._auto_tristate():
            for c in self._children:
                c._set_check_quiet(col, state)
        self._check[col] = state

    # ---- 子 ----
    def parent(self):
        return self._parent

    def child(self, i):
        return self._children[i] if 0 <= i < len(self._children) else None

    def childCount(self):
        return len(self._children)

    def indexOfChild(self, child):
        return self._children.index(child) if child in self._children else -1

    def addChild(self, child):
        self.insertChild(len(self._children), child)

    def addChildren(self, children):
        for c in children:
            c._parent = self
            c._tree = None
        self._children.extend(children)
        tree = self.treeWidget()
        if tree is not None:
            tree._dirty()

    def insertChild(self, index, child):
        child._parent = self
        child._tree = None
        self._children.insert(index, child)
        tree = self.treeWidget()
        if tree is not None:
            tree._dirty()

    def takeChild(self, index):
        if not 0 <= index < len(self._children):
            return None
        c = self._children.pop(index)
        tree = self.treeWidget()
        c._parent = None
        if tree is not None:
            tree._forget(c)
            tree._dirty()
        return c

    def takeChildren(self):
        tree = self.treeWidget()
        out, self._children = self._children, []
        for c in out:
            c._parent = None
            if tree is not None:
                tree._forget(c)
        if tree is not None:
            tree._dirty()
        return out

    def removeChild(self, child):
        if child in self._children:
            self.takeChild(self._children.index(child))

    def sortChildren(self, *_a):
        pass

    # ---- 開閉・隠す・選ぶ ----
    def isExpanded(self):
        return self._expanded

    def setExpanded(self, on):
        on = bool(on)
        if on == self._expanded:
            return
        self._expanded = on
        tree = self.treeWidget()
        if tree is not None:
            tree._dirty()
            (tree.itemExpanded if on else tree.itemCollapsed).emit(self)

    def isHidden(self):
        return self._hidden

    def setHidden(self, on):
        self._hidden = bool(on)
        tree = self.treeWidget()
        if tree is not None:
            if on and self in tree._selected:
                tree._selected.remove(self)
            tree._dirty()

    def isSelected(self):
        tree = self.treeWidget()
        return tree is not None and self in tree._selected

    def setSelected(self, on):
        tree = self.treeWidget()
        if tree is not None:
            tree._select_one(self, on)

    def columnCount(self):
        return len(self._texts)


class _Root(QTreeWidgetItem):
    def __init__(self, tree):
        super().__init__()
        self._tree = weakref.ref(tree)


class QHeaderView(QWidget):
    class ResizeMode(enum.IntEnum):
        Interactive = 0
        Stretch = 1
        Fixed = 2
        ResizeToContents = 3

    sectionClicked = pyqtSignal(int)
    sortIndicatorChanged = pyqtSignal(int, object)

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None, view=None):
        self._view_ref = weakref.ref(view) if view is not None else None
        super().__init__(None)

    def _owner(self):
        return self._view_ref() if self._view_ref else None

    def setVisible(self, on):
        view = self._owner()
        if view is not None:
            view._header_visible(self, bool(on))

    def hide(self):
        self.setVisible(False)

    def show(self):
        self.setVisible(True)

    def setSectionResizeMode(self, *args):
        pass

    def setStretchLastSection(self, on):
        view = self._owner()
        if view is not None:
            view._stretch_last = bool(on)
            view._dirty()

    def setDefaultSectionSize(self, *_a):
        pass

    def setMinimumSectionSize(self, *_a):
        pass

    def setSortIndicatorShown(self, *_a):
        pass

    def setSortIndicator(self, col, order):
        view = self._owner()
        if view is not None:
            view._sort = (col, order)
            view._dirty()

    def setHighlightSections(self, *_a):
        pass

    def setDefaultAlignment(self, *_a):
        pass

    def resizeSection(self, col, width):
        view = self._owner()
        if view is not None:
            view.setColumnWidth(col, width)

    def sectionSize(self, col):
        view = self._owner()
        return view.columnWidth(col) if view is not None else 0


class QTreeWidget(QAbstractItemView):
    itemChanged = pyqtSignal(object, int)
    itemClicked = pyqtSignal(object, int)
    itemDoubleClicked = pyqtSignal(object, int)
    itemActivated = pyqtSignal(object, int)
    itemPressed = pyqtSignal(object, int)
    itemExpanded = pyqtSignal(object)
    itemCollapsed = pyqtSignal(object)
    itemSelectionChanged = pyqtSignal()
    currentItemChanged = pyqtSignal(object, object)

    ROW_INDENT = 18

    def __init__(self, parent=None):
        self._root = None
        self._selected: list = []
        self._current = None
        self._anchor = None
        self._pending = False
        self._header_hidden = False
        self._header_labels: list = []
        self._columns = 1
        self._item_els: dict = {}
        super().__init__(parent)
        self._root = _Root(self)

    def _build(self):
        QAbstractItemView._build(self)
        self._el.classList.add("qtree")
        self._header = _dom.el("div", "qtree-header")
        self._el.insertBefore(self._header, self._view)
        self._rows = _dom.el("div", "qtree-rows")
        self._view.appendChild(self._rows)
        _dom.listen(self, self._rows, "mousedown", self._on_press)
        _dom.listen(self, self._rows, "click", self._on_click)
        _dom.listen(self, self._rows, "dblclick", self._on_dblclick)
        self._view.tabIndex = 0
        _dom.listen(self, self._view, "keydown", self._on_key)

    # ---- 列・見出し ----
    def setHeaderHidden(self, on):
        self._header_hidden = bool(on)
        self._render_header()

    def isHeaderHidden(self):
        return self._header_hidden

    def setHeaderLabels(self, labels):
        self._header_labels = list(labels)
        self._columns = max(1, len(labels))
        self._render_header()

    def setHeaderLabel(self, label):
        self.setHeaderLabels([label])

    def _render_header(self):
        self._header.innerHTML = ""
        self._header.style.display = "none" if self._header_hidden or not self._header_labels else ""
        for label in self._header_labels:
            self._header.appendChild(_dom.el("div", "qtree-head", label))

    def header(self):
        return QHeaderView(Qt.Orientation.Horizontal, None, self)

    def _header_visible(self, header, on):
        self.setHeaderHidden(not on)

    def setColumnCount(self, n):
        self._columns = max(1, int(n))

    def columnCount(self):
        return self._columns

    def setColumnWidth(self, *_a):
        pass

    def setIndentation(self, *_a):
        pass

    def setRootIsDecorated(self, *_a):
        pass

    def setAnimated(self, *_a):
        pass

    def setItemsExpandable(self, *_a):
        pass

    def setExpandsOnDoubleClick(self, *_a):
        pass

    def setAllColumnsShowFocus(self, *_a):
        pass

    def setSortingEnabled(self, *_a):
        pass

    def resizeColumnToContents(self, *_a):
        pass

    # ---- 項目 ----
    def invisibleRootItem(self):
        return self._root

    def topLevelItemCount(self):
        return len(self._root._children)

    def topLevelItem(self, i):
        return self._root.child(i)

    def addTopLevelItem(self, item):
        self._root.addChild(item)

    def addTopLevelItems(self, items):
        self._root.addChildren(list(items))

    def insertTopLevelItem(self, index, item):
        self._root.insertChild(index, item)

    def takeTopLevelItem(self, index):
        return self._root.takeChild(index)

    def indexOfTopLevelItem(self, item):
        return self._root.indexOfChild(item)

    def clear(self):
        had = bool(self._selected)
        prev = self._current
        self._root.takeChildren()
        self._selected = []
        self._current = None
        self._anchor = None
        self._item_els = {}
        self._dirty()
        if prev is not None:
            self.currentItemChanged.emit(None, prev)
        if had:
            self.itemSelectionChanged.emit()

    def _forget(self, item):
        if item in self._selected:
            self._selected.remove(item)
        if item is self._current:
            self._current = None
        for c in item._children:
            self._forget(c)

    # ---- 選ぶ ----
    def selectedItems(self):
        order = {id(it): k for k, it in enumerate(self._visible_items())}
        return sorted(self._selected, key=lambda it: order.get(id(it), 1 << 30))

    def clearSelection(self):
        if self._selected:
            self._selected = []
            self._dirty()
            self.itemSelectionChanged.emit()

    def currentItem(self):
        return self._current

    def setCurrentItem(self, item, *_a):
        self._set_selection([item] if item is not None else [], item)

    def _select_one(self, item, on):
        if on and item not in self._selected:
            if self.selectionMode() in (QAbstractItemView.SelectionMode.SingleSelection,):
                self._selected = []
            self._selected.append(item)
        elif not on and item in self._selected:
            self._selected.remove(item)
        else:
            return
        self._dirty()
        self.itemSelectionChanged.emit()

    def _set_selection(self, items, current=None):
        prev = self._current
        changed = [id(x) for x in items] != [id(x) for x in self._selected]
        self._selected = list(items)
        if current is not None:
            self._current = current
        self._dirty()
        if changed:
            self.itemSelectionChanged.emit()
        if self._current is not prev:
            self.currentItemChanged.emit(self._current, prev)

    def expandAll(self):
        def walk(it):
            for c in it._children:
                if c._children:
                    c.setExpanded(True)
                walk(c)
        walk(self._root)

    def collapseAll(self):
        def walk(it):
            for c in it._children:
                c.setExpanded(False)
                walk(c)
        walk(self._root)

    def expandItem(self, item):
        item.setExpanded(True)

    def collapseItem(self, item):
        item.setExpanded(False)

    def scrollToItem(self, item, *_a):
        e = self._item_els.get(id(item))
        if e is not None:
            e.scrollIntoView(_block_nearest())

    def itemAt(self, *args):
        p = args[0] if len(args) == 1 else QPoint(*args)
        x, y, _, _ = _dom.rect(self._view)
        target = _dom.document.elementFromPoint(x + p.x(), y + p.y())
        return self._item_from_el(target)

    # ---- 描く ----
    def _dirty(self):
        if self._pending:
            return
        self._pending = True
        _dom.later(0, self._render)

    def _visible_items(self):
        out = []

        def walk(it):
            for c in it._children:
                if c._hidden:
                    continue
                out.append(c)
                if c._expanded:
                    walk(c)
        walk(self._root)
        return out

    def _render(self):
        self._pending = False
        if self._deleted:
            return
        scroll = self._view.scrollTop
        frag = _dom.document.createDocumentFragment()
        self._item_els = {}
        self._el_items = {}
        selected = {id(x) for x in self._selected}

        def depth(it):
            d, p = 0, it._parent
            while p is not None and not isinstance(p, _Root):
                d, p = d + 1, p._parent
            return d

        for it in self._visible_items():
            row = _dom.el("div", "q-item qtree-row")
            row.style.paddingLeft = f"{4 + depth(it) * self.ROW_INDENT}px"
            arrow = _dom.el("span", "qtree-arrow", ("▾" if it._expanded else "▸") if it._children else "")
            row.appendChild(arrow)
            if it._has_check(0) and int(it._flags) & int(Qt.ItemFlag.ItemIsUserCheckable):
                box = _dom.el("input")
                box.type = "checkbox"
                state = it.checkState(0)
                box.checked = state == Qt.CheckState.Checked
                box.indeterminate = state == Qt.CheckState.PartiallyChecked
                box.className = "qtree-check"
                fg = it._fg.get(0)
                if fg is not None and fg.name().lower() == "#9e9e9e":
                    box.style.filter = "grayscale(1)"   # 灰色の項目（地図に関係しないもの）はチェックも灰色に薄く（凡例と同じ）
                    box.style.opacity = "0.4"
                row.appendChild(box)
            for col in range(max(1, self._columns)):
                cell = _dom.el("span", "qtree-text", it.text(col))
                if it._fg.get(col):
                    cell.style.color = it._fg[col].name()
                if it._bold.get(col):
                    cell.style.fontWeight = "bold"
                row.appendChild(cell)
            tip = it._tooltips.get(0)
            if tip:
                row.title = tip
            if id(it) in selected:
                row.classList.add("selected")
            if it is self._current:
                row.classList.add("current")
            if not int(it._flags) & int(Qt.ItemFlag.ItemIsEnabled):
                row.classList.add("disabled")
            n = len(self._el_items)
            row.dataset.row = str(n)
            self._el_items[n] = it
            self._item_els[id(it)] = row
            frag.appendChild(row)
        self._rows.innerHTML = ""
        self._rows.appendChild(frag)
        self._view.scrollTop = scroll

    def _item_from_el(self, e):
        while not _dom.absent(e) and e != self._rows:
            try:
                r = e.dataset.row
            except Exception:  # noqa: BLE001
                r = None
            if r:
                return self._el_items.get(int(r))
            e = e.parentElement
        return None

    def _on_press(self, ev, *_a):
        it = self._item_from_el(ev.target)
        if it is None:
            return
        cls = str(ev.target.className or "")
        if "qtree-arrow" in cls:
            if it._children:
                it.setExpanded(not it._expanded)
            ev.preventDefault()
            return
        if "qtree-check" in cls:
            return
        if not int(it._flags) & int(Qt.ItemFlag.ItemIsEnabled):
            return
        self.itemPressed.emit(it, 0)
        mode = self.selectionMode()
        if mode == QAbstractItemView.SelectionMode.NoSelection:
            return
        if self._extend_mode() and ev.shiftKey and self._anchor is not None:
            items = self._visible_items()
            try:
                a, b = items.index(self._anchor), items.index(it)
            except ValueError:
                a = b = items.index(it) if it in items else 0
            lo, hi = min(a, b), max(a, b)
            self._set_selection(items[lo:hi + 1], it)
        elif self._extend_mode() and (ev.metaKey or ev.ctrlKey):
            sel = list(self._selected)
            if it in sel:
                sel.remove(it)
            else:
                sel.append(it)
            self._anchor = it
            self._set_selection(sel, it)
        else:
            self._anchor = it
            self._set_selection([it], it)

    def _on_click(self, ev, *_a):
        it = self._item_from_el(ev.target)
        if it is None:
            return
        cls = str(ev.target.className or "")
        if "qtree-check" in cls:
            ev.stopPropagation()
            state = it.checkState(0)
            it.setCheckState(0, Qt.CheckState.Unchecked if state == Qt.CheckState.Checked else Qt.CheckState.Checked)
            return
        if "qtree-arrow" in cls:
            return
        if int(it._flags) & int(Qt.ItemFlag.ItemIsEnabled):
            self.itemClicked.emit(it, 0)

    def _on_dblclick(self, ev, *_a):
        it = self._item_from_el(ev.target)
        if it is None or "qtree-arrow" in str(ev.target.className or ""):
            return
        self.itemDoubleClicked.emit(it, 0)
        self.itemActivated.emit(it, 0)
        if it._children:
            it.setExpanded(not it._expanded)

    def _on_key(self, ev):
        key = str(ev.key)
        items = self._visible_items()
        if not items or key not in ("ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"):
            return
        ev.preventDefault()
        cur = self._current if self._current in items else None
        if key in ("ArrowLeft", "ArrowRight") and cur is not None:
            if cur._children:
                cur.setExpanded(key == "ArrowRight")
            return
        i = items.index(cur) if cur is not None else -1
        i = min(len(items) - 1, i + 1) if key == "ArrowDown" else max(0, i - 1)
        self._anchor = items[i]
        self._set_selection([items[i]], items[i])
        _dom.later(0, lambda: self.scrollToItem(items[i]))


# ================= 表 =================
class QTableView(QAbstractItemView):
    """大きな表（数万行）を、見えている行だけ描いて表示する。"""
    clicked = pyqtSignal(object)
    doubleClicked = pyqtSignal(object)
    pressed = pyqtSignal(object)
    activated = pyqtSignal(object)

    ROW_HEIGHT = 22

    def __init__(self, parent=None):
        self._model = None
        self._selection = None
        self._widths: dict = {}
        self._stretch_last = False
        self._sorting = False
        self._sort = None
        self._anchor = -1
        self._pending = False
        self._show_header = True
        super().__init__(parent)
        self._hheader = QHeaderView(Qt.Orientation.Horizontal, None, self)
        self._vheader = _VHeader()

    def _build(self):
        QAbstractItemView._build(self)
        self._el.classList.add("qtable")
        self._head = _dom.el("div", "qtable-head")
        self._el.insertBefore(self._head, self._view)
        self._spacer = _dom.el("div", "qtable-spacer")
        self._body = _dom.el("div", "qtable-body")
        self._view.appendChild(self._spacer)
        self._spacer.appendChild(self._body)
        self._view.tabIndex = 0
        _dom.listen(self, self._view, "scroll", lambda ev: (self._sync_head_scroll(), self._render_rows()))
        _dom.listen(self, self._body, "mousedown", self._on_press)
        _dom.listen(self, self._body, "dblclick", self._on_dblclick)
        _dom.listen(self, self._view, "keydown", self._on_key)
        from ._widgets import _watch_resize
        _watch_resize(self)

    def resizeEvent(self, event):
        self._render_rows()

    # ---- モデル ----
    def setModel(self, model):
        self._model = model
        self._selection = QItemSelectionModel(model)
        model.modelReset.connect(self._on_reset)
        model.layoutChanged.connect(self._on_layout)
        model.rowsInserted.connect(lambda *_a: self._dirty())
        model.rowsRemoved.connect(lambda *_a: self._on_layout())
        model.dataChanged.connect(lambda *_a: self._dirty())
        self._dirty()

    def model(self):
        return self._model

    def selectionModel(self):
        return self._selection

    def _on_reset(self):
        self._selection._rows = set()
        self._selection._current = -1
        self._anchor = -1
        self._dirty()

    def _on_layout(self):
        # 並べ替え・絞り込みで行の並びが変わったら、選択は外す（説明欄はそのまま）
        if self._selection._rows:
            self._selection._rows = set()
            self._selection._current = -1
        self._dirty()

    # ---- 見出し ----
    def horizontalHeader(self):
        return self._hheader

    def verticalHeader(self):
        return self._vheader

    def _header_visible(self, header, on):
        self._show_header = on
        self._head.style.display = "" if on else "none"

    def setSortingEnabled(self, on):
        self._sorting = bool(on)

    def sortByColumn(self, col, order=Qt.SortOrder.AscendingOrder):
        self._sort = (col, order)
        if self._model is not None:
            self._model.sort(col, order)
        self._dirty()

    def setColumnWidth(self, col, width):
        self._widths[col] = int(width)
        self._dirty()

    def columnWidth(self, col):
        return self._widths.get(col, 100)

    def resizeColumnsToContents(self):
        m = self._model
        if m is None:
            return
        fm = QFontMetrics()
        rows = min(m.rowCount(), 300)
        for c in range(m.columnCount()):
            head = m.headerData(c, Qt.Orientation.Horizontal, ROLE.DisplayRole) or ""
            width = fm.horizontalAdvance(str(head)) + 28
            for r in range(rows):
                text = m.data(m.index(r, c), ROLE.DisplayRole)
                if text:
                    width = max(width, fm.horizontalAdvance(str(text)[:80]) + 14)
            self._widths[c] = min(width, 2000)
        self._dirty()

    def resizeRowsToContents(self):
        pass

    def setShowGrid(self, *_a):
        pass

    def setWordWrap(self, *_a):
        pass

    def setCornerButtonEnabled(self, *_a):
        pass

    # ---- 選ぶ ----
    def currentIndex(self):
        return self._selection.currentIndex() if self._selection else QModelIndex()

    def setCurrentIndex(self, index):
        if index.isValid():
            self._anchor = index.row()
            self._selection._set({index.row()}, index.row())
            self._dirty()

    def selectRow(self, row):
        self._anchor = row
        self._selection._set({row}, row)
        self._dirty()

    def clearSelection(self):
        if self._selection is not None and self._selection._rows:
            self._selection.clearSelection()
            self._dirty()

    def indexAt(self, p):
        if self._model is None:
            return QModelIndex()
        row = int((p.y() + self._view.scrollTop) // self.ROW_HEIGHT)
        if 0 <= row < self._model.rowCount():
            return self._model.index(row, 0)
        return QModelIndex()

    def scrollTo(self, index, *_a):
        if index.isValid():
            top = index.row() * self.ROW_HEIGHT
            v = self._view
            if top < v.scrollTop:
                v.scrollTop = top
            elif top + self.ROW_HEIGHT > v.scrollTop + v.clientHeight:
                v.scrollTop = top + self.ROW_HEIGHT - v.clientHeight

    def _row_at(self, ev) -> int:
        x, y, _, _ = _dom.rect(self._view)
        return int((ev.clientY - y + self._view.scrollTop) // self.ROW_HEIGHT)

    def _on_press(self, ev):
        if self._model is None:
            return
        row = self._row_at(ev)
        if not 0 <= row < self._model.rowCount():
            return
        if ev.button == 2 and row in self._selection._rows:
            return   # 右クリックは選んだ行の上ならそのまま（メニューで選んだ行に使う）
        rows = set(self._selection._rows)
        if self._extend_mode() and ev.shiftKey and self._anchor >= 0:
            lo, hi = min(self._anchor, row), max(self._anchor, row)
            rows = set(range(lo, hi + 1))
        elif self._extend_mode() and (ev.metaKey or ev.ctrlKey):
            rows ^= {row}
            self._anchor = row
        else:
            rows = {row}
            self._anchor = row
        self._selection._set(rows, row)
        self._dirty()
        index = self._model.index(row, 0)
        self.pressed.emit(index)
        self.clicked.emit(index)

    def _before_context(self, ev):
        self._on_press(ev)

    def _on_dblclick(self, ev):
        if self._model is None:
            return
        row = self._row_at(ev)
        if 0 <= row < self._model.rowCount():
            self.doubleClicked.emit(self._model.index(row, 0))

    def _on_key(self, ev):
        key = str(ev.key)
        if self._model is None or key not in ("ArrowDown", "ArrowUp"):
            return
        ev.preventDefault()
        n = self._model.rowCount()
        if not n:
            return
        cur = self._selection._current
        row = min(n - 1, cur + 1) if key == "ArrowDown" else max(0, cur - 1 if cur >= 0 else 0)
        self._anchor = row
        self._selection._set({row}, row)
        self.scrollTo(self._model.index(row, 0))
        self._dirty()

    # ---- 描く ----
    def _dirty(self):
        if self._pending:
            return
        self._pending = True
        _dom.later(0, self._render)

    def _col_widths(self):
        m = self._model
        cols = m.columnCount() if m else 0
        widths = [self._widths.get(c, 100) for c in range(cols)]
        if self._stretch_last and cols:
            avail = int(self._view.clientWidth) - sum(widths[:-1])
            widths[-1] = max(widths[-1], avail)
        return widths

    def _render(self):
        self._pending = False
        if self._deleted or self._model is None:
            return
        m = self._model
        widths = self._col_widths()
        total = sum(widths)
        # 見出し
        self._head.innerHTML = ""
        inner = _dom.el("div", "qtable-head-inner")
        inner.style.width = f"{total}px"
        for c, w in enumerate(widths):
            text = m.headerData(c, Qt.Orientation.Horizontal, ROLE.DisplayRole) or ""
            cell = _dom.el("div", "qtable-hcell", str(text))
            cell.style.width = f"{w}px"
            if self._sort and self._sort[0] == c:
                cell.appendChild(_dom.el("span", "qtable-sort",
                                         " ▲" if self._sort[1] == Qt.SortOrder.AscendingOrder else " ▼"))
            if self._sorting:
                _dom.listen(self, cell, "click", lambda ev, c=c: self._on_head_click(ev, c))
            grip = _dom.el("div", "qtable-grip")
            _dom.listen(self, grip, "mousedown", lambda ev, c=c: self._start_resize(ev, c))
            cell.appendChild(grip)
            inner.appendChild(cell)
        self._head.appendChild(inner)
        self._spacer.style.height = f"{m.rowCount() * self.ROW_HEIGHT}px"
        self._spacer.style.width = f"{total}px"
        self._render_rows(force=True)

    def _sync_head_scroll(self):
        self._head.scrollLeft = self._view.scrollLeft

    def _on_head_click(self, ev, col):
        if "qtable-grip" in str(ev.target.className or ""):
            return
        order = Qt.SortOrder.AscendingOrder
        if self._sort and self._sort[0] == col and self._sort[1] == Qt.SortOrder.AscendingOrder:
            order = Qt.SortOrder.DescendingOrder
        self.sortByColumn(col, order)

    def _start_resize(self, ev, col):
        ev.stopPropagation()
        ev.preventDefault()
        start, width = ev.clientX, self._col_widths()[col]
        state = {"on": True}

        def move(e):
            if state["on"]:
                self._widths[col] = max(30, int(width + e.clientX - start))
                self._dirty()

        def up(e):
            state["on"] = False
            _dom.document.removeEventListener("mousemove", proxy_move)
            _dom.document.removeEventListener("mouseup", proxy_up)

        from pyodide.ffi import create_proxy
        proxy_move = create_proxy(move)
        proxy_up = create_proxy(up)
        _dom.document.addEventListener("mousemove", proxy_move)
        _dom.document.addEventListener("mouseup", proxy_up)

    def _render_rows(self, force=False):
        m = self._model
        if m is None:
            return
        n = m.rowCount()
        h = self.ROW_HEIGHT
        top = int(self._view.scrollTop)
        height = int(self._view.clientHeight) or 600
        first = max(0, top // h - 5)
        last = min(n, (top + height) // h + 6)
        key = (first, last, n)
        if not force and key == getattr(self, "_rendered", None):
            return
        self._rendered = key
        widths = self._col_widths()
        sel = self._selection._rows if self._selection else set()
        frag = _dom.document.createDocumentFragment()
        cols = len(widths)
        for r in range(first, last):
            row = _dom.el("div", "qtable-row" + (" odd" if r % 2 else "") + (" selected" if r in sel else ""))
            row.style.top = f"{r * h}px"
            for c in range(cols):
                index = m.index(r, c)
                text = m.data(index, ROLE.DisplayRole)
                cell = _dom.el("div", "qtable-cell", "" if text is None else str(text))
                cell.style.width = f"{widths[c]}px"
                bg = m.data(index, ROLE.BackgroundRole)
                if bg is not None:
                    color = bg.color() if isinstance(bg, QBrush) else QColor(bg)
                    cell.style.background = color.name()
                if text and len(str(text)) > 20:
                    cell.title = str(text)[:2000]
                row.appendChild(cell)
            frag.appendChild(row)
        self._body.innerHTML = ""
        self._body.appendChild(frag)


class _VHeader:
    def setVisible(self, *_a):
        pass

    def hide(self):
        pass

    def setDefaultSectionSize(self, *_a):
        pass

    def setSectionResizeMode(self, *_a):
        pass


class QListView(QAbstractItemView):
    pass


class QTreeView(QAbstractItemView):
    pass


# ================= 入力の候補 =================
class QCompleter(QObject):
    """入力欄の下に出す候補の一覧（打った文字を含む名前）。"""
    activated = pyqtSignal(str)
    highlighted = pyqtSignal(str)

    class CompletionMode(enum.IntEnum):
        PopupCompletion = 0
        UnfilteredPopupCompletion = 1
        InlineCompletion = 2

    def __init__(self, strings=None, parent=None):
        if isinstance(strings, QObject) and parent is None:
            strings, parent = None, strings
        super().__init__(parent)
        self._model = QStringListModel(strings or [])
        self._edit = None
        self._prefix = ""
        self._contains = False
        self._case = Qt.CaseSensitivity.CaseInsensitive
        self._max_visible = 7
        self._popup = _CompleterPopup(self)

    def model(self):
        return self._model

    def setModel(self, model):
        self._model = model

    def setCaseSensitivity(self, cs):
        self._case = cs

    def setFilterMode(self, mode):
        self._contains = bool(int(mode) & int(Qt.MatchFlag.MatchContains))

    def setMaxVisibleItems(self, n):
        self._max_visible = int(n)

    def setCompletionMode(self, *_a):
        pass

    def setModelSorting(self, *_a):
        pass

    def setWidget(self, w):
        self._attach(w)

    def widget(self):
        return self._edit() if self._edit else None

    def _attach(self, edit):
        self._edit = weakref.ref(edit)

    def popup(self):
        return self._popup

    def setCompletionPrefix(self, prefix):
        self._prefix = prefix or ""

    def completionPrefix(self):
        return self._prefix

    def _matches(self):
        key = self._prefix if self._case == Qt.CaseSensitivity.CaseSensitive else self._prefix.lower()
        out = []
        for s in self._model.stringList():
            t = s if self._case == Qt.CaseSensitivity.CaseSensitive else s.lower()
            if (key in t) if self._contains else t.startswith(key):
                out.append(s)
        return out

    def complete(self, *_a):
        edit = self.widget()
        if edit is None:
            return
        matches = self._matches()
        if not matches:
            self._popup.hide()
            return
        self._popup.open(edit, matches)

    def _on_typed(self, text):
        self._prefix = text
        if text:
            self.complete()
        else:
            self._popup.hide()

    def _handle_key(self, ev) -> bool:
        return self._popup._handle_key(ev)

    def _choose(self, text):
        edit = self.widget()
        self._popup.hide()
        if edit is not None:
            edit._el.value = text
            edit.textChanged.emit(text)
        self.activated.emit(text)


class _CompleterPopup:
    def __init__(self, completer):
        self._c = weakref.ref(completer)
        self._el = None
        self._items: list = []
        self._index = -1
        self._outside = None

    def isVisible(self):
        return self._el is not None

    def hide(self):
        if self._el is not None:
            self._el.remove()
            self._el = None
        if self._outside is not None:
            _dom.document.removeEventListener("mousedown", self._outside, True)
            self._outside.destroy()
            self._outside = None

    def rect(self):
        from ._core import QRect
        if self._el is None:
            return QRect()
        _, _, w, h = _dom.rect(self._el)
        return QRect(0, 0, round(w), round(h))

    def mapFromGlobal(self, p):
        if self._el is None:
            return p
        x, y, _, _ = _dom.rect(self._el)
        return QPoint(round(p.x() - x), round(p.y() - y))

    def window(self):
        return self

    def parentWidget(self):
        return None

    def open(self, edit, matches):
        from pyodide.ffi import create_proxy
        c = self._c()
        if self._el is None:
            self._el = _dom.el("div", "qpopup-list")
            _dom.document.body.appendChild(self._el)

            def outside(ev):
                if self._el is not None and not self._el.contains(ev.target) and ev.target != edit._el:
                    self.hide()

            self._outside = create_proxy(outside)
            _dom.document.addEventListener("mousedown", self._outside, True)
        x, y, w, h = _dom.rect(edit._el)
        self._el.style.left = f"{x}px"
        self._el.style.top = f"{y + h + 1}px"
        self._el.style.minWidth = f"{max(w, 140)}px"
        self._el.style.maxHeight = f"{(c._max_visible if c else 10) * 22 + 4}px"
        self._el.style.zIndex = "100000"
        self._el.innerHTML = ""
        self._items = list(matches[:2000])
        self._index = -1
        for k, text in enumerate(self._items):
            row = _dom.el("div", "q-item", text)
            row.dataset.k = str(k)
            self._el.appendChild(row)
        if not getattr(self, "_wired", False):
            pass
        self._el.onmousedown = create_proxy(self._on_mouse)

    def _on_mouse(self, ev):
        ev.preventDefault()
        t = ev.target
        k = getattr(t.dataset, "k", None) if hasattr(t, "dataset") else None
        if _dom.absent(k):
            return
        c = self._c()
        if c is not None:
            c._choose(self._items[int(k)])

    def _highlight(self, k):
        if self._el is None:
            return
        rows = self._el.children
        for i in range(rows.length):
            rows.item(i).classList.toggle("selected", i == k)
        if 0 <= k < rows.length:
            rows.item(k).scrollIntoView(_block_nearest())
        self._index = k

    def _handle_key(self, ev) -> bool:
        if self._el is None:
            return False
        key = str(ev.key)
        if key == "ArrowDown":
            self._highlight(min(len(self._items) - 1, self._index + 1))
            return True
        if key == "ArrowUp":
            self._highlight(max(0, self._index - 1))
            return True
        if key == "Escape":
            self.hide()
            return True
        if key == "Enter" and 0 <= self._index < len(self._items):
            c = self._c()
            if c is not None:
                c._choose(self._items[self._index])
            return True
        if key == "Enter":
            self.hide()
        return False
