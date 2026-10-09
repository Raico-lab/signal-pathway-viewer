"""色・フォント・キー・ショートカット・操作（QAction）・外部のリンク。"""
import enum
import re

from . import _dom
from ._core import QEvent, QKeyEvent, QObject, Qt, pyqtSignal

BASE_FONT_PX = 13
FONT_FAMILY = ('-apple-system, BlinkMacSystemFont, "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Yu Gothic UI", '
               '"Meiryo UI", "Segoe UI", sans-serif')

_NAMED = {"black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000", "blue": "#0000ff",
          "gray": "#808080", "grey": "#808080", "transparent": "#000000"}


class QColor:
    def __init__(self, *args):
        self._valid = True
        if not args:
            self._rgb = (0, 0, 0)
            self._valid = False
        elif len(args) >= 3:
            self._rgb = tuple(int(a) for a in args[:3])
        elif isinstance(args[0], QColor):
            self._rgb, self._valid = args[0]._rgb, args[0]._valid
        else:
            self._rgb = self._parse(str(args[0]))

    def _parse(self, text: str):
        text = _NAMED.get(text.strip().lower(), text.strip())
        m = re.fullmatch(r"#?([0-9a-fA-F]{6})", text)
        if m:
            h = m.group(1)
            return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        m = re.fullmatch(r"#?([0-9a-fA-F]{3})", text)
        if m:
            h = m.group(1)
            return tuple(int(c * 2, 16) for c in h)
        self._valid = False
        return (0, 0, 0)

    def isValid(self) -> bool:
        return self._valid

    def name(self) -> str:
        return "#%02x%02x%02x" % self._rgb

    def red(self):
        return self._rgb[0]

    def green(self):
        return self._rgb[1]

    def blue(self):
        return self._rgb[2]

    def lightness(self) -> int:
        return (max(self._rgb) + min(self._rgb)) // 2

    def color(self):
        return self


class QBrush:
    def __init__(self, color=None):
        self._color = QColor(color) if color is not None else QColor()

    def color(self):
        return self._color


class QPalette:
    class ColorRole(enum.IntEnum):
        Window = 10
        Base = 9
        Text = 6

    def base(self):
        return QBrush(QColor("#ffffff"))

    def window(self):
        return QBrush(QColor("#f0f0f0"))

    def text(self):
        return QBrush(QColor("#000000"))

    def color(self, *_a):
        return QColor("#ffffff")


class QFont:
    def __init__(self, other=None, *_a):
        self._px = BASE_FONT_PX
        self._bold = False
        if isinstance(other, QFont):
            self._px, self._bold = other._px, other._bold

    def setBold(self, on: bool):
        self._bold = bool(on)

    def bold(self) -> bool:
        return self._bold

    def setPointSize(self, pt):
        self._px = int(pt)

    def pointSize(self):
        return self._px

    def setPixelSize(self, px):
        self._px = int(px)

    def css(self) -> str:
        return f"{'bold ' if self._bold else ''}{self._px}px {FONT_FAMILY}"


class QFontMetrics:
    def __init__(self, font=None):
        self._font = font if isinstance(font, QFont) else QFont()

    def horizontalAdvance(self, text: str) -> int:
        return int(round(_dom.text_width(str(text), self._font.css())))

    width = horizontalAdvance

    def height(self) -> int:
        return int(self._font._px * 1.35)

    def lineSpacing(self) -> int:
        return self.height()

    def elidedText(self, text: str, mode, width: int, *_a) -> str:
        if self.horizontalAdvance(text) <= width:
            return text
        ell = "…"
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.horizontalAdvance(text[:mid] + ell) <= width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + ell


class QIcon:
    def __init__(self, *_a):
        pass


class QCursor:
    def __init__(self, *_a):
        pass

    @staticmethod
    def pos():
        from ._core import QPoint
        return QPoint(_LAST_POS[0], _LAST_POS[1])


_LAST_POS = [0, 0]
_LAST_MODS = [0]   # 最後に一覧を押したときの Shift・⌘（QApplication.keyboardModifiers が返す）


class QGuiApplication:
    @staticmethod
    def applicationState():
        return Qt.ApplicationState.ApplicationActive

    @staticmethod
    def focusWindow():
        return None


class QDesktopServices:
    @staticmethod
    def openUrl(url) -> bool:
        text = url.toString() if hasattr(url, "toString") else str(url)
        if text.startswith("file:"):
            _dom.window.alert("ブラウザ版では、データはこのブラウザの中に保存しています（フォルダとしては開けません）。")
            return False
        _dom.window.open(text, "_blank", "noopener")
        return True


