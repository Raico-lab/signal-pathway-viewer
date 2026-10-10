"""部品（QWidget とその仲間）と並び（QLayout）。HTML の要素で作る。

並びは CSS の flex で作る。Qt の伸び方（stretch と、部品ごとの大きさの方針）を、flex-grow に置き換える。
"""
import enum
import re
import weakref

import js
from pyodide.ffi import create_proxy

from . import _dom
from ._core import (BoundSignal, QEvent, QMouseEvent, QObject, QPoint, QPointF, QRect, QResizeEvent, QSize, Qt,
                    QTimer, _filtered, pyqtSignal)
from ._gui import QAction, QColor, QFont, QFontMetrics, QPalette, _LAST_POS, key_event

_registry: "weakref.WeakValueDictionary[int, QWidget]" = weakref.WeakValueDictionary()
_next_id = [0]


def widget_of(element):
    """HTML の要素から、それを持つ部品（いちばん内側）を探す。"""
    e = element
    while not _dom.absent(e):
        try:
            qid = getattr(e.dataset, "qid", None) if hasattr(e, "dataset") else None
        except Exception:  # noqa: BLE001
            qid = None
        if qid:
            w = _registry.get(int(qid))
            if w is not None and not w._deleted:
                return w
        e = e.parentElement
    return None


# ================= 大きさの方針 =================
class QSizePolicy:
    class Policy(enum.IntEnum):
        Fixed = 0
        Minimum = 1
        Maximum = 4
        Preferred = 5
        MinimumExpanding = 3
        Expanding = 7
        Ignored = 13

    def __init__(self, h=Policy.Preferred, v=Policy.Preferred, *_a):
        self._h, self._v = h, v

    def horizontalPolicy(self):
        return self._h

    def verticalPolicy(self):
        return self._v

    def setHorizontalPolicy(self, p):
        self._h = p

    def setVerticalPolicy(self, p):
        self._v = p

    def setHeightForWidth(self, *_a):
        pass


def _expands(policy) -> bool:
    return bool(int(policy) & 2)


# ================= 見た目の指定（Qt のスタイルシート → CSS） =================
_QSS_SUB = [(re.compile(r"::item"), " .q-item"), (re.compile(r":selected"), ".selected"),
            (re.compile(r"::chunk"), " .q-chunk")]


