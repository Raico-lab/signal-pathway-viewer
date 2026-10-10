"""表示枠：1 つのネットワーク図（Cytoscape.js）と、その注目範囲・戻る/進む履歴。

可視化タブには表示枠を複数並べられる。モデル・詳細パネルはタブ側で共有する。
"""
import base64
import json
import sys

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (QListWidget, QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QSizePolicy, QSpinBox, QVBoxLayout)

from .. import relation_paths
from ..paths import resource_path
from ..subgraph import Subgraph, ViewSpec, build_subgraph, grow_targets, link_path, regrow

DEFAULT_FOCUS = ["TOR1"]
DEFAULT_UP, DEFAULT_DOWN = 1, 1
MAX_HISTORY = 50         # 戻る・進むで覚えておく表示の数
WARN_NODES = 400         # これを超えたら段数を減らすよう案内する
WARN_EDGES = 200         # 描く線がこれを超えそうなら、計算を始める前に確認する（キャンセルで元に戻す）

ACTIVE_STYLE = "GraphPane { border: 2px solid #1e88e5; border-radius: 4px; }"
INACTIVE_STYLE = "GraphPane { border: 2px solid #d0d0d0; border-radius: 4px; }"


class Bridge(QObject):
    """JavaScript → Python の通知窓口（QWebChannel で公開）。"""

    ready = pyqtSignal()
    nodeClicked = pyqtSignal(int)
    edgeClicked = pyqtSignal(int)
    complexClicked = pyqtSignal(str)
    backgroundClicked = pyqtSignal()
    nodeFocus = pyqtSignal(int)
    nodeGrow = pyqtSignal(int)
    ungrow = pyqtSignal(int)
    pickedChanged = pyqtSignal(list)
    lodChanged = pyqtSignal(int, int)
    filterToggled = pyqtSignal(str, str, bool)
    filterReset = pyqtSignal(str)
    displayOptionChanged = pyqtSignal(str, bool)
    helpRequested = pyqtSignal()

    @pyqtSlot()
    def onHelp(self):
        self.helpRequested.emit()

    @pyqtSlot()
    def onReady(self):
        self.ready.emit()

    @pyqtSlot(str)
    def onNodeClicked(self, element_id):
        self.nodeClicked.emit(int(element_id[1:]))

    @pyqtSlot(str)
    def onEdgeClicked(self, element_id):
        self.edgeClicked.emit(int(element_id[1:]))

    @pyqtSlot(str)
    def onComplexClicked(self, name):
        self.complexClicked.emit(name)

    @pyqtSlot()
    def onBackgroundClicked(self):
        self.backgroundClicked.emit()

    @pyqtSlot(str)
    def onNodeFocus(self, element_id):
        self.nodeFocus.emit(int(element_id[1:]))

    @pyqtSlot(str)
    def onNodeGrow(self, element_id):
        self.nodeGrow.emit(int(element_id[1:]))

    @pyqtSlot(str)
    def onPickedChanged(self, ids_json):
        self.pickedChanged.emit([int(e[1:]) for e in json.loads(ids_json)])

    @pyqtSlot(str)
    def onUngrow(self, element_id):
        self.ungrow.emit(int(element_id[1:]))

    @pyqtSlot(int, int)
    def onLodChanged(self, shown, total):
        self.lodChanged.emit(shown, total)

    @pyqtSlot(str, str, bool)
    def onFilterToggled(self, kind, key, shown):
        self.filterToggled.emit(kind, key, shown)

    @pyqtSlot(str)
    def onFilterReset(self, kind):
        self.filterReset.emit(kind)

    @pyqtSlot(str, bool)
    def onDisplayOption(self, key, on):
        self.displayOptionChanged.emit(key, on)


class ShrinkCombo(QComboBox):
    """普段は中身に合わせた幅、枠が狭いときは縮められる選択肢（表示枠を並べたときに枠を広げすぎない）。"""

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(min(hint.width(), 60))
        return hint


class ShrinkLabel(QLabel):
    """普段は文字に合わせた幅、枠が狭いときは途中で切れてよい表示。"""

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(20)
        return hint


class LoggingPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source):
        print(f"[js] {message} ({source}:{line})", file=sys.stderr)


