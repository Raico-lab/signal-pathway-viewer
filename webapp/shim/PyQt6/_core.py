"""Qt の土台: 列挙・QObject・シグナル・タイマー・イベント・座標・URL・項目のモデル。"""
import enum
import inspect
import weakref

from . import _dom


# ================= 列挙 =================
class _Flag(enum.IntFlag):
    pass


class Qt:
    class Orientation(_Flag):
        Horizontal = 1
        Vertical = 2

    class ItemDataRole(enum.IntEnum):
        DisplayRole = 0
        DecorationRole = 1
        EditRole = 2
        ToolTipRole = 3
        StatusTipRole = 4
        WhatsThisRole = 5
        FontRole = 6
        TextAlignmentRole = 7
        BackgroundRole = 8
        ForegroundRole = 9
        CheckStateRole = 10
        SizeHintRole = 13
        UserRole = 256

    class CheckState(enum.IntEnum):
        Unchecked = 0
        PartiallyChecked = 1
        Checked = 2

    class ItemFlag(_Flag):
        NoItemFlags = 0
        ItemIsSelectable = 1
        ItemIsEditable = 2
        ItemIsDragEnabled = 4
        ItemIsDropEnabled = 8
        ItemIsUserCheckable = 16
        ItemIsEnabled = 32
        ItemIsAutoTristate = 64
        ItemNeverHasChildren = 128
        ItemIsUserTristate = 256

    class SortOrder(enum.IntEnum):
        AscendingOrder = 0
        DescendingOrder = 1

    class FocusPolicy(enum.IntEnum):
        NoFocus = 0
        TabFocus = 1
        ClickFocus = 2
        StrongFocus = 11
        WheelFocus = 15

    class ScrollBarPolicy(enum.IntEnum):
        ScrollBarAsNeeded = 0
        ScrollBarAlwaysOff = 1
        ScrollBarAlwaysOn = 2

    class ArrowType(enum.IntEnum):
        NoArrow = 0
        UpArrow = 1
        DownArrow = 2
        LeftArrow = 3
        RightArrow = 4

    class ToolButtonStyle(enum.IntEnum):
        ToolButtonIconOnly = 0
        ToolButtonTextOnly = 1
        ToolButtonTextBesideIcon = 2
        ToolButtonTextUnderIcon = 3

    class CaseSensitivity(enum.IntEnum):
        CaseInsensitive = 0
        CaseSensitive = 1

    class MatchFlag(_Flag):
        MatchExactly = 0
        MatchContains = 1
        MatchStartsWith = 2

    class TextInteractionFlag(_Flag):
        NoTextInteraction = 0
        TextSelectableByMouse = 1
        TextSelectableByKeyboard = 2
        LinksAccessibleByMouse = 4

    class TextElideMode(enum.IntEnum):
        ElideLeft = 0
        ElideRight = 1
        ElideMiddle = 2
        ElideNone = 3

    class MouseButton(_Flag):
        NoButton = 0
        LeftButton = 1
        RightButton = 2
        MiddleButton = 4

    class KeyboardModifier(_Flag):
        NoModifier = 0
        ShiftModifier = 0x02000000
        ControlModifier = 0x04000000
        AltModifier = 0x08000000
        MetaModifier = 0x10000000

    class Key(enum.IntEnum):
        Key_Escape = 0x01000000
        Key_Tab = 0x01000001
        Key_Backspace = 0x01000003
        Key_Return = 0x01000004
        Key_Enter = 0x01000005
        Key_Delete = 0x01000007
        Key_Left = 0x01000012
        Key_Up = 0x01000013
        Key_Right = 0x01000014
        Key_Down = 0x01000015
        Key_PageUp = 0x01000016
        Key_PageDown = 0x01000017
        Key_Other = 0

    class WindowType(_Flag):
        Widget = 0
        Window = 1
        Dialog = 3
        Popup = 9
        Tool = 11

    class WidgetAttribute(enum.IntEnum):
        WA_DeleteOnClose = 55
        WA_WState_Hidden = 16

    class CursorShape(enum.IntEnum):
        ArrowCursor = 0
        WaitCursor = 3
        BusyCursor = 16

    class ContextMenuPolicy(enum.IntEnum):
        NoContextMenu = 0
        DefaultContextMenu = 1
        ActionsContextMenu = 2
        CustomContextMenu = 3

    class ColorScheme(enum.IntEnum):
        Unknown = 0
        Light = 1
        Dark = 2

    class ShortcutContext(enum.IntEnum):
        WidgetShortcut = 0
        WindowShortcut = 1
        ApplicationShortcut = 2
        WidgetWithChildrenShortcut = 3

    class AlignmentFlag(_Flag):
        AlignLeft = 0x1
        AlignRight = 0x2
        AlignHCenter = 0x4
        AlignTop = 0x20
        AlignBottom = 0x40
        AlignVCenter = 0x80
        AlignCenter = 0x84

    class ApplicationState(enum.IntEnum):
        ApplicationSuspended = 0
        ApplicationHidden = 1
        ApplicationInactive = 2
        ApplicationActive = 4


