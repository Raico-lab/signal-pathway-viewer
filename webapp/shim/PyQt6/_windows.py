"""窓まわり: メインの窓・メニュー・状態の表示・ダイアログ・確認の小窓・アプリ（QApplication）。"""
import enum
import os
import weakref

import js

from . import _dom
from ._core import QCoreApplication, QEvent, QMouseEvent, QObject, QPoint, QPointF, Qt, _filtered, pyqtSignal
from ._gui import QAction, QColor, QPalette, _LAST_MODS, _LAST_POS
from ._widgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget, _next_z, _registry,
                       _visibility_changed, widget_of)


# ================= メニュー =================
class QMenu(QWidget):
    aboutToShow = pyqtSignal()
    aboutToHide = pyqtSignal()
    triggered = pyqtSignal(object)

    def __init__(self, *args):
        title, parent = "", None
        for a in args:
            if isinstance(a, str):
                title = a
            elif isinstance(a, QWidget):
                parent = a
        self._actions: list = []
        self._menu_title = title
        self._opener = None
        self._outside = None
        super().__init__(parent, Qt.WindowType.Popup)
        self._window_flag = True
        self._el.classList.add("qmenu")

    def title(self):
        return self._menu_title

    def addAction(self, *args):
        if len(args) == 1 and isinstance(args[0], QAction):
            action = args[0]
        else:
            text = next((a for a in args if isinstance(a, str)), "")
            action = QAction(text, self)
            slot = next((a for a in args if callable(a) and not isinstance(a, str)), None)
            if slot is not None:
                action.triggered.connect(slot)
        self._actions.append(action)
        self._render()
        return action

    def addSeparator(self):
        sep = QAction("", self)
        sep._separator = True
        self._actions.append(sep)
        self._render()
        return sep

    def addMenu(self, menu_or_title):
        menu = QMenu(menu_or_title, self) if isinstance(menu_or_title, str) else menu_or_title
        action = QAction(menu.title(), self)
        action.triggered.connect(lambda: None)
        self._actions.append(action)
        self._render()
        return menu

    def actions(self):
        return list(self._actions)

    def clear(self):
        self._actions = []
        self._render()

    def isEmpty(self):
        return not self._actions

    def _render(self):
        self._el.innerHTML = ""
        for action in self._actions:
            if getattr(action, "_separator", False):
                self._el.appendChild(_dom.el("div", "qmenu-sep"))
                continue
            widget = getattr(action, "_default_widget", None)
            if widget is not None:
                holder = _dom.el("div", "qmenu-widget")
                holder.appendChild(widget._el)
                self._el.appendChild(holder)
                continue
            item = _dom.el("div", "qmenu-item" + ("" if action.isEnabled() else " disabled"), action.text())
            action._menu_item = item
            _dom.listen(self, item, "click", lambda ev, a=action: self._activate(a))
            self._el.appendChild(item)

    def _activate(self, action):
        if not action.isEnabled():
            return
        self.hide()
        action.trigger()
        self.triggered.emit(action)

    def popup(self, pos, *_a):
        self.aboutToShow.emit()
        self._render()
        self._hidden = False
        self._top_shown = True
        el = self._el
        el.style.display = ""
        el.style.position = "fixed"
        el.style.zIndex = str(_next_z() + 5000)
        _dom.document.body.appendChild(el)
        vw, vh = int(_dom.window.innerWidth), int(_dom.window.innerHeight)
        x, y = pos.x(), pos.y()
        el.style.left = f"{x}px"
        el.style.top = f"{y}px"
        _, _, w, h = _dom.rect(el)
        el.style.left = f"{max(0, min(x, vw - w - 4))}px"
        el.style.top = f"{max(0, min(y, vh - h - 4))}px"
        from pyodide.ffi import create_proxy

        def outside(ev):
            if not el.contains(ev.target):
                opener = self._opener() if self._opener else None
                if opener is not None and opener._el.contains(ev.target):
                    return   # 開いたボタンを押したときは、ボタンの側で閉じる
                self.hide()

        if self._outside is None:
            self._outside = create_proxy(outside)
            _dom.later(0, lambda: _dom.document.addEventListener("mousedown", self._outside, True))
        _visibility_changed()

    def exec(self, pos=None, *_a):
        self.popup(pos or QPoint(*_LAST_POS))
        return None

    exec_ = exec

    def _show_window(self):
        self.popup(QPoint(*_LAST_POS))

    def hide(self):
        if not self._top_shown:
            return
        self._top_shown = False
        self._hidden = True
        self._el.remove()
        if self._outside is not None:
            _dom.document.removeEventListener("mousedown", self._outside, True)
            self._outside.destroy()
            self._outside = None
        self.aboutToHide.emit()
        _visibility_changed()

    def isVisible(self):
        return self._top_shown

    def parentWidget(self):
        opener = self._opener() if self._opener else None
        return opener or QWidget.parentWidget(self)


