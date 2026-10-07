"""HTML の要素の出し入れ（js の呼び出しをまとめたもの）。"""
import js
from pyodide.ffi import create_once_callable, create_proxy

document = js.document
window = js.window


def el(tag: str = "div", cls: str = "", text: str | None = None):
    e = document.createElement(tag)
    if cls:
        e.className = cls
    if text is not None:
        e.textContent = text
    return e


def listen(owner, target, event: str, handler, capture: bool = False, passive: bool | None = None):
    """target の event に handler をつなぐ。proxy は owner に覚えさせ、owner を消すときに解放する。"""
    proxy = create_proxy(handler)
    if passive is None:
        target.addEventListener(event, proxy, capture)
    else:
        opts = js.Object.new()
        opts.capture = capture
        opts.passive = passive
        target.addEventListener(event, proxy, opts)
    store = owner.__dict__.setdefault("_proxies", [])
    store.append((target, event, proxy, capture))
    return proxy


def release(owner) -> None:
    for target, event, proxy, capture in owner.__dict__.pop("_proxies", []):
        try:
            target.removeEventListener(event, proxy, capture)
        except Exception:  # noqa: BLE001
            pass
        proxy.destroy()


def later(ms: int, fn) -> int:
    """ms ミリ秒後に fn を 1 回呼ぶ（例外はブラウザのコンソールに出す）。"""
    return window.setTimeout(create_once_callable(lambda: _guard(fn)), max(0, int(ms)))


def _guard(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001 - 画面のイベントの中の例外は、アプリを止めずに記録する
        report()


def report() -> None:
    import traceback
    text = traceback.format_exc()
    print(text)
    try:
        window.pvReportError(text)
    except Exception:  # noqa: BLE001
        pass


def rect(e):
    """要素の画面上の位置と大きさ（x, y, w, h）。"""
    r = e.getBoundingClientRect()
    return r.left, r.top, r.width, r.height


_canvas = None


def text_width(text: str, font: str) -> float:
    global _canvas
    if _canvas is None:
        _canvas = document.createElement("canvas").getContext("2d")
    _canvas.font = font
    return _canvas.measureText(text).width


try:
    from pyodide.ffi import jsnull as _jsnull
except ImportError:   # 古い Pyodide では null は None になる
    _jsnull = None


def absent(x) -> bool:
    """JavaScript の null・undefined か（Pyodide では None か jsnull になる）。"""
    return x is None or (_jsnull is not None and x is _jsnull) or type(x).__name__ in ("JsNull", "JsUndefined")


def place(parent, element, before=None) -> None:
    """element を parent の中の before の前（None なら最後）に置く。すでにその場所なら何もしない。
    地図の iframe は、ふつうに動かすと読み込み直しになるので、使えるブラウザでは moveBefore で状態を保ったまま動かす。"""
    if element.parentNode == parent:
        nxt = element.nextSibling
        if (before is None and absent(nxt)) or (before is not None and nxt == before):
            return
    if element.isConnected and parent.isConnected and hasattr(parent, "moveBefore"):
        try:
            parent.moveBefore(element, before)
            return
        except Exception:  # noqa: BLE001 - 動かせない組み合わせのときは、ふつうに入れる
            pass
    parent.insertBefore(element, before)