# ================= シグナル =================
_arity_cache: dict = {}


def _max_args(slot) -> int | None:
    """slot が受け取れる位置引数の最大数（いくつでもなら None）。PyQt と同じく、多すぎる引数は後ろから捨てて渡す。"""
    key = getattr(slot, "__func__", slot)
    try:
        return _arity_cache[key]
    except (KeyError, TypeError):
        pass
    try:
        params = inspect.signature(slot).parameters.values()
    except (TypeError, ValueError):
        result = None
    else:
        if any(p.kind == p.VAR_POSITIONAL for p in params):
            result = None
        else:
            result = sum(p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) for p in params)
    try:
        _arity_cache[key] = result
    except TypeError:
        pass
    return result


class BoundSignal:
    __slots__ = ("_owner", "_slots", "_name")

    def __init__(self, owner, name):
        self._owner = weakref.ref(owner) if owner is not None else None
        self._slots: list = []
        self._name = name

    def connect(self, slot):
        if isinstance(slot, BoundSignal):
            target = slot
            slot = lambda *a: target.emit(*a)   # noqa: E731 - シグナルどうしをつなぐ
        self._slots.append(slot)
        return slot

    def disconnect(self, slot=None):
        if slot is None:
            self._slots.clear()
        else:
            try:
                self._slots.remove(slot)
            except ValueError:
                raise TypeError("disconnect() failed between signal and slot") from None

    def emit(self, *args):
        owner = self._owner() if self._owner is not None else None
        if owner is not None and (getattr(owner, "_blocked", False) or getattr(owner, "_deleted", False)):
            return
        for slot in list(self._slots):
            n = _max_args(slot)
            call_args = args if n is None or n >= len(args) else args[:n]
            slot(*call_args)

    def __getitem__(self, _types):
        return self   # activated[str] など（型ごとの区別はしない）

    def __call__(self, *args):
        self.emit(*args)


class pyqtSignal:
    def __init__(self, *types, name=None, **_kw):
        self.types = types
        self.name = name

    def __set_name__(self, owner, name):
        if self.name is None:
            self.name = name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        key = "_sig_" + self.name
        bound = obj.__dict__.get(key)
        if bound is None:
            bound = obj.__dict__[key] = BoundSignal(obj, self.name)
        return bound


def pyqtSlot(*_types, **_kw):
    return lambda fn: fn


def pyqtProperty(*_a, **_kw):
    return property


