"""② ネットワークタブ：表示枠（GraphPane）を並べて描画する。

表示枠ごとに注目遺伝子・段数・戻る/進む履歴・表示する TFs を持つ。モデル・詳細パネルは共有する。
左側には、操作中の表示枠で注目している遺伝子の TFs（転写制御する転写因子）をチェックボックスで並べる。
"""
import html
import json
import re
from dataclasses import dataclass, field

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QSizePolicy, QButtonGroup, QCheckBox, QComboBox, QFrame, QGroupBox, QToolButton, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QPushButton, QRadioButton, QScrollArea, QSpinBox, QSplitter, QStyle,
                             QStyledItemDelegate, QTabWidget, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import complex_groups, conditions, confidence, expression, relation_paths
from ..db import Database
from ..db.vocab import DETAILS, EFFECTS, INTERACTION_TYPES, detail_text, resolve_effect, type_label
from ..graph_data import PathwayModel
from ..paths import resource_path, user_data_dir
from . import help_text
from .gene_set_window import GeneSetPane, MapWindow
from .saved_views import SavedViewsPanel
from .graph_pane import DEFAULT_DOWN, DEFAULT_UP, GraphPane

LAYOUT_CHOICES = [
    ("階層（縦）", "dagre_tb"),
    ("階層（横）", "dagre_lr"),
    ("同心円", "concentric"),
    ("格子", "grid"),
]

VIEW_FILE = user_data_dir() / "view.json"
MAX_PANES = 4
MAX_LIST = 40            # 詳細パネルに並べる上流・下流の最大数

# 根拠欄の項目の出典（先頭の語で見分ける）: (見出し, 色)
EVIDENCE_SOURCES = [
    ("⚠", ("食い違い", "#e65100")),
    ("文献で確認", ("文献", "#ad1457")),
    ("作用の向き: IDEA", ("IDEA", "#00695c")),
    ("SGD GO 注釈", ("SGD の GO 注釈", "#5d4037")),
    ("作用の向き: GO-CAM", ("GO-CAM", "#5d4037")),
    ("GO-CAM", ("GO-CAM", "#5d4037")),
    ("作用の向き: 破壊株データ", ("破壊株データからの推定", "#6a1b9a")),
    ("破壊株データ", ("破壊株データ", "#6a1b9a")),
    ("BioGRID", ("BioGRID", "#1565c0")),
    ("YEASTRACT", ("YEASTRACT+", "#00838f")),
    ("TF acting", ("YEASTRACT+", "#00838f")),
    ("activator と inhibitor", ("YEASTRACT+", "#00838f")),
    ("公開データ", ("公開データ", "#455a64")),
    ("注記", ("BioGRID の注記", "#1565c0")),
    ("サンプル", ("サンプル", "#9e9e9e")),
]


@dataclass
class DetailState:
    """説明欄の開閉の状態。地図とデータベースの説明欄は同じ関数（NetworkTab.render_html）で中身を作り、状態だけを別に持つ。"""
    open_lists: set[str] = field(default_factory=set)        # 上流・下流の一覧で、畳んだ分を開いているもの（up / down）
    open_conds: set[str] = field(default_factory=set)        # 条件ごとの変化で、測定の一覧を開いている条件
    width: int = 0                                           # 説明欄の幅（px。0 なら地図の説明欄の幅）。条件名の折り返しに使う

    def reset(self) -> None:
        """別の遺伝子・線を開いたとき: 一覧を閉じる（開いた条件は、続けて比べられるように残す）。"""
        self.open_lists = set()


class WidthWatcher(QObject):
    """説明欄の幅が変わったら、少し待ってから changed を出す（条件名の折り返しを幅に合わせて作り直すため）。"""
    changed = pyqtSignal()

    def __init__(self, widget):
        super().__init__(widget)
        self._width = widget.width()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.changed)
        widget.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == event.Type.Resize and obj.width() != self._width:
            self._width = obj.width()
            self._timer.start()
        return False