# ================= キー =================
_IS_MAC = "Mac" in str(_dom.window.navigator.platform)


class QKeySequence:
    class StandardKey(enum.IntEnum):
        Back = 1
        Forward = 2
        Copy = 3

    class SequenceFormat(enum.IntEnum):
        NativeText = 0
        PortableText = 1

    _TEXT = {StandardKey.Back: ("⌘[", "Alt+Left"), StandardKey.Forward: ("⌘]", "Alt+Right"),
             StandardKey.Copy: ("⌘C", "Ctrl+C")}

    def __init__(self, key=None):
        self._key = key

    def toString(self, *_a) -> str:
        pair = self._TEXT.get(self._key)
        if pair:
            return pair[0] if _IS_MAC else pair[1]
        return str(self._key or "")

    def matches_dom(self, ev) -> bool:
        key = str(ev.key)
        meta, alt = bool(ev.metaKey), bool(ev.altKey)
        if self._key == QKeySequence.StandardKey.Back:
            return (meta and key == "[") or (alt and key == "ArrowLeft")
        if self._key == QKeySequence.StandardKey.Forward:
            return (meta and key == "]") or (alt and key == "ArrowRight")
        return False


_DOM_KEYS = {"Escape": Qt.Key.Key_Escape, "ArrowDown": Qt.Key.Key_Down, "ArrowUp": Qt.Key.Key_Up,
             "ArrowLeft": Qt.Key.Key_Left, "ArrowRight": Qt.Key.Key_Right, "Enter": Qt.Key.Key_Return,
             "Tab": Qt.Key.Key_Tab, "Backspace": Qt.Key.Key_Backspace, "Delete": Qt.Key.Key_Delete,
             "PageUp": Qt.Key.Key_PageUp, "PageDown": Qt.Key.Key_PageDown}


def key_event(ev) -> QKeyEvent:
    """ブラウザのキーの押下を QKeyEvent にする。"""
    key = str(ev.key)
    code = _DOM_KEYS.get(key)
    if code is None:
        code = ord(key.upper()) if len(key) == 1 else Qt.Key.Key_Other
    mods = Qt.KeyboardModifier.NoModifier
    if ev.shiftKey:
        mods |= Qt.KeyboardModifier.ShiftModifier
    if ev.ctrlKey:
        mods |= Qt.KeyboardModifier.ControlModifier
    if ev.altKey:
        mods |= Qt.KeyboardModifier.AltModifier
    if ev.metaKey:
        mods |= Qt.KeyboardModifier.MetaModifier
    return QKeyEvent(QEvent.Type.KeyPress, code, mods, key if len(key) == 1 else "")


class QShortcut(QObject):
    """キーの組み合わせ（戻る・進むなど）。parent の部品が見えている間だけ働く。"""
    activated = pyqtSignal()

    def __init__(self, sequence, parent=None, *_a):
        super().__init__(parent)
        self._seq = sequence if isinstance(sequence, QKeySequence) else QKeySequence(sequence)
        self._widget = parent
        _dom.listen(self, _dom.document, "keydown", self._on_key)

    def setContext(self, *_a):
        pass

    def _on_key(self, ev):
        if self._deleted or not self._seq.matches_dom(ev):
            return
        w = self._widget
        if w is not None and hasattr(w, "isVisible") and not w.isVisible():
            return
        # 入力欄で文字を打っているときの ⌥← などは、入力欄の操作に任せる
        tag = str(getattr(ev.target, "tagName", "") or "").upper()
        if tag in ("INPUT", "TEXTAREA") and not ev.metaKey:
            return
        ev.preventDefault()
        try:
            self.activated.emit()
        except Exception:  # noqa: BLE001
            _dom.report()


class QAction(QObject):
    triggered = pyqtSignal(bool)

    def __init__(self, *args):
        text, parent = "", None
        for a in args:
            if isinstance(a, str):
                text = a
            elif isinstance(a, QObject):
                parent = a
        super().__init__(parent)
        self._text = text
        self._enabled = True
        self._visible = True
        self._menu_item = None   # メニューに並べた要素（有効・無効を反映する）

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text
        if self._menu_item is not None:
            self._menu_item.textContent = text

    def setEnabled(self, on: bool):
        self._enabled = bool(on)
        if self._menu_item is not None:
            self._menu_item.classList.toggle("disabled", not self._enabled)
        return self

    def isEnabled(self):
        return self._enabled

    def setVisible(self, on):
        self._visible = bool(on)

    def setShortcut(self, *_a):
        pass

    def setToolTip(self, *_a):
        pass

    def trigger(self):
        if self._enabled:
            self.triggered.emit(False)