# ================= QObject =================
class QObject:
    destroyed = pyqtSignal(object)

    def __init__(self, parent=None, *_args, **_kw):
        d = self.__dict__
        d.setdefault("_parent", None)
        d.setdefault("_children", [])
        d.setdefault("_filters", [])
        d.setdefault("_blocked", False)
        d.setdefault("_deleted", False)
        d.setdefault("_props", {})
        d.setdefault("_object_name", "")
        if parent is not None:
            QObject.setParent(self, parent)

    # ---- 親子 ----
    def setParent(self, parent, *_flags):
        old = self.__dict__.get("_parent")
        if old is not None and self in old._children:
            old._children.remove(self)
        self._parent = parent
        if parent is not None:
            parent.__dict__.setdefault("_children", []).append(self)

    def parent(self):
        return self.__dict__.get("_parent")

    def children(self):
        return list(self._children)

    def deleteLater(self):
        _dom.later(0, self._destroy)

    def _destroy(self):
        if self._deleted:
            return
        for child in list(self._children):
            child._destroy()
        self._on_destroy()
        self._deleted = True
        _dom.release(self)
        # destroyed は消えた後でも届ける（シグナルを止める印より前に、つないだ先を直接呼ぶ）
        sig = self.__dict__.get("_sig_destroyed")
        for s in list(sig._slots) if sig is not None else ():
            try:
                n = _max_args(s)
                s(*((self,)[:n] if n is not None else (self,)))
            except Exception:  # noqa: BLE001
                _dom.report()
        parent = self.__dict__.get("_parent")
        if parent is not None and self in parent._children:
            parent._children.remove(self)

    def _on_destroy(self):
        """消すときの後始末（部品は HTML の要素を外す）。"""

    # ---- シグナル ----
    def blockSignals(self, block: bool) -> bool:
        old = self._blocked
        self._blocked = bool(block)
        return old

    def signalsBlocked(self) -> bool:
        return self._blocked

    # ---- イベントの横取り ----
    def installEventFilter(self, filt):
        if filt not in self._filters:
            self._filters.append(filt)

    def removeEventFilter(self, filt):
        if filt in self._filters:
            self._filters.remove(filt)

    def eventFilter(self, obj, event) -> bool:
        return False

    def event(self, event) -> bool:
        return False

    # ---- 名前・属性 ----
    def setObjectName(self, name: str):
        self._object_name = name

    def objectName(self) -> str:
        return self._object_name

    def setProperty(self, name: str, value):
        self._props[name] = value
        return True

    def property(self, name: str):
        return self._props.get(name)

    def thread(self):
        return None


def _filtered(obj, event) -> bool:
    """obj に入れたイベントフィルタに event を見せる（どれかが True を返したら、そこで止める）。"""
    for f in list(obj.__dict__.get("_filters", ())):
        if getattr(f, "_deleted", False):
            continue
        try:
            if f.eventFilter(obj, event):
                return True
        except Exception:  # noqa: BLE001
            _dom.report()
    return False


# ================= タイマー =================
class QTimer(QObject):
    timeout = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._interval = 0
        self._single = False
        self._handle = None
        self._gen = 0

    def setSingleShot(self, on: bool):
        self._single = bool(on)

    def isSingleShot(self) -> bool:
        return self._single

    def setInterval(self, ms: int):
        self._interval = int(ms)

    def interval(self) -> int:
        return self._interval

    def isActive(self) -> bool:
        return self._handle is not None

    def start(self, ms: int | None = None):
        if ms is not None:
            self._interval = int(ms)
        self.stop()
        self._gen += 1
        gen = self._gen

        def fire():
            if gen != self._gen or self._deleted:
                return
            self._handle = None
            if not self._single:
                self.start()
            self.timeout.emit()

        self._handle = _dom.later(self._interval, fire)

    def stop(self):
        if self._handle is not None:
            _dom.window.clearTimeout(self._handle)
            self._handle = None
        self._gen += 1

    @staticmethod
    def singleShot(ms, *args):
        fn = args[-1]
        if isinstance(fn, BoundSignal):
            sig = fn
            fn = sig.emit
        _dom.later(ms, fn)