class QWidgetAction(QAction):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._default_widget = None

    def setDefaultWidget(self, w):
        self._default_widget = w
        w._container = None
        w._el.classList.remove("floating")
        w._sync_display()

    def defaultWidget(self):
        return self._default_widget


class QMenuBar(QWidget):
    def _build(self):
        self._el.classList.add("qmenubar")

    def addMenu(self, title):
        menu = title if isinstance(title, QMenu) else QMenu(title, self)
        button = _dom.el("button", "qmenubar-item", menu.title())
        button.type = "button"
        holder = _ButtonRef(button)
        menu._opener = weakref.ref(holder)

        def toggle(ev):
            if menu.isVisible():
                menu.hide()
                return
            x, y, w, h = _dom.rect(button)
            menu.popup(QPoint(round(x), round(y + h)))

        _dom.listen(self, button, "click", toggle)
        self._el.appendChild(button)
        return menu


class _ButtonRef:
    def __init__(self, el):
        self._el = el


class QStatusBar(QWidget):
    messageChanged = pyqtSignal(str)

    def _build(self):
        self._el.classList.add("qstatusbar")
        self._msg = _dom.el("div", "qstatus-msg")
        self._perm = _dom.el("div", "qstatus-perm")
        self._el.appendChild(self._msg)
        self._el.appendChild(self._perm)
        self._timer = None

    def showMessage(self, text, timeout=0):
        text = _downloads(text or "")
        self._msg.textContent = text
        self._msg.title = text or ""
        if self._timer is not None:
            _dom.window.clearTimeout(self._timer)
            self._timer = None
        if timeout:
            self._timer = _dom.later(timeout, self.clearMessage)
        self.messageChanged.emit(text or "")

    def clearMessage(self):
        self._timer = None
        self._msg.textContent = ""
        self.messageChanged.emit("")

    def currentMessage(self):
        return str(self._msg.textContent)

    def addPermanentWidget(self, w, stretch=0):
        w._container = self
        w._el.classList.remove("floating")
        QObject.setParent(w, self)
        self._perm.appendChild(w._el)

    def addWidget(self, w, stretch=0):
        w._container = self
        QObject.setParent(w, self)
        self._el.insertBefore(w._el, self._perm)

    def _release_child(self, w):
        w._container = None
        w._el.remove()

    def _child_shown(self, w):
        return True


