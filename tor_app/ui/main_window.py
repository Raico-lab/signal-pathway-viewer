"""メインウィンドウ（3 タブ構成）。"""
import subprocess
import sys
import threading

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QDesktopServices
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QTabWidget,
                             QVBoxLayout, QWidget)

from .. import datastore, server
from ..db import Database
from ..paths import TITLE, TITLE_HTML, user_data_dir
from .db_tab import DatabaseTab
from . import help_text
from .network_tab import NetworkTab
from .sources_tab import SourcesTab

# メニューの「使い方」: Help ボタンと同じ説明（tor_app/ui/help_text.py）に、データの保存場所を加える
HELP_TEXT = f"<h2>{TITLE_HTML}</h2>" + help_text.ALL + """<h3>データ</h3>
<p>データベースはサーバー（公開しているブラウザ版の置き場）の版が正です。地図を軽く描くため、この PC にも写しを置いて使います。
起動するたびにサーバーの版と照らし、新しい版があれば自動で入れ替えます（状態の帯に知らせます）。
「ファイル &gt; 最新データを確認」でいつでも確かめられ、「ヘルプ &gt; データの情報」で今のデータの版と取得元が分かります。
サーバーにつながらないときは、この PC の写しでそのまま使えます。情報源タブの論文・サイトの情報も、サーバーから取ります。</p>"""


class _Done(QObject):
    """裏のスレッドの結果を画面のスレッドに渡す。"""
    update_checked = pyqtSignal(object, bool)   # (server.UpdateStatus, 手動で確認したか)
    downloaded = pyqtSignal(object)             # バックアップのパス、または失敗した例外


