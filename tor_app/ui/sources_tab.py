"""上部の「情報源」タブ: DB を作るのに使ったサイトと、参考にした論文を分けて並べる。

サイト: data/sources.json の sites（名前・URL・根拠欄で見分ける語）と、それが根拠になっている関係の本数。
同じところが出すもの（SGD と SGD SPELL、BioGRID と PhosphoGRID）は includes で 1 つにまとめ、cite にサイトが引用を求める論文を並べる。
論文: data/papers.json（tools/fetch_paper_list.py が作る。1 本ずつ書誌と URL）。
10 本ずつのページに分け（スクロールなし）、題名で絞り込める。サイトの本数は DB を開いたときに数える。
サーバーがあれば、論文・サイトの情報はサーバーのものを使う（tor_app/server.py。取れるまでは手元の写しか同梱のもの）。
"""
import html
import re
import unicodedata
import threading

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from .. import server

_loaded: dict[str, dict] = {}   # サーバーから新しく取ったもの（取れるまでは手元の版を使う）


def _load(name: str) -> dict:
    return _loaded.get(name) or server.local_json(name)


class _Fetched(QObject):
    done = pyqtSignal()


PAGE_SIZE = 10   # 論文を 1 ページに並べる本数（スクロールせずに収まる数）
ROW_BG = ("#ffffff", "#f5f7fa")   # 行を 1 行おきに塗り分ける
SITE_COLUMNS = 4   # サイトを横に並べる数（上の枠に収まるように）
URL_COLOR = "#1a5fb4"   # URL の文字の色
HEAD = "color:#000;font-size:11px;font-weight:bold;letter-spacing:1px"


def _number(n: str, size: int = 17) -> str:
    """数だけを太字で（単位は付けない）。"""
    return f"<span style='font-size:{size}px;font-weight:bold;color:#000'>{n}</span>"