class QMainWindow(QWidget):
    """アプリの窓（ページ全体）: メニューの帯・中身・状態の表示。"""

    def __init__(self, parent=None, *_a):
        super().__init__(parent)
        self._window_flag = True
        self._central = None
        self._menubar = None
        self._statusbar = None

    def _build(self):
        self._el.classList.add("qmainwindow")
        self._bar_host = _dom.el("div", "qmain-menubar")
        self._center = _dom.el("div", "qmain-center")
        self._status_host = _dom.el("div", "qmain-status")
        for e in (self._bar_host, self._center, self._status_host):
            self._el.appendChild(e)

    def setCentralWidget(self, w):
        container = w._in_layout or w._container
        if container is not None:
            container._release_child(w)
        self._central = w
        w._container = self
        w._el.classList.remove("floating")
        QObject.setParent(w, self)
        self._center.appendChild(w._el)
        w._sync_display()

    def centralWidget(self):
        return self._central

    def _release_child(self, w):
        w._container = None
        w._el.remove()

    def _child_shown(self, w):
        return True

    def menuBar(self):
        if self._menubar is None:
            self._menubar = QMenuBar()
            self._menubar._container = self
            QObject.setParent(self._menubar, self)
            self._bar_host.appendChild(self._menubar._el)
        return self._menubar

    def statusBar(self):
        if self._statusbar is None:
            self._statusbar = QStatusBar()
            self._statusbar._container = self
            QObject.setParent(self._statusbar, self)
            self._status_host.appendChild(self._statusbar._el)
        return self._statusbar

    def setWindowTitle(self, title):
        QWidget.setWindowTitle(self, title)
        _dom.document.title = title

    def _open_overlay(self):
        root = _dom.document.getElementById("app")
        if _dom.absent(root):
            root = _dom.el("div")
            root.id = "app"
            _dom.document.body.appendChild(root)
        root.appendChild(self._el)

    def _hide_overlay(self):
        pass

    def resize(self, *_a):
        pass

    def addToolBar(self, *_a):
        return None

    def setStatusBar(self, bar):
        pass


# ================= ダイアログ =================
class QDialog(QWidget):
    accepted = pyqtSignal()
    rejected = pyqtSignal()
    finished = pyqtSignal(int)

    class DialogCode(enum.IntEnum):
        Rejected = 0
        Accepted = 1

    def __init__(self, parent=None, *_a):
        super().__init__(parent, Qt.WindowType.Dialog)
        self._window_flag = True
        self._result = 0
        self._backdrop = None

    def _open_overlay(self):
        self._backdrop = _dom.el("div", "qmodal-backdrop")
        self._backdrop.style.zIndex = str(_next_z())
        _dom.document.body.appendChild(self._backdrop)
        QWidget._open_overlay(self)
        frame = self._overlay
        frame.classList.add("qdialog")
        frame.style.height = "auto"
        size = getattr(self, "_win_size", None)
        frame.style.width = f"{size[0]}px" if size else "auto"
        frame.style.minWidth = self._el.style.minWidth or "320px"
        _dom.later(0, self._center_frame)

    def _center_frame(self):
        if self._overlay is None:
            return
        vw, vh = int(_dom.window.innerWidth), int(_dom.window.innerHeight)
        _, _, w, h = _dom.rect(self._overlay)
        self._overlay.style.left = f"{max(10, (vw - w) // 2)}px"
        self._overlay.style.top = f"{max(10, (vh - h) // 3)}px"

    def _hide_overlay(self):
        QWidget._hide_overlay(self)
        if self._backdrop is not None:
            self._backdrop.remove()
            self._backdrop = None

    def open(self):
        self.show()

    def exec(self):
        # ブラウザでは画面を止めて待てないので、開くだけにする（結果は accepted・finished で受け取る）
        print("QDialog.exec() はブラウザ版では待てません。open() と accepted を使ってください")
        self.show()
        return 0

    exec_ = exec

    def accept(self):
        self.done(1)

    def reject(self):
        self.done(0)

    def done(self, result):
        self._result = int(result)
        self.hide()
        (self.accepted if self._result else self.rejected).emit()
        self.finished.emit(self._result)
        if Qt.WidgetAttribute.WA_DeleteOnClose in self._attrs:
            self.deleteLater()

    def close(self):
        if self._top_shown:
            self.reject()
        return True

    def result(self):
        return self._result

    def setModal(self, *_a):
        pass

    def setSizeGripEnabled(self, *_a):
        pass