# 「ヘルプ > このアプリについて」: 著作権・ライセンス・保証がないこと・ソースの場所（GPLv3 の第 5 条 d が求める表示）
SOURCE_URL = "https://github.com/Raico-lab/signal-pathway-viewer"
ABOUT_TEXT = (
    "<b>Pathways Viewer for Saccharomyces cerevisiae</b><br>"
    "Copyright (C) 2026 Rai Katsukawa<br><br>"
    "このプログラムはフリーソフトウェアです。GNU General Public License version 3（GPLv3）の条件のもとで、"
    "再配布・改変ができます。<b>このプログラムは無保証です。</b>詳しくは同梱の LICENSE をご覧ください。<br><br>"
    f"ソース: <a href='{SOURCE_URL}'>{SOURCE_URL}</a><br>"
    "ほかの部品のライセンス: THIRD_PARTY_NOTICES.md（PyQt6・Qt・Cytoscape.js など）<br>"
    "データの利用条件: DATA_LICENSES.md（出典ごとの条件に従います。情報源タブに出典を並べています）<br>"
    "SGD・Gene Ontology・UniProt・Alliance of Genome Resources・Leutert et al. 2023（Zenodo）のデータは CC BY 4.0、"
    "BioGRID のデータは MIT、Complex Portal は CC0 で、このアプリのために選択・統合・要約して使っています"
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db: Database | None = None
        self.setWindowTitle(TITLE)
        self.resize(1500, 900)

        self.db_tab = DatabaseTab()
        self.network_tab = NetworkTab()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.network_tab, "ネットワーク")
        self.tabs.addTab(self.db_tab, "データベース")
        self.sources_tab = SourcesTab()
        self.tabs.addTab(self.sources_tab, "情報源")
        # 画面上部の案内（サーバーに新しいデータがあるときだけ出す）
        self.banner = QFrame()
        self.banner.setStyleSheet("QFrame { background: #fff8e1; border-bottom: 1px solid #ffe082; }")
        bar = QHBoxLayout(self.banner)
        bar.setContentsMargins(10, 4, 10, 4)
        self.banner_label = QLabel()
        self.banner_later = QPushButton("閉じる")
        self.banner_later.clicked.connect(self.banner.hide)
        bar.addWidget(self.banner_label, 1)
        bar.addWidget(self.banner_later)
        self.banner.hide()
        self._update_status: server.UpdateStatus | None = None
        self._done = _Done()
        self._done.update_checked.connect(self._on_update_checked)
        self._done.downloaded.connect(self._on_downloaded)
        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.banner)
        column.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self.db_label = QLabel()
        self.statusBar().addPermanentWidget(self.db_label)
        self.db_tab.set_network(self.network_tab)
        self.network_tab.statusMessage.connect(lambda m: self.statusBar().showMessage(m, 6000))
        self._build_menu()

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("ファイル")
        for text, slot in (("最新データを確認", lambda: self.check_update(manual=True)),
                           ("データの保存フォルダを開く", self.open_data_folder)):
            action = QAction(text, self)
            action.triggered.connect(slot)
            file_menu.addAction(action)
        file_menu.addSeparator()
        quit_action = QAction("終了", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)
        help_menu = self.menuBar().addMenu("ヘルプ")
        about = QAction("使い方", self)
        about.triggered.connect(lambda: QMessageBox.information(self, "使い方", HELP_TEXT))
        help_menu.addAction(about)
        info = QAction("データの情報", self)
        info.triggered.connect(self.show_data_info)
        help_menu.addAction(info)
        license_action = QAction("このアプリについて", self)
        license_action.triggered.connect(lambda: QMessageBox.about(self, "このアプリについて", ABOUT_TEXT))
        help_menu.addAction(license_action)

    # ---- DB を開く ----
    def start(self) -> bool:
        """起動時にローカル DB を用意して開く。DB はサーバーの版が正で、この PC には写しを置く（編集はしない）。
        サーバーから入れた写しがあればそれを使い、なければアプリに同梱の版を使う。そのあと裏でサーバーと照らし、
        新しい版があれば自動で入れ替える。"""
        try:
            if not datastore.LOCAL_DB.exists():
                datastore.install_bundled(backup=False)
            elif datastore.bundled_is_newer():
                if datastore.installed_info().get("source") == "server" and server.enabled():
                    datastore.keep_local()   # サーバーから入れた版のほうが新しいことがある（このあとサーバーと照らす）
                else:
                    datastore.install_bundled(backup=False)
        except OSError as e:
            QMessageBox.critical(self, "エラー", f"データを準備できませんでした。\n{e}")
            return False
        if not self.open_database():
            return False
        # サーバーがあれば、裏で新しいデータを確かめる（画面はサーバーの応答を待たない）
        if server.enabled():
            self.check_update(manual=False)
        return True

    # ---- サーバー（データの更新） ----
    def check_update(self, manual: bool) -> None:
        threading.Thread(target=lambda: self._done.update_checked.emit(server.check_update(), manual),
                         daemon=True).start()

    def _on_update_checked(self, status, manual: bool) -> None:
        self._update_status = status
        label = f"{status.version}版" if status.version else ""
        if status.state == "new":
            self.download_update()   # サーバーの版が正なので、尋ねずに入れ替える
        elif status.state == "app_old":
            self.banner_label.setText(f"新しいデータ {label} を使うには、アプリの更新が必要です（ブラウザ版の「デスクトップアプリ」タブ）")
            self.banner.show()
        elif status.state == "latest":
            self.statusBar().showMessage(f"データ: {label} 最新です", 6000)
        elif status.state == "offline":
            self.statusBar().showMessage("サーバーにつながりません。この PC の写しで表示しています", 6000)
        if manual and status.state in ("off", "latest", "offline"):
            QMessageBox.information(self, "最新データを確認", {
                "off": "データを配信するサーバーは、設定されていません。",
                "latest": f"この PC のデータは最新です: {label}",
                "offline": "サーバーにつながりませんでした。この PC の写しで表示しています。"}[status.state])

    def download_update(self) -> None:
        status = self._update_status
        if not status or not status.manifest:
            return
        label = f" {status.version}版" if status.version else ""
        self.statusBar().showMessage(f"新しいデータ{label}を取り込んでいます…")

        def work():
            try:
                self._done.downloaded.emit(server.fetch_update(status.manifest))
            except Exception as e:  # noqa: BLE001
                self._done.downloaded.emit(e)
        threading.Thread(target=work, daemon=True).start()

    def _on_downloaded(self, result) -> None:
        if not isinstance(result, Exception):
            # 置き換えは画面のスレッドで、DB を閉じてから行う
            if self.db:
                self.db.engine.dispose()
            try:
                server.install_update(*result, self._update_status.manifest)
            except OSError as e:
                result = e
        if isinstance(result, Exception):
            self.statusBar().showMessage(f"新しいデータを取り込めませんでした。この PC の写しのままです: {result}", 10000)
            self.open_database()
            return
        if self.open_database():
            version = self._update_status.version if self._update_status else ""
            self.statusBar().showMessage(f"データを最新{f'（{version}版）' if version else ''}に更新しました", 10000)

    def show_data_info(self) -> None:
        info = datastore.installed_info()
        source = {"bundled": "アプリに同梱のデータ", "server": "サーバーから取得したデータ"}.get(info.get("source"), "不明")
        lines = [f"取得元: {source}"]
        if info.get("version"):
            lines.append(f"版: {info['version']}")
        if info.get("at"):
            lines.append(f"取り込んだ日時: {info['at']}")
        lines.append(f"保存場所: {datastore.LOCAL_DB}")
        lines.append("サーバー: " + (server.config().get("base_url") if server.enabled() else "未設定"))
        QMessageBox.information(self, "データの情報", "\n".join(lines))

    def open_database(self) -> bool:
        if self.db:
            self.db.engine.dispose()
        try:
            db = Database(str(datastore.LOCAL_DB))
            db.proteins()  # 接続確認
        except Exception as e:  # noqa: BLE001 - 破損などを利用者に伝える
            QMessageBox.critical(self, "DB を開けません",
                                 f"{e}\n\n「ファイル > 同梱データに戻す」で初期状態に戻せます。")
            return False
        self.db = db
        self.db_tab.set_database(db)
        self.network_tab.set_database(db)
        self.sources_tab.set_database(db)
        self.db_label.setText(f"データ: {datastore.LOCAL_DB}")
        return True

    def open_data_folder(self):
        folder = str(user_data_dir())
        if sys.platform == "darwin":
            subprocess.run(["open", folder], check=False)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