# ================= 座標 =================
class QPoint:
    def __init__(self, x=0, y=0):
        self._x, self._y = int(x), int(y)

    def x(self):
        return self._x

    def y(self):
        return self._y

    def toPoint(self):
        return QPoint(self._x, self._y)

    def __add__(self, other):
        return QPoint(self._x + other.x(), self._y + other.y())

    def __sub__(self, other):
        return QPoint(self._x - other.x(), self._y - other.y())

    def __repr__(self):
        return f"QPoint({self._x}, {self._y})"


class QPointF(QPoint):
    def __init__(self, x=0.0, y=0.0):
        if isinstance(x, QPoint):
            x, y = x.x(), x.y()
        self._x, self._y = float(x), float(y)

    def toPoint(self):
        return QPoint(round(self._x), round(self._y))


class QSize:
    def __init__(self, w=0, h=0):
        self._w, self._h = int(w), int(h)

    def width(self):
        return self._w

    def height(self):
        return self._h

    def setWidth(self, w):
        self._w = int(w)

    def setHeight(self, h):
        self._h = int(h)


class QSizeF(QSize):
    def __init__(self, w=0.0, h=0.0):
        self._w, self._h = float(w), float(h)


class QRect:
    def __init__(self, x=0, y=0, w=0, h=0):
        self._x, self._y, self._w, self._h = int(x), int(y), int(w), int(h)

    def x(self):
        return self._x

    def y(self):
        return self._y

    def left(self):
        return self._x

    def top(self):
        return self._y

    def width(self):
        return self._w

    def height(self):
        return self._h

    def right(self):
        return self._x + self._w - 1

    def bottom(self):
        return self._y + self._h - 1

    def bottomLeft(self):
        return QPoint(self._x, self._y + self._h)

    def topLeft(self):
        return QPoint(self._x, self._y)

    def contains(self, p) -> bool:
        return self._x <= p.x() < self._x + self._w and self._y <= p.y() < self._y + self._h

    def getRect(self):
        return (self._x, self._y, self._w, self._h)


# ================= イベント =================
class QEvent:
    class Type(enum.IntEnum):
        None_ = 0
        MouseButtonPress = 2
        MouseButtonRelease = 3
        MouseButtonDblClick = 4
        MouseMove = 5
        KeyPress = 6
        KeyRelease = 7
        FocusIn = 8
        FocusOut = 9
        Move = 13
        Resize = 14
        Show = 17
        Hide = 18
        Close = 19
        ContextMenu = 82

    def __init__(self, kind):
        self._type = kind
        self._accepted = True

    def type(self):
        return self._type

    def accept(self):
        self._accepted = True

    def ignore(self):
        self._accepted = False

    def isAccepted(self) -> bool:
        return self._accepted

    def setAccepted(self, on: bool):
        self._accepted = bool(on)


class QMouseEvent(QEvent):
    def __init__(self, kind, local=None, global_pos=None, button=Qt.MouseButton.LeftButton,
                 buttons=Qt.MouseButton.LeftButton, modifiers=Qt.KeyboardModifier.NoModifier, *_a):
        super().__init__(kind)
        self._local = QPointF(local) if local is not None else QPointF()
        self._global = QPointF(global_pos) if global_pos is not None else QPointF()
        self._button = button
        self._buttons = buttons
        self._modifiers = modifiers

    def position(self):
        return self._local

    def pos(self):
        return self._local.toPoint()

    def globalPosition(self):
        return self._global

    def globalPos(self):
        return self._global.toPoint()

    def button(self):
        return self._button

    def buttons(self):
        return self._buttons

    def modifiers(self):
        return self._modifiers


class QKeyEvent(QEvent):
    def __init__(self, kind, key, modifiers=Qt.KeyboardModifier.NoModifier, text=""):
        super().__init__(kind)
        self._key = key
        self._modifiers = modifiers
        self._text = text

    def key(self):
        return self._key

    def modifiers(self):
        return self._modifiers

    def text(self):
        return self._text


