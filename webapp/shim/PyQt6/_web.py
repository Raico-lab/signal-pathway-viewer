"""地図を表示する iframe（QWebEngineView）と、地図のページとのやりとり（QWebChannel）。

地図のページ（tor_app/web/network.html）は表示枠ごとに iframe で開く。ページの qwebchannel.js（webapp/qwebchannel.js）は、
iframe の要素に付けた pvChannel から Python の bridge を受け取る。Qt と同じく、やりとりはどちらの向きも後回しで届ける
（JavaScript の実行・bridge の呼び出しは、それぞれ setTimeout の順に）。
"""
import js
from pyodide.ffi import JsProxy, create_proxy, to_js

from . import _dom
from ._core import QObject, QUrl, pyqtSignal
from ._widgets import QSizePolicy, QWidget

APP_ROOT = "/app/"   # Pyodide の中でアプリを置いた場所（サイトの一番上に当たる）


def _to_py(value):
    if isinstance(value, JsProxy):
        try:
            return value.to_py()
        except Exception:  # noqa: BLE001
            return None
    if _dom.absent(value):
        return None
    return value


class QWebEngineSettings:
    pass


class QWebEnginePage(QObject):
    loadFinished = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent if isinstance(parent, QObject) else None)
        self._view = None
        self._channel = None

    def setWebChannel(self, channel):
        self._channel = channel
        if self._view is not None:
            self._view._attach_channel(channel)

    def webChannel(self):
        return self._channel

    def runJavaScript(self, code, *args):
        callback = next((a for a in args if callable(a)), None)
        view = self._view

        def run():
            if view is None or view._deleted:
                return
            frame = view._iframe.contentWindow
            result = None
            # 地図のページを読み込み直している間（iframe を動かしたときなど）は捨てる。読み込み終わると
            # ページが bridge.onReady を呼び、Python 側がすべてを送り直す
            ready = not _dom.absent(frame) and bool(js.Reflect.has(frame, "app"))
            try:
                result = _to_py(frame.eval(code)) if ready else None
            except Exception as e:  # noqa: BLE001
                print(f"[js] {e}")
            if callback is not None:
                callback(result)

        _dom.later(0, run)

    def javaScriptConsoleMessage(self, *_a):
        pass

    def settings(self):
        return QWebEngineSettings()


class QWebEngineView(QWidget):
    _hpolicy = QSizePolicy.Policy.Expanding
    _vpolicy = QSizePolicy.Policy.Expanding
    loadFinished = pyqtSignal(bool)

    def __init__(self, parent=None):
        self._page = None
        super().__init__(parent)
        self.setPage(QWebEnginePage(self))

    def _build(self):
        self._el.classList.add("qwebview")
        self._iframe = _dom.el("iframe", "qwebview-frame")
        self._el.appendChild(self._iframe)
        _dom.listen(self, self._iframe, "load", lambda ev: self._on_load())

    def _on_load(self):
        if str(self._iframe.src or "") in ("", "about:blank"):
            return
        self.loadFinished.emit(True)
        if self._page is not None:
            self._page.loadFinished.emit(True)

    def setPage(self, page):
        self._page = page
        page._view = self
        if page._channel is not None:
            self._attach_channel(page._channel)

    def page(self):
        return self._page

    def _attach_channel(self, channel):
        self._iframe.pvChannel = channel._js_object()

    def setUrl(self, url):
        text = url.toString() if isinstance(url, QUrl) else str(url)
        if text.startswith("file://"):
            text = text[len("file://"):]
        if text.startswith(APP_ROOT):
            text = text[len(APP_ROOT):]
        version = getattr(js.window, "PV_VERSION", None)
        v = ""
        try:
            v = str(version.version) if not _dom.absent(version) else ""
        except Exception:  # noqa: BLE001
            v = ""
        self._iframe.src = text + (f"?v={v}" if v else "")

    load = setUrl

    def setHtml(self, html, *_a):
        self._iframe.srcdoc = html

    def setZoomFactor(self, *_a):
        pass

    def zoomFactor(self):
        return 1.0

    def settings(self):
        return QWebEngineSettings()

    def _on_destroy(self):
        self._iframe.src = "about:blank"
        QWidget._on_destroy(self)


class QWebChannel(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._objects: dict = {}
        self._js = None

    def registerObject(self, name, obj):
        self._objects[name] = obj
        self._js = None

    def registeredObjects(self):
        return dict(self._objects)

    def _js_object(self):
        """iframe のページに渡す { objects: { 名前: { メソッド: 関数 } } }。呼ばれたら後回しで Python のメソッドを呼ぶ。"""
        if self._js is not None:
            return self._js
        objects = js.Object.new()
        for name, obj in self._objects.items():
            target = js.Object.new()
            for attr in dir(type(obj)):
                if attr.startswith("_") or attr in dir(QObject):
                    continue
                method = getattr(obj, attr, None)
                if not callable(method) or isinstance(getattr(type(obj), attr, None), pyqtSignal):
                    continue

                def call(*args, m=method):
                    values = [_to_py(a) for a in args]
                    _dom.later(0, lambda: m(*values))

                proxy = create_proxy(call)
                self.__dict__.setdefault("_proxies_keep", []).append(proxy)
                setattr(target, attr, proxy)
            setattr(objects, name, target)
        holder = js.Object.new()
        holder.objects = objects
        self._js = holder
        return holder