class QDialogButtonBox(QWidget):
    accepted = pyqtSignal()
    rejected = pyqtSignal()
    clicked = pyqtSignal(object)

    class StandardButton(enum.IntFlag):
        NoButton = 0
        Ok = 0x400
        Save = 0x800
        Cancel = 0x400000
        Close = 0x200000
        Yes = 0x4000
        No = 0x10000

    _LABELS = [(StandardButton.Ok, "OK", True), (StandardButton.Save, "保存", True), (StandardButton.Yes, "はい", True),
               (StandardButton.No, "いいえ", False), (StandardButton.Cancel, "キャンセル", False),
               (StandardButton.Close, "閉じる", False)]

    def __init__(self, buttons=None, parent=None):
        if isinstance(buttons, QWidget):
            buttons, parent = None, buttons
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addStretch(1)
        self._buttons = {}
        for flag, label, accept in self._LABELS:
            if buttons is not None and int(buttons) & int(flag):
                b = QPushButton(label)
                b.clicked.connect(lambda _=False, a=accept, b=b: (self.clicked.emit(b),
                                                                  (self.accepted if a else self.rejected).emit()))
                layout.addWidget(b)
                self._buttons[flag] = b

    def button(self, which):
        return self._buttons.get(which)

    def addButton(self, *args):
        text = next((a for a in args if isinstance(a, str)), "")
        b = QPushButton(text)
        self.layout().addWidget(b)
        return b


# ================= 確認の小窓 =================
class QMessageBox(QWidget):
    class StandardButton(enum.IntFlag):
        NoButton = 0
        Ok = 0x400
        Save = 0x800
        Yes = 0x4000
        No = 0x10000
        Cancel = 0x400000
        Close = 0x200000

    class ButtonRole(enum.IntEnum):
        InvalidRole = -1
        AcceptRole = 0
        RejectRole = 1
        DestructiveRole = 2
        ActionRole = 3
        HelpRole = 4
        YesRole = 5
        NoRole = 6

    class Icon(enum.IntEnum):
        NoIcon = 0
        Information = 1
        Warning = 2
        Critical = 3
        Question = 4

    def __init__(self, icon=None, title="", text="", *args, parent=None, **_kw):
        super().__init__(None)
        self._title, self._text, self._info = title, text, ""
        self._buttons: list = []
        self._clicked = None

    def setText(self, text):
        self._text = text

    def setInformativeText(self, text):
        self._info = text

    def setWindowTitle(self, title):
        self._title = title

    def setIcon(self, *_a):
        pass

    def setDetailedText(self, *_a):
        pass

    def addButton(self, text, role=None):
        b = _MsgButton(text, role)
        self._buttons.append(b)
        return b

    def setDefaultButton(self, *_a):
        pass

    def setStandardButtons(self, buttons):
        pass

    def clickedButton(self):
        return self._clicked

    def exec(self):
        message = _plain(self._text) + (f"\n\n{_plain(self._info)}" if self._info else "")
        accept = next((b for b in self._buttons if b.role in (QMessageBox.ButtonRole.AcceptRole,
                                                              QMessageBox.ButtonRole.YesRole)), None)
        reject = next((b for b in self._buttons if b.role in (QMessageBox.ButtonRole.RejectRole,
                                                              QMessageBox.ButtonRole.NoRole)), None)
        if accept is not None and reject is not None:
            self._clicked = accept if _dom.window.confirm(message) else reject
        else:
            _dom.window.alert(message)
            self._clicked = accept or (self._buttons[0] if self._buttons else None)
        return 0

    exec_ = exec

    @staticmethod
    def question(parent, title, text, buttons=None, default=None):
        ok = _dom.window.confirm(_plain(text))
        return QMessageBox.StandardButton.Yes if ok else QMessageBox.StandardButton.No

    @staticmethod
    def information(parent, title, text, *_a):
        _show_info(title, text)
        return QMessageBox.StandardButton.Ok

    @staticmethod
    def warning(parent, title, text, *_a):
        _dom.window.alert(_plain(text))
        return QMessageBox.StandardButton.Ok

    @staticmethod
    def critical(parent, title, text, *_a):
        _dom.window.alert(_plain(text))
        return QMessageBox.StandardButton.Ok

    @staticmethod
    def about(parent, title, text):
        _show_info(title, text)


