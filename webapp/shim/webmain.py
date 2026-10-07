"""ブラウザ版の起動（boot.js から呼ぶ）。デスクトップ版の main.py に当たる。

ブラウザでは使えないものを差し替えてから、デスクトップ版と同じ MainWindow を作る。
- スレッド: Pyodide ではスレッドを作れないので、threading.Thread は少し後に同じスレッドで動かす
- 通信: urllib.request.urlopen は、ブラウザの同期の XMLHttpRequest で行う
- 上部のタブの「情報源」の右に「デスクトップアプリ」（Windows 版・Mac 版のダウンロード）を加える
"""
import html
import io
import json
import threading
import urllib.error
import urllib.request

import js

from PyQt6 import _dom

SITE = json.loads(js.JSON.stringify(js.window.PV_VERSION)) if getattr(js.window, "PV_VERSION", None) else {}


# ================= ブラウザで使えないものの差し替え =================
def _thread_start(self):
    """スレッドの代わりに、画面の処理の合間に同じスレッドで動かす（通信は数秒止まることがある）。"""
    _dom.later(0, self.run)


class _Response(io.BytesIO):
    def __init__(self, data: bytes, status: int, url: str):
        super().__init__(data)
        self.status = status
        self.url = url

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _urlopen(request, data=None, timeout=None, **_kw):
    url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
    method = request.get_method() if isinstance(request, urllib.request.Request) else ("POST" if data else "GET")
    body = request.data if isinstance(request, urllib.request.Request) else data
    xhr = js.XMLHttpRequest.new()
    try:
        xhr.open(method, url, False)
        if isinstance(request, urllib.request.Request):
            for k, v in request.header_items():
                if k.lower() not in ("user-agent", "content-length", "host", "connection"):
                    xhr.setRequestHeader(k, v)
        xhr.send(body.decode("utf-8") if isinstance(body, bytes) else body)
    except Exception as e:  # noqa: BLE001 - CORS で断られた・つながらない
        raise urllib.error.URLError(str(e)) from None
    status = int(xhr.status)
    if status == 0:
        raise urllib.error.URLError("接続できませんでした")
    if status >= 400:
        raise urllib.error.HTTPError(url, status, str(xhr.statusText), None, None)
    return _Response(str(xhr.responseText).encode("utf-8"), status, url)


def _patch() -> None:
    threading.Thread.start = _thread_start
    urllib.request.urlopen = _urlopen


# ================= デスクトップアプリのタブ =================
def desktop_html() -> str:
    """「デスクトップアプリ」のタブ: Windows 版・Mac 版のダウンロードと、ダウンロードした後の開き方。
    どちらも署名していないアプリなので、初めて開くときは OS の警告を越える手順が要る（OS ごとのカードに書く）。"""
    repo = SITE.get("repo", "")
    version = SITE.get("desktop_version", "")
    base = f"https://github.com/{repo}/releases/latest/download/" if repo else ""
    sizes = SITE.get("desktop_sizes", {})

    def card(title: str, file: str, target: str, steps: list[str], after: str) -> str:
        size = f"・約 {sizes[file] / 1e6:.0f} MB" if sizes.get(file) else ""
        link = (f"<a class='dl-button' href='{html.escape(base + file)}'>{title} 版をダウンロード</a>" if base
                else "<span style='color:#999'>準備中</span>")
        items = "".join(f"<li>{x}</li>" for x in steps)
        return (f"<div class='dl-card'><div class='dl-title'>{title}</div>"
                f"<div class='dl-file'>{html.escape(file)}{size}・{target}</div>{link}"
                f"<div class='dl-howto'>ダウンロードした後の開き方</div><ol>{items}</ol>"
                f"<div class='dl-note'>{after}</div></div>")

    windows = card(
        "Windows", "PathwaysViewer-windows-setup.exe", "Windows 10 / 11",
        ["ダウンロードした <b>PathwaysViewer-windows-setup.exe</b> をダブルクリックします。"
         "ブラウザが「一般的にダウンロードされていません」などと止めたときは、ダウンロードの一覧の「…」から<b>「保存」→「保持する」</b>を選びます。",
         "<b>「Windows によって PC が保護されました」</b>と出たら、<b>「詳細情報」</b>を押し、出てきた<b>「実行」</b>を押します"
         "（署名していないアプリのため、そのままでは開けません）。",
         "画面に従ってインストールします（管理者権限は要りません）。",
         "スタートメニューの <b>Pathways viewer</b> から起動します。"],
        "2 回目からは、ふつうに起動できます。アンインストールは「設定 &gt; アプリ」から行います。")
    mac = card(
        "Mac", "PathwaysViewer-mac.dmg", "Apple シリコン（M1 以降）",
        ["ダウンロードした <b>PathwaysViewer-mac.dmg</b> をダブルクリックし、出てきた <b>PathwaysViewer.app</b> を"
         "隣の<b>「アプリケーション」</b>フォルダへドラッグします。",
         "Finder の「アプリケーション」で <b>PathwaysViewer.app</b> を<b>右クリック（control + クリック）→「開く」</b>を選び、"
         "確認の画面でもう一度<b>「開く」</b>を押します（署名していないアプリのため、ダブルクリックでは開けません）。",
         "macOS 15 以降で「開く」が出ないときは、一度ダブルクリックして警告を閉じてから、"
         "<b>「システム設定」→「プライバシーとセキュリティ」</b>の下にある<b>「このまま開く」</b>を押し、パスワードを入れます。"],
        "2 回目からは、ふつうにダブルクリックで起動できます。アンインストールは PathwaysViewer.app をゴミ箱に入れます。")
    return f"""
<div class='dl-page'>
<h2>デスクトップアプリ</h2>
<p>このページと同じ機能を、PC にインストールして使える版です。{f"版: {html.escape(version)}" if version else ""}</p>
<div class='dl-cards'>{windows}{mac}</div>
<p class='dl-small'>データベースは、起動するたびにこのページと同じ最新の版に自動でそろいます（アプリを入れ直す必要はありません）。</p>
</div>"""


