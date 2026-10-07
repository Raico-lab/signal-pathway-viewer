"""リソース・ユーザーデータの場所を解決する（PyInstaller 対応）。"""
import os
import sys
from pathlib import Path

APP_NAME = "PathwaysViewer"   # データの保存先・配布物のファイル名（表示名は TITLE）
OLD_APP_NAMES = ("TorPathwayViewer",)   # 以前の名前（保存先がこの名前で残っていれば、新しい名前へ移す）
# アプリの表示名。種名は斜体で書く（HTML では TITLE_HTML）。ウィンドウの題名など斜体にできない所は TITLE
TITLE = "Pathways viewer for Saccharomyces cerevisiae"
TITLE_HTML = "Pathways viewer for <i>Saccharomyces cerevisiae</i>"


def resource_path(*parts: str) -> Path:
    """同梱リソースのパス。PyInstaller 実行時は展開先 (_MEIPASS) を基準にする。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


def user_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = base / APP_NAME
    if not path.exists():
        # 名前を変える前の保存先（DB・登録した表示など）があれば、そのまま引き継ぐ
        old = next((base / n for n in OLD_APP_NAMES if (base / n).is_dir()), None)
        if old is not None:
            try:
                old.rename(path)
            except OSError:
                pass
    path.mkdir(parents=True, exist_ok=True)
    return path

