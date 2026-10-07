"""将来設置するサーバーとのやりとり（データの更新の通知と、情報源の情報の取得）。

サーバーはまだないので、接続先（data/server.json の base_url）が空のあいだは何もしない。
アプリは常にローカルの DB を使い、サーバーは「新しいものを届ける経路」としてだけ使う
（止まっていても、つながらなくても、今までどおり動く）。方針は docs/EXTERNAL_DB_PLAN.md。

サーバーに置くもの（base_url からの相対パス。data/server.json で変えられる）:
- manifest.json: 配信している DB の版（tools/make_manifest.py で作る）
    {"data_version": "2026-10-15", "schema_version": 1, "sha256": "…", "url": "tor_pathway.db", "notes": "…"}
- papers.json・sources.json: 情報源タブの論文・サイトの情報（サーバー上の DB から書き出したもの。形は data/ の同名のファイルと同じ）。
  地図で使う DB（遺伝子・関係）はローカルで扱い、情報源の情報はサーバーを正とする。取ってきたものはこの PC に写しを置き、
  つながらないときは写し（なければ同梱の data/ のファイル）を使う
"""
import gzip
import hashlib
import json
import sqlite3
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import datastore
from .paths import resource_path, user_data_dir

CONFIG_FILE = resource_path("data", "server.json")
SCHEMA_VERSION = 1      # このアプリが読める DB の構造の版（manifest の schema_version と比べる）
TIMEOUT = 5             # 版の確認・情報源の情報の取得を待つ秒数
DOWNLOAD_TIMEOUT = 120  # DB のダウンロードを待つ秒数
HEADERS = {"User-Agent": "PathwaysViewer"}


def config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def enabled() -> bool:
    return bool(config().get("base_url"))


def _url(key: str, default: str) -> str:
    cfg = config()
    return urllib.parse.urljoin(cfg["base_url"].rstrip("/") + "/", cfg.get(key, default))


def _get(url: str, timeout: float = TIMEOUT) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as res:
        return res.read()


# ---- データの更新の通知 ----
@dataclass
class UpdateStatus:
    state: str              # "off"（サーバーなし）/ "latest" / "new" / "offline" / "app_old"
    version: str = ""
    notes: str = ""
    manifest: dict | None = None


def check_update() -> UpdateStatus:
    """サーバーの manifest.json を読み、手元の DB より新しい版があるか調べる（画面とは別のスレッドから呼ぶ）。"""
    if not enabled():
        return UpdateStatus("off")
    try:
        manifest = json.loads(_get(_url("manifest", "manifest.json")))
    except Exception:   # noqa: BLE001  つながらない・形式が違うときは、手元のデータのまま
        return UpdateStatus("offline")
    version, notes = str(manifest.get("data_version", "")), str(manifest.get("notes", ""))
    if int(manifest.get("schema_version", 1)) > SCHEMA_VERSION:
        return UpdateStatus("app_old", version, notes, manifest)
    if manifest.get("sha256") and manifest["sha256"] == datastore.installed_info().get("sha256"):
        return UpdateStatus("latest", version, notes, manifest)
    return UpdateStatus("new", version, notes, manifest)


def fetch_update(manifest: dict) -> tuple[Path, str]:
    """新しい DB を一時ファイルにダウンロードして確かめる（画面とは別のスレッドから呼ぶ）。返り値: (一時ファイル, ハッシュ)。
    ハッシュが合わない・SQLite として開けない・表がそろわないときは例外を出す（一時ファイルは消す）。"""
    url = urllib.parse.urljoin(_url("manifest", "manifest.json"), manifest.get("url", "tor_pathway.db"))
    data = _get(url, DOWNLOAD_TIMEOUT)
    if url.endswith(".gz"):   # 圧縮して置いた DB（約 1/9 の大きさ）。ハッシュは展開した DB のもの
        data = gzip.decompress(data)
    digest = hashlib.sha256(data).hexdigest()
    if manifest.get("sha256") and digest != manifest["sha256"]:
        raise ValueError("ダウンロードしたデータが壊れています。ハッシュが一致しません")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        f.write(data)
        tmp = Path(f.name)
    try:
        with sqlite3.connect(tmp) as con:
            tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        if not {"proteins", "interactions", "categories"} <= tables:
            raise ValueError("ダウンロードしたデータに必要な表がありません")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, digest


def install_update(tmp: Path, digest: str, manifest: dict) -> Path | None:
    """確かめた DB をローカルの DB と置き換える（DB を閉じてから呼ぶ）。前の DB のバックアップのパスを返す。"""
    try:
        return datastore.install_file(tmp, source="server", version=str(manifest.get("data_version", "")),
                                      sha256=digest)
    finally:
        tmp.unlink(missing_ok=True)


# ---- 情報源（論文・サイト）の情報 ----
CACHE_DIR = user_data_dir() / "server_cache"


def local_json(name: str) -> dict:
    """情報源の情報の手元の版: サーバーがあり、そこから取った写しがあればそれ、なければ同梱の data/ のファイル。"""
    for path in ((CACHE_DIR / name,) if enabled() else ()) + (resource_path("data", name),):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            continue
    return {}


def fetch_json(name: str) -> dict | None:
    """サーバーの情報源の情報（papers.json・sources.json）を取り、写しを保存して返す（別のスレッドから呼ぶ）。
    サーバーがない・つながらない・形が違うときは None。"""
    if not enabled():
        return None
    try:
        data = json.loads(_get(_url(name.removesuffix(".json"), name)))
    except Exception:   # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        (CACHE_DIR / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return data

