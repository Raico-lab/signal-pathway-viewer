"""DB の写しを各 PC のローカルに置いて使う（地図を軽く描くため）。DB はサーバーの版が正で、各 PC では編集しない。

- 初回起動時: 同梱 DB (data/tor_pathway.db) をユーザーデータへコピーする。
- 同梱 DB が新しくなったアプリで起動したとき: サーバーから入れた写しがなければ、尋ねずに同梱 DB に置き換える
  （サーバーから入れた写しがあればそのまま。起動後にサーバーと照らす。tor_app/ui/main_window.py の start）。
同梱 DB の変化はファイルのハッシュで判定する（最後に取り込んだハッシュを bundle.json に記録）。

サーバーの新しい版は、tor_app/server.py が確かめたファイルを install_file でローカルへ入れる
（アプリは常にローカル DB を読む）。方針は docs/EXTERNAL_DB_PLAN.md を参照。
"""
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from .paths import resource_path, user_data_dir

BUNDLED_DB = resource_path("data", "tor_pathway.db")
LOCAL_DB = user_data_dir() / "tor_pathway.db"
BACKUP_DIR = user_data_dir() / "backups"
_STATE_FILE = user_data_dir() / "bundle.json"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _state() -> dict:
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _recorded_hash() -> str | None:
    return _state().get("bundled_sha256")


def _record(bundled_hash: str, installed: dict | None = None) -> None:
    """bundled_sha256: 最後に見た同梱 DB。installed: 今のローカル DB の取得元（source・version・sha256・at）。"""
    state = _state()
    state["bundled_sha256"] = bundled_hash
    if installed is not None:
        state["installed"] = installed
    _STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def installed_info() -> dict:
    """今のローカル DB をどこから入れたか（{"source": "bundled" | "server", "version", "sha256", "at"}）。
    記録がなければ同梱 DB から入れたものとみなす。"""
    return _state().get("installed") or {"source": "bundled", "sha256": _recorded_hash() or ""}


def bundled_is_newer() -> bool:
    """ローカル DB があり、前回取り込んだ後に同梱 DB が変わっているか。"""
    return LOCAL_DB.exists() and _recorded_hash() != _hash(BUNDLED_DB)


def backup_local() -> Path:
    BACKUP_DIR.mkdir(exist_ok=True)
    stem = f"tor_pathway_{datetime.now():%Y%m%d_%H%M%S}"
    dest = BACKUP_DIR / f"{stem}.db"
    n = 1
    while dest.exists():
        n += 1
        dest = BACKUP_DIR / f"{stem}_{n}.db"
    shutil.copy2(LOCAL_DB, dest)
    return dest


def install_bundled(backup: bool = True) -> Path | None:
    """同梱 DB をローカルへコピーする。既存のローカル DB はバックアップしてそのパスを返す。"""
    backup_path = backup_local() if backup and LOCAL_DB.exists() else None
    shutil.copyfile(BUNDLED_DB, LOCAL_DB)
    digest = _hash(BUNDLED_DB)
    _record(digest, {"source": "bundled", "version": "", "sha256": digest, "at": f"{datetime.now():%Y-%m-%d %H:%M}"})
    return backup_path


def install_file(path: Path, source: str, version: str, sha256: str, backup: bool = False) -> Path | None:
    """サーバーから取ってきた DB（確かめ済み）をローカルへコピーする。backup なら既存のローカル DB をバックアップしてそのパスを返す。
    DB はサーバーの版が正で、各 PC では編集しないので、ふだんはバックアップしない（更新のたびに 30 MB 近く溜まるため）。"""
    backup_path = backup_local() if backup and LOCAL_DB.exists() else None
    shutil.copyfile(path, LOCAL_DB)
    state = _state()
    _record(state.get("bundled_sha256") or _hash(BUNDLED_DB),
            {"source": source, "version": version, "sha256": sha256, "at": f"{datetime.now():%Y-%m-%d %H:%M}"})
    return backup_path


def keep_local() -> None:
    """同梱 DB の更新を見送る（次回から同じ版については尋ねない）。"""
    _record(_hash(BUNDLED_DB))