def _qss_to_css(qid: int, text: str) -> str:
    """Qt のスタイルシートを、その部品（data-qid）の中だけに効く CSS にする。
    「QPushButton#grow:hover { … }」→ その部品自身か中の、q-QPushButton で objectName が grow のもの。
    宣言だけ（「color:#777;」）なら、その部品自身に効かせる。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S).strip()
    scope = f'[data-qid="{qid}"]'
    if "{" not in text:
        return f"{scope} {{ {_fix_decls(text)} }}" if text else ""
    out = []
    for sel_text, decls in re.findall(r"([^{}]+)\{([^{}]*)\}", text):
        sels = []
        for sel in sel_text.split(","):
            sel = sel.strip()
            if not sel:
                continue
            css = _qss_selector(sel)
            sels.append(f"{scope}{css}")      # 部品自身
            sels.append(f"{scope} {css}")     # 中の部品
        if sels:
            out.append(f"{', '.join(sels)} {{ {_fix_decls(decls)} }}")
    return "\n".join(out)


def _qss_selector(sel: str) -> str:
    for pat, rep in _QSS_SUB:
        sel = pat.sub(rep, sel)
    parts = []
    for token in sel.split():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)?(#[A-Za-z0-9_\-]+)?(.*)$", token)
        name, oid, rest = m.group(1), m.group(2), m.group(3)
        css = ""
        if name and name != "*":
            css += f".q-{name}"
        if oid:
            css += f'[data-objname="{oid[1:]}"]'
        parts.append(css + rest)
    return " ".join(parts)


def _fix_decls(decls: str) -> str:
    # Qt の書き方のうち、CSS と違うもの
    decls = decls.replace("text-align: left", "text-align: left; justify-content: flex-start")
    return decls


# ================= 部品の基本 =================
class _ScrollBar(QObject):
    """スクロールの位置（QScrollBar の代わり）。"""
    valueChanged = pyqtSignal(int)

    def __init__(self, element, vertical=True):
        super().__init__()
        self._e = element
        self._vertical = vertical

    def value(self) -> int:
        return int(self._e.scrollTop if self._vertical else self._e.scrollLeft)

    def setValue(self, v):
        if self._vertical:
            self._e.scrollTop = int(v)
        else:
            self._e.scrollLeft = int(v)

    def maximum(self) -> int:
        if self._vertical:
            return max(0, int(self._e.scrollHeight - self._e.clientHeight))
        return max(0, int(self._e.scrollWidth - self._e.clientWidth))

    def minimum(self) -> int:
        return 0

    def isVisible(self) -> bool:
        return self.maximum() > 0


class QWidget(QObject):
    _tag = "div"
    _hpolicy = QSizePolicy.Policy.Preferred
    _vpolicy = QSizePolicy.Policy.Preferred
    _accepts_mouse = False     # 押下をここで受け止める（親に伝えない）部品か
    _shrinkable = False        # 並びの中で、中身の幅より縮めてよいか

    windowTitleChanged = pyqtSignal(str)
    customContextMenuRequested = pyqtSignal(object)

    def __init__(self, parent=None, flags=None, *_a, **_kw):
        QObject.__init__(self)
        _next_id[0] += 1
        self._qid = _next_id[0]
        _registry[self._qid] = self
        self._el = _dom.el(self._tag, "qw " + " ".join(f"q-{c.__name__}" for c in type(self).__mro__
                                                        if c.__module__.startswith(("PyQt6", "tor_app", "webmain"))
                                                        or issubclass(c, QWidget) and c is not object))
        self._el.dataset.qid = str(self._qid)
        self._layout = None          # この部品に置いた並び
        self._in_layout = None       # この部品が入っている並び（ほかの部品に入っていれば、その部品）
        self._container = None       # 並び以外で入れている部品（QSplitter・QTabWidget・QScrollArea など）
        self._hidden = False         # hide() したか
        self._window_flag = bool(flags is not None and int(flags) & int(Qt.WindowType.Window))
        self._top_shown = False
        self._overlay = None         # 別の窓として出しているときの枠
        self._enabled = True
        self._policy = QSizePolicy(self._hpolicy, self._vpolicy)
        self._focus_policy = None
        self._title = ""
        self._attrs: set = set()
        self._style_el = None
        self._stylesheet = ""
        self._font = None
        self._tooltip = ""
        self._fixed_w = self._fixed_h = None
        self._last_visible = None
        self._last_size = None
        self._context_policy = Qt.ContextMenuPolicy.DefaultContextMenu
        self._build()
        cls = type(self)
        if cls.minimumSizeHint is not QWidget.minimumSizeHint:
            self._shrinkable = True
            self._el.classList.add("shrink")
        if cls.showEvent is not QWidget.showEvent or cls.hideEvent is not QWidget.hideEvent:
            _visibility_watch.add(self)
        if cls.resizeEvent is not QWidget.resizeEvent:
            _watch_resize(self)
        if parent is not None:
            self.setParent(parent, flags) if flags is not None else self.setParent(parent)

    def _build(self):
        """部品ごとの中身の要素を作る（並びは _host() の中に置く）。"""

    def _host(self):
        return self._el

    def __repr__(self):
        return f"<{type(self).__name__} #{self._qid}>"

    # ---- 親子 ----
    def setParent(self, parent, flags=None):
        if flags is not None:
            self._window_flag = bool(int(flags) & int(Qt.WindowType.Window))
        old_container = self._in_layout or self._container
        if old_container is not None:
            old_container._release_child(self)
        QObject.setParent(self, parent)
        if parent is None:
            self._hide_overlay()
            self._el.remove()
            self._el.classList.remove("floating")
            return
        if self.isWindow():
            return
        # 並びに入れていない子は、親の中に重ねて置く（setGeometry・move で位置を決める）
        self._el.classList.add("floating")
        _dom.place(parent._host(), self._el)
        self._sync_display()

    def parentWidget(self):
        if self._in_layout is not None:
            owner = self._in_layout._owner_widget()
            if owner is not None:
                return owner
        if self._container is not None:
            return self._container
        p = self.__dict__.get("_parent")
        return p if isinstance(p, QWidget) else None

    def parent(self):
        return self.parentWidget() or self.__dict__.get("_parent")

    def isWindow(self) -> bool:
        if self._window_flag:
            return True
        return self.parentWidget() is None

    def window(self):
        w = self
        while not w.isWindow():
            p = w.parentWidget()
            if p is None:
                break
            w = p
        return w

    def isAncestorOf(self, other) -> bool:
        w = other.parentWidget() if other is not None else None
        while w is not None:
            if w is self:
                return True
            if w.isWindow():
                return False
            w = w.parentWidget()
        return False

    def _release_child(self, child):
        """子を並び・入れ物から外す（入れ物の部品が上書きする）。"""

    def _on_destroy(self):
        container = self._in_layout or self._container
        if container is not None:
            container._release_child(self)
        self._hide_overlay()
        self._el.remove()
        if self._style_el is not None:
            self._style_el.remove()
        _visibility_watch.discard(self)
        _unwatch_resize(self)

    # ---- 見える・隠す ----
    def show(self):
        if self.isWindow():
            self._hidden = False
            self._show_window()
        else:
            self._hidden = False
            self._sync_display()
        _visibility_changed()

    def hide(self):
        if self._hidden and not self._top_shown:
            return
        self._hidden = True
        if self.isWindow() and self._top_shown:
            self._top_shown = False
            self._hide_overlay()
        self._sync_display()
        _visibility_changed()

    def setVisible(self, on: bool):
        self.show() if on else self.hide()

    def setHidden(self, on: bool):
        self.setVisible(not on)

    def isHidden(self) -> bool:
        if self.isWindow():
            return self._hidden or not self._top_shown
        return self._hidden

    def isVisible(self) -> bool:
        if self._deleted or self._hidden:
            return False
        if self.isWindow():
            return self._top_shown
        if self._container is not None and not self._container._child_shown(self):
            return False
        p = self.parentWidget()
        return p is not None and p.isVisible()

    def isVisibleTo(self, ancestor) -> bool:
        w = self
        while w is not None and w is not ancestor:
            if w._hidden:
                return False
            w = w.parentWidget()
        return True

    def _sync_display(self):
        self._el.style.display = "none" if self._hidden else ""
        if self._in_layout is not None:
            self._in_layout._refresh()

    def _show_window(self):
        if self._top_shown:
            if self._overlay is not None:
                self._overlay.style.zIndex = str(_next_z())
            return
        self._top_shown = True
        self._el.style.display = ""
        self._open_overlay()

    def _open_overlay(self):
        """別の窓として出す（題名の帯・閉じるボタン付きの枠）。QMainWindow・QMenu などは上書きする。"""
        frame = _dom.el("div", "qwindow")
        bar = _dom.el("div", "qwindow-bar")
        title = _dom.el("span", "qwindow-title", self._title or "")
        close = _dom.el("button", "qwindow-close", "✕")
        close.title = "閉じる"
        bar.appendChild(title)
        bar.appendChild(close)
        body = _dom.el("div", "qwindow-body")
        frame.appendChild(bar)
        frame.appendChild(body)
        size = getattr(self, "_win_size", None)
        vw, vh = int(_dom.window.innerWidth), int(_dom.window.innerHeight)
        w, h = (size or (min(900, vw - 40), min(650, vh - 60)))
        w, h = min(w, vw - 20), min(h, vh - 20)
        frame.style.width = f"{w}px"
        frame.style.height = f"{h}px"
        frame.style.left = f"{max(10, (vw - w) // 2)}px"
        frame.style.top = f"{max(10, (vh - h) // 3)}px"
        frame.style.zIndex = str(_next_z())
        self._overlay = frame
        self._overlay_title = title
        _dom.listen(self, close, "click", lambda ev: self.close())
        _make_draggable(self, frame, bar)
        _dom.listen(self, frame, "mousedown", lambda ev: setattr(frame.style, "zIndex", str(_next_z())), True)
        _dom.document.body.appendChild(frame)
        _dom.place(body, self._el)   # 窓の枠をページに入れてから中身を動かす（地図の状態を保つため）

    def _hide_overlay(self):
        if self._overlay is not None:
            self._el.remove()
            self._overlay.remove()
            self._overlay = None

    def close(self) -> bool:
        ev = QEvent(QEvent.Type.Close)
        self.closeEvent(ev)
        if not ev.isAccepted():
            return False
        self.hide()
        if Qt.WidgetAttribute.WA_DeleteOnClose in self._attrs:
            self.deleteLater()
        return True

    def closeEvent(self, event):
        event.accept()

    def showEvent(self, event):
        pass

    def hideEvent(self, event):
        pass

    def resizeEvent(self, event):
        pass

    def moveEvent(self, event):
        pass

    def raise_(self):
        if self._overlay is not None:
            self._overlay.style.zIndex = str(_next_z())
        else:
            self._el.style.zIndex = str(_next_z())

    def lower(self):
        self._el.style.zIndex = "0"

    def activateWindow(self):
        self.raise_()

    def setWindowTitle(self, title: str):
        self._title = title
        if getattr(self, "_overlay_title", None) is not None:
            self._overlay_title.textContent = title
        self.windowTitleChanged.emit(title)

    def windowTitle(self) -> str:
        return self._title

    def setWindowFlags(self, flags):
        self._window_flag = bool(int(flags) & int(Qt.WindowType.Window))

    def setWindowModality(self, *_a):
        pass

    def setAttribute(self, attr, on: bool = True):
        (self._attrs.add if on else self._attrs.discard)(attr)

    def testAttribute(self, attr) -> bool:
        if attr == Qt.WidgetAttribute.WA_WState_Hidden:
            return self.isHidden()
        return attr in self._attrs

    def windowHandle(self):
        return None

    # ---- 使える・使えない ----
    def setEnabled(self, on: bool):
        self._enabled = bool(on)
        self._el.classList.toggle("disabled", not self._enabled)
        self._apply_enabled()

    def setDisabled(self, on: bool):
        self.setEnabled(not on)

    def _apply_enabled(self):
        """入力の要素（ボタン・入力欄）の disabled を合わせる（部品ごとに上書きする）。"""

    def isEnabled(self) -> bool:
        w = self
        while w is not None:
            if not w._enabled:
                return False
            if w.isWindow():
                break
            w = w.parentWidget()
        return True

    # ---- 大きさ ----
    def setSizePolicy(self, *args):
        if len(args) == 1 and isinstance(args[0], QSizePolicy):
            self._policy = args[0]
        elif len(args) >= 2:
            self._policy = QSizePolicy(args[0], args[1])
        if args and (int(self._policy.horizontalPolicy()) & 4 or self._policy.horizontalPolicy() ==
                     QSizePolicy.Policy.Ignored):
            if self._policy.horizontalPolicy() in (QSizePolicy.Policy.Ignored,):
                self._el.classList.add("shrink")
                self._shrinkable = True
        self._relayout_parent()

    def sizePolicy(self):
        return self._policy

    def _expanding(self, horizontal: bool) -> bool:
        policy = self._policy.horizontalPolicy() if horizontal else self._policy.verticalPolicy()
        if _expands(policy):
            return True
        if self._layout is not None and int(policy) & 1:   # 伸びられる部品は、中の並びが伸びるなら伸ばす
            return self._layout._expanding(horizontal)
        return False

    def _relayout_parent(self):
        if self._in_layout is not None:
            self._in_layout._refresh()

    def setMinimumWidth(self, w):
        self._el.style.minWidth = f"{int(w)}px"

    def setMinimumHeight(self, h):
        self._el.style.minHeight = f"{int(h)}px"

    def setMaximumWidth(self, w):
        self._el.style.maxWidth = f"{int(w)}px" if w < 16777215 else ""

    def setMaximumHeight(self, h):
        self._el.style.maxHeight = f"{int(h)}px" if h < 16777215 else ""

    def setMinimumSize(self, *args):
        w, h = (args[0].width(), args[0].height()) if len(args) == 1 else args
        self.setMinimumWidth(w)
        self.setMinimumHeight(h)

    def setMaximumSize(self, *args):
        w, h = (args[0].width(), args[0].height()) if len(args) == 1 else args
        self.setMaximumWidth(w)
        self.setMaximumHeight(h)

    def setFixedWidth(self, w):
        self._fixed_w = int(w)
        s = self._el.style
        s.width = s.minWidth = s.maxWidth = f"{int(w)}px"
        self._relayout_parent()

    def setFixedHeight(self, h):
        self._fixed_h = int(h)
        s = self._el.style
        s.height = s.minHeight = s.maxHeight = f"{int(h)}px"
        self._relayout_parent()

    def setFixedSize(self, *args):
        w, h = (args[0].width(), args[0].height()) if len(args) == 1 else args
        self.setFixedWidth(w)
        self.setFixedHeight(h)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def sizeHint(self):
        return QSize(int(self._el.scrollWidth), int(self._el.scrollHeight))

    def minimumWidth(self):
        return _px(self._el.style.minWidth)

    def maximumWidth(self):
        return _px(self._el.style.maxWidth) or 16777215

    def resize(self, *args):
        w, h = (args[0].width(), args[0].height()) if len(args) == 1 else args
        self._win_size = (int(w), int(h))
        if self._overlay is not None:
            self._overlay.style.width = f"{int(w)}px"
            self._overlay.style.height = f"{int(h)}px"

    def adjustSize(self):
        pass

    def width(self) -> int:
        return int(self._el.offsetWidth)

    def height(self) -> int:
        return int(self._el.offsetHeight)

    def size(self):
        return QSize(self.width(), self.height())

    def rect(self):
        return QRect(0, 0, self.width(), self.height())

    def geometry(self):
        x, y, w, h = _dom.rect(self._el)
        p = self.parentWidget()
        if p is not None:
            px, py, _, _ = _dom.rect(p._host())
            x, y = x - px, y - py
        return QRect(round(x), round(y), round(w), round(h))

    def pos(self):
        g = self.geometry()
        return QPoint(g.x(), g.y())

    def x(self):
        return self.geometry().x()

    def y(self):
        return self.geometry().y()

    def setGeometry(self, *args):
        x, y, w, h = (args[0].x(), args[0].y(), args[0].width(), args[0].height()) if len(args) == 1 else args
        s = self._el.style
        s.left, s.top, s.width, s.height = f"{int(x)}px", f"{int(y)}px", f"{int(w)}px", f"{int(h)}px"

    def move(self, *args):
        x, y = (args[0].x(), args[0].y()) if len(args) == 1 else args
        if self._overlay is not None:
            self._overlay.style.left, self._overlay.style.top = f"{int(x)}px", f"{int(y)}px"
            return
        self._el.style.left, self._el.style.top = f"{int(x)}px", f"{int(y)}px"

    def mapToGlobal(self, p):
        x, y, _, _ = _dom.rect(self._el)
        return QPoint(round(x + p.x()), round(y + p.y()))

    def mapFromGlobal(self, p):
        x, y, _, _ = _dom.rect(self._el)
        return QPoint(round(p.x() - x), round(p.y() - y))

    def mapTo(self, other, p):
        return other.mapFromGlobal(self.mapToGlobal(p))

    def mapFrom(self, other, p):
        return self.mapFromGlobal(other.mapToGlobal(p))

    def contentsRect(self):
        return self.rect()

    # ---- 見た目 ----
    def setStyleSheet(self, text: str):
        self._stylesheet = text or ""
        css = _qss_to_css(self._qid, self._stylesheet)
        if self._style_el is None:
            if not css:
                return
            self._style_el = _dom.el("style")
            _dom.document.head.appendChild(self._style_el)
        self._style_el.textContent = css

    def styleSheet(self) -> str:
        return self._stylesheet

    def setObjectName(self, name: str):
        QObject.setObjectName(self, name)
        self._el.dataset.objname = name

    def setToolTip(self, text: str):
        self._tooltip = text or ""
        target = self._tip_el()
        if self._tooltip:
            target.title = self._tooltip
        else:
            target.removeAttribute("title")

    def _tip_el(self):
        return self._el

    def toolTip(self) -> str:
        return self._tooltip

    def setStatusTip(self, *_a):
        pass

    def setWhatsThis(self, *_a):
        pass

    def font(self):
        return QFont(self._font) if self._font else QFont()

    def setFont(self, font):
        self._font = QFont(font)
        self._el.style.fontWeight = "bold" if font.bold() else ""
        self._el.style.fontSize = f"{font.pointSize()}px"

    def fontMetrics(self):
        return QFontMetrics(self.font())

    def palette(self):
        return QPalette()

    def setPalette(self, *_a):
        pass

    def setAutoFillBackground(self, *_a):
        pass

    def setCursor(self, *_a):
        pass

    def unsetCursor(self):
        pass

    def style(self):
        return _Style()

    def update(self, *_a):
        pass

    def repaint(self, *_a):
        pass

    def updateGeometry(self):
        pass

    def setContentsMargins(self, *args):
        pass

    def setUpdatesEnabled(self, *_a):
        pass

    # ---- フォーカス ----
    def setFocusPolicy(self, policy):
        self._focus_policy = policy
        if policy == Qt.FocusPolicy.NoFocus:
            target = self._focus_el()
            target.tabIndex = -1
            # 押してもフォーカスを移さない（入力欄の文字の入力を続けられるように）
            _dom.listen(self, target, "mousedown", lambda ev: ev.preventDefault())

    def focusPolicy(self):
        return self._focus_policy

    def _focus_el(self):
        return self._el

    def setFocus(self, *_a):
        try:
            self._focus_el().focus()
        except Exception:  # noqa: BLE001
            pass

    def clearFocus(self):
        try:
            self._focus_el().blur()
        except Exception:  # noqa: BLE001
            pass

    def hasFocus(self) -> bool:
        return self._focus_el() == _dom.document.activeElement

    # ---- 並び ----
    def setLayout(self, layout):
        if self._layout is not None and self._layout is not layout:
            self._layout._el.remove()
        self._layout = layout
        layout._owner = weakref.ref(self)
        layout._top = True
        layout._apply_margins()
        self._host().appendChild(layout._el)
        self._el.classList.add("has-layout")
        layout._refresh()
        layout._adopt_children()

    def layout(self):
        return self._layout

    # ---- 押下 ----
    def mousePressEvent(self, event):
        if not self._accepts_mouse:
            event.ignore()

    def mouseReleaseEvent(self, event):
        pass

    def mouseDoubleClickEvent(self, event):
        pass

    def keyPressEvent(self, event):
        event.ignore()

    def contextMenuEvent(self, event):
        pass

    def setContextMenuPolicy(self, policy):
        self._context_policy = policy
        if policy == Qt.ContextMenuPolicy.CustomContextMenu and not getattr(self, "_ctx_listening", False):
            self._ctx_listening = True
            target = self._context_el()

            def on_ctx(ev):
                ev.preventDefault()
                vp = self._context_widget()
                x, y, _, _ = _dom.rect(vp._el)
                self._before_context(ev)
                self.customContextMenuRequested.emit(QPoint(round(ev.clientX - x), round(ev.clientY - y)))

            _dom.listen(self, target, "contextmenu", on_ctx)

    def _context_el(self):
        return self._el

    def _context_widget(self):
        return self

    def _before_context(self, ev):
        pass

    def contextMenuPolicy(self):
        return self._context_policy

    def setAcceptDrops(self, *_a):
        pass

    def grab(self, *_a):
        return None

    def installEventFilter(self, filt):
        QObject.installEventFilter(self, filt)
        _watch_resize(self)


def _px(text) -> int:
    try:
        return int(float(str(text).replace("px", "")))
    except ValueError:
        return 0


class _Style:
    def standardPalette(self):
        return QPalette()

    def pixelMetric(self, *_a):
        return 16

    def standardIcon(self, *_a):
        return None


_z = [1000]


def _next_z() -> int:
    _z[0] += 1
    return _z[0]


def _make_draggable(owner, frame, handle):
    state = {}

    def down(ev):
        if ev.button != 0 or str(ev.target.tagName).upper() == "BUTTON":
            return
        x, y, _, _ = _dom.rect(frame)
        state.update(dx=ev.clientX - x, dy=ev.clientY - y, on=True)
        ev.preventDefault()

    def move(ev):
        if state.get("on"):
            frame.style.left = f"{max(0, ev.clientX - state['dx'])}px"
            frame.style.top = f"{max(0, ev.clientY - state['dy'])}px"

    def up(ev):
        state["on"] = False

    _dom.listen(owner, handle, "mousedown", down)
    _dom.listen(owner, _dom.document, "mousemove", move)
    _dom.listen(owner, _dom.document, "mouseup", up)


# ---- 見える・見えないの変化（showEvent・hideEvent を上書きした部品に知らせる） ----
_visibility_watch: "weakref.WeakSet[QWidget]" = weakref.WeakSet()
_visibility_pending = [False]


def _visibility_changed():
    if _visibility_pending[0] or not len(_visibility_watch):
        return
    _visibility_pending[0] = True
    _dom.later(0, _check_visibility)


def _check_visibility():
    _visibility_pending[0] = False
    for w in list(_visibility_watch):
        if w._deleted:
            continue
        now = w.isVisible()
        if now == w._last_visible:
            continue
        w._last_visible = now
        try:
            (w.showEvent if now else w.hideEvent)(QEvent(QEvent.Type.Show if now else QEvent.Type.Hide))
            _filtered(w, QEvent(QEvent.Type.Show if now else QEvent.Type.Hide))
        except Exception:  # noqa: BLE001
            _dom.report()


# ---- 大きさの変化（resizeEvent を上書きした部品と、イベントフィルタを入れた部品に知らせる） ----
_resize_map: dict = {}
_resize_observer = [None]


def _on_resize(entries, _observer=None):
    for entry in entries:
        try:
            qid = int(entry.target.dataset.qid)
        except Exception:  # noqa: BLE001
            continue
        w = _registry.get(qid)
        if w is None or w._deleted:
            continue
        size = QSize(w.width(), w.height())
        old = w._last_size or QSize(-1, -1)
        if (size.width(), size.height()) == (old.width(), old.height()):
            continue
        w._last_size = size
        ev = QResizeEvent(size, old)
        try:
            if not _filtered(w, ev):
                w.resizeEvent(ev)
        except Exception:  # noqa: BLE001
            _dom.report()


def _watch_resize(w):
    if w._qid in _resize_map:
        return
    if _resize_observer[0] is None:
        _resize_observer[0] = js.ResizeObserver.new(create_proxy(_on_resize))
    _resize_map[w._qid] = True
    _resize_observer[0].observe(w._el)


def _unwatch_resize(w):
    if _resize_map.pop(w._qid, None) and _resize_observer[0] is not None:
        _resize_observer[0].unobserve(w._el)


# ================= 並び（レイアウト） =================
class QLayoutItem:
    def __init__(self, kind, obj=None, stretch=0, el=None, size=0):
        self.kind, self.obj, self.stretch, self.el, self.size = kind, obj, stretch, el, size

    def widget(self):
        return self.obj if self.kind == "widget" else None

    def layout(self):
        return self.obj if self.kind == "layout" else None

    def spacerItem(self):
        return self if self.kind in ("stretch", "spacing") else None


class QLayout(QObject):
    _dir = "v"

    def __init__(self, parent=None):
        QObject.__init__(self)
        self._el = _dom.el("div", f"ql ql-{self._dir}")
        self._items: list[QLayoutItem] = []
        self._owner = None           # 置いた部品（weakref）
        self._parent_layout = None
        self._top = False
        self._margins = None
        self._spacing = 6
        self._el.style.gap = f"{self._spacing}px"
        if isinstance(parent, QWidget):
            parent.setLayout(self)

    def _owner_widget(self):
        layout = self
        while layout is not None:
            if layout._owner is not None:
                return layout._owner()
            layout = layout._parent_layout
        return None

    def parentWidget(self):
        return self._owner_widget()

    def _apply_margins(self):
        m = self._margins if self._margins is not None else ((9, 9, 9, 9) if self._top else (0, 0, 0, 0))
        self._el.style.padding = f"{m[1]}px {m[2]}px {m[3]}px {m[0]}px"

    def setContentsMargins(self, *args):
        if len(args) == 1:
            m = args[0]
            args = (m.left(), m.top(), m.right(), m.bottom())
        self._margins = tuple(int(a) for a in args)
        self._apply_margins()

    def contentsMargins(self):
        return self._margins or (0, 0, 0, 0)

    def setSpacing(self, n):
        self._spacing = int(n)
        self._el.style.gap = f"{self._spacing}px"

    def spacing(self):
        return self._spacing

    def setAlignment(self, *_a):
        pass

    def setSizeConstraint(self, *_a):
        pass

    # ---- 入れる・外す ----
    def _attach_widget(self, w, before=None):
        container = w._in_layout or w._container
        if container is not None:
            container._release_child(w)
        w._in_layout = self
        w._el.classList.remove("floating")
        w._el.style.left = w._el.style.top = ""
        if not w._window_flag:
            w._hidden = w._hidden   # 並びに入れた部品は、hide() していなければ見える
        _dom.place(self._el, w._el, before)
        owner = self._owner_widget()
        if owner is not None:
            QObject.setParent(w, owner)
        w._sync_display()
        _visibility_changed()

    def _adopt_children(self):
        owner = self._owner_widget()
        if owner is None:
            return
        for it in self._items:
            if it.kind == "widget":
                QObject.setParent(it.obj, owner)
            elif it.kind == "layout":
                it.obj._adopt_children()

    def addWidget(self, w, stretch=0, alignment=None):
        self._attach_widget(w)
        self._items.append(QLayoutItem("widget", w, int(stretch or 0), w._el))
        self._refresh()

    def insertWidget(self, index, w, stretch=0, alignment=None):
        if w._in_layout is self:
            self._release_child(w)
        index = len(self._items) if index < 0 else min(index, len(self._items))
        before = self._items[index].el if index < len(self._items) else None
        self._attach_widget(w, before)
        self._items.insert(index, QLayoutItem("widget", w, int(stretch or 0), w._el))
        self._refresh()

    def addLayout(self, layout, stretch=0):
        layout._parent_layout = self
        self._el.appendChild(layout._el)
        self._items.append(QLayoutItem("layout", layout, int(stretch or 0), layout._el))
        layout._apply_margins()
        layout._adopt_children()
        self._refresh()

    def insertLayout(self, index, layout, stretch=0):
        layout._parent_layout = self
        before = self._items[index].el if index < len(self._items) else None
        self._el.insertBefore(layout._el, before)
        self._items.insert(index, QLayoutItem("layout", layout, int(stretch or 0), layout._el))
        layout._apply_margins()
        layout._adopt_children()
        self._refresh()

    def addStretch(self, stretch=0):
        e = _dom.el("div", "ql-stretch")
        self._el.appendChild(e)
        self._items.append(QLayoutItem("stretch", None, int(stretch or 0), e))
        self._refresh()

    def insertStretch(self, index, stretch=0):
        e = _dom.el("div", "ql-stretch")
        before = self._items[index].el if 0 <= index < len(self._items) else None
        self._el.insertBefore(e, before)
        self._items.insert(index if index >= 0 else len(self._items), QLayoutItem("stretch", None, int(stretch or 0), e))
        self._refresh()

    def addSpacing(self, size):
        e = _dom.el("div", "ql-spacing")
        if self._dir == "h":
            e.style.width = f"{int(size)}px"
        else:
            e.style.height = f"{int(size)}px"
        self._el.appendChild(e)
        self._items.append(QLayoutItem("spacing", None, 0, e, int(size)))
        self._refresh()

    def addItem(self, item):
        self._el.appendChild(item.el)
        self._items.append(item)
        self._refresh()

    def addSpacerItem(self, item):
        self.addStretch()

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def indexOf(self, obj) -> int:
        for i, it in enumerate(self._items):
            if it.obj is obj:
                return i
        return -1

    def takeAt(self, i):
        if not 0 <= i < len(self._items):
            return None
        it = self._items.pop(i)
        if it.kind == "widget":
            it.obj._in_layout = None
            it.el.remove()
        elif it.kind == "layout":
            it.obj._parent_layout = None
            it.el.remove()
        else:
            it.el.remove()
        self._refresh()
        return it

    def removeWidget(self, w):
        i = self.indexOf(w)
        if i >= 0:
            self._items.pop(i)
            w._in_layout = None
            w._el.remove()
            self._refresh()

    def removeItem(self, item):
        if item in self._items:
            self._items.remove(item)
            item.el.remove()
            self._refresh()

    def _release_child(self, w):
        self.removeWidget(w)

    def _child_shown(self, w) -> bool:
        return True

    def setStretchFactor(self, obj, stretch) -> bool:
        i = self.indexOf(obj)
        if i < 0:
            return False
        self._items[i].stretch = int(stretch)
        self._refresh()
        return True

    def setStretch(self, index, stretch):
        if 0 <= index < len(self._items):
            self._items[index].stretch = int(stretch)
            self._refresh()

    def _expanding(self, horizontal: bool) -> bool:
        for it in self._items:
            if it.kind == "widget" and not it.obj._hidden and it.obj._expanding(horizontal):
                return True
            if it.kind == "layout" and it.obj._expanding(horizontal):
                return True
            if it.kind == "stretch" and horizontal == (self._dir == "h"):
                return True
        return False

    def _refresh(self):
        """Qt の伸び方を flex に置き換える。stretch を付けたものがあればその比で、なければ伸びる方針の部品で分ける。"""
        horizontal = self._dir == "h"
        items = [it for it in self._items if not (it.kind == "widget" and it.obj._hidden)]
        any_stretch = any(it.stretch > 0 for it in items)
        for it in self._items:
            if it.kind == "spacing":
                it.el.style.flex = "0 0 auto"
                continue
            if any_stretch:
                grow = it.stretch
            elif it.kind == "stretch":
                grow = 1
            elif it.kind == "widget":
                grow = 1 if it.obj._expanding(horizontal) else 0
            else:
                grow = 1 if it.obj._expanding(horizontal) else 0
            w = it.obj if it.kind == "widget" else None
            fixed = w is not None and ((w._fixed_w is not None) if horizontal else (w._fixed_h is not None))
            if fixed:
                flex = "0 0 auto"
            elif grow:
                flex = f"{grow} 1 0px"
            elif w is not None and (w._shrinkable or (horizontal and w._expanding(True))):
                flex = "0 1 auto"
            else:
                flex = "0 0 auto"
            it.el.style.flex = flex
            # 横の並びでは、縦に伸びない部品は中央にそろえる（ボタンなどが縦に伸びないように）
            if horizontal:
                cross = it.kind == "layout" or (w is not None and w._expanding(False))
                it.el.style.alignSelf = "stretch" if cross else ""


class QBoxLayout(QLayout):
    class Direction(enum.IntEnum):
        LeftToRight = 0
        RightToLeft = 1
        TopToBottom = 2
        BottomToTop = 3

    def __init__(self, direction=None, parent=None):
        if isinstance(direction, QBoxLayout.Direction):
            self._dir = "h" if direction in (0, 1) else "v"
        elif direction is not None and parent is None:
            parent = direction
        super().__init__(parent)

    def setDirection(self, direction):
        self._dir = "h" if direction in (0, 1) else "v"
        self._el.className = f"ql ql-{self._dir}"
        self._refresh()


class QVBoxLayout(QBoxLayout):
    _dir = "v"

    def __init__(self, parent=None):
        QLayout.__init__(self, parent)


class QHBoxLayout(QBoxLayout):
    _dir = "h"

    def __init__(self, parent=None):
        QLayout.__init__(self, parent)


class QFormLayout(QLayout):
    _dir = "form"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = 0

    def addRow(self, label, field=None):
        if field is None:
            field, label = label, ""
        lab = _dom.el("div", "qform-label")
        if isinstance(label, QWidget):
            self._attach_widget(label)
            lab.appendChild(label._el)
            self._items.append(QLayoutItem("widget", label, 0, label._el))
        else:
            lab.textContent = str(label or "")
        self._el.appendChild(lab)
        box = _dom.el("div", "qform-field")
        self._el.appendChild(box)
        if isinstance(field, QLayout):
            field._parent_layout = self
            box.appendChild(field._el)
            self._items.append(QLayoutItem("layout", field, 0, field._el))
            field._adopt_children()
        else:
            self._attach_widget(field)
            box.appendChild(field._el)
            self._items.append(QLayoutItem("widget", field, 0, field._el))
        self._rows += 1

    def rowCount(self):
        return self._rows

    def _refresh(self):
        pass


class QGridLayout(QLayout):
    _dir = "grid"

    def addWidget(self, w, row=0, col=0, rowspan=1, colspan=1, *_a):
        self._attach_widget(w)
        w._el.style.gridRow = f"{row + 1} / span {rowspan}"
        w._el.style.gridColumn = f"{col + 1} / span {colspan}"
        self._items.append(QLayoutItem("widget", w, 0, w._el))

    def _refresh(self):
        pass


class QSpacerItem(QLayoutItem):
    def __init__(self, w=0, h=0, *_a):
        super().__init__("stretch", None, 0, _dom.el("div", "ql-stretch"))


# ================= 文字 =================
_HTML_RE = re.compile(r"<\s*(b|i|a|br|span|p|div|table|font|u|small|sup|sub|h\d|ul|li)\b", re.I)


class QLabel(QWidget):
    linkActivated = pyqtSignal(str)

    def __init__(self, text="", parent=None, *_a):
        if isinstance(text, QWidget):
            text, parent = "", text
        self._text = ""
        self._wrap = False
        super().__init__(parent)
        self.setText(text)

    def _build(self):
        self._el.classList.add("qlabel")
        _dom.listen(self, self._el, "click", self._on_click)

    def _on_click(self, ev):
        a = ev.target.closest("a") if hasattr(ev.target, "closest") else None
        if not _dom.absent(a):
            ev.preventDefault()
            href = str(a.getAttribute("href") or "")
            if getattr(self, "_external", False):   # setOpenExternalLinks(True): Qt と同じく、自分でリンク先を開く
                _dom.window.open(href, "_blank", "noopener")
            else:
                self.linkActivated.emit(href)

    def setText(self, text):
        self._text = "" if text is None else str(text)
        if _HTML_RE.search(self._text):
            self._el.innerHTML = self._text
            self._el.classList.add("rich")
        else:
            self._el.textContent = self._text
            self._el.classList.remove("rich")

    def text(self) -> str:
        return self._text

    def clear(self):
        self.setText("")

    def setNum(self, n):
        self.setText(str(n))

    def setWordWrap(self, on: bool):
        self._wrap = bool(on)
        self._el.classList.toggle("wrap", self._wrap)

    def wordWrap(self) -> bool:
        return self._wrap

    def setTextInteractionFlags(self, flags):
        self._el.classList.toggle("selectable", bool(int(flags) & 1))

    def setOpenExternalLinks(self, on):
        self._external = bool(on)

    def setAlignment(self, align):
        a = int(align)
        self._el.style.textAlign = "right" if a & 0x2 else "center" if a & 0x4 else ""

    def setTextFormat(self, *_a):
        pass

    def setBuddy(self, *_a):
        pass

    def setPixmap(self, *_a):
        pass

    def setIndent(self, *_a):
        pass

    def setMargin(self, *_a):
        pass


# ================= ボタン =================
class QAbstractButton(QWidget):
    _accepts_mouse = True
    clicked = pyqtSignal(bool)
    toggled = pyqtSignal(bool)
    pressed = pyqtSignal()
    released = pyqtSignal()

    def __init__(self, text="", parent=None, *_a):
        if isinstance(text, QWidget):
            text, parent = "", text
        self._checkable = False
        self._checked = False
        self._group = None
        self._text = ""
        super().__init__(parent)
        self.setText(text)

    def setText(self, text):
        self._text = "" if text is None else str(text)
        self._render_text()

    def _render_text(self):
        self._el.textContent = self._text

    def text(self):
        return self._text

    def setCheckable(self, on: bool):
        self._checkable = bool(on)

    def isCheckable(self) -> bool:
        return self._checkable

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, on: bool):
        on = bool(on)
        if not self._checkable or on == self._checked:
            return
        group = self._group
        if not on and group is not None and group._exclusive and group.checkedButton() is self:
            return   # 1 つだけ選ぶ組では、選んでいるものを外せない
        if on and group is not None and group._exclusive:
            for other in group._buttons:
                if other is not self and other._checked:
                    other._checked = False
                    other._sync_checked()
                    other.toggled.emit(False)
                    group._emit_toggled(other, False)
        self._checked = on
        self._sync_checked()
        self.toggled.emit(on)
        if group is not None:
            group._emit_toggled(self, on)

    def _sync_checked(self):
        self._el.classList.toggle("checked", self._checked)

    def toggle(self):
        self.setChecked(not self._checked)

    def click(self):
        if not self.isEnabled():
            return
        if self._checkable:
            if self._group is not None and self._group._exclusive and self._checked:
                pass
            else:
                self.setChecked(not self._checked)
        self.clicked.emit(self._checked)
        if self._group is not None:
            self._group._emit_clicked(self)

    def animateClick(self, *_a):
        self.click()

    def setIcon(self, *_a):
        pass

    def setIconSize(self, *_a):
        pass

    def setAutoExclusive(self, *_a):
        pass

    def setShortcut(self, *_a):
        pass

    def setDown(self, *_a):
        pass

    def group(self):
        return self._group


class QPushButton(QAbstractButton):
    _tag = "button"
    _hpolicy = QSizePolicy.Policy.Minimum
    _vpolicy = QSizePolicy.Policy.Fixed

    def __init__(self, text="", parent=None, *_a):
        self._menu = None
        super().__init__(text, parent)

    def _build(self):
        self._el.type = "button"
        self._el.classList.add("qbutton")
        _dom.listen(self, self._el, "click", self._on_dom_click)

    def _on_dom_click(self, ev):
        ev.stopPropagation()
        if self._menu is not None:
            self.showMenu()
            return
        self.click()

    def _apply_enabled(self):
        self._el.disabled = not self._enabled

    def setMenu(self, menu):
        self._menu = menu
        menu._opener = weakref.ref(self)
        self._el.classList.add("has-menu")

    def menu(self):
        return self._menu

    def showMenu(self):
        if self._menu is None:
            return
        if self._menu.isVisible():
            self._menu.hide()
            return
        x, y, w, h = _dom.rect(self._el)
        self._menu.popup(QPoint(round(x), round(y + h)))

    def setDefault(self, *_a):
        pass

    def setAutoDefault(self, *_a):
        pass

    def setFlat(self, on):
        self._el.classList.toggle("flat", bool(on))

    def _render_text(self):
        self._el.textContent = self._text.replace("&&", "\0").replace("&", "").replace("\0", "&")


class QToolButton(QAbstractButton):
    _tag = "button"
    _hpolicy = QSizePolicy.Policy.Fixed
    _vpolicy = QSizePolicy.Policy.Fixed
    _ARROWS = {Qt.ArrowType.DownArrow: "▾", Qt.ArrowType.RightArrow: "▸", Qt.ArrowType.UpArrow: "▴",
               Qt.ArrowType.LeftArrow: "◂"}

    def __init__(self, parent=None, *_a):
        self._arrow = Qt.ArrowType.NoArrow
        super().__init__("", parent)

    def _build(self):
        self._el.type = "button"
        self._el.classList.add("qtoolbutton")
        _dom.listen(self, self._el, "click", lambda ev: (ev.stopPropagation(), self.click()))

    def _apply_enabled(self):
        self._el.disabled = not self._enabled

    def setArrowType(self, arrow):
        self._arrow = arrow
        self._render_text()

    def _render_text(self):
        arrow = self._ARROWS.get(self._arrow, "")
        self._el.textContent = (arrow + (" " if arrow and self._text else "") + self._text) if (arrow or self._text) else ""

    def setAutoRaise(self, on):
        self._el.classList.toggle("autoraise", bool(on))

    def setToolButtonStyle(self, *_a):
        pass

    def setPopupMode(self, *_a):
        pass

    def setDefaultAction(self, *_a):
        pass

    def setMenu(self, menu):
        pass


class QCheckBox(QAbstractButton):
    _tag = "label"
    _hpolicy = QSizePolicy.Policy.Preferred
    _vpolicy = QSizePolicy.Policy.Fixed
    stateChanged = pyqtSignal(int)
    checkStateChanged = pyqtSignal(object)
    _input_type = "checkbox"

    def __init__(self, text="", parent=None, *_a):
        super().__init__(text, parent)
        self._checkable = True

    def _build(self):
        self._el.classList.add("qcheck")
        self._input = _dom.el("input")
        self._input.type = self._input_type
        self._label = _dom.el("span", "qcheck-text")
        self._el.appendChild(self._input)
        self._el.appendChild(self._label)
        _dom.listen(self, self._input, "click", self._on_input_click)

    def _render_text(self):
        self._label.textContent = self._text
        self._label.style.display = "" if self._text else "none"

    def _focus_el(self):
        return self._input

    def _apply_enabled(self):
        self._input.disabled = not self._enabled

    def _on_input_click(self, ev):
        ev.stopPropagation()
        # ブラウザが切り替えた後の値ではなく、こちらの状態で切り替える（組・シグナルを Qt と同じ順に）
        want = bool(self._input.checked)
        if self._group is not None and self._group._exclusive and self._checked and not want:
            self._input.checked = True
            return
        if want != self._checked:
            self.setChecked(want)
            self.clicked.emit(self._checked)
            if self._group is not None:
                self._group._emit_clicked(self)
        else:
            self._sync_checked()

    def _sync_checked(self):
        self._input.checked = self._checked

    def setChecked(self, on: bool):
        before = self._checked
        QAbstractButton.setChecked(self, on)
        if self._checked != before:
            self.stateChanged.emit(2 if self._checked else 0)
            self.checkStateChanged.emit(Qt.CheckState.Checked if self._checked else Qt.CheckState.Unchecked)
        self._sync_checked()

    def checkState(self):
        return Qt.CheckState.Checked if self._checked else Qt.CheckState.Unchecked

    def setCheckState(self, state):
        self.setChecked(state == Qt.CheckState.Checked)

    def setTristate(self, *_a):
        pass


class QRadioButton(QCheckBox):
    _input_type = "radio"

    def __init__(self, text="", parent=None, *_a):
        super().__init__(text, parent)
        self._input.name = f"qradio-{id(self)}"

    def _on_input_click(self, ev):
        ev.stopPropagation()
        if self._checked:
            self._input.checked = True
            return
        # 同じ組（なければ同じ親）のほかのボタンを外す
        if self._group is None:
            parent = self.parentWidget()
            for w in list(_registry.values()):
                if isinstance(w, QRadioButton) and w is not self and w._group is None and w._checked \
                        and w.parentWidget() is parent:
                    w._checked = False
                    w._sync_checked()
                    w.toggled.emit(False)
        self.setChecked(True)
        self.clicked.emit(True)
        if self._group is not None:
            self._group._emit_clicked(self)


class QButtonGroup(QObject):
    buttonToggled = pyqtSignal(object, bool)
    buttonClicked = pyqtSignal(object)
    idToggled = pyqtSignal(int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buttons: list = []
        self._ids: dict = {}
        self._exclusive = True

    def addButton(self, button, bid=-1):
        self._buttons.append(button)
        button._group = self
        self._ids[button] = bid

    def removeButton(self, button):
        if button in self._buttons:
            self._buttons.remove(button)
            button._group = None

    def buttons(self):
        return list(self._buttons)

    def checkedButton(self):
        return next((b for b in self._buttons if b._checked), None)

    def setExclusive(self, on):
        self._exclusive = bool(on)

    def id(self, button):
        return self._ids.get(button, -1)

    def _emit_toggled(self, button, on):
        self.buttonToggled.emit(button, on)
        self.idToggled.emit(self._ids.get(button, -1), on)

    def _emit_clicked(self, button):
        self.buttonClicked.emit(button)


# ================= 入力 =================
class QLineEdit(QWidget):
    _tag = "input"
    _accepts_mouse = True
    _hpolicy = QSizePolicy.Policy.Expanding
    _vpolicy = QSizePolicy.Policy.Fixed
    textChanged = pyqtSignal(str)
    textEdited = pyqtSignal(str)
    returnPressed = pyqtSignal()
    editingFinished = pyqtSignal()

    def __init__(self, text="", parent=None):
        if isinstance(text, QWidget):
            text, parent = "", text
        self._completer = None
        super().__init__(parent)
        if text:
            self.setText(text)

    def _build(self):
        self._el.type = "text"
        self._el.classList.add("qlineedit")
        self._el.setAttribute("autocomplete", "off")
        self._el.setAttribute("spellcheck", "false")
        _dom.listen(self, self._el, "input", self._on_input)
        _dom.listen(self, self._el, "keydown", self._on_key)
        _dom.listen(self, self._el, "blur", lambda ev: self.editingFinished.emit())
        # 日本語などの変換: 確定の Enter を「Enter を押した」として扱わない（Safari などは確定の Enter を
        # isComposing なしで送るので、変換の始まりと終わりを自分で見る）
        self._composing = False
        self._composed_at = 0.0
        _dom.listen(self, self._el, "compositionstart", lambda ev: setattr(self, "_composing", True))
        _dom.listen(self, self._el, "compositionend", self._on_composition_end)

    def _on_input(self, ev):
        text = str(self._el.value)
        self.textEdited.emit(text)
        self.textChanged.emit(text)
        # 変換中の読みでは候補を出さない（確定したときに出す）
        if self._completer is not None and not self._composing:
            self._completer._on_typed(text)

    def _on_composition_end(self, ev):
        self._composing = False
        self._composed_at = float(js.Date.now())
        if self._completer is not None:
            self._completer._on_typed(str(self._el.value))

    def _in_composition(self, ev) -> bool:
        """変換中か、変換を確定した直後（確定の Enter）のキーか。"""
        return bool(ev.isComposing or int(ev.keyCode or 0) == 229 or self._composing
                    or float(js.Date.now()) - self._composed_at < 80)

    def _on_key(self, ev):
        if self._in_composition(ev):
            return   # 変換中のキー（確定の Enter・候補を選ぶ ↑↓・取り消しの Esc など）は入力欄に任せる
        kev = key_event(ev)
        if self._completer is not None and self._completer._handle_key(ev):
            ev.preventDefault()
            return
        if _filtered(self, kev):
            ev.preventDefault()
            return
        if str(ev.key) == "Enter":
            ev.preventDefault()
            self.returnPressed.emit()
            return
        kev.ignore()
        self.keyPressEvent(kev)

    def _apply_enabled(self):
        self._el.disabled = not self._enabled

    def text(self) -> str:
        return str(self._el.value)

    def setText(self, text):
        text = "" if text is None else str(text)
        if text == str(self._el.value):
            return
        self._el.value = text
        self.textChanged.emit(text)

    def clear(self):
        self.setText("")

    def setPlaceholderText(self, text):
        self._el.placeholder = text

    def placeholderText(self):
        return str(self._el.placeholder)

    def setClearButtonEnabled(self, on):
        self._el.type = "search" if on else "text"
        if on and not getattr(self, "_search_listening", False):
            self._search_listening = True
            # 入力欄の × で消したとき（input の後の search）
            _dom.listen(self, self._el, "search", lambda ev: None)

    def setReadOnly(self, on):
        self._el.readOnly = bool(on)

    def isReadOnly(self):
        return bool(self._el.readOnly)

    def setMaxLength(self, n):
        self._el.maxLength = int(n)

    def setValidator(self, *_a):
        pass

    def setEchoMode(self, *_a):
        pass

    def setAlignment(self, *_a):
        pass

    def setFrame(self, *_a):
        pass

    def selectAll(self):
        self._el.select()

    def cursorPosition(self):
        return int(self._el.selectionStart or 0)

    def setCursorPosition(self, n):
        self._el.setSelectionRange(n, n)

    def setCompleter(self, completer):
        self._completer = completer
        if completer is not None:
            completer._attach(self)

    def completer(self):
        return self._completer


class QPlainTextEdit(QWidget):
    _tag = "textarea"
    _accepts_mouse = True
    _hpolicy = QSizePolicy.Policy.Expanding
    _vpolicy = QSizePolicy.Policy.Expanding
    textChanged = pyqtSignal()

    def __init__(self, text="", parent=None):
        if isinstance(text, QWidget):
            text, parent = "", text
        super().__init__(parent)
        self._el.value = text or ""

    def _build(self):
        self._el.classList.add("qtextedit")
        _dom.listen(self, self._el, "input", lambda ev: self.textChanged.emit())

    def toPlainText(self) -> str:
        return str(self._el.value)

    def setPlainText(self, text):
        self._el.value = text or ""
        self.textChanged.emit()

    def appendPlainText(self, text):
        self.setPlainText(self.toPlainText() + ("\n" if self.toPlainText() else "") + text)

    def clear(self):
        self.setPlainText("")

    def setPlaceholderText(self, text):
        self._el.placeholder = text

    def setReadOnly(self, on):
        self._el.readOnly = bool(on)

    def setLineWrapMode(self, *_a):
        pass

    def _apply_enabled(self):
        self._el.disabled = not self._enabled


QTextEdit_plain = QPlainTextEdit


class QSpinBox(QWidget):
    _tag = "input"
    _accepts_mouse = True
    _hpolicy = QSizePolicy.Policy.Minimum
    _vpolicy = QSizePolicy.Policy.Fixed
    valueChanged = pyqtSignal(int)
    textChanged = pyqtSignal(str)
    editingFinished = pyqtSignal()

    def __init__(self, parent=None):
        self._min, self._max, self._value = 0, 99, 0
        super().__init__(parent)

    def _build(self):
        self._el.type = "number"
        self._el.classList.add("qspin")
        self._el.value = "0"
        _dom.listen(self, self._el, "input", self._on_input)
        _dom.listen(self, self._el, "change", self._on_change)

    def _on_input(self, ev):
        try:
            v = int(float(str(self._el.value)))
        except ValueError:
            return
        if self._min <= v <= self._max and v != self._value:
            self._value = v
            self.valueChanged.emit(v)

    def _on_change(self, ev):
        self._el.value = str(self._value)
        self.editingFinished.emit()

    def _apply_enabled(self):
        self._el.disabled = not self._enabled

    def setRange(self, lo, hi):
        self._min, self._max = int(lo), int(hi)
        self._el.min, self._el.max = str(self._min), str(self._max)
        self.setValue(min(max(self._value, self._min), self._max))

    def setMinimum(self, lo):
        self.setRange(lo, self._max)

    def setMaximum(self, hi):
        self.setRange(self._min, hi)

    def minimum(self):
        return self._min

    def maximum(self):
        return self._max

    def setSingleStep(self, *_a):
        pass

    def setSuffix(self, *_a):
        pass

    def setPrefix(self, *_a):
        pass

    def setSpecialValueText(self, *_a):
        pass

    def value(self) -> int:
        return self._value

    def setValue(self, v):
        v = min(max(int(v), self._min), self._max)
        self._el.value = str(v)
        if v != self._value:
            self._value = v
            self.valueChanged.emit(v)


class QComboBox(QWidget):
    _tag = "select"
    _accepts_mouse = True
    _hpolicy = QSizePolicy.Policy.Preferred
    _vpolicy = QSizePolicy.Policy.Fixed
    currentIndexChanged = pyqtSignal(int)
    currentTextChanged = pyqtSignal(str)
    activated = pyqtSignal(int)
    textActivated = pyqtSignal(str)

    def __init__(self, parent=None):
        self._items: list[list] = []   # [文字, 値]
        self._index = -1
        self._editable = False
        self._input = None
        self._datalist = None
        super().__init__(parent)

    def _build(self):
        self._el.classList.add("qcombo")
        _dom.listen(self, self._el, "change", self._on_change)

    def _on_change(self, ev):
        if self._editable:
            return
        i = int(self._el.selectedIndex)
        if i != self._index:
            self._index = i
            self.currentIndexChanged.emit(i)
            self.currentTextChanged.emit(self.currentText())
        self.activated.emit(i)
        self.textActivated.emit(self.currentText())

    def _apply_enabled(self):
        (self._input or self._el).disabled = not self._enabled

    def _focus_el(self):
        return self._input or self._el

    # ---- 項目 ----
    def addItem(self, text, data=None):
        self.insertItem(len(self._items), text, data)

    def addItems(self, texts):
        for t in texts:
            self.addItem(t)

    def insertItem(self, index, text, data=None):
        index = min(max(0, index), len(self._items))
        self._items.insert(index, [str(text), data])
        opt = _dom.el("option", "", str(text))
        if self._editable:
            self._datalist.insertBefore(opt, self._datalist.children.item(index))
        else:
            self._el.insertBefore(opt, self._el.options.item(index) if index < self._el.options.length else None)
        if self._index >= index:
            self._index += 1
        if self._index < 0 and not self._editable:
            self._index = 0
            self._el.selectedIndex = 0
            self.currentIndexChanged.emit(0)
            self.currentTextChanged.emit(self.currentText())
        elif not self._editable:
            self._el.selectedIndex = self._index

    def removeItem(self, index):
        if not 0 <= index < len(self._items):
            return
        self._items.pop(index)
        (self._datalist or self._el).children.item(index).remove()
        if self._index >= len(self._items) or self._index > index:
            self.setCurrentIndex(min(self._index - (self._index > index), len(self._items) - 1))

    def clear(self):
        changed = self._index != -1
        self._items = []
        self._index = -1
        if self._editable:
            self._datalist.innerHTML = ""
        else:
            self._el.innerHTML = ""
        if changed:
            self.currentIndexChanged.emit(-1)
            self.currentTextChanged.emit("")

    def count(self):
        return len(self._items)

    def itemText(self, i):
        return self._items[i][0] if 0 <= i < len(self._items) else ""

    def itemData(self, i, role=None):
        if role is not None and int(role) == int(Qt.ItemDataRole.ForegroundRole):
            return self._items[i][2] if 0 <= i < len(self._items) and len(self._items[i]) > 2 else None
        return self._items[i][1] if 0 <= i < len(self._items) else None

    def setItemData(self, i, data, role=None):
        if not 0 <= i < len(self._items):
            return
        # 文字の色（ForegroundRole）は選択肢の色にする（値は変えない）。ほかの役割は値として持つ
        if role is not None and int(role) == int(Qt.ItemDataRole.ForegroundRole):
            item = self._items[i]
            while len(item) < 3:
                item.append(None)
            item[2] = data
            color = data.color() if hasattr(data, "color") else data
            name = color.name() if hasattr(color, "name") else str(color or "")
            (self._datalist or self._el).children.item(i).style.color = name
            return
        self._items[i][1] = data

    def setItemText(self, i, text):
        if 0 <= i < len(self._items):
            self._items[i][0] = text
            (self._datalist or self._el).children.item(i).textContent = text

    def findData(self, data, *_a) -> int:
        for i, item in enumerate(self._items):   # 項目は [文字, 値] か [文字, 値, 文字の色]
            d = item[1]
            if d == data and (d is not None or data is None):
                return i
        return -1

    def findText(self, text, *_a) -> int:
        for i, item in enumerate(self._items):
            if item[0] == text:
                return i
        return -1

    # ---- 選んでいるもの ----
    def currentIndex(self) -> int:
        if self._editable:
            return self.findText(self.currentText())
        return self._index

    def setCurrentIndex(self, i):
        i = int(i)
        if not -1 <= i < len(self._items):
            return
        if self._editable:
            self._input.value = self._items[i][0] if i >= 0 else ""
            self.currentIndexChanged.emit(i)
            return
        self._el.selectedIndex = i
        if i != self._index:
            self._index = i
            self.currentIndexChanged.emit(i)
            self.currentTextChanged.emit(self.currentText())

    def currentText(self) -> str:
        if self._editable:
            return str(self._input.value)
        return self._items[self._index][0] if 0 <= self._index < len(self._items) else ""

    def setCurrentText(self, text):
        if self._editable:
            self._input.value = text
            return
        i = self.findText(text)
        if i >= 0:
            self.setCurrentIndex(i)

    def currentData(self, *_a):
        i = self.currentIndex()
        return self._items[i][1] if 0 <= i < len(self._items) else None

    # ---- 書き込めるプルダウン（候補は datalist で出す） ----
    def setEditable(self, on: bool):
        if bool(on) == self._editable:
            return
        self._editable = bool(on)
        if not on:
            return
        old = self._el
        wrap = _dom.el("div", "qw qcombo-edit")
        wrap.dataset.qid = old.dataset.qid
        wrap.className = old.className + " qcombo-edit"
        self._input = _dom.el("input", "qlineedit")
        self._datalist = _dom.el("datalist")
        self._datalist.id = f"dl-{self._qid}"
        self._input.setAttribute("list", self._datalist.id)
        self._input.setAttribute("autocomplete", "off")
        for item in self._items:
            self._datalist.appendChild(_dom.el("option", "", item[0]))
        wrap.appendChild(self._input)
        wrap.appendChild(self._datalist)
        if old.parentNode:
            old.parentNode.replaceChild(wrap, old)
        _dom.release(self)
        self._el = wrap
        if 0 <= self._index < len(self._items):
            self._input.value = self._items[self._index][0]
        self._index = -1
        _dom.listen(self, self._input, "input", lambda ev: self.currentTextChanged.emit(self.currentText()))
        for item in getattr(self, "_items_with_layout", []):
            pass

    def isEditable(self):
        return self._editable

    def lineEdit(self):
        return None

    def setCompleter(self, *_a):
        pass

    def completer(self):
        return None

    def setInsertPolicy(self, *_a):
        pass

    def setMaxVisibleItems(self, *_a):
        pass

    def setSizeAdjustPolicy(self, *_a):
        pass

    def setMinimumContentsLength(self, *_a):
        pass

    def view(self):
        return _ComboView()

    def showPopup(self):
        try:
            (self._input or self._el).showPicker()
        except Exception:  # noqa: BLE001
            self.setFocus()

    def hidePopup(self):
        pass


class _ComboView:
    def isVisible(self):
        return False


# ================= 枠 =================
class QFrame(QWidget):
    class Shape(enum.IntEnum):
        NoFrame = 0
        Box = 1
        Panel = 2
        WinPanel = 3
        HLine = 4
        VLine = 5
        StyledPanel = 6

    class Shadow(enum.IntEnum):
        Plain = 16
        Raised = 32
        Sunken = 48

    def setFrameShape(self, shape):
        for s in ("frame-box", "frame-hline", "frame-vline"):
            self._el.classList.remove(s)
        if shape in (QFrame.Shape.Box, QFrame.Shape.Panel, QFrame.Shape.StyledPanel, QFrame.Shape.WinPanel):
            self._el.classList.add("frame-box")
        elif shape == QFrame.Shape.HLine:
            self._el.classList.add("frame-hline")
        elif shape == QFrame.Shape.VLine:
            self._el.classList.add("frame-vline")

    def setFrameShadow(self, *_a):
        pass

    def setLineWidth(self, *_a):
        pass

    def setFrameStyle(self, *_a):
        pass

    def frameWidth(self):
        return 1


class QGroupBox(QWidget):
    def __init__(self, title="", parent=None):
        if isinstance(title, QWidget):
            title, parent = "", title
        super().__init__(parent)
        self.setTitle(title)

    def _build(self):
        self._el.classList.add("qgroup")
        self._title_el = _dom.el("div", "qgroup-title")
        self._body = _dom.el("div", "qgroup-body")
        self._el.appendChild(self._title_el)
        self._el.appendChild(self._body)

    def _host(self):
        return self._body

    def setTitle(self, title):
        self._title = title or ""
        self._title_el.textContent = self._title
        self._title_el.style.display = "" if self._title else "none"

    def title(self):
        return self._title

    def setCheckable(self, *_a):
        pass

    def setFlat(self, *_a):
        pass


class QAbstractScrollArea(QFrame):
    """スクロールする部品の共通（表示する領域 viewport とスクロールの位置）。"""
    _hpolicy = QSizePolicy.Policy.Expanding
    _vpolicy = QSizePolicy.Policy.Expanding

    def _build(self):
        self._el.classList.add("qscroll-frame")
        self._view = _dom.el("div", "qviewport")
        self._el.appendChild(self._view)
        self._viewport = _Viewport(self)

    def viewport(self):
        return self._viewport

    def verticalScrollBar(self):
        if not hasattr(self, "_vbar"):
            self._vbar = _ScrollBar(self._view, True)
            _dom.listen(self, self._view, "scroll", lambda ev: self._vbar.valueChanged.emit(self._vbar.value()))
        return self._vbar

    def horizontalScrollBar(self):
        if not hasattr(self, "_hbar"):
            self._hbar = _ScrollBar(self._view, False)
        return self._hbar

    def setVerticalScrollBarPolicy(self, policy):
        self._view.style.overflowY = {Qt.ScrollBarPolicy.ScrollBarAlwaysOff: "hidden",
                                      Qt.ScrollBarPolicy.ScrollBarAlwaysOn: "scroll"}.get(policy, "auto")

    def setHorizontalScrollBarPolicy(self, policy):
        self._view.style.overflowX = {Qt.ScrollBarPolicy.ScrollBarAlwaysOff: "hidden",
                                      Qt.ScrollBarPolicy.ScrollBarAlwaysOn: "scroll"}.get(policy, "auto")

    def setSizeAdjustPolicy(self, *_a):
        pass

    def frameWidth(self):
        return 1

    def _context_el(self):
        return self._view

    def _context_widget(self):
        return self._viewport


class _Viewport(QWidget):
    """スクロールする部品の、中身を見せる領域（押下・座標の基準）。"""

    def __init__(self, area):
        self._area_ref = weakref.ref(area)
        QWidget.__init__(self)
        # 自分の要素は作らず、スクロールの要素を使う
        _registry.pop(self._qid, None)
        self._el = area._view
        self._el.dataset.qid = str(self._qid)
        _registry[self._qid] = self

    def parentWidget(self):
        return self._area_ref()

    def isWindow(self):
        return False

    def width(self) -> int:
        return int(self._el.clientWidth)

    def height(self) -> int:
        return int(self._el.clientHeight)


class QScrollArea(QAbstractScrollArea):
    def __init__(self, parent=None):
        self._widget = None
        self._resizable = False
        super().__init__(parent)
        self._el.classList.add("qscrollarea")

    def setWidget(self, w):
        if self._widget is not None:
            self._widget._container = None
            self._widget._el.remove()
        self._widget = w
        container = w._in_layout or w._container
        if container is not None:
            container._release_child(w)
        w._container = self
        w._el.classList.remove("floating")
        QObject.setParent(w, self)
        _dom.place(self._view, w._el)
        w._sync_display()
        _visibility_changed()

    def widget(self):
        return self._widget

    def takeWidget(self):
        w = self._widget
        if w is not None:
            w._container = None
            w._el.remove()
        self._widget = None
        return w

    def _release_child(self, w):
        if w is self._widget:
            self.takeWidget()

    def _child_shown(self, w):
        return True

    def setWidgetResizable(self, on):
        self._resizable = bool(on)
        self._el.classList.toggle("resizable", self._resizable)

    def ensureWidgetVisible(self, *_a):
        pass


# ================= 入れ物（分割・タブ） =================
class QSplitter(QFrame):
    _hpolicy = QSizePolicy.Policy.Expanding
    _vpolicy = QSizePolicy.Policy.Expanding
    splitterMoved = pyqtSignal(int, int)

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        if isinstance(orientation, QWidget):
            orientation, parent = Qt.Orientation.Horizontal, orientation
        self._children_w: list = []
        self._handles: list = []
        self._grow: dict = {}       # 部品 → 伸びの比（setSizes で決める）
        self._stretch: dict = {}
        self._orientation = orientation
        super().__init__(parent)
        self._el.classList.add("qsplitter")
        self.setOrientation(orientation)

    def setOrientation(self, orientation):
        self._orientation = orientation
        horizontal = orientation == Qt.Orientation.Horizontal
        self._el.classList.toggle("h", horizontal)
        self._el.classList.toggle("v", not horizontal)

    def orientation(self):
        return self._orientation

    def setChildrenCollapsible(self, *_a):
        pass

    def setHandleWidth(self, *_a):
        pass

    def setOpaqueResize(self, *_a):
        pass

    def addWidget(self, w):
        self.insertWidget(len(self._children_w), w)

    def insertWidget(self, index, w):
        if w in self._children_w:
            self._children_w.remove(w)
        else:
            container = w._in_layout or w._container
            if container is not None:
                container._release_child(w)
        index = min(max(0, index), len(self._children_w))
        self._children_w.insert(index, w)
        w._container = self
        w._el.classList.remove("floating")
        w._el.style.left = w._el.style.top = ""
        QObject.setParent(w, self)
        self._grow.setdefault(w, 1000)
        self._render()
        w._sync_display()
        _visibility_changed()

    def _release_child(self, w):
        if w in self._children_w:
            self._children_w.remove(w)
            self._grow.pop(w, None)
            w._container = None
            w._el.remove()
            self._render()

    def _child_shown(self, w):
        return True

    def _render(self):
        # 区切りは使い回し、部品は場所が違うときだけ動かす（地図の iframe を読み込み直さないように）
        need = max(0, len(self._children_w) - 1)
        while len(self._handles) < need:
            handle = _dom.el("div", "qsplitter-handle")
            self._wire_handle(handle, len(self._handles) + 1)
            self._handles.append(handle)
        for h in self._handles[need:]:
            h.remove()
        self._handles = self._handles[:need]
        order = []
        for i, w in enumerate(self._children_w):
            if i:
                order.append(self._handles[i - 1])
            order.append(w._el)
        ref = self._el.firstChild
        for node in order:
            if not _dom.absent(ref) and node == ref:
                ref = ref.nextSibling
                continue
            _dom.place(self._el, node, None if _dom.absent(ref) else ref)
        self._apply_sizes()

    def _apply_sizes(self):
        for w in self._children_w:
            w._el.style.flex = f"{max(1, self._grow.get(w, 1000))} 1 0px"

    def _wire_handle(self, handle, i):
        horizontal = self._orientation == Qt.Orientation.Horizontal
        state = {}

        def down(ev):
            if i >= len(self._children_w):
                return
            a, b = self._children_w[i - 1], self._children_w[i]
            sa, sb = (a.width(), b.width()) if horizontal else (a.height(), b.height())
            state.update(on=True, start=ev.clientX if horizontal else ev.clientY, a=a, b=b, sa=sa, sb=sb)
            sizes = self.sizes()
            for w, s in zip(self._children_w, sizes):
                self._grow[w] = max(1, s)
            self._apply_sizes()
            ev.preventDefault()
            _dom.document.body.classList.add("dragging-" + ("h" if horizontal else "v"))

        def move(ev):
            if not state.get("on"):
                return
            d = (ev.clientX if horizontal else ev.clientY) - state["start"]
            total = state["sa"] + state["sb"]
            na = min(max(40, state["sa"] + d), total - 40)
            self._grow[state["a"]] = max(1, na)
            self._grow[state["b"]] = max(1, total - na)
            self._apply_sizes()

        def up(ev):
            if state.get("on"):
                state["on"] = False
                _dom.document.body.classList.remove("dragging-h", "dragging-v")
                self.splitterMoved.emit(0, i)

        _dom.listen(self, handle, "mousedown", down)
        _dom.listen(self, _dom.document, "mousemove", move)
        _dom.listen(self, _dom.document, "mouseup", up)

    def count(self):
        return len(self._children_w)

    def widget(self, i):
        return self._children_w[i] if 0 <= i < len(self._children_w) else None

    def indexOf(self, w):
        return self._children_w.index(w) if w in self._children_w else -1

    def sizes(self):
        horizontal = self._orientation == Qt.Orientation.Horizontal
        out = []
        for w in self._children_w:
            size = w.width() if horizontal else w.height()
            out.append(size if size or not w._hidden else 0)
        if not any(out):
            return [self._grow.get(w, 1000) for w in self._children_w]
        return out

    def setSizes(self, sizes):
        for w, s in zip(self._children_w, sizes):
            self._grow[w] = max(1, int(s))
        self._apply_sizes()

    def setStretchFactor(self, index, stretch):
        self._stretch[index] = stretch

    def handle(self, i):
        return None


class QTabBar(QWidget):
    currentChanged = pyqtSignal(int)

    def __init__(self, tabs=None):
        self._tabs = tabs
        super().__init__()

    def setTabTextColor(self, *_a):
        pass

    def setExpanding(self, *_a):
        pass

    def count(self):
        return self._tabs.count() if self._tabs else 0

    def sizeHint(self):
        """見出しを全部並べたときの幅（ページに置かれる前は 0）。"""
        if not self._tabs:
            return QSize(0, 0)
        bar = self._tabs._bar
        first, last = bar.firstElementChild, bar.lastElementChild
        if not first or not last:
            return QSize(0, 0)
        # 見出しの行は枠の幅いっぱいに広がるので、最初の見出しの左端から最後の見出しの右端までを測る
        width = last.offsetLeft + last.offsetWidth - first.offsetLeft
        return QSize(int(width), int(bar.offsetHeight))


class QTabWidget(QWidget):
    _hpolicy = QSizePolicy.Policy.Expanding
    _vpolicy = QSizePolicy.Policy.Expanding
    currentChanged = pyqtSignal(int)
    tabBarClicked = pyqtSignal(int)

    def __init__(self, parent=None):
        self._pages: list = []
        self._labels: list = []
        self._buttons: list = []
        self._current = -1
        super().__init__(parent)

    def _build(self):
        self._el.classList.add("qtabs")
        self._bar = _dom.el("div", "qtabs-bar")
        self._stack = _dom.el("div", "qtabs-stack")
        self._el.appendChild(self._bar)
        self._el.appendChild(self._stack)

    def addTab(self, w, label):
        return self.insertTab(len(self._pages), w, label)

    def insertTab(self, index, w, label):
        container = w._in_layout or w._container
        if container is not None:
            container._release_child(w)
        index = min(max(0, index), len(self._pages))
        self._pages.insert(index, w)
        self._labels.insert(index, label)
        button = _dom.el("button", "qtab", label)
        button.type = "button"
        self._buttons.insert(index, button)
        self._bar.insertBefore(button, self._bar.children.item(index) if index < self._bar.children.length else None)
        _dom.listen(self, button, "click", lambda ev, w=w: self._on_tab(w))
        w._container = self
        w._el.classList.remove("floating")
        w._el.classList.add("qtab-page")
        QObject.setParent(w, self)
        self._stack.appendChild(w._el)
        if self._current < 0:
            self._current = 0
            self._sync()
            self.currentChanged.emit(0)
        else:
            if index <= self._current and len(self._pages) > 1:
                self._current += 1
            self._sync()
        return index

    def _on_tab(self, w):
        i = self._pages.index(w)
        self.tabBarClicked.emit(i)
        self.setCurrentIndex(i)

    def _release_child(self, w):
        if w in self._pages:
            self.removeTab(self._pages.index(w))

    def removeTab(self, index):
        if not 0 <= index < len(self._pages):
            return
        w = self._pages.pop(index)
        self._labels.pop(index)
        self._buttons.pop(index).remove()
        w._container = None
        w._el.classList.remove("qtab-page")
        w._el.remove()
        if self._current >= len(self._pages):
            self._current = len(self._pages) - 1
        self._sync()
        self.currentChanged.emit(self._current)

    def _child_shown(self, w):
        return w in self._pages and self._pages.index(w) == self._current

    def _sync(self):
        for i, (w, b) in enumerate(zip(self._pages, self._buttons)):
            on = i == self._current
            b.classList.toggle("current", on)
            w._el.style.display = "" if on and not w._hidden else "none"
        _visibility_changed()

    def setCurrentIndex(self, i):
        if not 0 <= i < len(self._pages) or i == self._current:
            return
        self._current = i
        self._sync()
        self.currentChanged.emit(i)

    def setCurrentWidget(self, w):
        if w in self._pages:
            self.setCurrentIndex(self._pages.index(w))

    def currentIndex(self):
        return self._current

    def currentWidget(self):
        return self._pages[self._current] if 0 <= self._current < len(self._pages) else None

    def widget(self, i):
        return self._pages[i] if 0 <= i < len(self._pages) else None

    def count(self):
        return len(self._pages)

    def indexOf(self, w):
        return self._pages.index(w) if w in self._pages else -1

    def setTabText(self, i, text):
        if 0 <= i < len(self._pages):
            self._labels[i] = text
            self._buttons[i].textContent = text

    def tabText(self, i):
        return self._labels[i] if 0 <= i < len(self._labels) else ""

    def setTabToolTip(self, i, text):
        if 0 <= i < len(self._buttons):
            self._buttons[i].title = text

    def setTabEnabled(self, i, on):
        if 0 <= i < len(self._buttons):
            self._buttons[i].disabled = not on

    def setTabVisible(self, i, on):
        if 0 <= i < len(self._buttons):
            self._buttons[i].style.display = "" if on else "none"

    def tabBar(self):
        return QTabBar(self)

    def setDocumentMode(self, *_a):
        pass

    def setTabPosition(self, *_a):
        pass

    def setUsesScrollButtons(self, *_a):
        pass

    def setElideMode(self, *_a):
        pass

    def setMovable(self, *_a):
        pass

    def setTabsClosable(self, *_a):
        pass

    def setCornerWidget(self, w, *_a):
        container = w._in_layout or w._container
        if container is not None:
            container._release_child(w)
        corner = _dom.el("div", "qtabs-corner")
        corner.appendChild(w._el)
        self._bar.appendChild(corner)
        w._container = self


class QStackedWidget(QWidget):
    currentChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        self._pages: list = []
        self._current = -1
        super().__init__(parent)

    def addWidget(self, w):
        container = w._in_layout or w._container
        if container is not None:
            container._release_child(w)
        self._pages.append(w)
        w._container = self
        QObject.setParent(w, self)
        self._el.appendChild(w._el)
        if self._current < 0:
            self._current = 0
        self._sync()
        return len(self._pages) - 1

    def _release_child(self, w):
        if w in self._pages:
            self._pages.remove(w)
            w._container = None
            w._el.remove()
            self._sync()

    def _child_shown(self, w):
        return w in self._pages and self._pages.index(w) == self._current

    def _sync(self):
        for i, w in enumerate(self._pages):
            w._el.style.display = "" if i == self._current and not w._hidden else "none"
        _visibility_changed()

    def setCurrentIndex(self, i):
        if 0 <= i < len(self._pages) and i != self._current:
            self._current = i
            self._sync()
            self.currentChanged.emit(i)

    def setCurrentWidget(self, w):
        if w in self._pages:
            self.setCurrentIndex(self._pages.index(w))

    def currentIndex(self):
        return self._current

    def currentWidget(self):
        return self._pages[self._current] if 0 <= self._current < len(self._pages) else None

    def count(self):
        return len(self._pages)

    def widget(self, i):
        return self._pages[i] if 0 <= i < len(self._pages) else None


# ================= 説明欄（HTML を出す） =================
class _TextDocument:
    def __init__(self, browser):
        self._b = weakref.ref(browser)

    def setTextWidth(self, *_a):
        pass

    def size(self):
        from ._core import QSizeF
        b = self._b()
        return QSizeF(b._content.scrollWidth, b._content.scrollHeight) if b else QSizeF()

    def setDefaultStyleSheet(self, css):
        b = self._b()
        if b is not None:
            b._doc_style.textContent = css

    def toPlainText(self):
        b = self._b()
        return str(b._content.textContent) if b else ""

    def setDocumentMargin(self, *_a):
        pass

    def isEmpty(self):
        return not self.toPlainText()


class QTextBrowser(QAbstractScrollArea):
    _accepts_mouse = True
    anchorClicked = pyqtSignal(object)
    textChanged = pyqtSignal()

    def __init__(self, parent=None):
        self._open_links = True
        self._open_external = False
        super().__init__(parent)

    def _build(self):
        QAbstractScrollArea._build(self)
        self._el.classList.add("qtextbrowser")
        self._doc_style = _dom.el("style")
        self._content = _dom.el("div", "qtext-content")
        self._view.appendChild(self._doc_style)
        self._view.appendChild(self._content)
        self._document = _TextDocument(self)
        _dom.listen(self, self._view, "click", self._on_click)

    def _on_click(self, ev):
        a = ev.target.closest("a") if hasattr(ev.target, "closest") else None
        if _dom.absent(a):
            return
        href = str(a.getAttribute("href") or "")
        if not href:
            return
        ev.preventDefault()
        if self._open_external and href.startswith(("http://", "https://")):
            _dom.window.open(href, "_blank", "noopener")
        self.anchorClicked.emit(_url(href))

    def setHtml(self, html):
        self._content.innerHTML = html or ""
        self.textChanged.emit()

    def setText(self, text):
        self.setHtml(text)

    def setPlainText(self, text):
        self._content.textContent = text or ""
        self.textChanged.emit()

    def toPlainText(self):
        return str(self._content.textContent)

    def toHtml(self):
        return str(self._content.innerHTML)

    def append(self, html):
        self._content.insertAdjacentHTML("beforeend", f"<div>{html}</div>")

    def clear(self):
        self.setHtml("")

    def setOpenLinks(self, on):
        self._open_links = bool(on)

    def setOpenExternalLinks(self, on):
        self._open_external = bool(on)

    def setReadOnly(self, *_a):
        pass

    def document(self):
        return self._document

    def setSearchPaths(self, *_a):
        pass

    def scrollToAnchor(self, *_a):
        pass

    def setLineWrapMode(self, *_a):
        pass


QTextEdit = QTextBrowser


def _url(text):
    from ._core import QUrl
    return QUrl(text)


# ================= 進み具合など =================
class QProgressBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0

    def setRange(self, *_a):
        pass

    def setValue(self, v):
        self._value = v

    def setTextVisible(self, *_a):
        pass


class QSlider(QWidget):
    valueChanged = pyqtSignal(int)


class QRubberBand(QWidget):
    pass


class QSizeGrip(QWidget):
    pass