class GraphPane(QFrame):
    activated = pyqtSignal(object)
    nodeClicked = pyqtSignal(object, int)
    edgeClicked = pyqtSignal(object, int)
    complexClicked = pyqtSignal(object, str)
    backgroundClicked = pyqtSignal(object)
    viewChanged = pyqtSignal(object)      # 注目範囲が変わった
    closeRequested = pyqtSignal(object)
    filterToggled = pyqtSignal(str, str, bool)   # 凡例のチェックボックス（種類, キー, 表示するか）
    pickedChanged = pyqtSignal(object, list)            # 緑で選択中の遺伝子が変わった
    filterReset = pyqtSignal(str)                 # 凡例の欄ごとの「全て選択」（種類: effects / types / roles）
    displayOptionChanged = pyqtSignal(str, bool)
    statusMessage = pyqtSignal(str)
    helpRequested = pyqtSignal(object)      # 凡例の「Help」を押した（右側に凡例の見方を出す）
    detachRequested = pyqtSignal(object)    # 「別ウィンドウで開く」を押した
    relationGene = pyqtSignal(object, int)   # 経路の表示中に地図の遺伝子をクリックした（一覧をその遺伝子を通る経路に絞る）

    def __init__(self, tab, state: dict | None = None):
        super().__init__()
        self.tab = tab                    # NetworkTab（モデル・共有設定）
        state = state or {}
        self.focus_names: list[str] = list(state.get("focus", []))[:1]   # 注目する遺伝子は常に 1 つ
        self.up_steps = int(state.get("up", DEFAULT_UP))
        self.down_steps = int(state.get("down", DEFAULT_DOWN))
        self.extra: set[int] = set()      # 展開で追加した遺伝子
        self.uncapped: set[int] = set()   # 件数制限を外した遺伝子
        self.excluded: set[int] = set()   # 2 回目のクリックで段数が増えすぎないよう隠した段の遺伝子
        self.grown: set[int] = set()      # 2 回目のクリックで上流・下流を追加した起点（紫の印を残す）
        self.grown_order: list[int] = []  # 紫の印を付けた順（新しい枠に開くときに一番最後のものを使う）
        # 紫の起点ごとに、2 回目のクリックで変えた中身（印を外したときに元に戻す）:
        # added = 追加した遺伝子、hidden = 隠した段の遺伝子、unextra = 隠した段のうち追加表示から外したもの
        self.grown_info: dict[int, dict[str, set[int]]] = {}
        self.trf: set[int] = set()        # 左の TFs 一覧でチェックした転写因子（一番上の段に表示）
        self.picked: list[int] = []       # 緑で選択中の遺伝子
        # 左の「経路」タブの表示（緑の選択とは独立）。OFF なら None。
        # relation_extra は経路のために加えた中継の遺伝子（設定を変えるたびに入れ替え、OFF で消す。履歴には残さない）
        self.relation: dict | None = None
        self.relation_extra: set[int] = set()
        # 「経路」の一覧で選んだ経路のために一時的に加えた遺伝子（背景をクリックすると消える）
        self.relation_focus_extra: set[int] = set()
        # 経路タブの経路として、地図に描かない線（関係の多い遺伝子・転写因子どうし）でも描くもの（全体の表示・一覧で選んだ経路）
        self.relation_allow: set[tuple[int, int]] = set()
        self.focus_allow: set[tuple[int, int]] = set()
        # 前回終了時の表示（拡張・選択など）。遺伝子名で保存されているので、モデルを読み込んでから当てはめる
        self._saved_state: dict | None = state if "extra" in state else None
        self._pending_picked: list[int] = []   # 描画し終えたら緑の選択に戻す遺伝子
        self._pending_detail = True            # 戻したとき、最後に選んだものの詳細を出すか
        self.history: list[dict] = []
        self.history_pos = -1
        self.sub = Subgraph(set(), set(), {}, {}, {})
        self.page_ready = False

        self.setStyleSheet(INACTIVE_STYLE)
        self._build_view()
        self._build_bar()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)
        layout.addLayout(self.bar)
        layout.addLayout(self.view_bar)
        layout.addLayout(self.info_bar)
        layout.addWidget(self.view, 1)

    # ================= 構築 =================
    def _build_view(self):
        self.view = QWebEngineView()
        self.view.setPage(LoggingPage(self.view))
        # ページ全体の拡大は使わない（凡例などの文字の大きさを固定する。図の拡大・縮小は図の側で行う）
        self.view.loadFinished.connect(lambda _ok: self.view.setZoomFactor(1.0))
        self.bridge = Bridge()
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self.bridge.ready.connect(self._on_page_ready)
        self.bridge.helpRequested.connect(lambda: (self.activate(), self.helpRequested.emit(self)))
        self.bridge.nodeClicked.connect(lambda pid: (self.activate(), self.nodeClicked.emit(self, pid)))
        self.bridge.nodeClicked.connect(self.focus_relation)   # 経路の表示中なら、その遺伝子を通る経路を強調
        self.bridge.backgroundClicked.connect(self._clear_relation_focus)
        self.bridge.complexClicked.connect(self._focus_relation_complex)
        self.bridge.edgeClicked.connect(lambda iid: (self.activate(), self.edgeClicked.emit(self, iid)))
        self.bridge.complexClicked.connect(lambda n: (self.activate(), self.complexClicked.emit(self, n)))
        self.bridge.backgroundClicked.connect(lambda: (self.activate(), self.backgroundClicked.emit(self)))
        self.bridge.nodeFocus.connect(self._focus_clicked)
        self.bridge.nodeGrow.connect(self.grow_node)
        self.bridge.ungrow.connect(self.remove_grown)
        self.bridge.pickedChanged.connect(self._on_picked_changed)
        self.bridge.lodChanged.connect(self._on_lod_changed)
        self.bridge.filterToggled.connect(self.filterToggled)
        self.bridge.filterReset.connect(self.filterReset)
        self.bridge.displayOptionChanged.connect(self.displayOptionChanged)
        self.view.setUrl(QUrl.fromLocalFile(str(resource_path("tor_app", "web", "network.html"))))

    def _build_bar(self):
        self.title = QLabel()
        self.title.setStyleSheet("font-weight:bold;")
        self.back_button = self._small_button("◀", self.go_back)
        self.forward_button = self._small_button("▶", self.go_forward)
        self.search = QLineEdit()
        self.search.setPlaceholderText("遺伝子名で検索 例: TOR1")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(120)
        # キー入力の処理が終わってから出す（Enter のキーを離したときに吹き出しが閉じないように）
        self.search.returnPressed.connect(lambda: QTimer.singleShot(0, self._on_search_enter))
        self._build_search_menu()
        self._build_candidates()
        self.new_pane_button = self._small_button(
            "⧉ 新しい枠", self.open_marked_in_new_pane,
            "新しい表示枠を開いて、緑で選択中の遺伝子に注目します。複数なら最後に選んだものです。\n"
            "緑がなければ最後に紫にしたもの、それもなければ注目中のものに注目します")
        self.close_button = self._small_button("✕", lambda: self.closeRequested.emit(self), "この表示枠を閉じる")
        self.detach_button = self._small_button(
            "別ウィンドウで開く", lambda: self.detachRequested.emit(self),
            "この表示枠を別ウィンドウで開きます。枠が 1 つなら今の内容を写して開き、2 つ以上ならこの枠を別ウィンドウに移します")
        self.bar = QHBoxLayout()
        for w in (self.title, self.back_button, self.forward_button, self.search, self.detach_button,
                  self.new_pane_button, self.close_button):
            self.bar.addWidget(w)
        self.bar.setStretchFactor(self.search, 1)

        # 配置・遺伝子の色・線の色（全表示枠に共通。値は NetworkTab の選択肢が持ち、変えると全表示枠でそろう）
        self.view_bar = QHBoxLayout()
        self.view_picks: list[tuple[QComboBox, QComboBox]] = []   # (この枠の選択肢, 値を持つ共通の選択肢)
        for label, shared in self.tab.view_choices():
            pick = ShrinkCombo()
            for i in range(shared.count()):
                pick.addItem(shared.itemText(i), shared.itemData(i))
            pick.setToolTip(shared.toolTip())
            pick.setCurrentIndex(shared.currentIndex())
            pick.setEnabled(shared.isEnabled())
            pick.currentIndexChanged.connect(lambda i, shared=shared: shared.setCurrentIndex(i))
            self.view_picks.append((pick, shared))
            self.view_bar.addWidget(QLabel(label))
            self.view_bar.addWidget(pick)
            self.view_bar.addSpacing(4)
        # 設定をクリア: 線の色の右隣（共通の設定の行に置く）
        self.clear_settings_button = self._small_button(
            "設定をクリア", lambda: self.tab.reset_settings(self),
            "TF・経路・条件・配置・色・線・凡例の設定を最初の状態に戻します（注目・拡張・段数はそのまま）")
        self.view_bar.addWidget(self.clear_settings_button)
        self.view_bar.addStretch(1)
        # この表示枠の図全体を画像で保存する（画面に見えていない部分も含む）
        for w in (self._small_button("PNG", self.export_png, "この表示枠の図全体を、3 倍の解像度の PNG 画像で保存します"),
                  self._small_button("SVG", self.export_svg, "この表示枠の図全体を SVG 画像で保存します。拡大しても荒くなりません")):
            self.view_bar.addWidget(w)

        self.up_spin = self._step_spin(self.up_steps, "上流を何段までたどるか")
        self.down_spin = self._step_spin(self.down_steps, "下流を何段までたどるか")
        # 段数を続けて変えたときは、少し待ってから最後の値だけ計算する（途中の値の重い計算をしない）
        self._steps_timer = QTimer(self)
        self._steps_timer.setSingleShot(True)
        self._steps_timer.setInterval(250)
        self._steps_timer.timeout.connect(self._apply_steps)
        self.up_spin.valueChanged.connect(lambda: self._steps_timer.start())
        self.down_spin.valueChanged.connect(lambda: self._steps_timer.start())
        # 遺伝子数などの表示は、枠が狭ければ途中で切れてよい（枠の最小の幅を広げない）
        self.focus_label = ShrinkLabel()
        self.lod_label = ShrinkLabel()
        self.lod_label.setStyleSheet("color:#777;")
        self.info_bar = QHBoxLayout()
        for w in (QLabel("上流"), self.up_spin, QLabel("段 下流"), self.down_spin, QLabel("段"), self.focus_label):
            self.info_bar.addWidget(w)
        self.info_bar.addStretch(1)
        self.info_bar.addWidget(self.lod_label)
        # この表示枠だけに効くボタン（行の右端）
        relayout = self._small_button("再配置", self.relayout,
                                      "注目・拡張・上流/下流の段数・凡例のチェックから表示する遺伝子を決め直し、\n"
                                      "選んだ方式で自動配置し直します。範囲外の遺伝子は消えます。同じ設定なら常に同じ図になります")
        fit = self._small_button("画面に合わせる", self.fit_view, "この表示枠の図全体が収まるように表示します")
        for w in (relayout, fit):
            self.info_bar.addWidget(w)
        self._update_nav_buttons()

    def sync_view_choices(self) -> None:
        for pick, shared in self.view_picks:
            pick.setEnabled(shared.isEnabled())
            if pick.currentIndex() != shared.currentIndex():
                pick.blockSignals(True)
                pick.setCurrentIndex(shared.currentIndex())
                pick.blockSignals(False)

    def relayout(self) -> None:
        """再配置: 注目・段数・拡張（紫）の起点・TFs・凡例のチェックだけから表示する遺伝子を決め直し、配置し直す。
        「経路」などで加えた遺伝子や、拡張で隠した段は元に戻し、範囲外の遺伝子は消す。同じ設定なら常に同じ図になる。"""
        self.activate()
        if not self.model:
            return
        snap = self._snapshot()
        before = set(self.sub.nodes)
        self._regrow()
        if not self._confirm_large(snap):
            return
        self._record_history()
        self.refresh_view()
        removed, new = len(before - self.sub.nodes), len(self.sub.nodes - before)
        detail = "、".join(t for t in (f"範囲外の {removed} 個を消しました" if removed else "",
                                       f"{new} 個を加えました" if new else "") if t)
        self.statusMessage.emit("再配置しました" + (f"。{detail}" if detail else ""))

    def _adjacency(self, with_tx: bool = False):
        """上流・下流をたどる関係（「作用が分かっている関係のみ」と、凡例のチェックで隠したものを反映）。
        with_tx なら転写制御もたどる。"""
        return self.model.view_adjacency(self.tab.known_only(), self.tab.hidden_filters(), set(self.focus_ids()),
                                         with_tx)

    def _regrow(self) -> None:
        """注目・段数・拡張（紫）の起点・TFs だけから、拡張で加える遺伝子を決め直す（操作の順序によらない）。"""
        self._prune_trf()
        spec = ViewSpec(self.focus_ids(), self.up_steps, self.down_steps, grown=set(self.grown), trf=set(self.trf))
        added = regrow(self._adjacency(), self.model.all_complexes(), self.model.names, spec,
                       self.LINK_DEPTH, self.LINK_CAP, self.model.paralogs)
        self.extra = set().union(*added.values())
        self.uncapped = set()
        self.excluded = set()
        self.grown_info = {g: {"added": a, "hidden": set(), "unextra": set()} for g, a in added.items()}

    def fit_view(self) -> None:
        self.activate()
        self.js("app.fit()")
        if not self.picked:   # 緑で選んでいるときは、その強調（線の表示）を保つ
            self.js("app.clearHighlight()")

    def _small_button(self, text: str, slot, tooltip: str = "") -> QPushButton:
        button = QPushButton(text)
        button.setToolTip(tooltip)
        # 狭い枠でも遺伝子名の入力欄に幅が残るよう、ボタンは文字に合わせた幅にする
        button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        button.setMaximumWidth(max(34, button.fontMetrics().horizontalAdvance(text) + 24))
        button.clicked.connect(lambda: (self.activate(), slot()))
        return button

    @staticmethod
    def _step_spin(value: int, tooltip: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 6)
        spin.setValue(value)
        spin.setToolTip(tooltip)
        return spin

    def set_title(self, index: int, closable: bool) -> None:
        self.title.setText(f"枠{index}")
        self.close_button.setVisible(closable)

    def set_active(self, active: bool) -> None:
        self.setStyleSheet(ACTIVE_STYLE if active else INACTIVE_STYLE)

    def activate(self) -> None:
        self.activated.emit(self)

    def mousePressEvent(self, event):
        self.activate()
        super().mousePressEvent(event)

    # ================= 状態 =================
    @property
    def model(self):
        return self.tab.model

    def state(self) -> dict:
        """次回起動時に復元する内容（注目・段数に加え、拡張・追加した遺伝子・TFs・緑の選択）。"""
        if self._saved_state is not None or not self.model:
            # まだモデルを読み込む前なら、前回の内容をそのまま残す
            return dict(self._saved_state or {"focus": self.focus_names, "up": self.up_steps, "down": self.down_steps})
        names = self.model.names
        state = self._view_state()
        state["grown_order"] = [names[g] for g in self.grown_order if g in names]
        state["picked"] = [names[p] for p in self.picked if p in names]
        return state

    def set_completer(self, names: list[str]) -> None:
        """検索欄の候補にする遺伝子名。"""
        self._gene_names = sorted(names, key=str.upper)
        self._update_candidates()

    # ---- 検索欄の候補の一覧 ----
    # 検索欄に文字が入っている間は、確定した後も候補を出し続ける（QCompleter は確定すると閉じるので使わない）。
    # 一覧は表示枠の中に重ねて、検索欄のすぐ下に出す（フォーカスは検索欄のまま）。拡張・注目の吹き出しは一覧の右に出す
    CANDIDATE_MAX = 50       # 一覧に並べる最大数
    CANDIDATE_ROWS = 10      # 一度に見える行数

    def _build_candidates(self) -> None:
        self._gene_names: list[str] = []
        # 別の窓にはしない（macOS では、別の小窓（Tool）を隠すとアプリ全体が「裏」扱いになり、ほかのプルダウンが開かなくなる）。
        # 表示枠の中に重ねて出す
        self.candidates = QListWidget(self)
        self.candidates.hide()
        self.candidates.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.candidates.setStyleSheet("QListWidget { border: 1px solid #b0bec5; background: #fff; }"
                                      "QListWidget::item:selected { background: #e3f2fd; color: #000; }")
        self.candidates.itemClicked.connect(lambda item: self._pick_candidate(item.text()))
        self.search.textChanged.connect(lambda _t: self._update_candidates())
        self.search.textChanged.connect(lambda _t: self._hide_search_menu_if_stale())
        self.search.installEventFilter(self)

    def _candidate_matches(self, text: str) -> list[str]:
        key = text.strip().upper()
        if not key:
            return []
        starts = [n for n in self._gene_names if n.upper().startswith(key)]
        inside = [n for n in self._gene_names if key in n.upper() and not n.upper().startswith(key)]
        return (starts + inside)[:self.CANDIDATE_MAX]

    def _update_candidates(self) -> None:
        """検索欄の文字に合う遺伝子を一覧にして出す（文字が空・枠が見えないときは隠す）。"""
        if not hasattr(self, "candidates"):
            return
        matches = self._candidate_matches(self.search.text())
        visible = bool(matches) and self.search.isVisible() and self.isVisible()
        if not visible:
            self.candidates.hide()
            return
        current = self.candidates.currentItem().text() if self.candidates.currentItem() else None
        if [self.candidates.item(i).text() for i in range(self.candidates.count())] != matches:
            self.candidates.clear()
            self.candidates.addItems(matches)
            if current in matches:
                self.candidates.setCurrentRow(matches.index(current))
        self._place_candidates()
        self.candidates.show()
        self.candidates.raise_()

    def _place_candidates(self) -> None:
        rows = min(self.candidates.count(), self.CANDIDATE_ROWS)
        height = self.candidates.sizeHintForRow(0) * rows + 4 if rows else 0
        below = self.search.mapTo(self, self.search.rect().bottomLeft())   # 表示枠の中の座標
        # 幅は中身（遺伝子名）に合わせる（検索欄と同じ幅にすると、右に出す拡張・注目の吹き出しが枠の外に出る）
        width = max(120, min(self.search.width(), self.candidates.sizeHintForColumn(0) + 30))
        height = min(height, max(40, self.height() - below.y() - 4))   # 枠の下にはみ出さない
        self.candidates.setGeometry(below.x(), below.y() + 1, width, height)

    def _pick_candidate(self, name: str) -> None:
        self.search.setText(name)
        self.search.setFocus()
        QTimer.singleShot(0, self._show_search_menu)

    def _on_search_enter(self) -> None:
        """Enter: 入力が遺伝子名そのものならそれ、違えば一覧で選んでいる（なければ先頭の）候補を使う。
        候補はここで計算し直す（アプリが裏にあると一覧は作られていない）。"""
        text = self.search.text().strip()
        if self.model and text.upper() not in self.model.by_name:
            matches = self._candidate_matches(text)
            if matches:
                item = self.candidates.currentItem() if self.candidates.isVisible() else None
                self.search.setText(item.text() if item and item.text() in matches else matches[0])
        self._show_search_menu()

    def eventFilter(self, obj, event):
        # 検索欄で ↑↓ を押すと候補の一覧の選択を動かす。Esc で検索欄を空にする
        if obj is self.search and event.type() == event.Type.KeyPress and self.candidates.isVisible():
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                row = self.candidates.currentRow() + (1 if key == Qt.Key.Key_Down else -1)
                self.candidates.setCurrentRow(max(0, min(self.candidates.count() - 1, row)))
                return True
            if key == Qt.Key.Key_Escape:
                self.search.clear()
                return True
        if event.type() == event.Type.MouseButtonPress and self._menu_alive() and self.search_menu.isVisible():
            # 吹き出しの外（検索欄・候補の一覧を除く）を押したら閉じる
            pos = event.globalPosition().toPoint()
            inside = [self.search_menu, self.candidates, self.search]
            if not any(w.isVisible() and w.rect().contains(w.mapFromGlobal(pos)) for w in inside):
                self._hide_search_menu()
        if (obj is self.search or obj is self.window()) and event.type() in (event.Type.Move, event.Type.Resize):
            self._update_candidates()   # 窓を動かしたら、候補の一覧もついていく
            if self._menu_alive() and self.search_menu.isVisible():
                self._place_search_menu()
        return super().eventFilter(obj, event)

    def moveEvent(self, event):
        super().moveEvent(event)
        self._update_candidates()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "candidates"):
            self.window().installEventFilter(self)   # 同じ窓に何度入れても 1 回分として扱われる
            QTimer.singleShot(0, self._update_candidates)

    def hideEvent(self, event):
        super().hideEvent(event)
        try:   # 終了するときは、別窓の一覧・吹き出しが先に消えていることがある
            if hasattr(self, "candidates"):
                self.candidates.hide()
            self._hide_search_menu()
        except RuntimeError:
            pass

    def on_model_reloaded(self, use_default: bool) -> None:
        """DB を読み直したとき。use_default なら、注目遺伝子が使えない場合に既定（TOR）に注目する。"""
        if self._saved_state is not None:
            saved, self._saved_state = self._saved_state, None
            self._apply_view_state(saved)
            by_name = self.model.by_name
            self.grown_order = [by_name[n.upper()] for n in saved.get("grown_order", []) if n.upper() in by_name]
            self._pending_picked = [by_name[n.upper()] for n in saved.get("picked", []) if n.upper() in by_name]
        self.extra = {n for n in self.extra if n in self.model.proteins}
        self.uncapped = {n for n in self.uncapped if n in self.model.proteins}
        self.excluded = {n for n in self.excluded if n in self.model.proteins}
        self.grown = {n for n in self.grown if n in self.model.proteins}
        self.grown_info = {g: i for g, i in self.grown_info.items() if g in self.grown}
        self.trf = {n for n in self.trf if n in self.model.proteins}
        if use_default and not self.focus_ids():
            self.focus_names = [g for g in DEFAULT_FOCUS if g.upper() in self.model.by_name]
            self._set_steps(DEFAULT_UP, DEFAULT_DOWN)
        if not self.history:
            self._record_history()
        self.refresh_view()

    def _set_steps(self, up: int, down: int) -> None:
        self.up_steps, self.down_steps = up, down
        for spin, value in ((self.up_spin, up), (self.down_spin, down)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def focus_ids(self) -> list[int]:
        return [self.model.by_name[n.upper()] for n in self.focus_names if n.upper() in self.model.by_name]

    # ================= 注目範囲の変更 =================
    def _build_search_menu(self) -> None:
        """検索欄の下に出す選択肢（拡張・注目）。地図上の遺伝子の吹き出しと同じ見た目にする。
        Qt.Popup にすると、開いている間に候補の一覧を押しても、その押下が吹き出しを閉じるだけで消えてしまう。
        別の小窓（Tool）にすると、macOS では隠したときにアプリ全体が「裏」扱いになり、ほかのプルダウンが開かなくなる。
        そこで表示枠の中に重ねる部品にし、外を押す・文字を変える・枠が隠れるときに閉じる。"""
        self.search_menu = QFrame(self)   # 候補の一覧と同じく、別の窓にせず表示枠の中に重ねる
        self.search_menu.hide()
        self.search_menu.setStyleSheet(
            "QFrame { background: #fff; border: 1px solid #cfd8dc; border-radius: 6px; }"
            "QLabel { border: none; color: #37474f; font-weight: bold; padding: 0 4px; }"
            "QPushButton { font-weight: bold; padding: 4px 14px; border-radius: 4px; background: #fff; }"
            "QPushButton#grow { color: #6a1b9a; border: 2px solid #8e24aa; }"
            "QPushButton#grow:hover { background: #f3e5f5; }"
            "QPushButton#focus { color: #e65100; border: 2px solid #ff8f00; }"
            "QPushButton#focus:hover { background: #fff3e0; }")
        h = QHBoxLayout(self.search_menu)
        h.setContentsMargins(6, 6, 6, 6)
        self.search_menu_label = QLabel()
        grow = QPushButton("拡張")
        grow.setObjectName("grow")
        grow.setToolTip("地図にある遺伝子なら、上流・下流を 1 階層追加します。\n"
                        "地図にない遺伝子なら、その遺伝子と、今の地図と 3 段以内でつながる経路上の遺伝子だけを加えます")
        focus = QPushButton("注目")
        focus.setObjectName("focus")
        focus.setToolTip("この遺伝子を中心に表示し直します")
        for b in (grow, focus):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # 押しても検索欄のフォーカスを奪わない
        grow.clicked.connect(lambda: self._search_action("grow"))
        focus.clicked.connect(lambda: self._search_action("focus"))
        for w in (self.search_menu_label, grow, focus):
            h.addWidget(w)

    def _show_search_menu(self) -> None:
        text = self.search.text().strip()
        pid = self.model.by_name.get(text.upper()) if (text and self.model) else None
        if pid is None:
            self.statusMessage.emit(f"「{text}」は登録されていません" if text else "遺伝子名を入力してください")
            return
        self.search_menu_label.setText(self.model.proteins[pid].gene_name)
        self.search_menu.adjustSize()
        self._place_search_menu()
        QApplication.instance().installEventFilter(self)   # 外を押したら閉じる（閉じるときに外す）
        self.search_menu.show()
        self.search_menu.raise_()

    def _place_search_menu(self) -> None:
        # 検索欄の下の左寄りに出す。候補の一覧が出ていれば、その右隣に並べる（一覧を隠さない。一覧は中身の幅なので左寄りになる）
        below = self.search.mapTo(self, self.search.rect().bottomLeft())   # 表示枠の中の座標
        if self.candidates.isVisible():
            x, y = self.candidates.geometry().right() + 6, below.y() + 1
        else:
            x, y = below.x(), below.y() + 4   # 検索欄の左端に合わせる
        # 表示枠の中に収める
        x = max(0, min(x, self.width() - self.search_menu.width()))
        y = max(0, min(y, self.height() - self.search_menu.height()))
        self.search_menu.move(x, y)

    def _menu_alive(self) -> bool:
        return hasattr(self, "search_menu") and not sip.isdeleted(self.search_menu)

    def _hide_search_menu(self) -> None:
        if self._menu_alive() and self.search_menu.isVisible():
            self.search_menu.hide()
            QApplication.instance().removeEventFilter(self)

    def _hide_search_menu_if_stale(self) -> None:
        """検索欄の文字が吹き出しの遺伝子と違うものになったら閉じる。"""
        if self._menu_alive() and self.search.text().strip().upper() != self.search_menu_label.text().upper():
            self._hide_search_menu()

    def _search_action(self, action: str) -> None:
        """検索欄の吹き出しで選んだ操作を行い、検索欄を空にする。"""
        self._hide_search_menu()
        text = self.search.text().strip()
        pid = self.model.by_name.get(text.upper()) if text else None
        if pid is None:
            return
        self.search.clear()
        if action == "grow":
            self.expand_gene(pid)
        else:
            self.change_focus(pid)

    def expand_gene(self, pid: int) -> None:
        """地図にあれば通常の拡張。なければ、今の地図とつながる経路上の遺伝子だけを加える。"""
        if pid in self.sub.nodes:
            self.grow_node(pid)
        else:
            self.link_to_map(pid)
        self.nodeClicked.emit(self, pid)

    LINK_DEPTH = 3       # 地図にない遺伝子から、今の地図まで何段までの経路を探すか
    LINK_CAP = 15        # 経路の途中の遺伝子として加える最大数

    # 経路の表示経路（左の「経路」タブで選ぶ）: キー → (表示名, 説明, 基準の遺伝子が必須か)
    RELATION_MODES = {
        "off": ("OFF", "経路を表示しない。普段の地図です", False),
        "all": ("すべての経路", "チェックした遺伝子どうしを、指定の段数で結ぶすべての経路。各段の矢印の向きは問わず、"
                "A → X ← B のように共通の標的を経由する経路も含む", False),
        "direct": ("矢印の向きにたどれる経路", "チェックした遺伝子から、別のチェックした遺伝子へ矢印の向きにたどれる経路。"
                   "途中はどの遺伝子でもよく、1 段なら直接の関係", False),
        "regulators": ("すべてを制御している遺伝子", "チェックした遺伝子すべてに、指定の段数で届く上流の遺伝子と、そこからの経路。"
                       "共通の制御因子を階層の上から順に並べる", False),
        "targets": ("すべてから制御を受けている遺伝子", "チェックした遺伝子すべてから、指定の段数で届く下流の遺伝子と、そこへの経路。"
                    "共通の標的を階層の上から順に並べる", False),
        "toward": ("基準の遺伝子へ作用している経路", "ほかのチェックした遺伝子から基準の遺伝子へ、矢印の向きにたどれる経路", True),
        "from": ("基準の遺伝子から作用している経路", "基準の遺伝子から、ほかのチェックした遺伝子へ矢印の向きにたどれる経路", True),
    }

    def _on_picked_changed(self, pids: list[int]) -> None:
        self.picked = [p for p in pids if self.model and p in self.model.proteins]
        self.pickedChanged.emit(self, self.picked)

    @staticmethod
    def row_edges(graph: relation_paths.UnitGraph, row: relation_paths.PathRow) -> set[tuple[int, int]]:
        """経路 1 本が通る関係（遺伝子の組。複合体は構成要素の関係）。"""
        edges = set()
        for a, b, forward in zip(row.nodes, row.nodes[1:], row.forward):
            edges |= set(graph.gene_edges(a, b) if forward else graph.gene_edges(b, a))
        return edges

    def show_relation(self, pids: list[int], mode: str, steps: int, anchor: int | None = None) -> str:
        """左の「経路」タブの設定で経路の一覧を求め、その経路を地図で目立たせる。結果の説明を返す。
        経路はちょうど steps 段のものだけ（tor_app/relation_paths.py）。mode が "off" なら普段の地図に戻す。"""
        if mode == "off":
            self.clear_relation()
            return ""
        pids = [p for p in pids if p in self.model.proteins]
        names = self.model.names
        if mode in ("toward", "from") and anchor not in pids:
            self.clear_relation()
            return "基準の遺伝子を選んでください"
        if len(pids) < (1 if mode in ("regulators", "targets") else 2):
            self.clear_relation()
            return ""   # チェックが足りないときは何も出さない
        known = self.tab.known_only()
        graph = relation_paths.UnitGraph(self._adjacency(with_tx=True), self.model.complexes(), names)
        keys = relation_paths.order_keys(graph, self.model.levels(known), self.model.layout_order(known),
                                         self.model.tx_adjacency)
        result = relation_paths.find(graph, pids, mode, steps, anchor if anchor in pids else None, keys)

        # 全体の経路の線は描かない。地図にはチェックした遺伝子だけを目立たせ、
        # 線と途中の遺伝子（共通の制御因子・標的も）は、一覧で経路を選んだときに描く（highlight_rows）
        # 共通の制御因子・標的も、一覧で経路を選ぶまで地図に加えない（表示経路を切り替えても配置が動かないように）
        found = [g for u in result.common for g in graph.genes(u)]
        shown = set(pids)
        base = self._build_sub(with_relation=False).nodes
        extra = shown - base
        if extra != self.relation_extra or self.relation_focus_extra or self.relation_allow or self.focus_allow:
            self.relation_extra = extra
            self.relation_focus_extra = set()
            self.relation_allow, self.focus_allow = set(), set()
            self.refresh_view(keep_positions=True, keep_view=True)
        self.relation = {"pids": pids, "mode": mode, "steps": steps, "anchor": anchor if anchor in pids else None,
                         "found": found, "nodes": shown, "edges": set(), "result": result, "graph": graph,
                         "anchor_node": anchor if anchor in pids else None, "sel_nodes": set(), "sel_edges": set()}
        self._push_relation()

        # 経路の本数は「経路」タブの「表示経路」の見出しに出す（relation_count）。ここでは共通の遺伝子などだけ
        lines = []
        if result.truncated:
            lines.append(f"経路が {relation_paths.TOTAL_CAP} 本を超えたので、そこで一覧を打ち切りました。"
                         "段数を減らすか、選ぶ遺伝子を絞ってください")
        if result.capped:
            lines.append(f"{relation_paths.ENUM_CAP} 本に達して数えるのをやめた組が {result.capped} 組あります")
        if mode in ("regulators", "targets"):
            kind = "共通の制御因子" if mode == "regulators" else "共通の標的"
            lines.append(f"{kind} {len(result.common)} 個: "
                         + ("、".join(graph.label(u) for u in result.common) if result.common else "なし"))
        if result.missing:
            lines.append(f"{'・'.join(graph.label(u) for u in result.missing)} は {steps} 段の経路でつながっていません")
        return "\n".join(lines)

    def relation_count(self) -> int | None:
        """今の経路タブの経路の本数（経路を表示していなければ None）。"""
        r = self.relation
        return len(r["result"].rows()) if r and "result" in r else None

    def _push_relation(self) -> None:
        r = self.relation
        if not r:
            return
        ends = set(r["pids"]) | set(r["found"])
        anchor = r.get("anchor_node")
        anchor = f"p{anchor}" if anchor is not None else None
        self.js("app.showPath({}, {}, {}, {}, {})".format(
            json.dumps([f"p{n}" for n in r["nodes"]]), json.dumps([f"p{a}>p{b}" for a, b in r["edges"]]),
            json.dumps([f"p{n}" for n in ends]), json.dumps(anchor),
            json.dumps({f"p{n}": mark for n, mark in r.get("marks", {}).items()})))

    def highlight_rows(self, rows: list[relation_paths.PathRow]) -> None:
        """「経路」の一覧で選んだ経路の線を描いて強調する。地図にない遺伝子は一時的に加える
        （選択を外す・背景をクリックすると消える）。"""
        r = self.relation
        if not r or "graph" not in r:
            return
        edges = set().union(*(self.row_edges(r["graph"], row) for row in rows)) if rows else set()
        nodes = {x for e in edges for x in e}
        r["sel_nodes"], r["sel_edges"] = nodes, edges
        base = self.sub.nodes - self.relation_focus_extra
        extra = nodes - base
        # 選んだ経路の線は、転写因子どうしの線でも描く（線は地図に渡してあるので、地図の側で見せる）。
        # 地図にない遺伝子を加えるときだけ描き直す（遺伝子が増減しなければ、配置は計算し直さない）
        self.focus_allow = edges
        if extra != self.relation_focus_extra:
            self.relation_focus_extra = extra
            self.refresh_view(keep_positions=True, keep_view=True)
        if not rows:
            self.js("app.focusPath(null)")
            return
        self.js("app.focusPath({}, {})".format(json.dumps([f"p{n}" for n in nodes]),
                                              json.dumps([f"p{a}>p{b}" for a, b in edges])))

    def _clear_relation_focus(self) -> None:
        """一覧で選んだ経路のために加えた遺伝子を消す（強調は地図の側で外れる）。"""
        if self.relation and "sel_nodes" in self.relation:
            self.relation["sel_nodes"], self.relation["sel_edges"] = set(), set()
        self.focus_allow = set()
        if self.relation_focus_extra:
            self.relation_focus_extra = set()
            self.refresh_view(keep_positions=True, keep_view=True)

    def clear_relation(self) -> None:
        """経路の表示をやめ、加えた中継の遺伝子も消す（普段の地図に戻す）。"""
        had = self.relation is not None or self.relation_extra
        self.relation = None
        self.js("app.clearPath()")
        if self.relation_extra or self.relation_focus_extra or self.relation_allow or self.focus_allow:
            self.relation_extra = set()
            self.relation_focus_extra = set()
            self.relation_allow, self.focus_allow = set(), set()
            self.refresh_view(keep_positions=True, keep_view=True)
        if had:
            self.viewChanged.emit(self)

    def focus_relation(self, pid: int) -> None:
        """経路の表示中に地図の遺伝子をクリックしたとき: 一覧をその遺伝子（を含む単位）を通る経路だけに絞り、強調する。"""
        r = self.relation
        if not r or "result" not in r:
            return
        self.relationGene.emit(self, pid)

    def _focus_relation_complex(self, name: str) -> None:
        """経路の表示中に複合体の枠をクリックしたとき: 構成要素をクリックしたのと同じ（複合体は 1 つの単位）。"""
        members = self.model.all_complexes().get(name) if self.model else None
        if members:
            self.focus_relation(members[0])

    def link_to_map(self, pid: int) -> None:
        """地図にない遺伝子 pid を加え、今の地図との最短のつながり（向きは問わず LINK_DEPTH 段まで）の途中にある
        遺伝子だけを加える。経路が多いときは、多くの経路が通る遺伝子を優先して LINK_CAP 個まで。紫の印を付け、
        × で外すと加えたものをまとめて隠す。"""
        self.activate()
        hits, middle, chosen = link_path(self._adjacency(), pid, set(self.sub.nodes), self.LINK_DEPTH, self.LINK_CAP,
                                         self.model.names)
        name = self.model.proteins[pid].gene_name
        snap = self._snapshot()
        added = {pid} | chosen
        self.extra |= added
        self.excluded -= added
        self.grown.add(pid)
        self._note_grown(pid)
        info = self.grown_info.setdefault(pid, {"added": set(), "hidden": set(), "unextra": set()})
        info["added"] |= added
        if not self._confirm_large(snap):
            return
        self._record_history()
        self.refresh_view(keep_positions=True, keep_view=True, center_on=pid)
        if not hits:
            self.statusMessage.emit(f"{name} は今の地図と {self.LINK_DEPTH} 段以内でつながっていないので、{name} だけを加えました")
        else:
            more = f"。経路の途中の遺伝子はほかに {len(middle) - len(chosen)} 個あります" if len(middle) > len(chosen) else ""
            self.statusMessage.emit(f"{name} を加えました。今の地図の {len(hits)} 個と直接つながっています" if not chosen else
                                    f"{name} と、今の地図につながる経路上の {len(chosen)} 個を加えました{more}")

    def set_focus(self, pid: int, center: bool = False) -> None:
        """pid に注目する（注目する遺伝子は常に 1 つ。前の注目と、拡張などで加えたものは外す）。"""
        snap = self._snapshot()
        self.focus_names = [self.model.proteins[pid].gene_name]
        self.extra = set()
        self.uncapped = set()
        self.excluded = set()
        self.grown = set()
        self.grown_info = {}
        self.trf = set()
        if not self._confirm_large(snap):
            return
        self._record_history()
        self.refresh_view(center_on=pid if center else None)

    def _focus_clicked(self, pid: int) -> None:
        """選択中の遺伝子をもう一度クリックしたとき: その遺伝子を中心に表示し直す。"""
        self.activate()
        self.change_focus(pid, center=True)

    def change_focus(self, pid: int, center: bool = False) -> None:
        """pid に注目する。pid が拡張（紫）か緑の選択なら、これまでの注目をその役割に入れ替え、ほかの拡張・選択は残す。
        どちらでもなければ、これまでどおり拡張などを外して注目し直す。"""
        name = self.model.proteins[pid].gene_name
        old = self.focus_ids()
        previous = old[0] if old else None
        role = None if previous is None or previous == pid else \
            "grown" if pid in self.grown else "picked" if pid in self.picked else None
        if role is None:
            self.set_focus(pid, center=center)
            self.nodeClicked.emit(self, pid)
            self.statusMessage.emit(f"{name} に注目しました")
            return
        snap = self._snapshot()
        picked = [p for p in self.picked if p != pid]
        self.focus_names = [name]
        if role == "grown":
            # これまでの注目を拡張（紫）の起点にする。紫を付けた順では、pid のあった位置に入れる
            self.grown = (self.grown - {pid}) | {previous}
            self.grown_order = [previous if g == pid else g for g in self.grown_order if g != previous]
            if previous not in self.grown_order:
                self.grown_order.append(previous)
        else:
            picked.append(previous)   # これまでの注目を緑の選択にする
        self._regrow()
        if not self._confirm_large(snap):
            return
        self._pending_picked = picked
        self._pending_detail = False   # 詳細には、緑の選択ではなく新しい注目を出す
        self._record_history()
        self.refresh_view(center_on=pid if center else None)
        self.nodeClicked.emit(self, pid)
        old_name = self.model.names[previous]
        self.statusMessage.emit(f"{name} に注目しました。{old_name} は"
                                + ("拡張" if role == "grown" else "緑の選択") + "に入れ替えました")

    def grow_node(self, pid: int) -> None:
        """2 回目のクリック: 選んだ遺伝子の直接の上流・下流のうち、表示されていないものを追加する。

        今ある遺伝子は動かさず、表示範囲もそのまま。下（上）に新しい階層の段が増えたとき、
        一番上（下）の段に注目中の遺伝子も、選んだ遺伝子に線でつながるものもなければ、その段を隠す。
        """
        self.activate()
        known_only = self.tab.known_only()
        # 同じ複合体の構成要素も、相互作用する遺伝子と同じように加える
        linked = grow_targets(self._adjacency(), self.model.all_complexes(), pid)
        new = linked - self.sub.nodes
        name = self.model.proteins[pid].gene_name
        if not new:
            # 追加するものはなくても、追加の起点として紫の印は残す
            if pid not in self.grown:
                self.grown.add(pid)
                self._note_grown(pid)
                self._record_history()
                self.refresh_view(keep_positions=True, keep_view=True, center_on=pid)
            self.statusMessage.emit(f"{name} の上流・下流はすべて表示されています")
            return
        levels = self.model.levels(known_only)
        before = {levels[n] for n in self.sub.nodes if levels.get(n) is not None}
        after = before | {levels[n] for n in new if levels.get(n) is not None}
        snap = self._snapshot()
        info = self.grown_info.setdefault(pid, {"added": set(), "hidden": set(), "unextra": set()})
        info["added"] |= new - self.extra
        self.extra |= new
        self.excluded -= new
        self.grown.add(pid)
        self._note_grown(pid)
        related = set(self.focus_ids()) | self.sub.seeds | {pid} | linked
        hidden_rows = []
        for grew, row in ((after and before and max(after) > max(before), min(before) if before else None),
                          (after and before and min(after) < min(before), max(before) if before else None)):
            if not grew or row is None:
                continue
            row_nodes = {n for n in self.sub.nodes if levels.get(n) == row}
            if row_nodes and not (row_nodes & related):
                info["hidden"] |= row_nodes - self.excluded
                info["unextra"] |= row_nodes & self.extra
                self.excluded |= row_nodes
                self.extra -= row_nodes
                hidden_rows.append(row + 1)
        if not self._confirm_large(snap):
            return
        self._record_history()
        self.refresh_view(keep_positions=True, keep_view=True, center_on=pid)
        message = f"{name} の上流・下流を {len(new)} 個追加しました"
        if hidden_rows:
            message += "。使われていない第 " + "・".join(map(str, hidden_rows)) + " 階層の段を隠しました"
        self.statusMessage.emit(message)

    def _note_grown(self, pid: int) -> None:
        self.grown_order = [n for n in self.grown_order if n != pid] + [pid]

    def open_marked_in_new_pane(self) -> None:
        """右上の「新しい枠」: 緑で選択中の遺伝子、なければ最後に紫にしたもの、なければ注目中のものを新しい枠で表示する。"""
        def done(result):
            # 注目は 1 つだけ。緑が複数（Shift で選択）なら、最後に選んだものに注目する
            picked = [int(e[1:]) for e in json.loads(result or "[]") if int(e[1:]) in self.model.proteins]
            grown = [n for n in self.grown_order if n in self.grown] or sorted(self.grown)
            candidates = picked or grown or self.focus_ids()
            if not candidates:
                self.statusMessage.emit("新しい枠で表示する遺伝子がありません")
                return
            self.tab.add_pane(candidates[-1])
        self.js("app.pickedNodes()", done)

    def trf_targets(self) -> list[tuple[int, str]]:
        """左の TFs 一覧に並べる遺伝子: 注目（オレンジ）と拡張（紫）の遺伝子。(遺伝子, "focus" / "grown")"""
        focus = self.focus_ids()
        grown = [g for g in self.grown_order if g in self.grown] + sorted(self.grown - set(self.grown_order))
        return [(f, "focus") for f in focus] + [(g, "grown") for g in grown if g not in focus]

    def _prune_trf(self) -> None:
        """一覧から外れた遺伝子の転写因子は、地図からも消す。"""
        allowed = set()
        for g, _ in self.trf_targets():
            allowed |= self.model.tx_adjacency.inc.get(g, set())
        self.trf &= allowed

    def set_trf(self, pids: list[int], on: bool) -> None:
        """左の TFs 一覧のチェック: 転写因子をマップの一番上の段に出す・消す。"""
        self.activate()
        snap = self._snapshot()
        self.trf = self.trf | set(pids) if on else self.trf - set(pids)
        if self.trf == snap["trf"] or not self._confirm_large(snap):
            self.viewChanged.emit(self)   # キャンセルしたときは一覧のチェックを元に戻す
            return
        self._record_history()
        self.refresh_view(keep_positions=True)

    def remove_grown(self, pid: int) -> None:
        """凡例の × ボタン: 紫の印を外し、その 2 回目のクリックで追加した遺伝子を隠し直す（隠した段は戻す）。
        追加した遺伝子から更に広げていた紫の起点も、一緒に外す。表示範囲は図全体に合わせ直す。"""
        self.activate()
        if pid not in self.grown:
            return
        removed = 0
        todo = [pid]
        while todo:
            g = todo.pop()
            if g not in self.grown:
                continue
            self.grown.discard(g)
            info = self.grown_info.pop(g, None)
            if not info:
                continue
            # 他の紫の起点でも追加したものは残す
            others = set().union(*(i["added"] for i in self.grown_info.values())) if self.grown_info else set()
            drop = info["added"] - others - set(self.focus_ids())
            self.extra -= drop
            removed += len(drop)
            self.excluded -= info["hidden"]
            self.extra |= info["unextra"]
            todo.extend(n for n in drop if n in self.grown)
        self._prune_trf()
        self._record_history()
        self.refresh_view(keep_positions=True)
        name = self.model.proteins[pid].gene_name
        self.statusMessage.emit(f"{name} の紫の印を外し、追加していた {removed} 個を隠しました" if removed
                                else f"{name} の紫の印を外しました")

    def expand_node(self, pid: int) -> None:
        """この遺伝子の上流・下流をすべて表示に加える。"""
        self.activate()
        snap = self._snapshot()
        self.extra.add(pid)
        self.uncapped.add(pid)
        if not self._confirm_large(snap):
            return
        self._record_history()
        self.refresh_view(keep_positions=True)
        self.statusMessage.emit(f"{self.model.proteins[pid].gene_name} の周辺を展開しました")

    def _apply_steps(self) -> None:
        self.activate()
        if (self.up_steps, self.down_steps) == (self.up_spin.value(), self.down_spin.value()):
            return
        snap = self._snapshot()
        self.up_steps, self.down_steps = self.up_spin.value(), self.down_spin.value()
        if not self._confirm_large(snap):
            return
        self._record_history()
        self.refresh_view()

    # ================= 戻る・進む =================
    def _view_state(self) -> dict:
        names = self.model.names
        return {"focus": list(self.focus_names), "up": self.up_steps, "down": self.down_steps,
                "extra": sorted(names[n] for n in self.extra if n in names),
                "uncapped": sorted(names[n] for n in self.uncapped if n in names),
                "excluded": sorted(names[n] for n in self.excluded if n in names),
                "grown": sorted(names[n] for n in self.grown if n in names),
                "grown_info": {names[g]: {k: sorted(names[n] for n in v if n in names) for k, v in i.items()}
                               for g, i in self.grown_info.items() if g in names},
                "trf": sorted(names[n] for n in self.trf if n in names)}

    @staticmethod
    def describe(state: dict) -> str:
        focus = ", ".join(state["focus"]) or "表示なし"
        extra = f"＋展開 {len(state['extra'])}" if state["extra"] else ""
        return f"{focus} 上流 {state['up']}・下流 {state['down']} 段{extra}"

    def _history_state(self) -> dict:
        """戻る・進むで覚える内容: この表示枠の表示（遺伝子・段数・TFs など）と、全表示枠に共通の凡例・条件タブのチェック。"""
        return {**self._view_state(), "filters": self.tab.filter_state()}

    def _record_history(self) -> None:
        state = self._history_state()
        if 0 <= self.history_pos < len(self.history) and self.history[self.history_pos] == state:
            return
        del self.history[self.history_pos + 1:]
        self.history.append(state)
        if len(self.history) > MAX_HISTORY:
            del self.history[0]
        self.history_pos = len(self.history) - 1
        self._update_nav_buttons()

    def _update_nav_buttons(self) -> None:
        can_back = self.history_pos > 0
        can_forward = self.history_pos < len(self.history) - 1
        self.back_button.setEnabled(can_back)
        self.forward_button.setEnabled(can_forward)
        back_key = QKeySequence(QKeySequence.StandardKey.Back).toString(QKeySequence.SequenceFormat.NativeText)
        forward_key = QKeySequence(QKeySequence.StandardKey.Forward).toString(QKeySequence.SequenceFormat.NativeText)
        self.back_button.setToolTip(f"前の表示に戻る {back_key}"
                                    + (f"\n{self.describe(self.history[self.history_pos - 1])}" if can_back else ""))
        self.forward_button.setToolTip(f"次の表示に進む {forward_key}"
                                       + (f"\n{self.describe(self.history[self.history_pos + 1])}"
                                          if can_forward else ""))

    def go_back(self) -> None:
        if self.history_pos > 0:
            self.history_pos -= 1
            self._restore(self.history[self.history_pos])

    def go_forward(self) -> None:
        if self.history_pos < len(self.history) - 1:
            self.history_pos += 1
            self._restore(self.history[self.history_pos])

    def _restore(self, state: dict) -> None:
        self._apply_view_state(state)
        if "filters" in state:
            self.tab.apply_filter_state(state["filters"])
        self._update_nav_buttons()
        self.refresh_view()
        focus = self.focus_ids()
        if focus:
            self.nodeClicked.emit(self, focus[0])
        else:
            self.backgroundClicked.emit(self)
        self.statusMessage.emit(f"表示: {self.describe(state)}")

    def load_state(self, state: dict) -> None:
        """登録した表示（state() の形。遺伝子は名前）を当てはめて描き直す（左の「登録」タブから呼び出したとき）。"""
        self._apply_view_state(state)
        by_name = self.model.by_name
        self.grown_order = [by_name[n.upper()] for n in state.get("grown_order", []) if n.upper() in by_name]
        self._pending_picked = [by_name[n.upper()] for n in state.get("picked", []) if n.upper() in by_name]
        self._prune_trf()
        self._record_history()
        self.refresh_view()
        if not self._pending_picked:
            self.backgroundClicked.emit(self)

    def _apply_view_state(self, state: dict) -> None:
        """_view_state の内容（遺伝子名）を、今のモデルの遺伝子に当てはめる。"""
        by_name = self.model.by_name
        self.focus_names = [n for n in state["focus"] if n.upper() in by_name][:1]
        self.extra = {by_name[n.upper()] for n in state["extra"] if n.upper() in by_name}
        self.uncapped = {by_name[n.upper()] for n in state["uncapped"] if n.upper() in by_name}
        self.excluded = {by_name[n.upper()] for n in state.get("excluded", []) if n.upper() in by_name}
        self.grown = {by_name[n.upper()] for n in state.get("grown", []) if n.upper() in by_name}
        self.grown_info = {by_name[g.upper()]: {k: {by_name[n.upper()] for n in v if n.upper() in by_name}
                                                for k, v in i.items()}
                           for g, i in state.get("grown_info", {}).items() if g.upper() in by_name}
        self.trf = {by_name[n.upper()] for n in state.get("trf", []) if n.upper() in by_name}
        self._set_steps(state["up"], state["down"])

    # ================= 描画 =================
    def _on_page_ready(self):
        self.page_ready = True
        self.push_label_options()
        if self.model:
            self.refresh_view()

    def _on_lod_changed(self, shown: int, total: int) -> None:
        self.lod_label.setText(f"縮小中: 下流 {shown}/{total} 段目まで" if shown < total else "")

    def refresh_view(self, keep_positions: bool = False, keep_view: bool = False, center_on: int | None = None) -> None:
        """注目範囲を計算し直して描画する。"""
        if not self.model:
            return
        self.sub = self._build_sub()
        self.update_count_label()
        # まとめの遺伝子（線を隠して枠に並べるもの）は軽いので数えない
        laid = len(self.sub.nodes) - sum(len(g) for g in self.sub.bundles.values())
        if laid > WARN_NODES:
            self.statusMessage.emit(f"表示が {laid} 個と多いため重くなることがあります。段数を減らしてください")
        self.viewChanged.emit(self)
        if self.page_ready:
            self._push_elements(keep_positions, keep_view, center_on)

    def _build_sub(self, with_relation: bool = True) -> Subgraph:
        extra = (self.extra | self.relation_extra | self.relation_focus_extra if with_relation else self.extra)
        spec = ViewSpec(self.focus_ids(), self.up_steps, self.down_steps, set(extra), set(self.uncapped),
                        set(self.excluded), set(self.grown), trf=set(self.trf))
        if not spec.focus and not spec.extra:
            return Subgraph(set(), set(), {}, {}, {})
        # 「作用が分かっている関係のみ」や凡例のチェックで隠した関係はたどらずに周辺を決める
        sub = build_subgraph(self._adjacency(), self.model.all_complexes(), self.model.names, spec, self.model.paralogs)
        sub.allow = self.relation_allow | self.focus_allow if with_relation else set()
        return sub

    # ================= 線が多いときの確認 =================
    def _snapshot(self) -> dict:
        return {"focus_names": list(self.focus_names), "extra": set(self.extra), "uncapped": set(self.uncapped),
                "excluded": set(self.excluded), "grown": set(self.grown), "grown_order": list(self.grown_order),
                "grown_info": {g: {k: set(v) for k, v in i.items()} for g, i in self.grown_info.items()},
                "trf": set(self.trf), "relation_extra": set(self.relation_extra),
                "relation_focus_extra": set(self.relation_focus_extra),
                "relation_allow": set(self.relation_allow), "focus_allow": set(self.focus_allow),
                "steps": (self.up_steps, self.down_steps)}

    def _restore_snapshot(self, snap: dict) -> None:
        self.focus_names, self.extra, self.uncapped = snap["focus_names"], snap["extra"], snap["uncapped"]
        self.excluded, self.grown, self.grown_order = snap["excluded"], snap["grown"], snap["grown_order"]
        self.grown_info = snap["grown_info"]
        self.trf = snap["trf"]
        self.relation_extra = snap["relation_extra"]
        self.relation_focus_extra = snap["relation_focus_extra"]
        self.relation_allow, self.focus_allow = snap["relation_allow"], snap["focus_allow"]
        self._set_steps(*snap["steps"])

    def _confirm_large(self, snap: dict) -> bool:
        """変更後の表示の線が多いときは、描画（線の経路の計算）を始める前に確認する。
        キャンセルしたら snap の状態に戻して False を返す。"""
        sub = self._build_sub()
        edges = self.model.count_edges(sub, self.tab.known_only(), self.tab.hidden_filters())
        if edges <= WARN_EDGES or edges <= self.model.count_edges(self.sub, self.tab.known_only(),
                                                                     self.tab.hidden_filters()):
            return True
        box = QMessageBox(QMessageBox.Icon.Warning, "線が多くなります",
                          f"表示すると線が {edges} 本、遺伝子が {len(sub.nodes)} 個になり、"
                          "線の計算と描画に時間がかかることがあります。\n表示しますか？", parent=self)
        box.setInformativeText("段数を減らすか、凡例のチェックで転写制御などの種類を隠すと、線を減らせます。")
        show = box.addButton("表示する", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("キャンセル", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(show)   # Enter で表示する（ブラウザ版の確認と同じ）
        box.exec()
        if box.clickedButton() is show:
            return True
        self._restore_snapshot(snap)
        self.js("app.resetPick()")
        self.statusMessage.emit("表示をキャンセルしました")
        return False

    def js(self, code: str, callback=None) -> None:
        if not self.page_ready:
            return
        if callback:
            self.view.page().runJavaScript(code, callback)
        else:
            self.view.page().runJavaScript(code)

    def _push_elements(self, keep_positions: bool = False, keep_view: bool = False, center_on: int | None = None):
        def push(current):
            if not self.page_ready or self.model is None:
                return
            positions = json.loads(current) if keep_positions and current else None
            args = ", ".join(json.dumps(x, ensure_ascii=False) for x in (
                self.model.elements(self.sub, self.tab.known_only()), self.model.legend(self.sub, self.tab.known_only()),
                positions,
                self.tab.layout_name(), max(self.sub.down_dist.values(), default=0),
                {"keepView": keep_view, "centerOn": f"p{center_on}" if center_on is not None else None}))
            self.js(f"app.setElements({args})")
            self.js(self.tab.data_js())
            self.js(f"app.setColorMode({json.dumps(self.tab.color_mode())})")
            self.js(f"app.setEdgeColorMode({json.dumps(self.tab.edge_color_mode())})")
            self.js(self.tab.condition_js())
            if self._pending_picked:
                # 起動時: 前回の緑の選択に戻し、最後に選んだものの詳細を出す
                pids = [p for p in self._pending_picked if p in self.sub.nodes]
                self._pending_picked = []
                if pids:
                    self.js(f"app.setPicked({json.dumps([f'p{p}' for p in pids])})")
                    self._on_picked_changed(pids)
                    if self._pending_detail:
                        self.nodeClicked.emit(self, pids[-1])
                self._pending_detail = True

        if keep_positions:
            self.js("app.getPositions()", push)
        else:
            push(None)

    def push_label_options(self) -> None:
        symbols, more = self.tab.label_options()
        self.js(f"app.setLod({json.dumps(self.tab.display_options['lod'])})")
        self.js(f"app.setEdgeLabels({json.dumps(symbols)}, {json.dumps(more)})")
        self.js(f"app.setOrthogonal({json.dumps(self.tab.orthogonal())})")
        self.push_filters()

    def push_filters(self) -> None:
        self.js(f"app.setFilters({json.dumps(self.tab.hidden_filters())})")
        self.update_count_label()

    def update_count_label(self) -> None:
        """上の行の「遺伝子数・線数」。線は凡例のチェックで隠したものを除いて数える。"""
        if not self.model:
            return
        edges = self.model.count_edges(self.sub, self.tab.known_only(), self.tab.hidden_filters())
        self.focus_label.setText(f" 遺伝子 {len(self.sub.nodes)} 個　線 {edges} 本")

    # ================= 保存・書き出し =================
    def _default_file_name(self, ext: str) -> str:
        return ("_".join(self.focus_names) or "network") + f".{ext}"

    def export_png(self):
        path, _ = QFileDialog.getSaveFileName(self, "PNG として保存", self._default_file_name("png"), "PNG 画像 (*.png)")
        if not path:
            return

        def done(data_uri):
            try:
                with open(path, "wb") as f:
                    f.write(base64.b64decode(data_uri.split(",", 1)[1]))
            except (OSError, AttributeError, IndexError) as e:
                QMessageBox.critical(self, "エラー", f"保存できませんでした。\n{e}")
                return
            self.statusMessage.emit(f"{path} に保存しました")

        self.js("app.exportPng()", done)

    def export_svg(self):
        path, _ = QFileDialog.getSaveFileName(self, "SVG として保存", self._default_file_name("svg"), "SVG 画像 (*.svg)")
        if not path:
            return

        def done(svg):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(svg)
            except (OSError, TypeError) as e:
                QMessageBox.critical(self, "エラー", f"保存できませんでした。\n{e}")
                return
            self.statusMessage.emit(f"{path} に保存しました")

        self.js("app.exportSvg()", done)