class NetworkTab(QWidget):
    statusMessage = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db: Database | None = None
        self.model: PathwayModel | None = None
        self.selected: tuple[str, object] | None = None
        self.detail_pane: GraphPane | None = None     # 詳細パネルに表示中の選択元の枠
        self.detail_state = DetailState()
        self._st = self.detail_state   # 説明欄を作っている間の状態（render_html でデータベースの状態に差し替える）
        self._render_pane = True       # 説明欄に操作中の表示枠の情報（表示されていない数など）を出すか

        self.panes: list[GraphPane] = []
        self.gene_windows: list[MapWindow] = []   # 「別ウィンドウで開く」で開いた地図
        self.active_pane: GraphPane | None = None
        self.col_splitters: list[QSplitter] = []   # 表示枠の列（左・右）。列の中は上下に並べる

        self._build_toolbar()
        # 凡例のチェックボックスで隠した項目（全表示枠に共通）
        self.hidden: dict[str, set[str]] = {"roles": set(), "types": set(), "effects": set()}
        self._build_trf_panel()
        self._build_detail_panel()
        self.pane_area = QSplitter(Qt.Orientation.Horizontal)
        self.pane_area.setChildrenCollapsible(False)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.left_tabs)
        splitter.addWidget(self.pane_area)
        splitter.addWidget(self.detail_panel)
        splitter.setStretchFactor(1, 1)
        self.main_splitter = splitter
        self._fit_left_width()
        QTimer.singleShot(0, self._fit_left_width)   # ブラウザ版は、ページに置かれてから見出しの幅が分かる
        layout = QVBoxLayout(self)
        layout.addWidget(splitter, 1)

        for key, action in ((QKeySequence.StandardKey.Back, lambda p: p.go_back()),
                            (QKeySequence.StandardKey.Forward, lambda p: p.go_forward())):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda a=action: self.active_pane and a(self.active_pane))

        states, layout_name, color_mode, hidden, edge_color, display = self._load_view()
        self.display_options = dict(self.DEFAULT_DISPLAY)
        self.display_options.update({k: bool(v) for k, v in display.items() if k in self.display_options})
        self._set_hidden_filters(hidden)
        self.layout_combo.blockSignals(True)
        self.layout_combo.setCurrentIndex(max(0, self.layout_combo.findData(layout_name)))
        self.layout_combo.blockSignals(False)
        self.color_combo.blockSignals(True)
        self.color_combo.setCurrentIndex(max(0, self.color_combo.findData(color_mode)))
        self.color_combo.blockSignals(False)
        if self.data_kind() is not None:   # 前回は条件の測定で色分けしていた: 条件タブの遺伝子の側を開いておく
            self.cond_mode.setCurrentIndex(max(0, self.cond_mode.findData("genes")))
        self.edge_color_combo.blockSignals(True)
        self.edge_color_combo.setCurrentIndex(max(0, self.edge_color_combo.findData(edge_color)))
        self.edge_color_combo.blockSignals(False)
        self._sync_data_cond_enabled()
        for state in states:
            self._create_pane(state)
        self._arrange()
        self.set_active(self.panes[0])

    # ================= 構築 =================
    def _build_toolbar(self):
        # 配置・遺伝子の色・線の色は全表示枠に共通の設定。値はここの選択肢が持ち、画面には各表示枠の検索窓の下に
        # 同じ選択肢を出す（GraphPane.view_bar。どれかを変えると、ここを通して全表示枠の選択肢をそろえる）
        self.layout_combo = QComboBox()
        for label, key in LAYOUT_CHOICES:
            self.layout_combo.addItem(label, key)
        self.layout_combo.setToolTip("遺伝子の配置\n階層（縦）: 制御する側が上になるよう段に並べる\n階層（横）: 制御する側が左になるよう列に並べる\n"
                                     "同心円: つながりの多い遺伝子ほど内側に置く\n格子: 配置の番号の順に格子状に並べる")
        self.layout_combo.currentIndexChanged.connect(lambda: self._save_view())   # 次回も同じ配置の方式で開く
        # 選んだらすぐ全表示枠に反映する
        self.layout_combo.currentIndexChanged.connect(
            lambda: self._all_js(f"app.runLayout({json.dumps(self.layout_name())})"))
        self.color_combo = QComboBox()
        self.color_combo.addItem("役割", "role")
        tip = ("遺伝子の色\n役割: SGD の機能説明から推定したキナーゼ・転写制御因子などの分類\n"
               "階層: ネットワーク全体で上流の起点から何段目か。青＝上流側 → 赤紫＝下流側")
        if expression.available():
            for key, label in expression.KINDS.items():
                self.color_combo.addItem(label, key)
            tip += ("\n発現・リン酸化・タンパク質量: 右の「条件」で選んだ条件での、対照との log2 比。"
                    "赤＝増える、青＝減る、灰＝データなし")
        if expression.has_deletions():
            for key, label in expression.DELETION_KINDS.items():
                self.color_combo.addItem(label, key)
            tip += ("\n破壊株の mRNA・リン酸化: 左の「破壊株」タブで選んだ株での、野生型との log2 比（実測）。"
                    "壊した遺伝子は黒。タブで株の選択が外れると、前の色に戻ります")
        self.color_combo.setToolTip(tip)
        self.color_combo.currentIndexChanged.connect(lambda: self._save_view())
        self.color_combo.currentIndexChanged.connect(
            lambda: self._all_js(f"app.setColorMode({json.dumps(self.color_mode())})"))
        # 発現・リン酸化・タンパク質量で色分けするときの条件（データのある条件だけ。data/expression.db）
        self.data_cond_combo = QComboBox()
        labels = conditions.labels()
        for key in expression.condition_keys():
            self.data_cond_combo.addItem(labels.get(key, key), key)
        self.data_cond_combo.setToolTip("発現・リン酸化・タンパク質量で色分けするときの条件\n"
                                        "その種類のデータがない条件では、遺伝子はすべて灰色になります")
        saved = self._load_setting("data_condition", "", set(expression.condition_keys()))
        self.data_cond_combo.setCurrentIndex(max(0, self.data_cond_combo.findData(saved or "rapamycin")))
        self.data_cond_combo.currentIndexChanged.connect(lambda: self._save_view())
        # 破壊株で色分けするときの、壊した遺伝子（Deleteome の mRNA・Bodenmiller のリン酸化のある遺伝子）。
        # 画面には出さず（株は左の「破壊株」タブで選ぶ）、選んだ株を覚えておくのに使う
        self.del_strain_combo = QComboBox()
        strains = expression.deletion_strains()
        for orf, name, kinds in strains:
            self.del_strain_combo.addItem(name, orf)
        self.del_strain_combo.setToolTip("破壊株の mRNA・リン酸化で色分けするときの、壊した遺伝子\n"
                                         "mRNA は Deleteome（Kemmeren 2014）の 1,481 株、リン酸化は Bodenmiller 2010 の"
                                         "キナーゼ・ホスファターゼの 113 株。その種類の測定がない株では、遺伝子はすべて灰色になります")
        saved = self._load_setting("deletion_strain", "", {orf for orf, _, _ in strains})
        self.del_strain_combo.setCurrentIndex(max(0, self.del_strain_combo.findData(saved or "YMR037C")))
        self.del_strain_combo.currentIndexChanged.connect(lambda: self._save_view())
        for combo in (self.color_combo, self.data_cond_combo, self.del_strain_combo):
            combo.currentIndexChanged.connect(lambda _i: self._push_data_values())
        self._data_cache: tuple[tuple, str] | None = None
        self._sync_data_cond_enabled()
        self.edge_color_combo = QComboBox()
        self.edge_color_combo.addItem("修飾・制御", "type")
        self.edge_color_combo.addItem("作用", "effect")
        self.edge_color_combo.setToolTip("線の色\n修飾・制御: 修飾・制御の種類で色分け。リン酸化＝橙、転写制御＝青など\n"
                                         "作用: 促進＝赤、抑制＝青、作用不明＝灰、結合＝薄い灰")
        self.edge_color_combo.currentIndexChanged.connect(lambda: self._save_view())
        self.edge_color_combo.currentIndexChanged.connect(
            lambda: self._all_js(f"app.setEdgeColorMode({json.dumps(self.edge_color_mode())})"))

        for combo in (self.layout_combo, self.color_combo, self.edge_color_combo):
            combo.currentIndexChanged.connect(lambda _i: self.sync_view_choices())

    def view_choices(self) -> list[tuple[str, QComboBox]]:
        """表示枠の検索窓の下に出す、全表示枠に共通の選択肢: (見出し, 値を持つ選択肢)。"""
        # 見出しは短くする（表示枠を 2 列に並べても枠が広がりすぎないように。説明は選択肢のツールチップ）
        # 遺伝子の色は左の「条件」（遺伝子）・「破壊株」タブで選ぶ（選択をやめると役割に戻る）ので、ここには出さない
        return [("配置", self.layout_combo), ("線", self.edge_color_combo)]

    def data_kind(self) -> str | None:
        """発現・リン酸化・タンパク質量で色分けしているなら、その種類（mrna / phospho / protein）。"""
        mode = self.color_mode()
        return mode if mode in expression.KINDS else None

    def deletion_kind(self) -> str | None:
        """破壊株で色分けしているなら、その種類（del_mrna / del_phospho）。"""
        mode = self.color_mode()
        return mode if mode in expression.DELETION_KINDS else None

    def data_condition(self) -> str | None:
        return self.data_cond_combo.currentData()

    def deletion_strain(self) -> str | None:
        """破壊株で色分けするときの、壊した遺伝子の ORF。"""
        return self.del_strain_combo.currentData()

    def _sync_data_cond_enabled(self) -> None:
        self.data_cond_combo.setEnabled(self.data_kind() is not None)
        self.del_strain_combo.setEnabled(self.deletion_kind() is not None)

    def color_by_strain(self, orf: str, kind: str) -> None:
        """説明欄のリンクから: 遺伝子 orf を壊した株の kind（del_mrna / del_phospho）で地図を色分けする。"""
        i = self.del_strain_combo.findData(orf)
        if i < 0:
            return
        self.del_strain_combo.setCurrentIndex(i)
        self._color_by_deletion(kind)

    def data_js(self) -> str:
        """地図の遺伝子に、選んだ条件・種類の値を渡す JavaScript（色分けが発現などでなければ空の表）。
        値は遺伝子名 → [log2 の比, 補足（リン酸化の部位）]。同じ条件・種類・地図の DB なら作り直さない。"""
        dkind, strain = self.deletion_kind(), self.deletion_strain()
        if dkind and strain and self.model is not None:
            key = (dkind, strain, id(self.model))
            if self._data_cache is None or self._data_cache[0] != key:
                vals = expression.deletion_values(dkind, strain)
                by_gene = {p.gene_name: [round(v[0], 3), v[1]] for p in self.model.proteins.values()
                           if (v := vals.get((p.standard_name or "").upper())) is not None}
                ko = next((p.gene_name for p in self.model.proteins.values()
                           if (p.standard_name or "").upper() == strain), None)
                label = f"{expression.DELETION_KINDS[dkind]}・{self.del_strain_combo.currentText()} 破壊株"
                note = ("野生型との log2 比（Deleteome）。白は測って大きく変わらなかった遺伝子" if dkind == "del_mrna" else
                        "野生型との logFC（Bodenmiller 2010）。報告された部位のうち変化の最も大きいもの。灰は報告なし")
                self._data_cache = (key, f"app.setDataValues({json.dumps(by_gene, ensure_ascii=False)}, "
                                         f"{json.dumps(label, ensure_ascii=False)}, {json.dumps(note, ensure_ascii=False)}, "
                                         f"{json.dumps(ko, ensure_ascii=False)})")
            return self._data_cache[1]
        kind, cond = self.data_kind(), self.data_condition()
        if not kind or not cond or self.model is None:
            return "app.setDataValues({}, null)"
        key = (kind, cond, id(self.model))
        if self._data_cache is None or self._data_cache[0] != key:
            vals = expression.values(kind, cond)
            by_gene = {p.gene_name: [round(v[0], 3), v[1]] for p in self.model.proteins.values()
                       if (v := vals.get((p.standard_name or "").upper())) is not None}
            label = f"{expression.KINDS[kind]}・{conditions.labels().get(cond, cond)}"
            self._data_cache = (key, f"app.setDataValues({json.dumps(by_gene, ensure_ascii=False)}, "
                                     f"{json.dumps(label, ensure_ascii=False)})")
        return self._data_cache[1]

    def _push_data_values(self) -> None:
        self._sync_data_cond_enabled()
        self.sync_view_choices()
        self._all_js(self.data_js())
        if getattr(self, "del_list", None) is not None:
            self._sync_deletion_panel()
        self._sync_condition_genes()
        if self.selected and self.selected[0] == "node":
            self._refresh_detail_text()

    def sync_view_choices(self) -> None:
        """全表示枠（別ウィンドウも）の、配置・遺伝子の色・線の色の選択肢を今の値にそろえる。"""
        for pane in self._views() if hasattr(self, "panes") else []:
            pane.sync_view_choices()

    def hidden_filters(self) -> dict[str, list[str]]:
        """凡例のチェックを外した（隠す）項目。"""
        return {kind: sorted(keys) for kind, keys in self.hidden.items()}

    def _set_hidden_filters(self, hidden: dict) -> None:
        self.hidden = {kind: set(hidden.get(kind, [])) for kind in ("roles", "types", "effects")}

    def toggle_filter(self, kind: str, key: str, shown: bool, pane: GraphPane | None = None) -> None:
        """どれかの表示枠の凡例でチェックを切り替えたとき。全表示枠に反映し、操作した枠の戻る・進むの履歴に残す。"""
        if kind not in self.hidden:
            return
        for part in key.split("|"):   # 凡例で 1 行にまとめた項目（機構不明の促進・抑制など）
            (self.hidden[kind].discard if shown else self.hidden[kind].add)(part)
        self._on_filter_changed()
        self._record_filters(pane)

    def reset_filters(self, kind: str | None = None, pane: GraphPane | None = None) -> None:
        """凡例のチェックを表示に戻す（kind を指定すると、その欄だけ。effects / types / roles）。"""
        for k in self.hidden:
            if kind is None or k == kind:
                self.hidden[k] = set()
        self._on_filter_changed()
        self._record_filters(pane)

    def _on_filter_changed(self) -> None:
        for pane in self._views():
            pane.push_filters()
        self._save_view()

    DEFAULT_DISPLAY = {"symbols": False, "more": True, "lod": False}   # 凡例の「表示」欄の最初の状態

    def reset_settings(self, pane: GraphPane) -> None:
        """「設定をクリア」: TFs（押した表示枠）・経路・条件・配置・色・線・凡例を最初の状態に戻す。
        注目・拡張・段数は変えない。戻る（◀）で TFs・凡例・条件は元に戻せる。"""
        pane.activate()
        for combo, value in ((self.layout_combo, LAYOUT_CHOICES[0][1]), (self.color_combo, "role"),
                             (self.edge_color_combo, "type"), (self.data_cond_combo, "rapamycin")):
            index = combo.findData(value)
            if index >= 0 and combo.currentIndex() != index:
                combo.setCurrentIndex(index)   # 変えたものだけ地図に反映される
        self._set_hidden_filters({})
        self.display_options = dict(self.DEFAULT_DISPLAY)
        for view in self._views():
            view.push_label_options()
        self.set_condition_top(False)
        self.set_conditions_off(set(), record=False)
        if pane in self.panes:
            self.set_relation_settings([], "off", 1, None)
        pane.trf = set()
        pane._record_history()
        pane.refresh_view(keep_positions=True)
        self._save_view()
        self.statusMessage.emit("設定を最初の状態に戻しました")

    def filter_state(self) -> dict:
        """戻る・進むで覚える、全表示枠に共通の絞り込み（凡例で隠した項目・条件タブで外した条件・最も上流の経路）。"""
        return {"hidden": self.hidden_filters(), "conditions_off": sorted(getattr(self, "conditions_off", ())),
                "conditions_top": self.cond_top.isChecked() if hasattr(self, "cond_top") else False}

    def apply_filter_state(self, state: dict) -> None:
        """戻る・進むで、凡例・条件タブのチェックを履歴の内容に戻す（履歴には新しく残さない）。"""
        if state.get("hidden") != self.hidden_filters():
            self._set_hidden_filters(state.get("hidden", {}))
            self._on_filter_changed()
        self.cond_top.blockSignals(True)
        self.cond_top.setChecked(bool(state.get("conditions_top", False)))
        self.cond_top.blockSignals(False)
        self.set_conditions_off(set(state.get("conditions_off", [])), record=False)

    def _record_filters(self, pane: GraphPane | None = None) -> None:
        """凡例・条件タブのチェックを変えたとき: 操作した表示枠（なければ操作中の枠）の戻る・進むの履歴に残す。"""
        pane = pane or self.active_pane
        if pane is not None and pane.model:
            pane._record_history()

    def _build_trf_panel(self):
        """左側: オレンジで注目した遺伝子を転写制御する転写因子（TFs）の一覧。チェックするとマップの一番上の段に出す。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.trf_layout = QVBoxLayout()
        layout.addLayout(self.trf_layout)
        layout.addStretch(1)
        layout.addWidget(self.help_button("trf"))
        self.trf_scroll = QScrollArea()
        self.trf_scroll.setWidget(panel)
        self.trf_scroll.setWidgetResizable(True)
        self.trf_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 左側はタブで切り替える: 遺伝子（今の地図の遺伝子）・TFs（転写因子）・経路（選んだ遺伝子どうしの経路）など
        self.left_tabs = QTabWidget()
        self.left_tabs.setMinimumWidth(290)   # 「経路」の中身（表示経路の選択肢など）が横にはみ出さない幅
        self.left_tabs.addTab(self._build_gene_panel(), "遺伝子")
        self.left_tabs.addTab(self.trf_scroll, "TF")
        self.left_tabs.addTab(self._build_relation_panel(), "経路")
        self.left_tabs.addTab(self._build_condition_panel(), "条件")
        if expression.has_deletions():
            self.left_tabs.addTab(self._build_deletion_panel(), "破壊株")
        self.saved_views = SavedViewsPanel(self)
        saved_scroll = QScrollArea()
        saved_scroll.setWidget(self.saved_views)
        saved_scroll.setWidgetResizable(True)
        saved_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.left_tabs.addTab(saved_scroll, "登録")

    def _fit_left_width(self) -> None:
        """左側の幅を、タブの見出しを全部並べた幅に合わせる（はみ出すと見出しが矢印で隠れ、広すぎると地図が狭くなる）。"""
        bar = self.left_tabs.tabBar().sizeHint().width()
        if bar <= 0:
            return
        width = max(290, bar + 4)   # 「経路」の中身（表示経路の選択肢など）が横にはみ出さない幅は残す
        self.left_tabs.setMinimumWidth(width)
        total = sum(self.main_splitter.sizes())
        rest = total - width - 300 if total > width + 600 else 910   # 描く前は全体の幅が分からない
        self.main_splitter.setSizes([width, rest, 300])   # 右の説明欄は 300

    # ================= 左の「遺伝子」タブ =================
    def _build_gene_panel(self) -> QWidget:
        """左の「遺伝子」タブ: 操作中の表示枠の地図にある遺伝子を、注目・拡張・そのほかに分けて名前の順に並べる。
        名前で絞り込める。一覧の選択は地図の緑の選択と連動する（Shift・⌘ で複数。押した遺伝子の説明を右側に出し、
        緑で選んでいる遺伝子をもう一度押すと、地図でその遺伝子へ移る）。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.gene_search = QLineEdit()
        self.gene_search.setPlaceholderText("名前で絞り込む")
        self.gene_search.setClearButtonEnabled(True)
        self.gene_search.textChanged.connect(lambda _t: self._refill_gene_list())
        layout.addWidget(self.gene_search)
        self.gene_list = QListWidget()
        self.gene_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.gene_list.itemClicked.connect(self._on_gene_list_clicked)
        layout.addWidget(self.gene_list, 1)
        layout.addWidget(self.help_button("genes"))
        return panel

    def _refill_gene_list(self) -> None:
        """「遺伝子」タブの一覧を、操作中の表示枠の地図の遺伝子で作り直す（注目・拡張・そのほかの順に見出しを付け、
        それぞれ名前の順。緑で選んでいる遺伝子は選んだままにする）。"""
        if getattr(self, "gene_list", None) is None:
            return
        pane = self.active_pane
        words = self.gene_search.text().strip().upper()
        groups: list[tuple[str, list[tuple[str, int]]]] = []
        if self.model is not None and pane is not None and pane.sub:
            shown = {pid: p.gene_name for pid in pane.sub.nodes if (p := self.model.proteins.get(pid)) is not None
                     and (not words or words in p.gene_name.upper())}
            focus = set(pane.focus_ids()) & shown.keys()
            grown = (set(pane.grown) & shown.keys()) - focus
            rest = shown.keys() - focus - grown
            groups = [(title, sorted(((shown[p], p) for p in pids), key=lambda g: g[0].upper()))
                      for title, pids in (("注目", focus), ("拡張", grown), ("そのほか", rest)) if pids]
        self.gene_list.blockSignals(True)
        self.gene_list.clear()
        # 見出しは地図の印と同じ色（注目＝橙・拡張＝紫）の帯にし、数を添える
        heads = {"注目": ("#e65100", "#fff3e0"), "拡張": ("#6a1b9a", "#f3e5f5"), "そのほか": ("#455a64", "#eceff1")}
        for title, genes in groups:
            head = QListWidgetItem(f"{title}（{len(genes)}）")
            head.setFlags(Qt.ItemFlag.NoItemFlags)   # 見出し（選べない）
            font = head.font()
            font.setBold(True)
            head.setFont(font)
            fg, bg = heads[title]
            head.setForeground(QColor(fg))
            head.setBackground(QColor(bg))
            self.gene_list.addItem(head)
            for name, pid in genes:
                item = QListWidgetItem(name)
                item.setData(Qt.ItemDataRole.UserRole, pid)
                self.gene_list.addItem(item)
        self.gene_list.blockSignals(False)
        self._sync_gene_list(pane.picked if pane is not None else [])

    def _sync_gene_list(self, picked: list[int]) -> None:
        """「遺伝子」タブの一覧の選択を、地図の緑の選択にそろえる。"""
        if getattr(self, "gene_list", None) is None:
            return
        chosen = set(picked)
        self.gene_list.blockSignals(True)
        for i in range(self.gene_list.count()):
            item = self.gene_list.item(i)
            pid = item.data(Qt.ItemDataRole.UserRole)
            if pid is not None:
                item.setSelected(pid in chosen)
        self.gene_list.blockSignals(False)

    def _on_gene_list_clicked(self, item) -> None:
        """一覧で遺伝子を押した: 選んでいる遺伝子を地図の緑の選択にし、押した遺伝子の説明を出す。
        緑で選んでいた遺伝子をもう一度押したときは、地図でその遺伝子へ移る（凡例の名前を押したときと同じ）。"""
        pane = self.active_pane
        pid = item.data(Qt.ItemDataRole.UserRole)
        if pane is None or pid is None:
            return
        was_picked = pid in pane.picked
        pids = [p for it in self.gene_list.selectedItems() if (p := it.data(Qt.ItemDataRole.UserRole)) is not None]
        if set(pids) != set(pane.picked):
            pane.js(f"app.setPicked({json.dumps([f'p{p}' for p in pids])})")
        if pid in pids:
            self.show_node(pid, pane)
            if was_picked:
                pane.js(f"app.zoomToGene({json.dumps(f'p{pid}')})")

    # ================= 左の「破壊株」タブ =================
    STRONG_LOG2 = 0.766       # 「大きく変わった」の目安: 1.7 倍（log2 1.7。Deleteome の論文の基準）。要約の数に使う

    def _build_deletion_panel(self) -> QWidget:
        """左の「破壊株」タブ: 遺伝子を 1 つ壊した株での実測（data/expression.db の strains・deletion。
        mRNA は Deleteome、リン酸化は Bodenmiller 2010）を地図で見る。上の一覧は、壊した遺伝子（株）か、今の地図の
        遺伝子。名前で絞り込める。株を押すと、地図をその株の実測で色分けする（地図の上の「色」と、選んだ株を覚える選択肢と
        そろう）。遺伝子を押すと、下の欄にその遺伝子を変化させた株が並ぶ。株は今の地図の遺伝子が変わった株、遺伝子は
        どれかの株で変化した遺伝子を黒、ほかを灰色にする。"""
        try:
            saved = json.loads(VIEW_FILE.read_text(encoding="utf-8")).get("deletion") or {}
        except (OSError, ValueError, AttributeError):
            saved = {}
        panel = QWidget()
        outer = QVBoxLayout(panel)
        # 探し方: 壊した遺伝子（株）を選ぶ／変化した遺伝子を選び、その遺伝子を変化させた破壊株を探す
        search_row = QHBoxLayout()
        self.del_search_mode = QComboBox()
        for text, key in (("壊した遺伝子", "strain"), ("変化した遺伝子", "changed")):
            self.del_search_mode.addItem(text, key)
        self.del_search_mode.setToolTip("壊した遺伝子: 株を、壊した遺伝子の名前で絞り込みます\n"
                                        "変化した遺伝子: 今の地図の遺伝子を選ぶと、その遺伝子の発現（リン酸化）を変化させた"
                                        "破壊株を、名前の順に並べます。どれかの破壊株で変化した遺伝子は黒、変化しなかった遺伝子は灰色")
        search_row.addWidget(self.del_search_mode)
        self.del_search = QLineEdit()
        self.del_search.setPlaceholderText("名前で絞り込む")
        self.del_search.setClearButtonEnabled(True)
        search_row.addWidget(self.del_search, 1)
        self.del_gene: str | None = None   # 「変化した遺伝子」で選んだ遺伝子の ORF
        outer.addLayout(search_row)
        self.del_list = QListWidget()
        self.del_list.setMinimumHeight(120)
        self.del_list.setMaximumHeight(200)
        self.del_list.itemClicked.connect(self._on_deletion_strain_clicked)
        # 選択が外れたら色を戻す（一覧を作り直す途中で一度空になるので、作り終えてから見る）
        self.del_list.itemSelectionChanged.connect(lambda: QTimer.singleShot(0, self._check_deletion_selection))
        outer.addWidget(self.del_list)
        # 選択肢の中身を作ってからつなぐ（作る途中の切り替えで一覧を作り直さない）
        self.del_search_mode.currentIndexChanged.connect(lambda _i: self._on_deletion_search_mode())
        self.del_search.textChanged.connect(lambda _t: self._refill_deletion_list())
        kinds = QHBoxLayout()
        kinds.addWidget(QLabel("データ"))
        self.del_kind_group = QButtonGroup(self)
        self.del_kind_buttons = {}
        for key, text in (("del_mrna", "mRNA"), ("del_phospho", "リン酸化")):
            b = QRadioButton(text)
            self.del_kind_group.addButton(b)
            self.del_kind_buttons[key] = b
            kinds.addWidget(b)
            b.toggled.connect(lambda on, k=key: on and self._on_deletion_kind(k))
        kinds.addStretch(1)
        start = saved.get("kind") if saved.get("kind") in self.del_kind_buttons else "del_mrna"
        self.del_kind_buttons[start].blockSignals(True)   # 作り終える前に切り替えの処理を走らせない
        self.del_kind_buttons[start].setChecked(True)
        self.del_kind_buttons[start].blockSignals(False)
        outer.addLayout(kinds)
        self.del_summary = QLabel()
        self.del_summary.setWordWrap(True)
        self.del_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        outer.addWidget(self.del_summary)
        self.del_changes = QListWidget()
        self.del_changes.setMinimumHeight(160)
        self.del_changes.setToolTip("選んだ破壊株で変わった、今の地図の遺伝子（名前の順）。押すと右側に説明を出します")
        self.del_changes.itemClicked.connect(self._on_deletion_change_clicked)
        self.del_changes.itemSelectionChanged.connect(lambda: QTimer.singleShot(0, self._check_deletion_selection))
        outer.addWidget(self.del_changes, 1)
        outer.addWidget(self.help_button("deletion"))
        self._refill_deletion_list()
        self._sync_deletion_panel()
        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return scroll

    def _del_kind(self) -> str:
        """破壊株タブで選んでいるデータの種類（del_mrna / del_phospho）。"""
        return next((k for k, b in self.del_kind_buttons.items() if b.isChecked()), "del_mrna")

    def _on_deletion_search_mode(self) -> None:
        self._refill_deletion_list()

    def _on_deletion_map_changed(self) -> None:
        """地図の遺伝子が変わった: 変化した遺伝子で探しているときは一覧を今の地図の遺伝子で作り直し、
        壊した遺伝子で探しているときは株の黒と灰色だけを塗り直す。"""
        if getattr(self, "del_list", None) is None:
            return
        if self.del_search_mode.currentData() == "changed":
            self._refill_deletion_list()
        else:
            self._update_deletion_presence()
            self._sync_deletion_panel()   # 下の欄（その株で変わった、今の地図の遺伝子）も作り直す

    def _map_orfs(self) -> set[str]:
        """操作中の表示枠の地図にある遺伝子の ORF。"""
        if self.model is None:
            return set()
        pane = self.active_pane
        return {(self.model.proteins[pid].standard_name or "").upper()
                for pid in (pane.sub.nodes if pane is not None and pane.sub else []) if pid in self.model.proteins} - {""}

    def _refill_deletion_list(self) -> None:
        """破壊株タブの上の一覧を作り直す。壊した遺伝子で探すときは株、変化した遺伝子で探すときは今の地図の遺伝子。
        名前か ORF に検索欄の文字を含むものを名前の順（何も入れていなければ全部）。"""
        mode = self.del_search_mode.currentData()
        entries: list[tuple[str, str]] = []
        words = self.del_search.text().strip().upper()
        if mode == "strain":
            for orf, name, kinds in expression.deletion_strains():
                if words and words not in name.upper() and words not in orf.upper():
                    continue
                tag = "（mRNA・リン酸化）" if len(kinds) > 1 else "（リン酸化）" if kinds == {"phospho"} else ""
                entries.append((orf, name + tag))
            current = self.deletion_strain()
        else:
            names = expression.gene_names()
            for orf in self._map_orfs():
                name = names.get(orf, orf)
                if not words or words in name.upper() or words in orf:
                    entries.append((orf, name))
            entries.sort(key=lambda e: e[1].upper())
            current = self.del_gene
        self.del_list.clear()
        for orf, label in entries:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, orf)
            self.del_list.addItem(item)
            if orf and orf == current:
                self.del_list.setCurrentItem(item)
        self._update_deletion_presence()
        self._sync_deletion_panel()

    def _on_deletion_strain_clicked(self, item) -> None:
        orf = item.data(Qt.ItemDataRole.UserRole)
        if not orf:
            return
        if self.del_search_mode.currentData() == "changed":
            self.del_gene = orf
            self._sync_deletion_panel()
            pid = self._orf_pid(orf)
            if pid is not None:
                self.show_node(pid, self.active_pane)   # 選んだ遺伝子の説明も出す
        elif self.deletion_kind() and orf == self.deletion_strain():
            self.del_list.clearSelection()   # 色分けしている株をもう一度押した: 選択を外し、色を戻す
        else:
            self._choose_strain(orf)

    def _update_deletion_presence(self) -> None:
        """破壊株タブの上の一覧を黒と灰色に塗り分ける（選んでいるデータで見る。変わった = 取り込んだ値。mRNA は
        p < 0.05 かつ 1.4 倍以上、リン酸化は報告された部位）。株は、操作中の表示枠の地図の遺伝子がその株で変わっていれば黒。
        遺伝子（今の地図の遺伝子）は、どれかの株で変化していれば黒。"""
        if getattr(self, "del_list", None) is None or self.model is None:
            return
        shown = self._map_orfs()
        changed = self.del_search_mode.currentData() == "changed"
        base = {"del_mrna": "mrna", "del_phospho": "phospho"}[self._del_kind()]
        targets = expression.deletion_targets(base)
        moved = expression.changed_genes(base) if changed else set()
        black, gray = QColor("#212121"), QColor("#9e9e9e")
        for i in range(self.del_list.count()):
            item = self.del_list.item(i)
            orf = item.data(Qt.ItemDataRole.UserRole)
            if changed:
                item.setForeground(black if orf in moved else gray)
                item.setToolTip("どれかの破壊株で変化しました" if orf in moved else "どの破壊株でも変化していません")
                continue
            hits = [g for g in targets.get(orf, {}) if g in shown]
            item.setForeground(black if hits else gray)
            item.setToolTip(f"今の地図の遺伝子のうち {len(hits)} 個がこの株で変わりました" if hits else
                            "今の地図の遺伝子は、この株で変わっていません")

    def _choose_strain(self, orf: str) -> None:
        """一覧で株を選んだ: 選んだ株を覚える選択肢（画面には出さない）をそろえ、地図をその株の実測（選んでいるデータ）で色分けする。
        その種類の測定がない株なら、ある方の種類に切り替える。"""
        kinds = {m["kind"] for m in expression.strain_info(orf)}
        kind = self._del_kind()
        if {"del_mrna": "mrna", "del_phospho": "phospho"}[kind] not in kinds and kinds:
            kind = "del_mrna" if "mrna" in kinds else "del_phospho"
        i = self.del_strain_combo.findData(orf)
        if i >= 0:
            self.del_strain_combo.setCurrentIndex(i)
        self._color_by_deletion(kind)
        self._sync_deletion_panel()

    def _on_deletion_kind(self, kind: str) -> None:
        if self.del_search_mode.currentData() != "strain":
            self._refill_deletion_list()     # 変化した遺伝子で探しているときは、データの種類で一覧と結果が変わる
        else:
            self._update_deletion_presence()
        if self.deletion_kind() and self.color_mode() != kind:
            self._color_by_deletion(kind)   # 色分け中なら地図も変える
        else:
            self._sync_deletion_panel()
        self._save_view()

    def _sync_deletion_panel(self) -> None:
        """破壊株タブの表示を、地図の上の「色」と、選んでいる株にそろえる。"""
        orf = self.deletion_strain()
        dkind = self.deletion_kind()
        for w in self.del_kind_buttons.values():
            w.blockSignals(True)
        if dkind:
            self.del_kind_buttons[dkind].setChecked(True)
        for w in self.del_kind_buttons.values():
            w.blockSignals(False)
        mark = self.del_gene if self.del_search_mode.currentData() == "changed" else orf
        if mark == orf and dkind is None:   # 破壊株で色分けしていない（条件で色分けしたなど）: 株の選択を外す
            self.del_list.blockSignals(True)
            self.del_list.clearSelection()
            self.del_list.blockSignals(False)
            mark = None
        for i in range(self.del_list.count()):
            item = self.del_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == mark:
                if self.del_list.currentItem() is not item:
                    self.del_list.setCurrentItem(item)
                    self.del_list.scrollToItem(item)
                break
        base = {"del_mrna": "mrna", "del_phospho": "phospho"}[self._del_kind()]
        self.del_changes.clear()
        if self.del_search_mode.currentData() == "changed":
            for b in self.del_kind_buttons.values():
                b.setEnabled(True)
            self._show_gene_changes(base)
            return
        info = {m["kind"]: m for m in expression.strain_info(orf)} if orf else {}
        for key, b in self.del_kind_buttons.items():
            b.setEnabled({"del_mrna": "mrna", "del_phospho": "phospho"}[key] in info or dkind is None)
        name = self.del_strain_combo.currentText()
        if dkind is None:   # 地図を破壊株で色分けしていない（最初・色を戻したとき）は、下の欄に何も出さない
            self.del_summary.setText("")
            return
        if base not in info:
            self.del_summary.setText(f"{name} の破壊株の{'mRNA' if base == 'mrna' else 'リン酸化'}の測定はありません"
                                     if orf else "")
            return
        pts = [x for x in expression.strain_changes(orf) if x.kind == base]
        up = sum(1 for x in pts if x.value > 0)
        strong = sum(1 for x in pts if abs(x.value) >= self.STRONG_LOG2)
        unit = "部位" if base == "phospho" else "遺伝子"
        shown = [x for x in pts if x.gene in self._map_orfs()]   # 一覧に出すのは今の地図の遺伝子だけ
        self.del_summary.setText(f"<b>{html.escape(name)} 破壊株</b>（{html.escape(info[base]['source'])}）<br>"
                                 f"上がった{unit} {up}・下がった{unit} {len(pts) - up}<br>うち 1.7 倍以上 {strong}"
                                 f"・今の地図 {len(shown)}")
        for x in sorted(shown, key=lambda x: (x.gene_name.upper(), x.site or "")):   # 名前の順
            text = f"{x.gene_name}{(' ' + x.site) if x.site else ''}   {x.value:+.2f}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, ("gene", x.gene))
            item.setForeground(QColor("#c62828" if x.value > 0 else "#1565c0"))
            self.del_changes.addItem(item)

    def _show_gene_changes(self, base: str) -> None:
        """「変化した遺伝子」で選んだ遺伝子について、下の欄に、その遺伝子を変化させた破壊株（壊した遺伝子と値）を
        名前の順に出す（遺伝子を選んでいなければ何も出さない）。株を押すと地図をその株で色分けする。"""
        gene = self.del_gene
        if not gene:
            self.del_summary.setText("")
            return
        name = expression.gene_names().get(gene, gene)
        pts = sorted((x for x in expression.changed_in(gene) if x.kind == base), key=lambda x: (x.strain_name.upper(), x.site or ""))
        kind = "mRNA" if base == "mrna" else "リン酸化"
        if not pts:
            self.del_summary.setText(f"{html.escape(name)} の{kind}が変化した破壊株はありません")
            return
        sources = "・".join(sorted({x.source for x in pts}))
        up = sum(1 for x in pts if x.value > 0)
        self.del_summary.setText(f"<b>{html.escape(name)} の{kind}を変化させた破壊株</b><br>"
                                 f"上げた株 {up}・下げた株 {len(pts) - up}（{html.escape(sources)}）")
        current = self.deletion_strain() if self.deletion_kind() else None
        for x in pts:
            item = QListWidgetItem(f"{x.strain_name}{(' ' + x.site) if x.site else ''}   {x.value:+.2f}")
            item.setData(Qt.ItemDataRole.UserRole, ("strain", x.strain))
            item.setForeground(QColor("#c62828" if x.value > 0 else "#1565c0"))
            self.del_changes.addItem(item)
            if x.strain == current:
                self.del_changes.setCurrentItem(item)

    def _orf_pid(self, orf: str) -> int | None:
        if self.model is None:
            return None
        cache = getattr(self, "_orf_pid_cache", None)
        if cache is None or cache[0] != id(self.model):
            cache = (id(self.model), {(q.standard_name or "").upper(): pid for pid, q in self.model.proteins.items()})
            self._orf_pid_cache = cache
        return cache[1].get(orf.upper())

    def _on_deletion_change_clicked(self, item) -> None:
        """下の欄を押した: 変わった遺伝子ならその説明を出す。破壊株（「変化した遺伝子」のとき）なら地図をその株で色分けし、
        壊した遺伝子の説明を出す。"""
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        kind, orf = data
        if kind == "strain":
            if self.deletion_kind() and orf == self.deletion_strain():
                self.del_changes.clearSelection()   # 色分けしている株をもう一度押した: 選択を外し、色を戻す
                return
            self._choose_strain(orf)
        pid = self._orf_pid(orf)
        if pid is not None:
            self.show_node(pid, self.active_pane)

    def _build_condition_panel(self) -> QWidget:
        """左の「条件」タブ。左上で見るものを切り替える（どちらも 上に条件の一覧・中ほどに見方・下に今の地図のもの、の形）。
        経路: 関係が働く条件（data/conditions.csv。YEASTRACT+ の環境条件の群・小群と、細胞周期の時期）をチェックで選ぶ。
        すべてオンなら普段の地図。外した条件があれば、チェックした条件の関係（線）とその両端の遺伝子だけを地図で目立たせ、
        ほかは薄くする。並べるのは DB の関係に付いている条件だけ。下の欄に、条件に合う今の地図の線。
        遺伝子: 測定のある条件を 1 つ選び、その条件の測定で地図の遺伝子を色分けする（下の欄に今の地図の遺伝子と値）。"""
        try:
            view = json.loads(VIEW_FILE.read_text(encoding="utf-8"))
            saved, top = view.get("conditions_off", []), bool(view.get("conditions_top", False))
        except (OSError, ValueError, AttributeError):
            saved, top = [], False
        self.conditions_off: set[str] = {k for k in saved if k in self._all_condition_keys()}
        self._cond_items: dict[str, QTreeWidgetItem] = {}   # 経路の条件 → 一覧の項目
        self._cond_open: set[str] = set()                   # 開いている群（経路・遺伝子で共通）
        self._cond_syncing = False
        page = QWidget()
        layout = QVBoxLayout(page)
        top_row = QHBoxLayout()
        self.cond_mode = QComboBox()
        self.cond_mode.addItem("経路", "edges")
        if expression.available():
            self.cond_mode.addItem("遺伝子", "genes")
        self.cond_mode.setToolTip("経路: チェックした条件で報告された関係（線）を目立たせます\n"
                                  "遺伝子: 条件を選ぶと、その条件での発現・リン酸化・タンパク質量で地図の遺伝子を色分けします")
        top_row.addWidget(self.cond_mode)
        self.cond_search = QLineEdit()
        self.cond_search.setPlaceholderText("名前で絞り込む")
        self.cond_search.setClearButtonEnabled(True)
        self.cond_search.textChanged.connect(lambda _t: self._apply_condition_search())
        top_row.addWidget(self.cond_search, 1)
        layout.addLayout(top_row)

        # ---- 経路 ----
        self.cond_edges_part = QWidget()
        edges = QVBoxLayout(self.cond_edges_part)
        edges.setContentsMargins(0, 0, 0, 0)
        self.cond_tree = self._condition_tree()
        self.cond_tree.itemChanged.connect(self._on_condition_item_changed)
        edges.addWidget(self.cond_tree)
        buttons = QHBoxLayout()
        for text, on in (("すべて選択", True), ("すべて解除", False)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, on=on: self.set_conditions_off(set() if on else set(self._all_condition_keys())))
            buttons.addWidget(b)
        edges.addLayout(buttons)
        # 最も上流の経路: 条件に合う線のうち、経路の起点になるもの（上流に同じく条件に合う線がない線）だけを目立たせる
        self.cond_top = QCheckBox("最も上流の経路を表示")
        self.cond_top.setChecked(top)
        self.cond_top.toggled.connect(lambda _on: (self._all_js(self.condition_js()), self._save_view(),
                                                   self._record_filters()))
        edges.addWidget(self.cond_top)   # 「すべて選択」「すべて解除」では変わらない
        self.cond_edges_summary = QLabel()
        self.cond_edges_summary.setWordWrap(True)
        self.cond_edges_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        edges.addWidget(self.cond_edges_summary)
        self.cond_edges_list = QListWidget()
        self.cond_edges_list.setMinimumHeight(160)
        self.cond_edges_list.setToolTip("チェックした条件に合う、今の地図の線（名前の順）。押すと右側に線の説明を出します")
        self.cond_edges_list.itemClicked.connect(self._on_condition_edge_clicked)
        edges.addWidget(self.cond_edges_list, 1)
        edges.addWidget(self.help_button("conditions"))
        layout.addWidget(self.cond_edges_part, 1)

        # ---- 遺伝子 ----
        self.cond_genes_part = QWidget()
        genes = QVBoxLayout(self.cond_genes_part)
        genes.setContentsMargins(0, 0, 0, 0)
        self.cond_gene_tree = self._condition_tree()
        self.cond_gene_tree.itemChanged.connect(self._on_condition_gene_checked)
        genes.addWidget(self.cond_gene_tree)
        kinds = QHBoxLayout()
        kinds.addWidget(QLabel("データ"))
        self.cond_kind_group = QButtonGroup(self)
        self.cond_kind_buttons: dict[str, QRadioButton] = {}
        for key, text in expression.SHORT.items():
            b = QRadioButton(text)
            self.cond_kind_group.addButton(b)
            self.cond_kind_buttons[key] = b
            kinds.addWidget(b)
            b.toggled.connect(lambda on, k=key: on and self._on_condition_kind(k))
        kinds.addStretch(1)
        self.cond_kind_buttons["mrna"].blockSignals(True)
        self.cond_kind_buttons["mrna"].setChecked(True)
        self.cond_kind_buttons["mrna"].blockSignals(False)
        genes.addLayout(kinds)
        self.cond_summary = QLabel()
        self.cond_summary.setWordWrap(True)
        self.cond_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        genes.addWidget(self.cond_summary)
        self.cond_values = QListWidget()
        self.cond_values.setMinimumHeight(160)
        self.cond_values.setToolTip("選んだ条件での、今の地図の遺伝子の値（名前の順）。押すと右側に説明を出します")
        self.cond_values.itemClicked.connect(self._on_condition_value_clicked)
        genes.addWidget(self.cond_values, 1)
        genes.addWidget(self.help_button("conditions"))
        self.cond_genes_part.setVisible(False)
        layout.addWidget(self.cond_genes_part, 1)
        self.cond_mode.currentIndexChanged.connect(lambda _i: self._on_condition_mode())
        self._refill_condition_list()
        return page

    class _DimGrayDelegate(QStyledItemDelegate):
        """灰色の項目（地図に関係しない条件）は、チェックも薄く描く（押せるまま。描き方だけ使えない見た目にする）。"""
        def initStyleOption(self, option, index):
            super().initStyleOption(option, index)
            fg = index.data(Qt.ItemDataRole.ForegroundRole)
            if fg is not None and fg.color().name().lower() == "#9e9e9e":
                option.state &= ~QStyle.StateFlag.State_Enabled

    def _condition_tree(self) -> QTreeWidget:
        """条件の一覧（群で開閉。経路・遺伝子で同じ形）。開いた群を覚える。"""
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setItemDelegate(self._DimGrayDelegate(tree))
        tree.setMinimumHeight(160)
        tree.setMaximumHeight(260)
        tree.itemExpanded.connect(lambda it: self._cond_open.add(it.text(0).split("（")[0]))
        tree.itemCollapsed.connect(lambda it: self._cond_open.discard(it.text(0).split("（")[0]))
        return tree

    @staticmethod
    def _group_item(tree: QTreeWidget, group: str, n: int, checkable: bool) -> QTreeWidgetItem:
        item = QTreeWidgetItem([f"{group}（{n}）" if n > 1 else group])
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        flags = Qt.ItemFlag.ItemIsEnabled
        if checkable:
            flags |= Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate
        item.setFlags(flags)
        tree.addTopLevelItem(item)
        return item

    def _apply_condition_search(self) -> None:
        """両方の一覧を、名前に検索欄の文字を含む条件に絞る（群の名前に含むなら群ごと残す）。絞っている間は群を開く。"""
        words = self.cond_search.text().strip().lower()
        for tree in (self.cond_tree, self.cond_gene_tree):
            for i in range(tree.topLevelItemCount()):
                group = tree.topLevelItem(i)
                name = group.text(0).split("（")[0]
                any_shown = False
                for j in range(group.childCount()):
                    child = group.child(j)
                    shown = not words or words in child.text(0).lower() or words in name.lower()
                    child.setHidden(not shown)
                    any_shown |= shown
                group.setHidden(not any_shown)
                group.setExpanded(bool(words and any_shown) or name in self._cond_open)

    def _on_condition_mode(self) -> None:
        genes = self.cond_mode.currentData() == "genes"
        self.cond_edges_part.setVisible(not genes)
        self.cond_genes_part.setVisible(genes)
        if not genes and self.data_kind() is not None:
            self._end_tab_color()   # 遺伝子の条件の選択をやめた: 役割の色に戻す
        self._sync_condition_genes()
        self._update_condition_edges()

    # ---- 条件タブの「遺伝子」 ----
    def _cond_gene_items(self) -> list[QTreeWidgetItem]:
        """「遺伝子」の一覧の条件の項目（群の中の項目）。"""
        out = []
        for i in range(self.cond_gene_tree.topLevelItemCount()):
            group = self.cond_gene_tree.topLevelItem(i)
            out += [group.child(j) for j in range(group.childCount())]
        return out

    def _cond_kind(self) -> str:
        """条件タブ（遺伝子）で選んでいるデータの種類（mrna / phospho / protein）。"""
        return next((k for k, b in self.cond_kind_buttons.items() if b.isChecked()), "mrna")

    def _refill_condition_list(self) -> None:
        """「遺伝子」の条件の一覧を作り直す（群で分ける。選んでいるデータの種類の測定がない条件は灰色）。"""
        if getattr(self, "cond_gene_tree", None) is None:
            return
        have = expression.condition_kinds()
        kind = self._cond_kind()
        keys = set(expression.condition_keys())
        groups: dict[str, list] = {}
        for c in conditions.load():
            if c.key in keys:
                groups.setdefault(c.group, []).append(c)
        self._cond_syncing = True
        self.cond_gene_tree.clear()
        for group, items in groups.items():
            head = self._group_item(self.cond_gene_tree, group, len(items), checkable=False)
            ok_any = False
            for c in items:
                item = QTreeWidgetItem([c.label])
                item.setData(0, Qt.ItemDataRole.UserRole, c.key)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Unchecked)
                ok = kind in have.get(c.key, set())
                ok_any |= ok
                item.setForeground(0, QColor("#212121" if ok else "#9e9e9e"))
                item.setToolTip(0, "、".join(expression.SHORT[k] for k in expression.SHORT if k in have.get(c.key, set()))
                                + " の測定があります")
                head.addChild(item)
            head.setForeground(0, QColor("#212121" if ok_any else "#9e9e9e"))
        self._cond_syncing = False
        self._apply_condition_search()
        self._sync_condition_genes()

    def _on_condition_gene_checked(self, item, _column: int = 0) -> None:
        """「遺伝子」の条件のチェックを切り替えた。地図の色は 1 つの条件でしか塗れないので、チェックした条件の測定で色分けし、
        ほかのチェックは外す（選んでいる種類の測定がなければ、ある種類に切り替える）。チェックをすべて外すと役割の色に戻す。"""
        if self._cond_syncing:
            return
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if not key:
            return
        if item.checkState(0) != Qt.CheckState.Checked:
            if self.data_kind() is not None and key == self.data_condition():
                self._end_tab_color()
            return
        have = expression.condition_kinds().get(key, set())
        kind = self._cond_kind()
        if kind not in have and have:
            kind = next(k for k in expression.SHORT if k in have)
            for k, b in self.cond_kind_buttons.items():
                b.blockSignals(True)
                b.setChecked(k == kind)
                b.blockSignals(False)
        self.data_cond_combo.blockSignals(True)
        self.data_cond_combo.setCurrentIndex(max(0, self.data_cond_combo.findData(key)))
        self.data_cond_combo.blockSignals(False)
        self._save_view()
        QTimer.singleShot(0, lambda: self._set_color(kind))   # チェックの知らせの中では一覧を作り直さない

    def _on_condition_kind(self, kind: str) -> None:
        self._refill_condition_list()
        if self.data_kind() is not None and kind != self.data_kind():
            self._set_color(kind)   # 色分け中なら地図も変える

    def _sync_condition_genes(self) -> None:
        """「遺伝子」の表示を、今の色分けにそろえる（色分けしている条件を選び、下の欄に今の地図の遺伝子と値）。"""
        if getattr(self, "cond_gene_tree", None) is None:
            return
        kind, cond = self.data_kind(), self.data_condition()
        self._cond_syncing = True
        for item in self._cond_gene_items():
            on = kind is not None and item.data(0, Qt.ItemDataRole.UserRole) == cond
            item.setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            if on:
                item.parent().setExpanded(True)
        self._cond_syncing = False
        if kind is not None:
            for k, b in self.cond_kind_buttons.items():
                b.blockSignals(True)
                b.setChecked(k == kind)
                b.blockSignals(False)
        have = expression.condition_kinds().get(cond, set()) if kind is not None else set(expression.SHORT)
        for k, b in self.cond_kind_buttons.items():
            b.setEnabled(k in have)
        self.cond_values.clear()
        if kind is None or not cond or self.model is None:
            self.cond_summary.setText("")
            return
        vals = expression.values(kind, cond)
        names = expression.gene_names()
        rows = sorted(((names.get(orf, orf), orf, v, d) for orf, (v, d) in vals.items() if orf in self._map_orfs()),
                      key=lambda r: r[0].upper())
        up = sum(1 for r in rows if r[2] > 0)
        label = conditions.labels().get(cond, cond)
        self.cond_summary.setText(f"<b>{html.escape(label)} の{expression.KINDS[kind]}</b><br>"
                                  f"今の地図 {len(rows)}（上がった {up}・下がった {len(rows) - up}）")
        for name, orf, v, d in rows:
            item = QListWidgetItem(f"{name}{(' ' + d) if d else ''}   {v:+.2f}")
            item.setData(Qt.ItemDataRole.UserRole, orf)
            item.setForeground(QColor("#c62828" if v > 0 else "#1565c0" if v < 0 else "#555"))
            self.cond_values.addItem(item)

    def _on_condition_value_clicked(self, item) -> None:
        pid = self._orf_pid(item.data(Qt.ItemDataRole.UserRole) or "")
        if pid is not None:
            self.show_node(pid, self.active_pane)

    def _rebuild_condition_panel(self) -> None:
        """条件タブ（経路）の一覧を、今の DB の関係に付いている条件で作り直す（DB を読み直したとき）。"""
        counts: dict[str, int] = {}
        for it in self.model.interactions.values():
            for k in conditions.split(it.conditions) or [conditions.NONE_KEY]:
                counts[k] = counts.get(k, 0) + 1
        groups: dict[str, list] = {}
        for c in conditions.load():
            if counts.get(c.key):
                groups.setdefault(c.group, []).append((c.key, c.label))
        groups["記録なし"] = [(conditions.NONE_KEY, conditions.NONE_LABEL)]
        self._cond_syncing = True
        self.cond_tree.clear()
        self._cond_items = {}
        for group, items in groups.items():
            head = self._group_item(self.cond_tree, group, len(items), checkable=True)
            for key, label in items:
                item = QTreeWidgetItem([label])
                item.setData(0, Qt.ItemDataRole.UserRole, key)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Unchecked if key in self.conditions_off else Qt.CheckState.Checked)
                head.addChild(item)
                self._cond_items[key] = item
        self._cond_syncing = False
        self._apply_condition_search()
        self._update_condition_presence()

    def _on_condition_item_changed(self, item, _column: int = 0) -> None:
        """経路の一覧でチェックを切り替えた（群のチェックは中の条件をまとめて切り替える。そのとき条件の数だけ
        知らせが来るので、まとめて 1 回だけ地図に送る）。"""
        if self._cond_syncing or getattr(self, "_cond_pending", False):
            return
        self._cond_pending = True
        QTimer.singleShot(0, self._apply_condition_checks)

    def _apply_condition_checks(self) -> None:
        self._cond_pending = False
        off = {k for k, it in self._cond_items.items() if it.checkState(0) != Qt.CheckState.Checked}
        if off == self.conditions_off:
            return
        self.conditions_off = off
        self._all_js(self.condition_js())
        self._save_view()
        self._record_filters()
        self._update_condition_edges()

    def _update_condition_presence(self) -> None:
        """条件タブ: 操作中の表示枠の地図に線がある条件はふだんの色、ない条件は灰色にする（凡例と同じ。操作はできる）。"""
        pane = self.active_pane
        if not self.model or pane is None or not getattr(self, "_cond_items", None):
            return
        present = set()
        for it in self.model.interactions.values():
            if self.model._edge_shown(it, pane.sub, False):
                present.update(conditions.split(it.conditions) or [conditions.NONE_KEY])
        black, gray = QColor("#212121"), QColor("#9e9e9e")
        self._cond_syncing = True
        for i in range(self.cond_tree.topLevelItemCount()):
            group = self.cond_tree.topLevelItem(i)
            keys = [group.child(j).data(0, Qt.ItemDataRole.UserRole) for j in range(group.childCount())]
            for j in range(group.childCount()):
                group.child(j).setForeground(0, black if keys[j] in present else gray)
            group.setForeground(0, black if present & set(keys) else gray)
        self._cond_syncing = False
        self._update_condition_edges()

    def _update_condition_edges(self) -> None:
        """経路の下の欄: チェックした条件に合う、今の地図の線（名前の順）。"""
        if getattr(self, "cond_edges_list", None) is None or self.model is None:
            return
        pane = self.active_pane
        self.cond_edges_list.clear()
        if pane is None or self.cond_mode.currentData() != "edges":
            self.cond_edges_summary.setText("")
            return
        names = {pid: p.gene_name for pid, p in self.model.proteins.items()}
        rows = []
        for it in self.model.interactions.values():
            if not self.model._edge_shown(it, pane.sub, False):
                continue
            keys = conditions.split(it.conditions) or [conditions.NONE_KEY]
            if all(k in self.conditions_off for k in keys):
                continue
            rows.append((names.get(it.source_id, "?"), names.get(it.target_id, "?"), it.id, it.effect))
        rows.sort(key=lambda r: (r[0].upper(), r[1].upper()))
        self.cond_edges_summary.setText(f"今の地図の線 {len(rows)} 本" + ("" if self.conditions_off else "（すべての条件）"))
        for src, tgt, iid, effect in rows:
            item = QListWidgetItem(f"{src} → {tgt}")
            item.setData(Qt.ItemDataRole.UserRole, iid)
            item.setForeground(QColor("#c62828" if effect == "activate" else "#1565c0" if effect == "inhibit" else "#555"))
            self.cond_edges_list.addItem(item)

    def _on_condition_edge_clicked(self, item) -> None:
        iid = item.data(Qt.ItemDataRole.UserRole)
        if iid is not None:
            self.show_edge(iid, self.active_pane)

    def condition_js(self) -> str:
        """地図に条件タブの状態を渡す JavaScript（外した条件と、「最も上流の経路を表示」）。"""
        return f"app.setConditionFilter({json.dumps(sorted(self.conditions_off))}, {json.dumps(self.cond_top.isChecked())})"

    def set_condition_top(self, on: bool) -> None:
        """「最も上流の経路を表示」を決める（登録の呼び出し。戻る・進むの履歴には残さない）。"""
        self.cond_top.blockSignals(True)
        self.cond_top.setChecked(bool(on))
        self.cond_top.blockSignals(False)
        self._all_js(self.condition_js())
        self._save_view()

    def _all_condition_keys(self) -> list[str]:
        return [c.key for c in conditions.load()] + [conditions.NONE_KEY]

    def set_conditions_off(self, off: set[str], record: bool = True) -> None:
        """外す条件をまとめて決める（「すべて選択」「すべて解除」、登録の呼び出し、戻る・進む）。"""
        self.conditions_off = {k for k in off if k in self._all_condition_keys()}
        self._cond_syncing = True
        for k, item in self._cond_items.items():
            item.setCheckState(0, Qt.CheckState.Unchecked if k in self.conditions_off else Qt.CheckState.Checked)
        self._cond_syncing = False
        self._all_js(self.condition_js())
        self._save_view()
        if record:
            self._record_filters()
        self._update_condition_edges()

    def _build_relation_panel(self) -> QWidget:
        """左の「経路」タブ: 注目・拡張している遺伝子のうちチェックしたものが、どの経路でどう関わるかを一覧にする。
        一覧の経路を選ぶと地図で強調する（tor_app/relation_paths.py）。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.rel_genes_box = QGroupBox("対象の遺伝子")
        genes_layout = QVBoxLayout(self.rel_genes_box)
        self.rel_list = QListWidget()
        # 高さは行数に合わせて固定する（空いた高さに広がらない。表示経路を切り替えて下の一覧が出ても縮まない）
        self.rel_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.rel_list.itemChanged.connect(self._on_relation_item_changed)
        check_all = QPushButton("すべて選択")
        check_all.setToolTip("一覧の遺伝子をすべてチェックします")
        check_all.clicked.connect(self._check_all_relation_genes)
        clear = QPushButton("すべて解除")
        clear.clicked.connect(self._clear_relation_checks)
        buttons = QHBoxLayout()
        buttons.addWidget(check_all)
        buttons.addWidget(clear)
        for w in (self.rel_list,):
            genes_layout.addWidget(w)
        genes_layout.addLayout(buttons)
        self._rel_checked: list[int] = []   # 一覧でチェックした遺伝子（チェックした順）

        settings = QHBoxLayout()
        settings.addWidget(QLabel("基準の遺伝子:"))
        self.rel_anchor = QComboBox()
        self.rel_anchor.setToolTip("チェックした遺伝子から選びます。選ぶと、基準の遺伝子が端にある経路だけを一覧にします。\n"
                                   "「基準の遺伝子へ・から作用している経路」では必ず選びます")
        settings.addWidget(self.rel_anchor, 1)
        steps_row = QHBoxLayout()
        steps_row.addWidget(QLabel("経路の段数:"))
        self.rel_steps = QSpinBox()
        self.rel_steps.setRange(1, 3)
        self.rel_steps.setValue(1)
        self.rel_steps.setToolTip("段数がちょうどこの数の経路だけを一覧にします。1 なら直接の関係です。複合体は 1 つとして数えます")
        steps_row.addWidget(self.rel_steps)
        steps_row.addWidget(QLabel("段"))
        steps_row.addStretch(1)

        modes_box = self.rel_modes_box = QGroupBox("表示経路")
        self.rel_modes_layout = QVBoxLayout(modes_box)
        self.rel_mode_group = QButtonGroup(self)
        self._rel_mode_buttons: dict[str, QRadioButton] = {}
        for key, (label, desc, _) in GraphPane.RELATION_MODES.items():
            rb = QRadioButton(label)
            rb.setToolTip(desc)
            rb.setProperty("mode", key)
            rb.setChecked(key == "off")
            self.rel_mode_group.addButton(rb)
            self.rel_modes_layout.addWidget(rb)
            self._rel_mode_buttons[key] = rb
        # 経路の一覧（選んだ表示経路の下に出す）: 同じ上流ごとにまとめ、開くと経路が並ぶ
        self.rel_paths_box = QWidget()
        paths_layout = QVBoxLayout(self.rel_paths_box)
        paths_layout.setContentsMargins(4, 2, 0, 6)
        self.rel_result = QLabel()
        self.rel_result.setWordWrap(True)
        self.rel_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.rel_path_filter = QLabel()
        self.rel_path_filter.setWordWrap(True)
        self.rel_path_filter.setStyleSheet("color:#00695c;")
        self.rel_tree = QTreeWidget()
        self.rel_tree.setHeaderHidden(True)
        self.rel_tree.setMinimumHeight(260)
        self.rel_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.rel_tree.setToolTip("経路を選ぶと地図で強調します。上流や「上流 … 行き先」の組を選ぶと、その中の経路をすべて強調します。\n"
                                 "記号: → 促進、⊣ 抑制、◇ 作用不明。← ⊢ は右から左への矢印。P・Tx などは関係の種類")
        self.rel_tree.itemSelectionChanged.connect(self._on_relation_path_selected)
        self.rel_tree.itemExpanded.connect(self._fill_group)
        for w in (self.rel_path_filter, self.rel_tree):
            paths_layout.addWidget(w)
        self.rel_paths_box.hide()
        self._rel_filter_unit: int | None = None   # 地図でクリックして一覧を絞っている単位

        self.rel_window_button = QPushButton("強調中の遺伝子を新しい枠で開く")
        self.rel_window_button.setToolTip("いま経路タブで強調している遺伝子だけを新しい表示枠に描きます。一覧で経路を選んでいなければ、チェックした遺伝子を描きます。\n"
                                          "その枠の「再配置」は、開いたときの遺伝子の組に戻して配置し直します")
        self.rel_window_button.setEnabled(False)
        self.rel_window_button.clicked.connect(lambda: self.open_relation_pane())
        layout.addWidget(self.rel_genes_box)
        layout.addLayout(settings)
        layout.addLayout(steps_row)
        # 共通の遺伝子などの結果は、表示経路の枠のすぐ下に出す
        for w in (modes_box, self.rel_result, self.rel_window_button):
            layout.addWidget(w)
        layout.addStretch(1)
        layout.addWidget(self.help_button("relation"))
        # 設定を変えたらすぐ反映する（続けて変えたときは最後の設定だけ計算する）
        self._rel_timer = QTimer(self)
        self._rel_timer.setSingleShot(True)
        self._rel_timer.setInterval(200)
        self._rel_timer.timeout.connect(self.apply_relation)
        self.rel_mode_group.buttonToggled.connect(lambda _b, on: on and self._on_relation_setting_changed())
        self.rel_anchor.currentIndexChanged.connect(lambda _i: self._rel_timer.start())
        self.rel_steps.valueChanged.connect(lambda _v: self._rel_timer.start())
        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._update_relation_genes()
        return scroll

    def _relation_mode(self) -> str:
        button = self.rel_mode_group.checkedButton()
        return button.property("mode") if button else "off"

    def _update_relation_controls(self) -> None:
        mode = self._relation_mode()   # 基準の遺伝子・段数は、表示経路によらずいつでも選べる
        # 経路の一覧を、選んだ表示経路のすぐ下に移す
        self.rel_modes_layout.removeWidget(self.rel_paths_box)
        if mode == "off":
            self.rel_paths_box.hide()
            return
        index = self.rel_modes_layout.indexOf(self._rel_mode_buttons[mode])
        self.rel_modes_layout.insertWidget(index + 1, self.rel_paths_box)
        self.rel_paths_box.show()

    def _on_relation_setting_changed(self) -> None:
        self._update_relation_controls()
        self._rel_timer.start()

    def _relation_targets(self, pane: GraphPane | None) -> list[int]:
        """「経路」の対象にできる遺伝子: 操作中の表示枠で注目（橙）・拡張（紫）している遺伝子。"""
        if pane is None or not self.model:
            return []
        ids = list(dict.fromkeys(pane.focus_ids() + [g for g in pane.grown_order if g in pane.grown]))
        return [p for p in ids if p in self.model.proteins]

    def _update_relation_genes(self) -> None:
        """「経路」タブの一覧を、操作中の表示枠の注目・拡張している遺伝子に合わせる（チェックはそこにあるものだけ残す）。"""
        nodes = self._relation_targets(self.active_pane)
        allowed = set(nodes)
        checked = [p for p in self._rel_checked if p in allowed]
        changed = checked != self._rel_checked
        self._rel_checked = checked
        self.rel_list.blockSignals(True)
        self.rel_list.clear()
        for n in nodes:
            pane = self.active_pane
            # 注目・拡張の区別は文字の色で（地図の印と同じ: 注目＝橙、拡張＝紫）
            item = QListWidgetItem(self.model.names[n])
            item.setForeground(QColor("#e65100" if n in pane.focus_ids() else "#6a1b9a"))
            item.setData(Qt.ItemDataRole.UserRole, n)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if n in checked else Qt.CheckState.Unchecked)
            self.rel_list.addItem(item)
        if not nodes:
            item = QListWidgetItem("地図で遺伝子に注目・拡張すると、ここに並びます")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.rel_list.addItem(item)
        self.rel_list.blockSignals(False)
        rows = max(3, min(10, self.rel_list.count()))   # 3〜10 行分（それより多ければスクロール）
        self.rel_list.setFixedHeight(self.rel_list.sizeHintForRow(0) * rows + 2 * self.rel_list.frameWidth() + 4)
        self._update_relation_checked_label()
        self._update_relation_controls()
        if changed:
            self._rel_timer.start()   # チェックしていた遺伝子が注目・拡張から外れたら、経路を求め直す

    def _update_relation_checked_label(self) -> None:
        # 基準の遺伝子は、チェックした遺伝子から選ぶ（なしも選べる）
        anchor = self.rel_anchor.currentData()
        self.rel_anchor.blockSignals(True)
        self.rel_anchor.clear()
        self.rel_anchor.addItem("なし", None)
        for p in self._rel_checked:
            self.rel_anchor.addItem(self.model.names[p], p)
        if anchor is not None and self.rel_anchor.findData(anchor) >= 0:
            self.rel_anchor.setCurrentIndex(self.rel_anchor.findData(anchor))
        self.rel_anchor.blockSignals(False)

    def _on_relation_item_changed(self, item: QListWidgetItem) -> None:
        pid = item.data(Qt.ItemDataRole.UserRole)
        self._rel_checked = [p for p in self._rel_checked if p != pid]
        if item.checkState() == Qt.CheckState.Checked:
            self._rel_checked.append(pid)
        self._update_relation_checked_label()
        self._rel_timer.start()

    def _check_all_relation_genes(self) -> None:
        """一覧の遺伝子をすべてチェックする（チェックした順は一覧の順）。"""
        for i in range(self.rel_list.count()):
            item = self.rel_list.item(i)
            pid = item.data(Qt.ItemDataRole.UserRole)
            if pid is not None and not item.isHidden() and pid not in self._rel_checked:
                self._rel_checked.append(pid)
        self._update_relation_genes()
        self._rel_timer.start()

    def _clear_relation_checks(self) -> None:
        self._rel_checked = []
        self._update_relation_genes()
        self._rel_timer.start()

    def _on_picked_changed(self, pane: GraphPane, pids: list[int]) -> None:
        self._save_view()   # 緑の選択も次回起動時に戻す（経路のチェックとは独立）
        if pane is self.active_pane:
            self._sync_gene_list(pids)   # 左の「遺伝子」タブの選択もそろえる

    def apply_relation(self) -> None:
        """「経路」タブの今の設定を、操作中の表示枠に反映し、経路の一覧を作り直す（OFF なら普段の地図に戻す）。"""
        pane = self.active_pane
        if pane is None or not self.model:
            return
        self.rel_result.setText(pane.show_relation(list(self._rel_checked), self._relation_mode(),
                                                   self.rel_steps.value(), self.rel_anchor.currentData()))
        self.rel_window_button.setEnabled(pane.relation is not None)
        self.rel_result.setVisible(bool(self.rel_result.text()))
        count = pane.relation_count()
        self.rel_modes_box.setTitle("表示経路" + (f"　経路 {count} 本" if count is not None else ""))
        self._fill_relation_tree(pane)

    def _edge_marks(self) -> dict[tuple[int, int], set[tuple[str, str]]]:
        """遺伝子の組 → {(線上の記号, 作用)}（経路の一覧の表示用）。"""
        if getattr(self, "_edge_marks_model", None) is not self.model:
            marks: dict[tuple[int, int], set[tuple[str, str]]] = {}
            for it in self.model.interactions.values():
                symbol = INTERACTION_TYPES.get(it.interaction_type, ("", "", "", ""))[3]
                marks.setdefault((it.source_id, it.target_id), set()).add(
                    (symbol, resolve_effect(it.interaction_type, it.effect)))
            self._edge_marks_model, self._edge_marks_cache = self.model, marks
        return self._edge_marks_cache

    # 経路の一覧は 3 段にまとめる: 上流（経路の左端 X）→ 上流と行き先の組（X … A）→ 経路（X → Y → A など）。
    # 経路は数万本になりうるので、組の中の経路の行は、組を開いたときに作る
    ALL_ROWS = Qt.ItemDataRole.UserRole            # 上流・組: その中の経路すべて
    SHOWN_ROWS = Qt.ItemDataRole.UserRole + 1      # 上流・組・経路: いま表示する経路（絞り込み中はその遺伝子を通るものだけ）
    FILLED = Qt.ItemDataRole.UserRole + 2          # 組: 経路の行を作ったか
    CAPPED = Qt.ItemDataRole.UserRole + 3          # 上流・組: 数えるのをやめた（ENUM_CAP に達した）組があるか
    PAIR = Qt.ItemDataRole.UserRole + 4            # 組: (左端, 右端)

    def _fill_relation_tree(self, pane: GraphPane) -> None:
        self._rel_filter_unit = None
        self.rel_path_filter.setText("")
        self.rel_tree.blockSignals(True)
        self.rel_tree.clear()
        r = pane.relation
        if r and "result" in r:
            graph = r["graph"]
            by_left: dict[int, list] = {}   # 上流 → その上流からの組（並びは relation_paths.find の順のまま）
            for group in r["result"].groups:
                by_left.setdefault(group.left, []).append(group)
            for left, groups in by_left.items():
                top = QTreeWidgetItem()
                top.setData(0, self.ALL_ROWS, [row for g in groups for row in g.paths])
                top.setData(0, self.CAPPED, any(g.total >= relation_paths.ENUM_CAP for g in groups))
                top.setData(0, self.PAIR, (left, None))
                for g in groups:
                    pair = QTreeWidgetItem()
                    pair.setData(0, self.ALL_ROWS, g.paths)
                    pair.setData(0, self.CAPPED, g.total >= relation_paths.ENUM_CAP)
                    pair.setData(0, self.PAIR, (g.left, g.right))
                    self._set_pair_rows(pair, graph, g.paths)
                    top.addChild(pair)
                self._update_top(top, graph)
                self.rel_tree.addTopLevelItem(top)
            if self.rel_tree.topLevelItemCount() <= 3:
                for i in range(self.rel_tree.topLevelItemCount()):
                    self.rel_tree.topLevelItem(i).setExpanded(True)
        self.rel_tree.blockSignals(False)

    @staticmethod
    def _count_text(shown: int, total: int, capped: bool) -> str:
        count = f"{total} 本以上" if capped else f"{total} 本"
        return f"{shown} 本 / {count}" if shown != total else count

    def _set_pair_rows(self, pair: QTreeWidgetItem, graph, rows: list) -> None:
        """組（上流 … 行き先）に表示する経路を決める（経路の行は開いたときに作る）。"""
        left, right = pair.data(0, self.PAIR)
        count = self._count_text(len(rows), len(pair.data(0, self.ALL_ROWS)), pair.data(0, self.CAPPED))
        pair.setText(0, f"{graph.label(left)} … {graph.label(right)}（{count}）")
        pair.setData(0, self.SHOWN_ROWS, rows)
        pair.setData(0, self.FILLED, False)
        expanded = pair.isExpanded()
        pair.takeChildren()
        if rows:
            pair.addChild(QTreeWidgetItem(["…"]))   # 開けるようにする仮の行
        pair.setHidden(not rows)
        if expanded and rows:
            self._fill_group(pair)

    def _update_top(self, top: QTreeWidgetItem, graph) -> None:
        """上流のまとめの表示を、中の組（隠れていないもの）に合わせる。"""
        pairs = [top.child(k) for k in range(top.childCount()) if not top.child(k).isHidden()]
        rows = [row for pair in pairs for row in pair.data(0, self.SHOWN_ROWS) or []]
        count = self._count_text(len(rows), len(top.data(0, self.ALL_ROWS)), top.data(0, self.CAPPED))
        left = top.data(0, self.PAIR)[0]
        top.setText(0, f"{graph.label(left)} から（{count}）")
        top.setData(0, self.SHOWN_ROWS, rows)
        top.setHidden(not rows)

    def _fill_group(self, item: QTreeWidgetItem) -> None:
        """組を開いたとき、中の経路の行を作る。"""
        if item.parent() is None or item.data(0, self.PAIR) is None or item.data(0, self.FILLED):
            return
        pane = self.active_pane
        if pane is None or not pane.relation or "graph" not in pane.relation:
            return
        graph, marks, names = pane.relation["graph"], self._edge_marks(), self.model.names
        item.takeChildren()
        children = []
        for row in item.data(0, self.SHOWN_ROWS) or []:
            text = relation_paths.row_text(graph, row, marks)
            child = QTreeWidgetItem([text])
            child.setData(0, self.SHOWN_ROWS, [row])
            edges = sorted(GraphPane.row_edges(graph, row), key=lambda e: (names[e[0]], names[e[1]]))
            child.setToolTip(0, text + "\n関係: " + "、".join(f"{names[x]} → {names[y]}" for x, y in edges))
            children.append(child)
        item.addChildren(children)
        item.setData(0, self.FILLED, True)

    def _on_relation_path_selected(self) -> None:
        pane = self.active_pane
        if pane is None:
            return
        rows, seen = [], set()
        for item in self.rel_tree.selectedItems():
            for row in item.data(0, self.SHOWN_ROWS) or []:
                if id(row) not in seen:
                    seen.add(id(row))
                    rows.append(row)
        if rows:
            pane.highlight_rows(rows)
        else:
            self._relation_unit_rows(pane)   # 選択を外したら、絞り込み中の経路（なければ全体）の表示に戻す

    def _relation_unit_rows(self, pane: GraphPane) -> None:
        """一覧で何も選んでいないとき: 線は描かない（絞り込み中でも、一覧で選ぶまで描かない）。"""
        pane.highlight_rows([])

    def _refilter_tree(self, graph, unit: int | None) -> int:
        """一覧を unit を通る経路だけに絞る（None なら絞り込みを外す）。表示する経路の本数を返す。"""
        shown = 0
        for i in range(self.rel_tree.topLevelItemCount()):
            top = self.rel_tree.topLevelItem(i)
            for k in range(top.childCount()):
                pair = top.child(k)
                rows = pair.data(0, self.ALL_ROWS)
                self._set_pair_rows(pair, graph, rows if unit is None else [row for row in rows if unit in row.nodes])
            self._update_top(top, graph)
            shown += len(top.data(0, self.SHOWN_ROWS))
        return shown

    def _on_relation_gene(self, pane: GraphPane, pid: int) -> None:
        """地図の遺伝子をクリックした: 一覧を、その遺伝子（を含む単位）を通る経路だけに絞って強調する。"""
        if pane is not self.active_pane or not pane.relation or "graph" not in pane.relation:
            return
        if pane.relation.get("sel_edges"):
            return   # 一覧で選んだ経路を描いている間は、絞り込み直さない（描いた経路を消さない。背景のクリックで外れる）
        graph = pane.relation["graph"]
        unit = graph.unit(pid)
        self._rel_filter_unit = unit
        self.rel_tree.blockSignals(True)
        self.rel_tree.clearSelection()
        shown = self._refilter_tree(graph, unit)
        tops = [self.rel_tree.topLevelItem(i) for i in range(self.rel_tree.topLevelItemCount())]
        if sum(not t.isHidden() for t in tops) <= 3:
            for top in tops:
                if not top.isHidden():
                    top.setExpanded(True)
        self.rel_tree.blockSignals(False)
        name = graph.label(unit)
        self.rel_path_filter.setText(f"{name} を通る経路（{shown} 本）" if shown else f"{name} を通る経路はありません")
        self._relation_unit_rows(pane)

    def _on_relation_background(self, pane: GraphPane) -> None:
        """背景をクリックした: 一覧の絞り込みと選択を外す（地図の強調は地図の側で外れる）。"""
        if pane is not self.active_pane or not pane.relation or "graph" not in pane.relation:
            return
        filtered = self._rel_filter_unit is not None
        self._rel_filter_unit = None
        self.rel_path_filter.setText("")
        self.rel_tree.blockSignals(True)
        self.rel_tree.clearSelection()
        if filtered:
            self._refilter_tree(pane.relation["graph"], None)
        self.rel_tree.blockSignals(False)

    def set_relation_settings(self, genes: list[int], mode: str, steps: int, anchor: int | None) -> None:
        """「経路」タブの設定をまとめて変えて、すぐに反映する（左の「登録」タブから呼び出したとき）。
        チェックは操作中の表示枠で注目・拡張している遺伝子だけ。"""
        widgets = [self.rel_mode_group, self.rel_steps]
        for w in widgets:
            w.blockSignals(True)
        for b in self.rel_mode_group.buttons():
            b.setChecked(b.property("mode") == mode)
        self.rel_steps.setValue(min(3, max(1, steps)))
        for w in widgets:
            w.blockSignals(False)
        self._rel_checked = list(genes)
        self._update_relation_genes()   # 注目・拡張していない遺伝子のチェックは外れる
        index = self.rel_anchor.findData(anchor) if anchor is not None else 0
        self.rel_anchor.blockSignals(True)
        self.rel_anchor.setCurrentIndex(max(0, index))
        self.rel_anchor.blockSignals(False)
        self._rel_timer.stop()
        self.apply_relation()

    def open_relation_pane(self) -> None:
        """いま経路タブで強調している遺伝子（一覧で選んで描いている経路。なければチェックした遺伝子）だけを、
        新しい表示枠で開く（開いたときの状態を保つ）。"""
        pane = self.active_pane
        if pane is None or pane.relation is None or not self.model:
            return
        if len(self.panes) >= MAX_PANES:
            self.statusMessage.emit(f"表示枠は最大 {MAX_PANES} つまでです")
            return
        r = pane.relation
        names = self.model.names
        nodes = set(r["nodes"]) | set(r.get("sel_nodes", set()))
        edges = set(r.get("sel_edges", set()))
        label = GraphPane.RELATION_MODES.get(r["mode"], ("",))[0]
        state = {"focus": [], "gene_set": sorted(names[n] for n in nodes),
                 "gene_set_edges": sorted([names[a], names[b]] for a, b in edges),
                 "gene_set_ends": [names[n] for n in r["pids"]],
                 "gene_set_title": f"経路: {label}・{r['steps']} 段"}
        new = self._create_pane(state)
        self._arrange()
        self.set_active(new)
        new.on_model_reloaded(use_default=False)
        self._save_view()
        self.statusMessage.emit(f"強調中の遺伝子 {len(nodes)} 個を新しい枠で開きました")

    def _make_pane(self, state: dict | None) -> GraphPane:
        """表示枠を作る（state に "gene_set" があれば、遺伝子の組を固定した枠）。"""
        if state and "gene_set" in state:
            return GeneSetPane(self, state)
        return GraphPane(self, state)

    def detach_pane(self, pane: GraphPane) -> None:
        """表示枠の「別ウィンドウで開く」: 枠が 1 つなら今の内容を写した別ウィンドウを開き、
        2 つ以上なら、その枠を別ウィンドウに移す（メインの地図からは消す）。"""
        if not self.model:
            return
        state = pane.state()
        moved = len(self.panes) > 1 and pane in self.panes
        wpane = self._make_pane(state)
        self._wire_window_pane(wpane)
        title = "、".join(state.get("focus", [])) or state.get("gene_set_title") or "地図"
        window = MapWindow(wpane, f"地図: {title}")
        self.gene_windows.append(window)
        window.destroyed.connect(lambda _=None, w=window: self.gene_windows.remove(w) if w in self.gene_windows else None)
        wpane.on_model_reloaded(use_default=False)
        window.show()
        if moved:
            self.close_pane(pane)
        self.statusMessage.emit("表示枠を別ウィンドウに移しました" if moved else "今の表示を別ウィンドウで開きました")

    def _wire_window_pane(self, wpane: GraphPane) -> None:
        """別ウィンドウの表示枠: 詳細欄・凡例・状態表示はメインの画面とつなぐ（左のタブ・枠の管理とはつながない）。"""
        wpane.set_completer(self._gene_names())
        wpane.nodeClicked.connect(lambda p, pid: self.show_node(pid, p))
        wpane.edgeClicked.connect(lambda p, iid: self.show_edge(iid, p))
        wpane.complexClicked.connect(lambda p, name: self.show_complex(name, p))
        wpane.statusMessage.connect(self.statusMessage)
        wpane.helpRequested.connect(lambda _p: self.show_help("legend"))
        wpane.filterToggled.connect(lambda kind, key, shown, p=wpane: self.toggle_filter(kind, key, shown, p))
        wpane.filterReset.connect(lambda kind, p=wpane: self.reset_filters(kind, p))
        wpane.displayOptionChanged.connect(self.set_display_option)

    def _views(self) -> list[GraphPane]:
        """設定（配置・色・凡例・閾値の変更）を反映する地図: 表示枠と、別ウィンドウの地図。"""
        return self.panes + [w.pane for w in self.gene_windows]

    def _build_detail_panel(self):
        self.detail_panel = QWidget()
        self.detail_panel.setMinimumWidth(260)
        layout = QVBoxLayout(self.detail_panel)
        layout.setContentsMargins(4, 0, 0, 0)
        self.detail = QTextBrowser()
        self.detail.setOpenLinks(False)
        self.detail.anchorClicked.connect(lambda url: self._open_link(url.toString()))
        WidthWatcher(self.detail).changed.connect(self._on_detail_resized)
        self.detail_controls = QVBoxLayout()
        # 説明欄の ◀ ▶: 表示した遺伝子・線・複合体の履歴をたどり、地図の選択もそれに合わせる
        self._detail_hist: list[tuple] = []   # (種類, キー, 表示枠)
        self._detail_pos = -1
        self._detail_nav = False
        nav = QHBoxLayout()
        self.detail_back = QPushButton("◀")
        self.detail_forward = QPushButton("▶")
        for button, step, tip in ((self.detail_back, -1, "前に表示した遺伝子・線に戻る"),
                                  (self.detail_forward, 1, "次に表示した遺伝子・線に進む")):
            button.setFixedWidth(34)
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _=False, s=step: self._detail_go(s))
            nav.addWidget(button)
        nav.addStretch(1)
        layout.addLayout(nav)
        self._update_detail_nav()
        layout.addWidget(self.detail, 1)
        layout.addLayout(self.detail_controls)
        # 一番下の Help（左のタブと同じ）: 出している説明（遺伝子・線・複合体・パラログ）の見方を、説明欄の新しいページとして出す
        detail_help = QPushButton("Help")
        detail_help.setToolTip("この説明欄の見方を表示します（◀ で戻れます）")
        detail_help.clicked.connect(self.show_detail_help)
        layout.addWidget(detail_help)
        self.clear_detail()

    # ================= 共有設定（表示枠から参照される） =================
    def layout_name(self) -> str:
        return self.layout_combo.currentData()

    def known_only(self) -> bool:
        # 「作用が分かっている関係のみ」の切り替えは廃止した（常にすべての関係をたどる）
        return False

    def color_mode(self) -> str:
        return self.color_combo.currentData()

    def _set_color(self, mode: str) -> None:
        """地図の遺伝子の色を mode にする。値（発現・破壊株の実測）を先に送ってから色を切り替える
        （色を先に変えると、前の値・空の値で一度塗ってから塗り直すので、色が一瞬変わって見える）。"""
        if self.color_mode() == mode:
            self._push_data_values()   # 条件・株だけが変わった
            return
        self.color_combo.blockSignals(True)
        self.color_combo.setCurrentIndex(max(0, self.color_combo.findData(mode)))
        self.color_combo.blockSignals(False)
        self._push_data_values()   # 値を送る（説明欄・タブもそろえる）
        self._all_js(f"app.setColorMode({json.dumps(self.color_mode())})")
        self.sync_view_choices()
        self._save_view()

    def _color_by_deletion(self, kind: str) -> None:
        """地図を破壊株の実測（kind は del_mrna / del_phospho）で色分けする。"""
        self._set_color(kind)

    def _end_tab_color(self) -> None:
        """左のタブ（条件・破壊株）での色分けをやめ、役割の色に戻す。"""
        if self.color_mode() != "role":
            self._set_color("role")

    def _check_deletion_selection(self) -> None:
        """破壊株タブで株の選択が外れたら（上の一覧・「変化した遺伝子」の下の欄で、株が 1 つも選ばれていない）、役割の色に戻す。
        別の株を押している途中（押し下げた時点で選択が移る）は戻さない（押し終えたときにその株の色にする）。"""
        if getattr(self, "del_list", None) is None or self.deletion_kind() is None:
            return
        if self.del_search_mode.currentData() == "changed":
            if any(isinstance(d := it.data(Qt.ItemDataRole.UserRole), tuple) and d[0] == "strain"
                   for it in self.del_changes.selectedItems()):
                return
        elif self.del_list.selectedItems():
            return
        self._end_tab_color()

    def edge_color_mode(self) -> str:
        return self.edge_color_combo.currentData()

    def orthogonal(self) -> bool:
        return True   # 線は常に遺伝子を避ける直角の折れ線で描く

    def label_options(self) -> tuple[bool, bool]:
        """凡例の「表示」欄: 種類記号（線ラベル）、+数（表示されていない下流の数）。"""
        o = self.display_options
        return o["symbols"], o["more"]

    def set_display_option(self, key: str, on: bool) -> None:
        """どれかの表示枠の凡例で「表示」欄のチェックを切り替えたとき。全表示枠に反映する。"""
        if key not in self.display_options:
            return
        self.display_options[key] = on
        for pane in self._views():
            pane.push_label_options()
        self._save_view()

    def _all_js(self, code: str) -> None:
        for pane in self._views():
            pane.js(code)

    # ================= 表示枠の管理 =================
    @staticmethod
    def _load_view() -> tuple[list[dict], str, str, dict, str, dict]:
        """前回の表示（表示枠・配置の方式・色分け・凡例で隠した項目）。"""
        default_layout = LAYOUT_CHOICES[0][1]
        try:
            data = json.loads(VIEW_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return [{}], default_layout, "role", {}, "type", {}
        if "panes" in data:
            panes = [p for p in data["panes"] if isinstance(p, dict)][:MAX_PANES] or [{}]
            return (panes, data.get("layout", default_layout), data.get("color_mode", "role"),
                    data.get("hidden", {}), data.get("edge_color", "type"), data.get("display", {}))
        # 表示枠が 1 つだった頃の形式
        return [{"focus": data.get("focus", []), "up": data.get("up", DEFAULT_UP),
                 "down": data.get("down", DEFAULT_DOWN)}], default_layout, "role", {}, "type", {}

    @staticmethod
    def _load_setting(key: str, default: str, allowed) -> str:
        try:
            value = json.loads(VIEW_FILE.read_text(encoding="utf-8")).get(key)
        except (OSError, ValueError, AttributeError):
            value = None
        return value if value in allowed else default

    def _save_view(self) -> None:
        data = {"panes": [p.state() for p in self.panes], "layout": self.layout_combo.currentData(),
                "color_mode": self.color_mode(), "hidden": self.hidden_filters(),
                "conditions_off": sorted(self.conditions_off), "conditions_top": self.cond_top.isChecked(),
                "edge_color": self.edge_color_combo.currentData(), "display": self.display_options,
                "data_condition": self.data_cond_combo.currentData(),
                "deletion_strain": self.del_strain_combo.currentData(),
                "deletion": {"kind": self._del_kind()} if getattr(self, "del_list", None) is not None else {}}
        try:
            VIEW_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _create_pane(self, state: dict | None) -> GraphPane:
        pane = self._make_pane(state)
        pane.activated.connect(self.set_active)
        pane.nodeClicked.connect(lambda p, pid: self.show_node(pid, p))
        pane.edgeClicked.connect(lambda p, iid: self.show_edge(iid, p))
        pane.complexClicked.connect(lambda p, name: self.show_complex(name, p))
        pane.backgroundClicked.connect(lambda p: self.clear_detail())
        pane.viewChanged.connect(self._on_pane_view_changed)
        pane.closeRequested.connect(self.close_pane)
        pane.detachRequested.connect(self.detach_pane)
        pane.statusMessage.connect(self.statusMessage)
        pane.filterToggled.connect(lambda kind, key, shown, p=pane: self.toggle_filter(kind, key, shown, p))
        pane.filterReset.connect(lambda kind, p=pane: self.reset_filters(kind, p))
        pane.displayOptionChanged.connect(self.set_display_option)
        pane.pickedChanged.connect(self._on_picked_changed)
        pane.relationGene.connect(self._on_relation_gene)
        pane.helpRequested.connect(lambda _p: self.show_help("legend"))
        pane.backgroundClicked.connect(self._on_relation_background)
        self.panes.append(pane)
        if self.model:
            pane.set_completer(self._gene_names())
        return pane

    def add_pane(self, focus_pid: int | None = None) -> GraphPane | None:
        if len(self.panes) >= MAX_PANES:
            self.statusMessage.emit(f"表示枠は最大 {MAX_PANES} つまでです")
            return None
        pane = self._create_pane({"focus": [], "up": DEFAULT_UP, "down": DEFAULT_DOWN})
        self._arrange()
        self.set_active(pane)
        if self.model:
            pane.on_model_reloaded(use_default=False)
            if focus_pid is not None:
                pane.set_focus(focus_pid)
            else:
                pane.search.setFocus()
        self._save_view()
        return pane

    def close_pane(self, pane: GraphPane) -> None:
        if len(self.panes) <= 1:
            return
        self.panes.remove(pane)
        if self.detail_pane is pane:
            self.clear_detail()
        pane.setParent(None)
        pane.deleteLater()
        self._arrange()
        self.set_active(self.panes[0])
        self._rebuild_trf_panel()
        self._save_view()

    def set_active(self, pane: GraphPane) -> None:
        changed = self.active_pane is not pane
        self.active_pane = pane
        for p in self.panes:
            p.set_active(p is pane)
        if changed:
            self._rebuild_trf_panel()   # 左の TFs 一覧は操作中の表示枠の注目に合わせる
            self._update_condition_presence()
            self._on_deletion_map_changed()
            self._refill_gene_list()
            self._sync_condition_genes()
            # 経路は操作中の表示枠にだけ出す（ほかの枠は普段の地図に戻す）
            for p in self.panes:
                if p is not pane:
                    p.clear_relation()
            self._update_relation_genes()
            self._rel_timer.start()
            self.rel_window_button.setEnabled(False)

    def _arrange(self) -> None:
        """表示枠を並べ直す（最大 4 つ）。1 つ目・2 つ目は左右に、3 つ目は 1 つ目の下、4 つ目は 2 つ目の下（2×2 の格子）。

        列（縦の QSplitter）を使い回し、置き場所が変わる表示枠だけを動かす。QWebEngineView は親を付け替えると
        画面の更新が止まって拡大されたままぼやけることがあるので、枠を追加しただけのときは既存の枠を動かさない。
        大きさは、列の数が変わらなければ左右の幅をそのまま保ち、枠の数が変わった列だけ上下を等分する
        （枠を閉じても、ほかの枠の横幅は変えない）。
        """
        n = len(self.panes)
        columns = [self.panes[0::2], self.panes[1::2]]
        columns = [c for c in columns if c]
        old_widths = self.pane_area.sizes()
        old_counts = [col.count() for col in self.col_splitters]
        while len(self.col_splitters) < len(columns):
            col = QSplitter(Qt.Orientation.Vertical)
            col.setChildrenCollapsible(False)
            self.pane_area.addWidget(col)
            self.col_splitters.append(col)
        for col, group in zip(self.col_splitters, columns):
            for j, pane in enumerate(group):
                if col.indexOf(pane) != j:
                    col.insertWidget(j, pane)
        # 使わなくなった列（中の表示枠はすでに別の列へ移したか、閉じてある）を片付ける
        for old in self.col_splitters[len(columns):]:
            old.setParent(None)
            old.deleteLater()
        self.col_splitters = self.col_splitters[:len(columns)]
        for k, col in enumerate(self.col_splitters):
            if k >= len(old_counts) or old_counts[k] != col.count():
                col.setSizes([1000] * col.count())
        if len(old_widths) != len(self.col_splitters) or not all(old_widths):
            self.pane_area.setSizes([1000] * len(self.col_splitters))
        else:
            self.pane_area.setSizes(old_widths)
        for i, pane in enumerate(self.panes, start=1):
            pane.set_title(i, closable=n > 1)

    def _on_pane_view_changed(self, pane: GraphPane) -> None:
        self._save_view()
        self._rebuild_trf_panel()
        if pane is self.active_pane:
            self._update_relation_genes()   # 地図上の遺伝子が変わった
            self._update_condition_presence()
            self._on_deletion_map_changed()
            self._refill_gene_list()
            self._sync_condition_genes()

    def _gene_names(self) -> list[str]:
        return sorted(p.gene_name for p in self.model.proteins.values())

    # ================= データ =================
    def set_database(self, db: Database) -> None:
        self.db = db
        self._reload(use_default=True)

    def reload(self) -> None:
        """DB を読み直す。"""
        self._reload(use_default=False)

    def _reload(self, use_default: bool) -> None:
        self.model = PathwayModel(self.db.proteins(), self.db.interactions(), self.db.categories(),
                                  self.db.category_members())
        self._rebuild_condition_panel()
        names = self._gene_names()
        for window in list(self.gene_windows):
            window.close()
        for pane in self.panes:
            pane.set_completer(names)
            pane.on_model_reloaded(use_default)
        self._save_view()
        self.clear_detail()

    # ================= TFs の一覧 =================
    def _clear_layout(self, layout):
        """並びの中身を消す（入れ子の並びの中の部品も消す）。"""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _shown_nodes(self) -> set[int]:
        shown = set()
        for pane in self.panes:
            shown |= pane.sub.nodes
        return shown

    # 作用の色はアプリ全体で 促進＝赤・抑制＝青（地図の「線の色: 作用」と同じ。web/network.js の EFFECT_COLORS）
    TRF_EFFECT = {"activate": ("→", "促進", "#e53935"), "inhibit": ("⊣", "抑制", "#1e88e5"), "none": ("◇", "作用不明", "#757575")}

    def _rebuild_trf_panel(self):
        """操作中の表示枠で注目（オレンジ）・拡張（紫）している遺伝子ごとに、それを転写制御する転写因子を
        チェックボックスで並べる。遺伝子ごとに、題名 →「すべて表示」「すべて外す」→ 作用（促進・抑制・作用不明）ごとの
        開閉できる欄、の順。作用の欄は最初は閉じている（開いた欄は、作り直しても開いたまま）。
        同じ転写因子が複数の遺伝子の欄にあるときは、チェックが連動する。"""
        scroll = self.trf_scroll.verticalScrollBar().value()   # 作り直してもスクロール位置を保つ
        self._clear_layout(self.trf_layout)
        pane = self.active_pane
        if not self.model or pane is None:
            return
        targets = pane.trf_targets()
        if not targets:
            self.trf_layout.addWidget(QLabel("注目・拡張している遺伝子がありません"))
        boxes: dict[int, list[QCheckBox]] = {}   # 転写因子 → 各欄のチェックボックス（連動させる）

        def toggled(pid: int, on: bool):
            for other in boxes.get(pid, []):
                other.blockSignals(True)
                other.setChecked(on)
                other.blockSignals(False)
            pane.set_trf([pid], on)

        for g, kind in targets:
            regs = self.model.tx_adjacency.inc.get(g, set()) - {g}
            box = QFrame()
            box.setFrameShape(QFrame.Shape.StyledPanel)
            v = QVBoxLayout(box)
            v.setContentsMargins(6, 4, 6, 4)
            v.setSpacing(2)
            title = QLabel(self.model.names[g])   # 題名は遺伝子名だけ（注目＝橙・拡張＝紫の色で区別する）
            title.setStyleSheet("color: %s; font-weight: bold;" % ("#e65100" if kind == "focus" else "#6a1b9a"))
            v.addWidget(title)
            if not regs:
                note = QLabel("転写因子なし")
                note.setStyleSheet("color:#777;")
                v.addWidget(note)
                self.trf_layout.addWidget(box)
                continue
            order = sorted(regs, key=lambda n, g=g: self.model.tx_priority(n, g, regulator=True))
            buttons = QHBoxLayout()
            for text, on in (("すべて表示", True), ("すべて外す", False)):
                b = QPushButton(text)
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # Space・Enter で意図せず切り替わらないように
                b.clicked.connect(lambda _, ids=order, on=on: pane.set_trf(ids, on))
                buttons.addWidget(b)
            v.addLayout(buttons)
            for effect in ("activate", "inhibit", "none"):
                members = [n for n in order if self.model.tx_effect.get((n, g), "none") == effect]
                if not members:
                    continue
                mark, label, color = self.TRF_EFFECT[effect]
                shown = sum(n in pane.trf for n in members)
                section, inner = self._trf_section((g, effect), f"{mark} {label}（{len(members)}"
                                                   + (f"、表示 {shown}" if shown else "") + "）", color)
                for n in members:
                    elsewhere = n in pane.sub.nodes and n not in pane.trf   # 他の関係で既に表示中
                    shared = sum(n in self.model.tx_adjacency.inc.get(t, set()) for t, _ in targets) > 1
                    cb = QCheckBox(self.model.names[n] + (" 表示中" if elsewhere else "") + ("  ⇄" if shared else ""))
                    cb.setStyleSheet(f"QCheckBox {{ color: {color}; font-weight: normal; }}")
                    cb.setToolTip(self.model.proteins[n].description
                                  + ("\n⇄ 複数の遺伝子の TF。チェックは連動します" if shared else ""))
                    cb.setChecked(n in pane.trf or elsewhere)
                    cb.setEnabled(not elsewhere)
                    cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                    cb.toggled.connect(lambda on, pid=n: toggled(pid, on))
                    boxes.setdefault(n, []).append(cb)
                    inner.addWidget(cb)
                v.addWidget(section)
            self.trf_layout.addWidget(box)
        QTimer.singleShot(0, lambda: self.trf_scroll.verticalScrollBar().setValue(scroll))

    def _trf_section(self, key, title: str, color: str) -> tuple[QWidget, QVBoxLayout]:
        """TFs 一覧の、開閉できる欄（見出しのボタン＋中身）。key は開閉の状態を覚える鍵（遺伝子・作用）。
        返り値: (欄, 中身を入れる並び)。"""
        opened = getattr(self, "_trf_open", set())
        self._trf_open = opened
        box = QWidget()
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QToolButton()
        head.setText(title)
        head.setCheckable(True)
        head.setChecked(key in opened)
        head.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        head.setArrowType(Qt.ArrowType.DownArrow if key in opened else Qt.ArrowType.RightArrow)
        head.setAutoRaise(True)
        head.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        head.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        head.setStyleSheet("QToolButton { color: %s; font-weight: bold; text-align: left; border: none; }" % color)
        body = QWidget()
        body.setVisible(key in opened)
        v = QVBoxLayout(body)
        v.setContentsMargins(18, 0, 0, 2)
        v.setSpacing(1)

        def toggle(on: bool):
            body.setVisible(on)
            head.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
            (opened.add if on else opened.discard)(key)

        head.toggled.connect(toggle)
        outer.addWidget(head)
        outer.addWidget(body)
        return box, v

    # ================= 詳細パネル =================
    def _set_controls(self, widgets):
        self._clear_layout(self.detail_controls)
        for w in widgets:
            self.detail_controls.addWidget(w)

    def clear_detail(self):
        """右側の説明欄を既定の表示に戻す（表示するものがないときは、マップの使い方）。"""
        self.selected = None
        self._set_controls([])
        self.detail.setHtml(help_text.MAP)

    def help_button(self, topic: str) -> QPushButton:
        """左のタブの一番下に置く「Help」ボタン（押すと右側の説明欄に使い方を出す）。"""
        button = QPushButton("Help")
        button.setToolTip("このタブの使い方を右側に表示します")
        button.clicked.connect(lambda: self.show_help(topic))
        return button

    def show_help(self, topic: str) -> None:
        """右側の説明欄に使い方を出す（tor_app/ui/help_text.py）。"""
        self.selected = None
        self._set_controls([])
        self.detail.setHtml(help_text.TOPICS[topic])

    def detail_help_topic(self, kind: str | None, key) -> str:
        """説明欄に出しているものの見方の種類（help_text.DETAIL_HELP のキー）。何も出していなければ map。"""
        if kind == "complex":
            return "paralog" if self.model and all(p in self.model.paralogs for p in str(key).split("・")) else "complex"
        return kind if kind in ("node", "edge") else "map"

    def show_detail_help(self) -> None:
        """説明欄の一番下の Help: 今の説明の見方を新しいページとして出す（履歴に入れ、◀ で前の説明に戻れる）。"""
        kind, key = self.selected or (None, None)
        if kind == "help":
            return
        topic = self.detail_help_topic(kind, key)
        self._push_detail("help", topic, self.detail_pane or self.active_pane)
        self._show_help_page(topic)

    def _show_help_page(self, topic: str) -> None:
        self.selected = ("help", topic)
        self._set_controls([])
        self.detail.setHtml(help_text.detail_help(topic))

    def _pane(self, pane: GraphPane | None) -> GraphPane:
        return pane if pane in self.panes else self.active_pane

    def render_html(self, kind: str, key, state: DetailState) -> str:
        """データベースの説明欄用: 地図の説明欄と同じ中身を、渡した状態で作る（表示枠の情報は出さない）。
        kind: node（遺伝子の番号）/ edge（関係の番号）/ complex（複合体・パラログの名前）。"""
        if self.model is None:
            return ""
        saved = self._st, self._render_pane
        self._st, self._render_pane = state, False
        try:
            if kind == "node" and key in self.model.proteins:
                return self._node_html(key)
            if kind == "edge" and key in self.model.interactions:
                return self._edge_html(key)
            if kind == "complex":
                return self._complex_html(key)
            return ""
        finally:
            self._st, self._render_pane = saved

    def toggle_detail(self, state: DetailState, target: str) -> bool:
        """説明欄の中の開閉（一覧・条件の測定）。開閉のリンクなら状態を変えて True。"""
        kind, _, key = target.partition(":")
        if kind == "list":
            (state.open_lists.discard if key in state.open_lists else state.open_lists.add)(key)
        elif kind == "cond":
            (state.open_conds.discard if key in state.open_conds else state.open_conds.add)(key)
        else:
            return False
        return True

    def _on_detail_resized(self) -> None:
        """説明欄の幅が変わった: 遺伝子の説明なら、条件名の折り返しを作り直す（スクロール位置は保つ）。"""
        if self.selected and self.selected[0] == "node" and self.model:
            bar = self.detail.verticalScrollBar()
            pos = bar.value()
            self._refresh_detail_text()
            bar.setValue(pos)

    def _refresh_detail_text(self):
        kind, key = self.selected
        if kind == "node" and key in self.model.proteins:
            self.detail.setHtml(self._node_html(key))
        elif kind == "edge" and key in self.model.interactions:
            self.detail.setHtml(self._edge_html(key))
        elif kind == "complex":
            self.detail.setHtml(self._complex_html(key))

    @staticmethod
    def _arrow(it) -> str:
        effect = resolve_effect(it.interaction_type, it.effect)
        directed = it.interaction_type != "binding" or effect != "none"
        return {"activate": "→", "inhibit": "⊣", "none": "─◇" if directed else "—"}[effect]

    def _link_edge(self, it) -> str:
        return (f"<a href='edge:{it.id}'>{html.escape(it.source.gene_name)} {self._arrow(it)} "
                f"{html.escape(it.target.gene_name)}</a> "
                f"<span style='color:#777'>{html.escape(type_label(it.interaction_type))}</span>")

    def _edge_items(self, edges, shown: set[int]) -> list[tuple[tuple, str]]:
        """上流・下流の一覧の行（並べ替えの鍵, HTML）。論文が複合体の名前で書いた関係（構成遺伝子ごとに広げてある。
        tor_app/complex_groups.py）は、遺伝子ごとに並べず「TORC1 → RPC82」の 1 行にまとめ、先頭に並べる（リンクは代表の関係）。"""
        items, grouped = [], {}
        for it in edges:
            marks = complex_groups.parse(it.evidence)
            if marks:
                label, side = marks[0]
                end = it.target_id if side == "上流" else it.source_id
                grouped.setdefault((label, side, end, it.interaction_type), []).append(it)
                continue
            items.append(((1, not (it.source_id in shown and it.target_id in shown),
                           it.source.gene_name, it.target.gene_name), self._link_edge(it)))
        for (label, side, end, kind), its in grouped.items():
            rep = min(its, key=lambda it: it.id)
            other = html.escape(self.model.names[end])
            text = f"{html.escape(label)} {self._arrow(rep)} {other}" if side == "上流" else \
                f"{other} {self._arrow(rep)} {html.escape(label)}"
            on_map = any(it.source_id in shown and it.target_id in shown for it in its)
            items.append(((0, not on_map, label if side == "上流" else self.model.names[end],
                           self.model.names[end] if side == "上流" else label),
                          f"<a href='edge:{rep.id}'>{text}</a> <span style='color:#777'>"
                          f"{html.escape(type_label(kind))}・複合体として"
                          f"{f'（{len(its)} 遺伝子）' if len(its) > 1 else ''}</span>"))
        return sorted(items, key=lambda x: x[0])

    def _node_html(self, pid: int) -> str:
        p = self.model.proteins[pid]
        ups = [it for it in self.model.interactions.values() if it.target_id == pid]
        downs = [it for it in self.model.interactions.values() if it.source_id == pid]
        known_only = self.known_only()
        level = self.model.levels(known_only).get(pid)
        place = (f"No. {self.model.layout_order(known_only)[pid]}"
                 f"・{'階層なし' if level is None else f'第{level + 1}階層'}")
        rows = [("Systematic 名", p.standard_name), ("配置の番号", place),
                ("ORF の確度（SGD）", p.protein_properties)]
        table = "".join(f"<tr><td style='color:#777;padding-right:8px'>{k}</td><td>{html.escape(v or '')}</td></tr>"
                        for k, v in rows)
        hidden = ""
        sub = self._pane(self.detail_pane).sub if self._render_pane else None
        if sub is not None:
            if pid in sub.nodes:
                hu, hd = sub.hidden_up.get(pid, 0), sub.hidden_down.get(pid, 0)
                if hu or hd:
                    hidden = f"<p style='color:#b26a00'>表示されていない上流 {hu} 個・下流 {hd} 個</p>"
            else:
                hidden = "<p style='color:#b26a00'>操作中の表示枠にはありません</p>"
            hidden += self._tf_pair_note(pid, ups + downs, sub)
        title = f"<h2 style='margin-bottom:2px'>{html.escape(p.gene_name)}</h2>"
        if self._render_pane:
            # 遺伝子名の右端に「注目」「拡張」（検索欄の吹き出しと同じ操作を、操作中の表示枠に）
            button = ("<a href='{0}:{1}' style='text-decoration:none;color:{2};font-weight:bold'>"
                      "<span style='background-color:{3}'>&nbsp;{4}&nbsp;</span></a>").format
            title = (f"<table width='100%' cellspacing='0' cellpadding='0'><tr><td>{title}</td>"
                     f"<td align='right' valign='middle' style='white-space:nowrap'>"
                     f"{button('mapfocus', pid, '#e65100', '#fff3e0', '注目')}&nbsp;"
                     f"{button('mapgrow', pid, '#6a1b9a', '#f3e5f5', '拡張')}</td></tr></table>")
        return (f"{title}"
                f"{hidden}"
                f"<table>{table}</table>{self._description_html(p.description or '')}{self._portal_html(p.gene_name)}"
                f"{self._data_html(p.standard_name or '')}"
                f"{self._deletion_html(p.standard_name or '')}"
                f"{self._edge_list(ups, sub.nodes if sub else set(), 'up', '上流')}"
                f"{self._edge_list(downs, sub.nodes if sub else set(), 'down', '下流')}")

    DELETION_SHOWN = 8   # 破壊株での実測の一覧で、開く前に出す数

    def _orf_link(self, orf: str, name: str) -> str:
        """遺伝子名。地図の DB にあれば、押すとその遺伝子の説明に移るリンク。"""
        pid = self._orf_pid(orf)
        if pid is not None:
            return f"<a href='node:{pid}' style='text-decoration:none'>{html.escape(name)}</a>"
        return html.escape(name)

    def _deletion_html(self, orf: str) -> str:
        """破壊株での実測（tools/build_deletions.py）: この遺伝子を壊した株で変わった遺伝子と、この遺伝子が変わった破壊株。
        どちらも変化の大きい順に DELETION_SHOWN 個まで出し、「すべて」で開く。株の名前の「色分け」で、地図をその株で色分けする。"""
        if not orf or not expression.has_deletions():
            return ""
        own, seen_in = expression.strain_changes(orf), expression.changed_in(orf)
        info = expression.strain_info(orf)
        if not own and not seen_in and not info:
            return ""
        st = self._st
        kinds = {"mrna": ("mRNA", "del_mrna"), "phospho": ("リン酸化", "del_phospho")}

        def ref(source: str, pmid: str) -> str:
            return f"<a href='https://pubmed.ncbi.nlm.nih.gov/{pmid}/'>{html.escape(source)}</a>"

        def rows(points, show_strain: bool, key: str) -> str:
            is_open = key in st.open_lists
            out = []
            for x in points if is_open else points[:self.DELETION_SHOWN]:
                name = (self._orf_link(x.strain, x.strain_name) if show_strain else self._orf_link(x.gene, x.gene_name))
                site = f" <span style='color:#777;font-size:11px'>{html.escape(x.site)}</span>" if x.site else ""
                color = ""
                if show_strain:
                    color = (f" <a href='kocolor:{x.strain}:{kinds[x.kind][1]}' style='text-decoration:none;font-size:11px'>"
                             f"色分け</a>")
                out.append(f"<tr><td align='right' style='white-space:nowrap;padding-right:6px'>{self._value_html(x.value)}</td>"
                           f"<td>{name}{site}{color}</td></tr>")
            more = ""
            if len(points) > self.DELETION_SHOWN:
                more = (f"<a href='list:{key}' style='text-decoration:none;font-size:11px'>"
                        f"{'▾ 閉じる' if is_open else f'▸ すべて（{len(points)}）'}</a>")
            return f"<table cellspacing='0' cellpadding='1'>{''.join(out)}</table>{more}"

        parts = ["<h4 style='margin-bottom:2px'>破壊株での実測（野生型との log2 比）</h4>"]
        # この遺伝子を壊した株
        for kind, (title, color_kind) in kinds.items():
            meta = next((m for m in info if m["kind"] == kind), None)
            if meta is None:
                continue
            pts = [x for x in own if x.kind == kind]
            up = sum(1 for x in pts if x.value > 0)
            unit = "部位" if kind == "phospho" else "遺伝子"
            parts.append(f"<p style='margin:4px 0 2px 0'><b>この遺伝子を壊した株の{title}</b>"
                         f"（{ref(meta['source'], meta['pmid'])}）: 上がった{unit} {up}・下がった{unit} {len(pts) - up} "
                         f"<a href='kocolor:{orf}:{color_kind}' style='text-decoration:none'>地図で色分け</a></p>")
            if pts:
                parts.append(rows(pts, False, f"ko-own-{kind}"))
        # この遺伝子が変わった破壊株
        for kind, (title, _) in kinds.items():
            pts = [x for x in seen_in if x.kind == kind]
            if not pts:
                continue
            sources = sorted({(x.source, x.pmid) for x in pts})
            parts.append(f"<p style='margin:6px 0 2px 0'><b>この遺伝子の{title}が変わった破壊株</b>"
                         f"（{'・'.join(ref(*s) for s in sources)}）: {len({x.strain for x in pts})} 株</p>")
            parts.append(rows(pts, True, f"ko-in-{kind}"))
        if len(parts) == 1:
            return ""
        parts.append("<p style='margin:2px 0;color:#777;font-size:11px'>mRNA は p &lt; 0.05 かつ 1.4 倍以上の変化だけ。"
                     "リン酸化は論文が変化として報告した部位だけ</p>")
        return "".join(parts)

    @staticmethod
    def _value_html(v: float | None, note: str = "") -> str:
        """log2 の比を、地図と同じ色（赤＝増える、青＝減る）の数字で出す。"""
        if v is None:
            return "<span style='color:#bbb'>–</span>"
        color = "#c62828" if v >= 0.58 else "#1565c0" if v <= -0.58 else "#555"
        weight = "bold" if abs(v) >= 1 else "normal"
        extra = f" <span style='color:#777;font-size:11px'>{html.escape(note)}</span>" if note else ""
        return f"<span style='color:{color};font-weight:{weight}'>{v:+.2f}</span>{extra}"

    def _data_html(self, orf: str) -> str:
        """条件ごとの変化（data/expression.db）: 条件ごとの mRNA・リン酸化・タンパク質量のまとめの表。
        条件の名前を押すと、その下に測定ごとの値と出典（論文へのリンク）が開く（もう一度押すと閉じる）。
        測定がない条件は、名前を灰色にして開けないようにする。色分けで選んでいる条件は太字。
        リン酸化が測られていても p < 0.05 の部位がない条件は、0 ではなく n.s. と出す（変わらなかったとは言えないため）。"""
        if not orf or not expression.available():
            return ""
        summary = expression.gene_summary(orf)
        if not summary:
            return ""
        labels = conditions.labels()
        cur = self.data_condition() if self.data_kind() else None
        kinds = [k for k in expression.KINDS if any(k in v for v in summary.values())]
        # 開いた文献の欄は表の外に出す（表の中に入れると、欄の長い行が列の幅を押し広げて条件名が折り返す）。
        # 表は開いた条件のところで区切るので、値の列の幅を固定して、区切った表どうしの列をそろえる
        # リン酸化の部位は値の下の 2 行目に書くので、値の列はどれも数字の幅で足りる
        widths = {"mrna": 64, "phospho": 72, "protein": 72}
        # 条件名の欄の幅（説明欄の幅から値の列と ▸・余白の分を引いたもの）。名前はこの幅で 2 行までに収める
        name_width = max(60, (self._st.width or self.detail.viewport().width()) - sum(widths[k] for k in kinds) - 28)
        col = "<td width='{}' style='text-align:right;white-space:nowrap'>{}</td>".format
        head = "".join(f"<td width='{widths[k]}' style='text-align:right;white-space:nowrap'><b>{expression.SHORT[k]}</b></td>"
                       for k in kinds)
        table = "<table width='100%' cellspacing='0' cellpadding='1'>{}</table>".format
        listed = expression.gene_points(orf)
        blocks, rows = [], [f"<tr><td></td>{head}</tr>"]
        for key in expression.condition_keys():
            if key not in summary:
                continue
            mine = [x for x in listed if key in x.conditions.split(";")]
            is_open = bool(mine) and key in self._st.open_conds
            name = self._two_lines(labels.get(key, key), name_width, bold=key == cur)
            if key == cur:
                name = f"<b>{name}</b>"
            if mine:
                link = f"<a href='cond:{html.escape(key)}' style='text-decoration:none'>{'▾' if is_open else '▸'} {name}</a>"
            else:   # 測定がない: 灰色で、開けない（▸ の分の幅は空けて名前の位置をそろえる）
                link = f"<span style='color:#999'><span style='color:transparent'>▸</span> {name}</span>"
            cells = "".join(col(widths[k], self._summary_cell(k, summary[key].get(k))) for k in kinds)
            rows.append(f"<tr><td style='white-space:nowrap'>{link}</td>{cells}</tr>")
            if is_open:
                blocks.append(table("".join(rows)))
                rows = []
                blocks.append(f"<table width='100%' cellspacing='0' cellpadding='0'><tr>"
                              f"<td style='padding:2px 6px 6px 14px;background-color:{self._literature_bg()}'>"
                              f"{self._measures_html(mine)}</td></tr></table>")
        if rows:
            blocks.append(table("".join(rows)))
        return "<h4 style='margin-bottom:2px'>条件ごとの log2 比</h4>" + "".join(blocks)

    def _two_lines(self, text: str, width: int, bold: bool = False) -> str:
        """条件名を width px の幅で 2 行までに折り返す（HTML、改行は <br>）。2 行に収まらない分は「…」で省く。
        Qt の表示は行数を限れないので、文字の幅を測って自分で折り返す。"""
        font = QFont(self.detail.font())
        font.setBold(bold)
        metrics = QFontMetrics(font)
        first = ""
        for ch in text:
            if metrics.horizontalAdvance(first + ch) > width:
                break
            first += ch
        rest = text[len(first):]
        if not rest:
            return html.escape(text)
        # 1 行目は、なるべく区切り（・や空白）の後ろで折る
        cut = max(first.rfind("・"), first.rfind(" "), first.rfind("（"))
        if cut > len(first) // 2:
            first, rest = first[:cut + 1], text[cut + 1:]
        second = metrics.elidedText(rest.strip(), Qt.TextElideMode.ElideRight, width)
        # 2 行目は ▸ の分だけ下げて、1 行目の名前の頭にそろえる
        return f"{html.escape(first.strip())}<br><span style='color:transparent'>▸</span> {html.escape(second)}"

    @staticmethod
    def _literature_bg() -> str:
        """条件の測定の一覧（開いた文献の欄）の背景色。明るい画面では淡い青、暗い画面では暗い青。"""
        dark = QApplication.palette().base().color().lightness() < 128
        return "#1f2b36" if dark else "#eaf1f8"

    def _summary_cell(self, kind: str, value: tuple[float, str] | None) -> str:
        """まとめの表の 1 つの値。リン酸化で、測られているが p < 0.05 の部位がない（値 0・部位なし）なら n.s.。"""
        if value is None:
            return self._value_html(None)
        if kind == "phospho" and value == (0.0, ""):
            return "<span style='color:#999'>n.s.</span>"
        v, note = value
        if kind == "phospho" and note:   # 部位は値の下の 2 行目に（同じ行に並べると欄が広がり、条件名が何行にも折り返す）
            return f"{self._value_html(v)}<br><span style='color:#777;font-size:11px'>{html.escape(note)}</span>"
        return self._value_html(v, note)

    @staticmethod
    def _clear(x) -> bool:
        """はっきりした変化か。mRNA・タンパク質量はいつも True、リン酸化は p < 0.05 か 2 倍以上の部位。"""
        return x.kind != "phospho" or (x.pval is not None and x.pval < 0.05) or abs(x.value) >= 1

    def _measures_html(self, points: list) -> str:
        """1 つの条件の測定ごとの値と出典。種類（mRNA・リン酸化・タンパク質量）ごとに小見出しを付け、
        1 つの測定を 2 行で出す: 1 行目に値・部位・出典、2 行目に説明と p 値（説明欄が狭くても崩れないように）。
        値の列は折り返さない。リン酸化は、はっきりした部位（_clear）を先に並べ、それ以外の部位は後に灰色の小さい字で並べる。"""
        parts = []
        for kind, title in expression.KINDS.items():
            rows = []
            for x in sorted((x for x in points if x.kind == kind), key=lambda x: (not self._clear(x), -abs(x.value))):
                ref = (f"<a href='https://pubmed.ncbi.nlm.nih.gov/{x.pmid}/'>{html.escape(x.source)}</a>"
                       if x.pmid else html.escape(x.source))
                if not self._clear(x):
                    rows.append(f"<tr><td align='right' style='white-space:nowrap;padding:2px 8px 0 0;color:#999;font-size:11px'>"
                                f"{x.value:+.2f}</td><td style='color:#999;font-size:11px;padding-top:2px'>"
                                f"{html.escape(x.site)}・{html.escape(x.label)}・p = {x.pval:.2g} {ref}</td></tr>")
                    continue
                site = f"<b>{html.escape(x.site)}</b> " if x.site else ""
                sig = (f" <span style='color:#777;font-size:11px'>p = {x.pval:.2g}・補正後 {x.padj:.2g}</span>"
                       if x.padj is not None else "")
                rows.append(f"<tr><td align='right' style='white-space:nowrap;padding:3px 8px 0 0'>{self._value_html(x.value)}</td>"
                            f"<td style='padding-top:3px'>{site}{ref}</td></tr>"
                            f"<tr><td></td><td style='color:#999'>{html.escape(x.label)}{sig}</td></tr>")
            if rows:
                more = f"<div style='color:#777'>ほか {len(rows) - 30} 件</div>" if len(rows) > 30 else ""
                parts.append(f"<div style='margin-top:4px'><b>{title}</b></div>"
                             f"<table cellspacing='0' cellpadding='0'>{''.join(rows[:30])}</table>{more}")
        return "".join(parts)

    def portal_complexes(self) -> list[tuple[str, str, set[str], str]]:
        """data/complex_portal.tsv の複合体: (番号, 名前, 構成する遺伝子名（大文字）, 説明)。"""
        if getattr(self, "_portal_list", None) is None:
            self._portal_list = []
            try:
                with open(resource_path("data", "complex_portal.tsv"), encoding="utf-8") as f:
                    next(f)
                    for line in f:
                        ac, name, genes, *rest = line.rstrip("\n").split("\t")
                        self._portal_list.append((ac, name, {g.upper() for g in genes.split(";")}, rest[0] if rest else ""))
            except (OSError, ValueError, StopIteration):
                pass
        return self._portal_list

    def portal_by_gene(self) -> dict[str, list[tuple[str, str]]]:
        if getattr(self, "_portal", None) is None:
            self._portal = {}
            for ac, name, genes, _desc in self.portal_complexes():
                for g in genes:
                    self._portal.setdefault(g, []).append((ac, name))
        return self._portal

    def portal_category(self, ac: str) -> str | None:
        """Complex Portal の番号（CPX-…）→ その複合体のカテゴリの名前。"""
        if not self.model:
            return None
        return next((c.name for c in self.model.categories.values() if c.ref == ac and c.is_complex), None)

    def _portal_html(self, gene: str) -> str:
        """Complex Portal で、この遺伝子が構成要素になっている複合体（data/complex_portal.tsv）。名前を押すと
        アプリの中の複合体の説明を出す（出典の Complex Portal へのリンクは、複合体の説明の側に出す）。"""
        items = self.portal_by_gene().get(gene.upper(), [])
        out = ""
        if items:
            links = "".join(f"<li><a href='cpx:{html.escape(ac)}'>{html.escape(name)}</a></li>" for ac, name in items)
            out = f"<h4 style='margin-bottom:2px'>所属する複合体（Complex Portal）</h4><ul style='margin-top:0'>{links}</ul>"
        # パラログ（役割が重なりうる相手）。組の名前を押すと根拠を出す
        pid = self.model.by_name.get(gene.upper()) if self.model else None
        pairs = [n for n, genes in self.model.paralogs.items() if pid in genes] if pid is not None else []
        if pairs:
            gene_link = lambda g: f"<a href='node:{g}'>{html.escape(self.model.names[g])}</a>"   # noqa: E731
            mates = "".join(f"<li>{'・'.join(gene_link(g) for g in self.model.paralogs[n] if g != pid)} "
                            f"<a href='paralog:{html.escape(n)}' style='color:#777'>根拠</a></li>" for n in pairs)
            out += f"<h4 style='margin-bottom:2px'>パラログ</h4><ul style='margin-top:0'>{mates}</ul>"
        return out

    def _tf_pair_note(self, pid: int, relations: list, sub) -> str:
        """転写因子どうしの線を地図で省略していることの説明（相手も地図にあるのに描いていない線）。"""
        model = self.model
        omitted = [it for it in relations if it.source_id in sub.nodes and it.target_id in sub.nodes
                   and model.tf_pair_hidden(it, sub)]
        if not omitted:
            return ""
        names = sorted({model.names[it.source_id if it.source_id != pid else it.target_id] for it in omitted})
        shown = "、".join(names[:6]) + (f" ほか {len(names) - 6} 個" if len(names) > 6 else "")
        return f"<p style='color:#b26a00'>省いた転写因子どうしの線 {len(omitted)} 本: {html.escape(shown)}</p>"

    def _description_html(self, text: str) -> str:
        """説明欄: 英語の説明（原文のまま）。"""
        return f"<p>{html.escape(text)}</p>" if text else ""

    def _edge_list(self, edges, shown: set[int], key: str = "", title: str = "") -> str:
        """上流・下流の関係の一覧（title があれば「上流 (件数)」の見出しも付ける）。表示中の相手を先に並べる。
        MAX_LIST 件を超える分は畳み、「ほか N 件を表示」で開ける（key: up / down など）。"""
        items = self._edge_items(edges, shown)
        head = f"<h4>{title} ({len(items)})</h4>" if title else ""
        if not items:
            return head + "<span style='color:#999'>なし</span>"
        opened = key in self._st.open_lists
        lines = [h for _, h in (items if opened else items[:MAX_LIST])]
        if len(items) > MAX_LIST:
            lines.append(f"<a href='list:{key}' style='color:#555'>▴ 閉じる</a>" if opened else
                         f"<a href='list:{key}' style='color:#555'>▸ ほか {len(items) - MAX_LIST} 件を表示</a>")
        return head + "<br>".join(lines)

    def _edge_html(self, iid: int) -> str:
        it = self.model.interactions[iid]
        effect = resolve_effect(it.interaction_type, it.effect)
        level = self.model.confidence.get(iid, "U")
        rows = [("種類", html.escape(type_label(it.interaction_type))),
                ("作用の向き", html.escape(EFFECTS[effect])
                 + (" <span style='color:#e65100'>⚠ 出典で食い違いあり</span>" if "⚠" in (it.evidence or "") else "")),
                ("向きの根拠", html.escape(self._effect_basis(it, effect))),
                ("確度", html.escape(confidence.label(level))),
                ("条件", html.escape("・".join(conditions.labels().get(k, k) for k in conditions.split(it.conditions))
                                     or conditions.NONE_LABEL))]
        # 文献から拾った性質（組む相手・部位など）は、値のあるものだけ
        rows += [(label, html.escape(detail_text(getattr(it, name, "") or ""))) for name, label in DETAILS
                 if getattr(it, name, "")]
        table = "".join(f"<tr><td style='color:#777;padding-right:8px;vertical-align:top'>{k}</td>"
                        f"<td>{v}</td></tr>" for k, v in rows)
        a, b = it.source.gene_name, it.target.gene_name
        sgd = (f"<p style='font-size:11px;color:#777'>参照データで確かめる: "
               f"<a href='https://www.yeastgenome.org/locus/{a}/interaction'>SGD {html.escape(a)} の相互作用</a>・"
               f"<a href='https://www.yeastgenome.org/locus/{a}/regulation'>{html.escape(a)} の制御</a>・"
               f"<a href='https://www.yeastgenome.org/locus/{b}/interaction'>SGD {html.escape(b)} の相互作用</a>・"
               f"<a href='https://www.yeastgenome.org/locus/{b}/regulation'>{html.escape(b)} の制御</a></p>")
        return (f"<h2 style='margin-bottom:2px'><a href='node:{it.source_id}'>{html.escape(a)}</a>"
                f" → <a href='node:{it.target_id}'>{html.escape(b)}</a></h2>"
                f"<table>{table}</table>"
                f"<h4 style='margin-bottom:2px'>根拠・文献</h4>{self._evidence_html(it.evidence or '')}{sgd}")

    @staticmethod
    def _effect_basis(it, effect: str) -> str:
        """作用の向きを何から決めたか（根拠欄から読み取る）。分からなければ空。"""
        ev = it.evidence or ""
        if not it.effect:
            return f"記録がなく、{type_label(it.interaction_type)}の既定値"
        if effect == "none":
            return confidence.UNKNOWN_REASONS[confidence.unknown_reason(ev)]
        return ("文献で確認" if ev.startswith("文献で確認") else
                "転写因子の誘導実験 IDEA" if "作用の向き: IDEA" in ev else
                "SGD の GO 注釈" if "作用の向き: SGD GO" in ev or ev.startswith("SGD GO") else
                "GO-CAM" if "作用の向き: GO-CAM" in ev or ev.startswith("GO-CAM") else
                "破壊株データからの推定" if "作用の向き: 破壊株データ" in ev else
                "条件ごとの発現からの推定（間接の作用を含む）" if "作用の向き: 条件ごとの発現" in ev else
                "YEASTRACT+ の発現の報告" if "TF acting as" in ev else
                "構造確認用のサンプル" if "サンプル" in ev else "")

    @staticmethod
    def _evidence_html(evidence: str) -> str:
        """根拠欄を出典ごとの行に分け、PubMed の番号を外部へのリンクにする。"""
        if not evidence.strip():
            return "<span style='color:#999'>根拠の記録はありません</span>"

        def link(text: str) -> str:
            text = html.escape(text)
            # PMID: 123, 456 ほか → PubMed へのリンク
            text = re.sub(r"(PMID:? )([\d, ]+)", lambda m: m.group(1) + ", ".join(
                f"<a href='https://pubmed.ncbi.nlm.nih.gov/{n.strip()}/'>{n.strip()}</a>"
                for n in m.group(2).split(",") if n.strip()), text)
            return text

        lines = []
        for part in (p.strip() for p in evidence.split("; ")):
            if not part:
                continue
            label, color = next((v for k, v in EVIDENCE_SOURCES if part.startswith(k)), ("", "#455a64"))
            badge = (f"<span style='color:{color};font-weight:bold'>[{label}]</span> " if label else "")
            style = " style='color:#e65100'" if part.startswith("⚠") else ""
            lines.append(f"<div{style}>{badge}{link(part)}</div>")
        return "".join(lines)

    def paralog_html(self, name: str, heading: bool = True) -> str:
        """パラログの組の説明: 組の遺伝子、候補の出典、働きが重なる根拠（遺伝的相互作用・PMID・GO の重なり）。"""
        cat = self.model.categories.get(name) if self.model else None
        genes = self.model.paralogs.get(name, []) if self.model else []
        links = "・".join(f"<a href='node:{g}'>{html.escape(self.model.names[g])}</a>" for g in genes)
        lines = []
        for line in (cat.description if cat else "").split("\n"):
            text = html.escape(line)
            text = re.sub(r"(\d{6,9})", lambda m: f"<a href='https://pubmed.ncbi.nlm.nih.gov/{m.group(1)}/'>{m.group(1)}</a>", text) \
                if line.startswith("両方を壊すと") else text
            lines.append(f"<li>{text}</li>")
        title = f"<h2 style='margin-bottom:2px'>{html.escape(name)} パラログ</h2>" if heading else \
            f"<h4 style='margin-bottom:2px'>{html.escape(name)}</h4>"
        return f"{title}<p>{links}</p><ul style='margin-top:0'>{''.join(lines)}</ul>"

    def _complex_html(self, name: str) -> str:
        """地図の複合体の枠の説明: 構成する遺伝子と、Complex Portal の説明（出典へのリンク付き）。
        パラログの枠（名前は組を「・」でつないだもの）なら、組ごとの説明。"""
        pairs = name.split("・")
        if all(p in self.model.paralogs for p in pairs):
            if len(pairs) == 1:
                return self.paralog_html(pairs[0])
            return ("<h2 style='margin-bottom:2px'>パラログ</h2>"
                    + "".join(self.paralog_html(p, heading=False) for p in pairs))
        members = self.model.all_complexes().get(name, [])
        items = "".join(f"<li><a href='node:{m}'>{html.escape(self.model.proteins[m].gene_name)}</a></li>"
                        for m in sorted(members, key=lambda m: self.model.names[m]))
        cat = self.model.categories.get(name)
        about = ""
        if cat and cat.ref:
            about += (f"<p>出典: <a href='https://www.ebi.ac.uk/complexportal/complex/{html.escape(cat.ref)}'>"
                      f"Complex Portal {html.escape(cat.ref)}</a></p>")
        if cat and cat.description:
            about += f"<h4 style='margin-bottom:2px'>説明</h4>{self._description_html(cat.description)}"
        return (f"<h2>{html.escape(name)} 複合体</h2>{about}<h4>構成する遺伝子 ({len(members)})</h4><ul>{items}</ul>"
                + self._complex_edges(name, members))

    def _complex_edges(self, name: str, members: list[int]) -> str:
        """複合体の上流・下流: 構成遺伝子の関係（複合体の中どうしは除く）。論文が複合体の名前で書いた関係
        （data/complex_groups.csv。この複合体の Complex Portal の番号に当たるもの）は 1 行にまとめ、先頭に並べる。"""
        inside = set(members)
        cat = self.model.categories.get(name)
        labels = set(complex_groups.labels_for_portal(cat.ref if cat else ""))
        ups, downs = [], []
        for it in self.model.interactions.values():
            marks = complex_groups.parse(it.evidence)
            mine = {label for label, _ in marks} & labels
            if it.source_id in inside and it.target_id in inside:
                continue
            if it.target_id in inside or any(side == "下流" and label in mine for label, side in marks):
                ups.append(it)
            elif it.source_id in inside or any(side == "上流" and label in mine for label, side in marks):
                downs.append(it)
        sub = self._pane(self.detail_pane).sub if self._render_pane else None
        shown = sub.nodes if sub else set()
        return (self._edge_list(ups, shown, "cup", "上流") + self._edge_list(downs, shown, "cdown", "下流"))

    def _open_link(self, target: str):
        if target.startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(target))   # PubMed・SGD などはブラウザで開く
            return
        kind, _, key = target.partition(":")
        if self.toggle_detail(self.detail_state, target):
            # 開閉は説明欄のスクロール位置を保つ
            bar = self.detail.verticalScrollBar()
            pos = bar.value()
            self._refresh_detail_text()
            bar.setValue(pos)
        elif kind == "node":
            self.show_node(int(key), self.detail_pane)
        elif kind in ("mapfocus", "mapgrow") and self.model and int(key) in self.model.proteins:
            # 説明欄の「注目」「拡張」: 操作中の表示枠で、検索欄の吹き出しと同じ操作をする
            pane = self.active_pane
            (pane.change_focus if kind == "mapfocus" else pane.expand_gene)(int(key))
        elif kind == "paralog":
            self.show_complex(key, self.detail_pane)
        elif kind == "cpx":
            name = self.portal_category(key)
            if name:
                self.show_complex(name, self.detail_pane)
            else:   # DB にカテゴリがないときは、出典を開く
                QDesktopServices.openUrl(QUrl(f"https://www.ebi.ac.uk/complexportal/complex/{key}"))
        elif kind == "edge":
            self.show_edge(int(key), self.detail_pane)
        elif kind == "kocolor":
            orf, _, color_kind = key.partition(":")
            self.color_by_strain(orf, color_kind)

    # ---- 説明欄の履歴（◀ ▶） ----
    DETAIL_HISTORY_MAX = 50

    def _push_detail(self, kind: str, key, pane) -> None:
        if self._detail_nav:
            return
        if 0 <= self._detail_pos < len(self._detail_hist) and self._detail_hist[self._detail_pos][:2] == (kind, key):
            return
        del self._detail_hist[self._detail_pos + 1:]
        self._detail_hist.append((kind, key, pane))
        del self._detail_hist[:-self.DETAIL_HISTORY_MAX]
        self._detail_pos = len(self._detail_hist) - 1
        self._update_detail_nav()

    def _update_detail_nav(self) -> None:
        self.detail_back.setEnabled(self._detail_pos > 0)
        self.detail_forward.setEnabled(self._detail_pos < len(self._detail_hist) - 1)

    def _detail_go(self, step: int) -> None:
        """説明欄の ◀ ▶: 履歴の遺伝子・線・複合体を出し直し、地図でもそれを選ぶ（遺伝子は緑の選択、線は強調）。"""
        pos = self._detail_pos + step
        if not 0 <= pos < len(self._detail_hist) or not self.model:
            return
        self._detail_pos = pos
        kind, key, pane = self._detail_hist[pos]
        if pane not in self._views():
            pane = self.active_pane
        self._detail_nav = True
        try:
            if kind == "node" and key in self.model.proteins:
                picked = [f"p{key}"] if key in pane.sub.nodes else []
                pane.js(f"app.setPicked({json.dumps(picked)})")
                self.show_node(key, pane)
            elif kind == "edge" and key in self.model.interactions:
                pane.js("app.setPicked([])")
                self.show_edge(key, pane)
            elif kind == "complex":
                pane.js("app.setPicked([])")
                self.show_complex(key, pane)
            elif kind == "help":
                self._show_help_page(key)
        finally:
            self._detail_nav = False
        self._update_detail_nav()

    def show_node(self, pid: int, pane: GraphPane | None = None):
        if pid not in self.model.proteins:
            return
        self._push_detail("node", pid, self._pane(pane))
        self.detail_pane = self._pane(pane)
        if self.selected != ("node", pid):
            self.detail_state.reset()   # 別の遺伝子を開いたら、一覧を閉じる
        self.selected = ("node", pid)
        self.detail.setHtml(self._node_html(pid))
        self._set_controls([])
    def show_complex(self, name: str, pane: GraphPane | None = None):
        self._push_detail("complex", name, self._pane(pane))
        self.detail_pane = self._pane(pane)
        self.selected = ("complex", name)
        self.detail.setHtml(self._complex_html(name))
        self._set_controls([])

    def show_edge(self, iid: int, pane: GraphPane | None = None):
        if iid not in self.model.interactions:
            return
        self._push_detail("edge", iid, self._pane(pane))
        self.detail_pane = self._pane(pane)
        self.selected = ("edge", iid)
        self.detail.setHtml(self._edge_html(iid))
        # 地図でその線に注目する（転写因子どうしなど、ふだん省く線でも見せる）
        target = pane if pane is not None else self.detail_pane
        target.js(f"app.focusEdge({json.dumps('e' + str(iid))})")
        self._set_controls([])