class QResizeEvent(QEvent):
    def __init__(self, size, old):
        super().__init__(QEvent.Type.Resize)
        self._size, self._old = size, old

    def size(self):
        return self._size

    def oldSize(self):
        return self._old


# ================= URL =================
class QUrl:
    def __init__(self, text: str = ""):
        self._text = str(text)

    def toString(self, *_a) -> str:
        return self._text

    def url(self) -> str:
        return self._text

    def scheme(self) -> str:
        return self._text.split(":", 1)[0] if ":" in self._text else ""

    def isLocalFile(self) -> bool:
        return self._text.startswith("file:")

    def toLocalFile(self) -> str:
        return self._text[len("file://"):] if self._text.startswith("file://") else self._text

    def isValid(self) -> bool:
        return bool(self._text)

    @staticmethod
    def fromLocalFile(path) -> "QUrl":
        return QUrl("file://" + str(path))

    def __repr__(self):
        return f"QUrl({self._text!r})"


# ================= 項目のモデル（表） =================
class QModelIndex:
    __slots__ = ("_row", "_col", "_model", "_ptr")

    def __init__(self, row=-1, col=-1, model=None, ptr=None):
        self._row, self._col, self._model, self._ptr = row, col, model, ptr

    def row(self):
        return self._row

    def column(self):
        return self._col

    def model(self):
        return self._model

    def isValid(self) -> bool:
        return self._model is not None and self._row >= 0 and self._col >= 0

    def data(self, role=Qt.ItemDataRole.DisplayRole):
        return self._model.data(self, role) if self.isValid() else None

    def internalPointer(self):
        return self._ptr

    def sibling(self, row, col):
        return self._model.index(row, col) if self._model else QModelIndex()

    def __eq__(self, other):
        return isinstance(other, QModelIndex) and (self._row, self._col, self._model) == \
            (other._row, other._col, other._model)

    def __hash__(self):
        return hash((self._row, self._col, id(self._model)))


QPersistentModelIndex = QModelIndex


class QAbstractItemModel(QObject):
    modelAboutToBeReset = pyqtSignal()
    modelReset = pyqtSignal()
    layoutAboutToBeChanged = pyqtSignal()
    layoutChanged = pyqtSignal()
    rowsInserted = pyqtSignal(object, int, int)
    rowsRemoved = pyqtSignal(object, int, int)
    dataChanged = pyqtSignal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._persistent: list[QModelIndex] = []

    def index(self, row, col, parent=None):
        if 0 <= row < self.rowCount() and 0 <= col < self.columnCount():
            return QModelIndex(row, col, self)
        return QModelIndex()

    def rowCount(self, parent=None):
        return 0

    def columnCount(self, parent=None):
        return 0

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        return None

    def flags(self, index):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def beginResetModel(self):
        self.modelAboutToBeReset.emit()

    def endResetModel(self):
        self.modelReset.emit()

    def persistentIndexList(self):
        return []

    def changePersistentIndexList(self, old, new):
        pass

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        pass


class QAbstractTableModel(QAbstractItemModel):
    pass


class QAbstractListModel(QAbstractItemModel):
    def columnCount(self, parent=None):
        return 1


class QStringListModel(QAbstractListModel):
    def __init__(self, strings=None, parent=None):
        super().__init__(parent)
        self._strings = list(strings or [])

    def setStringList(self, strings):
        self.beginResetModel()
        self._strings = list(strings)
        self.endResetModel()

    def stringList(self):
        return list(self._strings)

    def rowCount(self, parent=None):
        return len(self._strings)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole) and index.isValid():
            return self._strings[index.row()]
        return None