def add_desktop_tab(window) -> None:
    from PyQt6.QtWidgets import QTextBrowser
    view = QTextBrowser()
    view.setOpenExternalLinks(False)
    view.setOpenLinks(False)
    view.anchorClicked.connect(lambda url: js.window.open(url.toString(), "_blank", "noopener"))
    view.setHtml(desktop_html())
    window.tabs.addTab(view, "デスクトップアプリ")


# ================= コンパイルした結果の使い回し =================
# Python のコードは毎回コンパイルし直すと 1 秒ほどかかる。起動した後で、読み込んだモジュールをコンパイルした結果（.pyc）を
# /persist/pycache（ブラウザの IndexedDB）に書いておき、次からはそれを使う。中身の確かめは .py のハッシュで行う
# （展開し直すとファイルの日時が変わるので、日時では確かめられない）。sys.pycache_prefix は boot.js が決める。
def _cache_bytecode() -> None:
    import importlib.util
    import py_compile
    import sys

    if not sys.pycache_prefix:
        return
    todo = []
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None) or ""
        if path.endswith(".py") and path.startswith(("/app/", "/lib/")):
            todo.append(path)

    def valid(path: str, cached: str) -> bool:
        try:
            with open(cached, "rb") as f:
                head = f.read(16)
            with open(path, "rb") as f:
                source = f.read()
        except OSError:
            return False
        flags = int.from_bytes(head[4:8], "little")
        return len(head) == 16 and flags & 1 and head[8:16] == importlib.util.source_hash(source)

    def work():
        for _ in range(25):   # 少しずつ（画面の操作を止めないように）
            if not todo:
                return
            path = todo.pop()
            cached = importlib.util.cache_from_source(path)
            if valid(path, cached):
                continue
            try:
                py_compile.compile(path, cfile=cached, doraise=True,
                                   invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
            except Exception:  # noqa: BLE001 - 書けなくても動作は変わらない
                pass
        _dom.later(30, work)

    _dom.later(3000, work)


# ================= 起動 =================
_keep: list = []   # 画面の部品を消さないように持っておく


def main() -> None:
    _patch()
    from PyQt6.QtWidgets import QApplication
    from tor_app.ui.main_window import MainWindow

    app = QApplication([])
    app.setApplicationName("PathwaysViewer")
    window = MainWindow()
    add_desktop_tab(window)
    _keep.extend([app, window])
    window.show()
    started = window.start()
    _cache_bytecode()
    if not started:
        return
