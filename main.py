"""Pathways viewer for Saccharomyces cerevisiae の起動スクリプト。"""
import os
import sys

# ピンチ操作でページ全体（凡例など）が拡大されないようにする。図の拡大・縮小は図の側で行う。
# 表示エンジンの初期化より前に設定する必要がある。
_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
if "--disable-pinch" not in _flags:
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (_flags + " --disable-pinch").strip()

from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401 - QApplication より前に読み込む必要がある
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from tor_app.ui.main_window import MainWindow
from tor_app.ui.popup_guard import ComboPopupGuard


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("PathwaysViewer")
    # OS のダークモードに関係なく、ネットワーク図と揃えて明るい配色で表示する
    if hasattr(app.styleHints(), "setColorScheme"):
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setStyle("Fusion")
    app.setPalette(app.style().standardPalette())
    ComboPopupGuard(app)   # 配置・色などのプルダウンが稀に開かないときに開き直す
    window = MainWindow()
    if not window.start():
        return 0
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