class QSortFilterProxyModel(QAbstractItemModel):
    """元の表の行を、filterAcceptsRow で残すものだけにした表（並べ替えは元の表に任せる）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None
        self._rows: list[int] = []   # この表の行 → 元の表の行
        self._sort_role = Qt.ItemDataRole.DisplayRole

    def setSourceModel(self, model):
        self._source = model
        model.modelReset.connect(self._rebuild_reset)
        model.layoutChanged.connect(self._rebuild_layout)
        self._rows = list(range(model.rowCount()))

    def sourceModel(self):
        return self._source

    def setFilterCaseSensitivity(self, *_a):
        pass

    def setFilterKeyColumn(self, *_a):
        pass

    def setSortRole(self, role):
        self._sort_role = role

    def setDynamicSortFilter(self, *_a):
        pass

    def filterAcceptsRow(self, row, parent):
        return True

    def _compute(self):
        src = self._source
        n = src.rowCount() if src else 0
        accept = self.filterAcceptsRow
        root = QModelIndex()
        self._rows = [r for r in range(n) if accept(r, root)]

    def _rebuild_reset(self):
        self.beginResetModel()
        self._compute()
        self.endResetModel()

    def _rebuild_layout(self):
        self.layoutAboutToBeChanged.emit()
        self._compute()
        self.layoutChanged.emit()

    def invalidateFilter(self):
        before = len(self._rows)
        self.layoutAboutToBeChanged.emit()
        self._compute()
        self.layoutChanged.emit()
        after = len(self._rows)
        if after < before:
            self.rowsRemoved.emit(QModelIndex(), after, before - 1)
        elif after > before:
            self.rowsInserted.emit(QModelIndex(), before, after - 1)

    invalidate = invalidateFilter

    def rowCount(self, parent=None):
        return len(self._rows)

    def columnCount(self, parent=None):
        return self._source.columnCount() if self._source else 0

    def mapToSource(self, index):
        if not index.isValid() or index.row() >= len(self._rows):
            return QModelIndex()
        return QModelIndex(self._rows[index.row()], index.column(), self._source)

    def mapFromSource(self, index):
        try:
            return QModelIndex(self._rows.index(index.row()), index.column(), self)
        except ValueError:
            return QModelIndex()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        return self._source.data(self.mapToSource(index), role)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        return self._source.headerData(section, orientation, role)

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        self._source.sort(column, order)


class QItemSelectionModel(QObject):
    """表の選択（行で選ぶ）。selectionChanged は (選ばれた, 外れた) を渡すが、中身は使っていないので空。"""
    selectionChanged = pyqtSignal(object, object)
    currentChanged = pyqtSignal(object, object)

    def __init__(self, model=None):
        super().__init__()
        self._model = model
        self._rows: set[int] = set()
        self._current = -1

    def model(self):
        return self._model

    def selectedRows(self, column=0):
        return [QModelIndex(r, column, self._model) for r in sorted(self._rows)]

    def selectedIndexes(self):
        return self.selectedRows()

    def isRowSelected(self, row, parent=None) -> bool:
        return row in self._rows

    def hasSelection(self) -> bool:
        return bool(self._rows)

    def currentIndex(self):
        return QModelIndex(self._current, 0, self._model) if self._current >= 0 else QModelIndex()

    def _set(self, rows: set[int], current: int | None = None):
        changed = rows != self._rows
        self._rows = set(rows)
        if current is not None:
            self._current = current
        if changed:
            self.selectionChanged.emit(None, None)

    def clearSelection(self):
        self._set(set(), -1)

    def clear(self):
        self.clearSelection()


class QCoreApplication(QObject):
    _instance = None

    @classmethod
    def instance(cls):
        return QCoreApplication._instance

    def setApplicationName(self, name):
        self._app_name = name

    def processEvents(self, *_a):
        pass


class QLibraryInfo:
    pass


class QThread(QObject):
    pass


class QMimeData(QObject):
    pass


class QRegularExpression:
    def __init__(self, pattern=""):
        self.pattern = pattern