class SourcesTab(QWidget):
    def __init__(self):
        super().__init__()
        open_url = lambda url: QDesktopServices.openUrl(QUrl(url.toString()))   # noqa: E731
        self.sites_view = QTextBrowser()
        self.sites_view.setOpenLinks(False)
        self.sites_view.anchorClicked.connect(open_url)
        self.papers_view = QTextBrowser()
        self.papers_view.setOpenLinks(False)
        self.papers_view.anchorClicked.connect(open_url)
        self.papers_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # ページで送る
        self.search = QLineEdit()
        self.search.setPlaceholderText("題名・著者で検索")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(320)
        self.search.textChanged.connect(lambda _t: self._show_page(0))
        self.prev_button, self.next_button = QPushButton("◀"), QPushButton("▶")
        for b in (self.prev_button, self.next_button):
            b.setFixedWidth(40)
        self.prev_button.clicked.connect(lambda: self._show_page(self.page - 1))
        self.next_button.clicked.connect(lambda: self._show_page(self.page + 1))
        self.page_label = QLabel()
        self.count_label = QLabel()   # 論文の数（題名で絞り込んでいれば、当てはまる数）
        bar = QHBoxLayout()
        bar.addWidget(self.search, 1)
        bar.addStretch(1)
        bar.addWidget(self.count_label)
        bar.addSpacing(8)
        bar.addWidget(self.prev_button)
        bar.addWidget(self.page_label)
        bar.addWidget(self.next_button)
        # 上にサイト、下に論文（縦幅は 1:2。サイトの枠は中身の高さより大きくしない）
        self.sites_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.sites_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # 表は枠の幅に合わせる
        layout = QVBoxLayout(self)
        layout.addWidget(self.sites_view, 1)
        layout.addLayout(bar)
        layout.addWidget(self.papers_view, 2)
        self.papers = self._indexed(_load("papers.json").get("papers", []))
        self.page = 0
        self.db = None
        self.show_counts({})
        if server.enabled():   # 裏でサーバーの情報源の情報を取り、取れたら出し直す
            self._fetched = _Fetched()
            self._fetched.done.connect(self._on_fetched)
            threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self) -> None:
        for name in ("papers.json", "sources.json"):
            data = server.fetch_json(name)
            if data:
                _loaded[name] = data
        self._fetched.done.emit()

    def _on_fetched(self) -> None:
        self.papers = self._indexed(_load("papers.json").get("papers", []))
        if self.db is not None:
            self.set_database(self.db)
        else:
            self._show_page(self.page)

    def set_database(self, db) -> None:
        # 根拠の数は、このタブが見えているときだけ数える（隠れているときは開いたときに数える。起動を速くするため）
        self.db = db
        if not self.isVisible():
            self._stale = True
            return
        self._stale = False
        sources = _load("sources.json")
        sites = sources.get("sites", [])
        site_counts = {s["name"]: 0 for s in sites}
        for it in db.interactions():
            ev = it.evidence or ""
            for s in sites:
                if any(m in ev for m in s["match"]):
                    site_counts[s["name"]] += 1
        self.show_counts(site_counts)

    def show_counts(self, site_counts: dict) -> None:
        sites = _load("sources.json").get("sites", [])

        papers = {p["pmid"]: p for p in self.papers}

        def site_count(s: dict) -> str:
            """根拠の数 = 根拠欄に出てくる関係の本数 ＋ note の数（Complex Portal の「複合体 633 件」、SGD SPELL の測定の数）。"""
            n = site_counts.get(s["name"]) or 0
            m = re.search(r"[\d,]+", s.get("note", ""))
            n += int(m.group(0).replace(",", "")) if m else 0
            return _number(f"{n:,}", 13) if n else ""

        def link(url: str, text: str, color: str = "#000") -> str:
            return f"<a href='{html.escape(url)}' style='color:{color};text-decoration:none'>{html.escape(text)}</a>"

        def site_cells(s: dict) -> str:
            hosts = "<br>".join(link(x["url"], re.sub(r"^https?://(www\.)?|/$", "", x["url"]), URL_COLOR)
                               for x in [s] + s.get("includes", []))
            # データの利用条件（data/sources.json の license。詳しくは DATA_LICENSES.md）
            lic = f"<br><span style='color:#555;font-size:10px'>{html.escape(s['license'])}</span>" if s.get("license") else ""
            # サイトが引用を求める論文（著者と年だけ。押すと論文を開く。papers.json に書誌がなければ PMID で）
            def short(p: dict) -> str:
                return p["cite"][:p["cite"].find(p["year"]) + len(p["year"])] if p.get("year") in p["cite"] else p["cite"]

            cites = "".join(
                "<br><span style='font-size:10px'>" + (link(papers[pmid]["url"], short(papers[pmid])) if pmid in papers
                                                       else link(f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", f"PMID {pmid}"))
                + "</span>" for pmid in s.get("cite", []))
            return (f"<td><a href='{s['url']}' style='color:#000;text-decoration:none;"
                    f"font-weight:bold;font-size:12px'>{html.escape(s['name'])}</a><br>"
                    f"<span style='font-size:10px'>{hosts}</span>{lic}{cites}</td>"
                    f"<td align='right' valign='middle'>{site_count(s)}</td><td width='24'></td>")

        # 横に SITE_COLUMNS 個ずつ並べる（上の枠に収まるように）
        rows = [sites[k:k + SITE_COLUMNS] for k in range(0, len(sites), SITE_COLUMNS)]
        head = f"<td style='{HEAD}'>サイト</td><td align='right' style='{HEAD}'>根拠の数</td><td></td>" * SITE_COLUMNS
        body = "".join(f"<tr bgcolor='{ROW_BG[r % 2]}'>" + "".join(site_cells(x) for x in row) + "</tr>"
                       for r, row in enumerate(rows))
        self.sites_view.setHtml(f"<table width='100%' cellspacing='0' cellpadding='6'><tr>{head}</tr>{body}</table>")
        self._fit_sites()
        self._show_page(self.page)

    def _fit_sites(self) -> None:
        """サイトの枠の縦幅を、中身の高さに合わせる（余白もスクロールも作らない）。"""
        doc = self.sites_view.document()
        doc.setTextWidth(self.sites_view.viewport().width())
        frame = self.sites_view.frameWidth() * 2
        self.sites_view.setFixedHeight(int(doc.size().height()) + frame + 2)

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "_stale", False) and self.db is not None:
            self.set_database(self.db)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_sites)   # 幅が変わると折り返しが変わり、中身の高さも変わる

    @classmethod
    def _indexed(cls, papers: list[dict]) -> list[dict]:
        """検索に使う文字（題名・全著者・引用。アクセントを外す）を、論文ごとに "_find" に入れておく。
        サーバーの papers.json が古く著者の欄がなければ、引用（筆頭著者）だけで探す。"""
        for p in papers:
            p["_find"] = cls._plain(f"{p.get('title', '')} | {p.get('authors', '')} | {p.get('cite', '')}")
        return papers

    @staticmethod
    def _plain(text: str) -> str:
        """アクセントを外した文字（"Keränen" → "Keranen"。著者名を英字だけで探せるように）。"""
        return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))

    def _matches(self) -> list[dict]:
        # 打った語がすべて、題名か著者（全著者と、引用の「Aalto et al. 1997 …」）のどこかの語の頭から一致するもの
        # （"TOR" は TORC1・Tor1 に当たり、factor には当たらない。"hall mn" は Hall MN の論文に当たる）
        words = [re.compile(r"\b" + re.escape(self._plain(w)), re.I) for w in self.search.text().split()]
        if not words:
            return self.papers
        return [p for p in self.papers
                if all(w.search(p["_find"]) for w in words)]

    def _show_page(self, page: int) -> None:
        papers = self._matches()
        pages = max(1, -(-len(papers) // PAGE_SIZE))
        self.page = max(0, min(page, pages - 1))
        shown = papers[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        self.page_label.setText(f"{self.page + 1} / {pages}")
        self.count_label.setText(f"{len(papers)} 件")
        self.prev_button.setEnabled(self.page > 0)
        self.next_button.setEnabled(self.page < pages - 1)

        rows = "".join(
            f"<tr bgcolor='{ROW_BG[i % 2]}'><td>"
            f"<span style='font-size:13px;color:#000'>{html.escape(p['title'])}</span><br>"
            f"<span style='color:#000;font-size:11px'>{html.escape(p['cite'])}</span>　"
            f"<a href='{html.escape(p['url'])}' style='color:{URL_COLOR};text-decoration:none;font-size:11px'>"
            f"{html.escape(p['url'])}</a></td></tr>" for i, p in enumerate(shown))
        self.papers_view.setHtml(
            "<table width='100%' cellspacing='0' cellpadding='8'>" + rows + "</table>"
            if shown else "<span style='color:#999'>該当する論文はありません</span>")