class _MsgButton:
    def __init__(self, text, role):
        self.text, self.role = text, role


def _downloads(text: str) -> str:
    """保存の知らせの「/downloads/名前」は、ブラウザのダウンロードに入るので名前だけにする。"""
    return text.replace("/downloads/", "ダウンロードフォルダの ")


def _plain(text) -> str:
    import html
    import re
    text = _downloads(str(text or ""))
    if "<" in text and ">" in text:
        text = re.sub(r"<br\s*/?>|</p>|</h\d>|</li>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
    return text.strip()


def _show_info(title, text):
    """お知らせ: HTML の長い説明（使い方など）は読みやすい小窓で、短い文は alert で出す。"""
    text = str(text or "")
    if "<" not in text or len(text) < 200:
        _dom.window.alert(_plain(text))
        return
    from ._widgets import QTextBrowser
    dialog = QDialog()
    dialog.setWindowTitle(title)
    dialog.resize(720, 560)
    layout = QVBoxLayout(dialog)
    view = QTextBrowser()
    view.setOpenExternalLinks(True)
    view.setHtml(text)
    view.setMinimumHeight(420)
    layout.addWidget(view)
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    box.rejected.connect(dialog.reject)
    layout.addWidget(box)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.open()


class QFileDialog(QDialog):
    """保存先を選ぶ代わりに、ブラウザのダウンロードとして保存する（boot.js が /downloads を見張る）。"""

    @staticmethod
    def getSaveFileName(parent=None, caption="", directory="", filter="", *_a, **_kw):
        name = os.path.basename(str(directory or "")) or "download"
        os.makedirs("/downloads", exist_ok=True)
        return f"/downloads/{name}", filter

    @staticmethod
    def getOpenFileName(*_a, **_kw):
        return "", ""

    @staticmethod
    def getExistingDirectory(*_a, **_kw):
        return ""


class QColorDialog(QDialog):
    @staticmethod
    def getColor(initial=None, parent=None, title="", *_a):
        start = initial.name() if isinstance(initial, QColor) else "#888888"
        text = _dom.window.prompt(f"{title or '色'}（#rrggbb）", start)
        if _dom.absent(text):
            return QColor()
        return QColor(str(text))


class QInputDialog(QDialog):
    @staticmethod
    def getText(parent, title, label, *_a, **_kw):
        text = _dom.window.prompt(label, "")
        if _dom.absent(text):
            return "", False
        return str(text), True


# ================= アプリ =================
class QApplication(QCoreApplication):
    """1 つだけのアプリ。押下をイベントフィルタと部品の mousePressEvent に渡す。"""

    def __init__(self, argv=None):
        super().__init__()
        QCoreApplication._instance = self
        self._cursor_stack = []
        _dom.listen(self, _dom.document, "mousedown", self._on_mouse_down, True)
        _dom.listen(self, _dom.document, "mousemove", self._on_mouse_move, True, True)

    @staticmethod
    def instance():
        return QCoreApplication._instance

    def _on_mouse_move(self, ev):
        _LAST_POS[0], _LAST_POS[1] = int(ev.clientX), int(ev.clientY)

    def _on_mouse_down(self, ev):
        _LAST_POS[0], _LAST_POS[1] = int(ev.clientX), int(ev.clientY)
        target = widget_of(ev.target)
        button = {0: Qt.MouseButton.LeftButton, 1: Qt.MouseButton.MiddleButton,
                  2: Qt.MouseButton.RightButton}.get(int(ev.button), Qt.MouseButton.LeftButton)
        gpos = QPointF(ev.clientX, ev.clientY)

        def make(w):
            local = w.mapFromGlobal(gpos.toPoint()) if w is not None else QPoint()
            return QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(local), gpos, button, button)

        try:
            # アプリ全体のイベントフィルタ（押した部品を渡す）
            event = make(target)
            if _filtered(self, event) if target is None else self._app_filtered(target, event):
                ev.preventDefault()
                ev.stopPropagation()
                return
            # 押した部品から親へ: 受け止める部品（ボタン・入力欄など）か、mousePressEvent で受けたところで止める
            w = target
            while w is not None:
                event = make(w)
                if _filtered(w, event):
                    ev.preventDefault()
                    return
                event.accept()
                w.mousePressEvent(event)
                if event.isAccepted():
                    break
                if w.isWindow():
                    break
                w = w.parentWidget()
        except Exception:  # noqa: BLE001
            _dom.report()

    def _app_filtered(self, target, event) -> bool:
        for f in list(self._filters):
            if getattr(f, "_deleted", False):
                continue
            try:
                if f.eventFilter(target, event):
                    return True
            except Exception:  # noqa: BLE001
                _dom.report()
        return False

    # ---- Qt の静的な問い合わせ ----
    @staticmethod
    def widgetAt(*args):
        p = args[0] if len(args) == 1 else QPoint(*args)
        e = _dom.document.elementFromPoint(p.x(), p.y())
        return widget_of(e) if not _dom.absent(e) else None

    @staticmethod
    def activePopupWidget():
        return None

    @staticmethod
    def activeModalWidget():
        return None

    @staticmethod
    def activeWindow():
        return None

    @staticmethod
    def focusWidget():
        e = _dom.document.activeElement
        return widget_of(e) if not _dom.absent(e) else None

    @staticmethod
    def mouseButtons():
        return Qt.MouseButton.NoButton

    @staticmethod
    def keyboardModifiers():
        return Qt.KeyboardModifier(_LAST_MODS[0])   # 一覧を押したときのもの（⌘ は Qt と同じく Control）

    @staticmethod
    def palette():
        return QPalette()

    @staticmethod
    def setOverrideCursor(*_a):
        _dom.document.body.classList.add("busy")

    @staticmethod
    def restoreOverrideCursor():
        _dom.document.body.classList.remove("busy")

    @staticmethod
    def sendEvent(target, event):
        if event.type() == QEvent.Type.MouseButtonRelease and hasattr(target, "click"):
            target.click()
        return True

    @staticmethod
    def postEvent(*_a):
        pass

    @staticmethod
    def clipboard():
        return _Clipboard()

    @staticmethod
    def styleHints():
        return _StyleHints()

    @staticmethod
    def style():
        from ._widgets import _Style
        return _Style()

    def setStyle(self, *_a):
        pass

    def setPalette(self, *_a):
        pass

    def setFont(self, *_a):
        pass

    def setQuitOnLastWindowClosed(self, *_a):
        pass

    def exec(self):
        return 0

    exec_ = exec

    def quit(self):
        pass

    @staticmethod
    def topLevelWidgets():
        return [w for w in _registry.values() if w.isWindow()]

    @staticmethod
    def allWidgets():
        return list(_registry.values())

    @staticmethod
    def screens():
        return []

    @staticmethod
    def primaryScreen():
        return None

    @staticmethod
    def beep():
        pass


QGuiApplicationInstance = QApplication


class _Clipboard:
    def setText(self, text):
        try:
            _dom.window.navigator.clipboard.writeText(text)
        except Exception:  # noqa: BLE001
            pass

    def text(self):
        return ""


class _StyleHints:
    def setColorScheme(self, *_a):
        pass

    def colorScheme(self):
        return Qt.ColorScheme.Light
