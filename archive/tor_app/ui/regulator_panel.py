"""左の「制御遺伝子探索」タブ: 観測した遺伝子ごとに発現・活性の変化（増・減・変化なし）を選び、それをもっともよく
説明する「1 つの遺伝子の活性化 / 不活性化」とその経路を探す（計算は tor_app/regulator_search.py）。"""
import csv
import threading

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QCompleter, QFileDialog, QGroupBox, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from .. import common_response
from .. import sim_verify
from ..regulator_search import Observation, SignedGraph, search

KINDS = [("expr", "発現"), ("act", "活性")]
CHANGES = [("up", "増"), ("down", "減"), ("none", "変化なし")]
KIND_ALIASES = {"発現": "expr", "expr": "expr", "expression": "expr", "活性": "act", "act": "act", "activity": "act"}
CHANGE_ALIASES = {"増": "up", "up": "up", "+": "up", "増加": "up", "減": "down", "down": "down", "-": "down", "減少": "down",
                  "変化なし": "none", "none": "none", "0": "none", "なし": "none"}
OUTCOME_LABELS = {"match": "標的・増減の向きも合う", "both": "どちらとも言えない", "unknown": "標的（増減の向きは不明）",
                  "contra": "標的だが増減の向きが逆", "unreached": "標的ではない", "flat_ok": "変化なしと矛盾しない",
                  "flat_bad": "変化を予測（変化なしと矛盾）", "self": "候補そのもの"}
ARROW = {1: "→", -1: "⊣", 0: "◇"}
SIM_TOP = 20   # シミュレーションで確かめる上位の候補の数
SHOW = 50   # 結果の一覧に並べる数


def _label(c, names) -> str:
    return f"{c.name or names[c.gene]} の" + {1: "活性化", -1: "不活性化"}.get(c.direction, "変化（向きは不明）")


class _Done(QObject):
    finished = pyqtSignal(object, object)   # (結果のリスト or None, 失敗の理由)
    progress = pyqtSignal(int, int)


class RegulatorSearchPanel(QWidget):
    def __init__(self, tab):
        super().__init__()
        self.tab = tab
        # 観測: 遺伝子 → {"use": bool, "kind": "expr"/"act", "change": "up"/"down"/"none"}。
        # added は地図にない遺伝子でも一覧に残すもの（検索欄・CSV で加えたもの、チェックしたもの）
        self.obs: dict[int, dict] = {}
        self.added: list[int] = []
        self.results = []
        self._observations: list[Observation] = []
        self._running = False
        self._common, self._common_model = set(), None   # 一般的なストレス応答の遺伝子（DB を読み直したら作り直す）
        self._done = _Done()
        self._done.finished.connect(self._on_finished)
        self._done.progress.connect(lambda i, n: self.status.setText(f"計算中… 観測した遺伝子 {i}/{n}"))

        layout = QVBoxLayout(self)
        intro = QLabel("観測した遺伝子ごとに、発現か活性か・増／減／変化なしを選んで「探索」を押すと、"
                       "変化した遺伝子に標的が多く含まれる制御因子（転写因子など）と、その上流のシグナル伝達の遺伝子を、点数の高い順に並べます。"
                       "候補を選ぶと、地図に経路を表示します。")
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#555;")

        obs_box = QGroupBox("観測した遺伝子")
        obs_layout = QVBoxLayout(obs_box)
        add_row = QHBoxLayout()
        self.add_edit = QLineEdit()
        self.add_edit.setPlaceholderText("遺伝子名を入力して加える（地図にない遺伝子も可）")
        self.add_edit.returnPressed.connect(self._add_from_edit)
        add_button = QPushButton("加える")
        add_button.clicked.connect(self._add_from_edit)
        add_row.addWidget(self.add_edit, 1)
        add_row.addWidget(add_button)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("一覧を遺伝子名で絞り込み")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["使う", "遺伝子", "観測", "変化"])
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setMinimumHeight(220)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in (0, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.count_label = QLabel()
        self.count_label.setStyleSheet("color:#37474f;")
        file_row = QHBoxLayout()
        load_csv = QPushButton("CSV 読み込み")
        load_csv.setToolTip("列: 遺伝子, 観測（発現 / 活性）, 変化（増 / 減 / 変化なし）")
        load_csv.clicked.connect(self._load_csv)
        save_csv = QPushButton("CSV 書き出し")
        save_csv.setToolTip("「使う」にチェックした観測を CSV に保存します")
        save_csv.clicked.connect(self._save_csv)
        clear = QPushButton("チェックを外す")
        clear.clicked.connect(self._clear_checks)
        for w in (load_csv, save_csv, clear):
            file_row.addWidget(w)
        obs_layout.addLayout(add_row)
        obs_layout.addWidget(self.filter_edit)
        obs_layout.addWidget(self.table)
        obs_layout.addWidget(self.count_label)
        obs_layout.addLayout(file_row)

        cond_box = QGroupBox("探索の条件")
        cond_layout = QVBoxLayout(cond_box)
        depth_row = QHBoxLayout()
        depth_row.addWidget(QLabel("さかのぼる段数:"))
        self.depth = QSpinBox()
        self.depth.setRange(1, 4)
        self.depth.setValue(3)
        self.depth.setToolTip("観測した遺伝子から上流へ何段までさかのぼって候補を探すか。1 なら直接の制御因子（転写因子など）だけ、"
                              "2 以上なら、そこからシグナル伝達の関係を（段数 − 1）段さかのぼった遺伝子も候補にします")
        depth_row.addWidget(self.depth)
        depth_row.addWidget(QLabel("段"))
        depth_row.addStretch(1)
        self.use_unknown = QCheckBox("作用不明の関係も使う（標的の重なりには数え、増減の向きの判定には使わない）")
        self.use_unknown.setChecked(True)
        self.sim_rerank = QCheckBox(f"上位 {SIM_TOP} 件を、シミュレーションで観測と合う度合いで並べ直す（試験中）")
        self.sim_rerank.setToolTip(
            f"上位 {SIM_TOP} 件は、並べ直さなくても、候補を壊した・活性化したときの予測を観測と照らした結果を「シミュ 合/逆」に出します。\n"
            "チェックすると、その一致度で並べ直します（点数 ×（1 + 10 × 一致度））。原因の分かっている条件のデータでは少し良くなり、\n"
            "破壊株データでは少し悪くなりました（10 位以内 21.9% → 19.4%）。")
        self.causal = QCheckBox("シグナル伝達の遺伝子を、経路の向きの一貫性で採点する（試験中）")
        self.causal.setToolTip(
            "観測から動いたと推定される転写因子（向きつき）それぞれについて、候補の変化から経路の符号で予測した向きと合えば加点、\n"
            "合わなければ減点します。多くの転写因子に届くだけの遺伝子（ハブ）が上に来にくくなり、説明に一致・不一致の内訳を出します。\n"
            "経路の途中に向き不明の関係があって判定できない候補は、これまでの点数を使います。\n"
            "破壊株データでの評価は今の方式とほぼ同じ（10 位以内 21.9% → 20.9%）。熱ショックの例では PKA が 14 → 5 位に上がる。")
        self.drop_common = QCheckBox("一般的なストレス応答の遺伝子を、転写因子の点数から外す（破壊株など、ストレスが本題でない実験向け）")
        self.drop_common.setToolTip(
            "多くの遺伝子破壊で一緒に変わる約 400 遺伝子（HSP26・CTT1 など。data/common_response_genes.csv）を、"
            "転写因子自身の点数では数えません。シグナル伝達の遺伝子（PKA・TORC1 など）の点数には使います。\n"
            "破壊株データでの評価では転写因子の順位が上がりましたが、熱ショック・ラパマイシンのように"
            "ストレス応答そのものを調べる実験では、MSN2/4・HSF1 などの手がかりが減るので外さないでください。")
        self.run_button = QPushButton("探索")
        self.run_button.setStyleSheet("font-weight:bold; padding:5px;")
        self.run_button.clicked.connect(self.run)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#555;")
        cond_layout.addLayout(depth_row)
        cond_layout.addWidget(self.use_unknown)
        cond_layout.addWidget(self.drop_common)
        cond_layout.addWidget(self.causal)
        cond_layout.addWidget(self.sim_rerank)
        cond_layout.addWidget(self.run_button)
        cond_layout.addWidget(self.status)

        result_box = QGroupBox("結果（点数の高い順）")
        result_layout = QVBoxLayout(result_box)
        self.result_table = QTableWidget(0, 7)
        self.result_table.setHorizontalHeaderLabels(["候補", "点数", "重なり", "向き 合/逆", "補正後の確率", "経由",
                                                     "シミュ 合/逆"])
        tips = ["候補の遺伝子と、関係の作用から決めた変化の向き（決められなければ「向きは不明」）",
                "変化した遺伝子の中に標的がどれだけ多く含まれるか（−log10 p）。シグナル伝達の遺伝子は、"
                "たどれる転写因子のうち最も高い点数 × 0.7^段数",
                "変化した遺伝子のうち、標的に含まれる数 / 標的の数",
                "重なった遺伝子のうち、関係の作用から予測した増減の向きが観測と合う数 / 逆の数",
                "標的の重なりが偶然でも起きる確率を、候補の数で補正した値（q 値。0.05 以上は灰色）",
                "シグナル伝達の遺伝子のとき、点数を受け継いだ転写因子と段数",
                f"上位 {SIM_TOP} 件: 候補を壊した・活性化したときのシミュレーション（発現と活性を分けた計算）の予測が、"
                "観測と合う数 / 逆の数（候補が観測とよく合う向きに変わったとして）"]
        for col, tip in enumerate(tips):
            self.result_table.horizontalHeaderItem(col).setToolTip(tip)
        self.result_table.verticalHeader().hide()
        self.result_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.result_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.result_table.setMinimumHeight(200)
        rh = self.result_table.horizontalHeader()
        rh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 7):
            rh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.result_table.itemSelectionChanged.connect(self._show_selected)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.window_button = QPushButton("この候補の経路を別ウィンドウで開く")
        self.window_button.setEnabled(False)
        self.window_button.clicked.connect(self._open_window)
        note = QLabel("点数は、地図のデータ（関係とその作用）だけから求めた目安です。作用不明の関係や、データにない関係は"
                      "反映されません。結果は実験で確かめる仮説として使ってください。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#777; font-size:11px;")
        for w in (self.result_table, self.detail, self.window_button, note):
            result_layout.addWidget(w)

        for w in (intro, obs_box, cond_box, result_box):
            layout.addWidget(w)
        layout.addStretch(1)

    # ================= 観測の一覧 =================
    def set_completer(self, names: list[str]) -> None:
        completer = QCompleter(names)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.add_edit.setCompleter(completer)

    def update_genes(self) -> None:
        """一覧を作り直す: チェックした・加えた遺伝子を上に、その下に操作中の表示枠の地図上の遺伝子。"""
        model = self.tab.model
        if not model:
            return
        pane = self.tab.active_pane
        self.obs = {p: o for p, o in self.obs.items() if p in model.proteins}
        self.added = [p for p in self.added if p in model.proteins]
        on_map = sorted(pane.sub.nodes, key=lambda n: model.names[n]) if pane else []
        top = self.added + [p for p in sorted(self.obs, key=lambda n: model.names[n])
                            if self.obs[p]["use"] and p not in self.added]
        rows = top + [p for p in on_map if p not in set(top)]
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for r, pid in enumerate(rows):
            o = self.obs.get(pid, {"use": False, "kind": "expr", "change": "up"})
            use = QCheckBox()
            use.setChecked(o["use"])
            use.setStyleSheet("margin-left:8px;")
            use.toggled.connect(lambda on, p=pid: self._set(p, "use", on))
            name = QTableWidgetItem(model.names[pid])
            name.setFlags(Qt.ItemFlag.ItemIsEnabled)
            name.setData(Qt.ItemDataRole.UserRole, pid)
            if not pane or pid not in pane.sub.nodes:
                name.setToolTip("地図にない遺伝子")
                name.setForeground(Qt.GlobalColor.darkGray)
            kind = self._combo(KINDS, o["kind"], lambda key, p=pid: self._set(p, "kind", key))
            change = self._combo(CHANGES, o["change"], lambda key, p=pid: self._set(p, "change", key))
            self.table.setCellWidget(r, 0, use)
            self.table.setItem(r, 1, name)
            self.table.setCellWidget(r, 2, kind)
            self.table.setCellWidget(r, 3, change)
        self._apply_filter(self.filter_edit.text())
        self._update_count()

    @staticmethod
    def _combo(options, current, on_change) -> QComboBox:
        box = QComboBox()
        for key, label in options:
            box.addItem(label, key)
        box.setCurrentIndex(max(0, box.findData(current)))
        box.currentIndexChanged.connect(lambda _i, b=box: on_change(b.currentData()))
        return box

    def _set(self, pid: int, key: str, value) -> None:
        o = self.obs.setdefault(pid, {"use": False, "kind": "expr", "change": "up"})
        o[key] = value
        if key != "use":
            o["use"] = True   # 観測・変化を選んだら「使う」にする
            self._sync_check(pid)
        self._update_count()

    def _sync_check(self, pid: int) -> None:
        for r in range(self.table.rowCount()):
            if self.table.item(r, 1).data(Qt.ItemDataRole.UserRole) == pid:
                box = self.table.cellWidget(r, 0)
                box.blockSignals(True)
                box.setChecked(self.obs[pid]["use"])
                box.blockSignals(False)

    def _apply_filter(self, text: str) -> None:
        key = text.strip().upper()
        for r in range(self.table.rowCount()):
            self.table.setRowHidden(r, bool(key) and key not in self.table.item(r, 1).text().upper())

    def _update_count(self) -> None:
        used = [o for o in self.obs.values() if o["use"]]
        counts = {k: sum(o["change"] == k for o in used) for k, _ in CHANGES}
        self.count_label.setText(f"使う遺伝子: {len(used)} 個（増 {counts['up']}・減 {counts['down']}・"
                                 f"変化なし {counts['none']}）")

    def _add_from_edit(self) -> None:
        text = self.add_edit.text().strip()
        pid = self.tab.model.by_name.get(text.upper()) if (text and self.tab.model) else None
        if pid is None:
            self.status.setText(f"「{text}」は登録されていません" if text else "遺伝子名を入力してください")
            return
        self.add_edit.clear()
        if pid not in self.added:
            self.added.insert(0, pid)
        self.obs.setdefault(pid, {"use": True, "kind": "expr", "change": "up"})["use"] = True
        self.update_genes()

    def _clear_checks(self) -> None:
        for o in self.obs.values():
            o["use"] = False
        self.added = []
        self.update_genes()

    def _load_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "観測を読み込む", "", "CSV (*.csv)")
        if not path:
            return
        by_name = self.tab.model.by_name
        missing, bad, loaded = [], [], 0
        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
        except (OSError, UnicodeDecodeError) as e:
            QMessageBox.critical(self, "エラー", f"読み込めませんでした。\n{e}")
            return
        if rows and rows[0] and rows[0][0].strip() in ("遺伝子", "gene", "Gene"):
            rows = rows[1:]
        for row in rows:
            if not row or not row[0].strip():
                continue
            gene = row[0].strip()
            kind = KIND_ALIASES.get((row[1] if len(row) > 1 else "発現").strip().lower(),
                                    KIND_ALIASES.get((row[1] if len(row) > 1 else "").strip()))
            change = CHANGE_ALIASES.get((row[2] if len(row) > 2 else "").strip().lower(),
                                        CHANGE_ALIASES.get((row[2] if len(row) > 2 else "").strip()))
            pid = by_name.get(gene.upper())
            if pid is None:
                missing.append(gene)
                continue
            if kind is None or change is None:
                bad.append(gene)
                continue
            self.obs[pid] = {"use": True, "kind": kind, "change": change}
            if pid not in self.added:
                self.added.append(pid)
            loaded += 1
        self.update_genes()
        message = f"{loaded} 個の観測を読み込みました"
        if missing:
            message += f"。登録されていない遺伝子: {'、'.join(missing[:10])}" + (" ほか" if len(missing) > 10 else "")
        if bad:
            message += f"。観測・変化を読み取れない行: {'、'.join(bad[:10])}"
        self.status.setText(message)

    def _save_csv(self) -> None:
        used = [(p, o) for p, o in self.obs.items() if o["use"]]
        if not used:
            self.status.setText("「使う」にチェックした遺伝子がありません")
            return
        path, _ = QFileDialog.getSaveFileName(self, "観測を書き出す", "observations.csv", "CSV (*.csv)")
        if not path:
            return
        kinds, changes = dict(KINDS), dict(CHANGES)
        names = self.tab.model.names
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["遺伝子", "観測", "変化"])
                for p, o in sorted(used, key=lambda x: names[x[0]]):
                    w.writerow([names[p], kinds[o["kind"]], changes[o["change"]]])
        except OSError as e:
            QMessageBox.critical(self, "エラー", f"保存できませんでした。\n{e}")
            return
        self.status.setText(f"{path} に保存しました")

    # ================= 探索 =================
    def run(self) -> None:
        tab = self.tab
        if self._running or not tab.model:
            return
        observations = [Observation(p, o["kind"], o["change"]) for p, o in self.obs.items()
                        if o["use"] and p in tab.model.proteins]
        if sum(o.change != "none" for o in observations) < 1:
            self.status.setText("増か減の観測を 1 つ以上チェックしてください")
            return
        hidden = tab.hidden_filters()
        roles = set(hidden.get("roles", []))
        blocked = {n for n, r in tab.model.roles.items() if r in roles} - {o.gene for o in observations}
        known_only = tab.known_only() or not self.use_unknown.isChecked()
        levels = tab.calc_levels()   # 計算に使う関係の確度（左の「計算に使う関係」）
        interactions = [it for it in tab.model.interactions.values()
                        if levels is None or tab.model.confidence[it.id] in levels]
        units, names = tab.model.units, tab.model.names   # 複合体・役割が重なる組は 1 つの候補にまとめる
        if self._common_model is not tab.model:   # 一般的なストレス応答の遺伝子（転写因子自身の点数では観測から外す）
            model = self._common_model = tab.model
            self._common = common_response.load(
                {**model.by_name, **{p.standard_name.upper(): p.id for p in model.proteins.values() if p.standard_name}})
        common = self._common if self.drop_common.isChecked() else set()
        signal_mode = "causal" if self.causal.isChecked() else "best"
        rerank = self.sim_rerank.isChecked()
        model, levels = tab.model, tab.calc_levels()
        depth = self.depth.value()
        self._observations = observations
        self._running = True
        self.run_button.setEnabled(False)
        self.status.setText("計算中…")

        def work():
            try:
                graph = SignedGraph(interactions, known_only, hidden, blocked, units, names)
                graph.common = common
                graph.signal_mode = signal_mode
                result = search(graph, observations, depth, names=names,
                                progress=lambda i, n: self._done.progress.emit(i, n))
                # 上位の候補をシミュレーションで壊して・活性化して確かめる（並べ直すかは選択）
                if rerank:
                    result = sim_verify.rerank(model, result, observations, SIM_TOP, levels, weight=10.0)
                else:
                    sim_verify.annotate(model, result, observations, SIM_TOP, levels)
                self._done.finished.emit(result, "")
            except Exception as e:  # noqa: BLE001 - 失敗の理由を画面に出す
                self._done.finished.emit(None, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_finished(self, result, error: str) -> None:
        self._running = False
        self.run_button.setEnabled(True)
        if result is None:
            self.status.setText(f"探索できませんでした: {error}")
            return
        self.results = result[:SHOW]
        names = self.tab.model.names
        n = len(self._observations)
        self.status.setText(f"観測 {n} 個・{self.depth.value()} 段まで: 候補 {len(result)} 件"
                            + ("" if result else "（説明できる候補はありませんでした）")
                            + (f"。上位 {SHOW} 件を表示" if len(result) > SHOW else ""))
        self.result_table.setRowCount(len(self.results))
        for r, c in enumerate(self.results):
            label = _label(c, names)
            via = f"{c.via_name}（{c.steps} 段）" if c.via is not None else ""
            sim = f"{c.sim_check.agree}/{c.sim_check.disagree}" if c.sim_check is not None else ""
            cells = [label, f"{c.score:.2f}", f"{c.overlap}/{c.regulon}", f"{c.count('match')}/{c.count('contra')}",
                     f"{c.q_value:.3g}", via, sim]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if col == 4 and c.q_value >= 0.05:
                    item.setForeground(Qt.GlobalColor.gray)
                self.result_table.setItem(r, col, item)
        self.detail.clear()
        self.window_button.setEnabled(False)
        if self.results:
            self.result_table.selectRow(0)

    def _unit_name(self, node: int) -> str:
        """遺伝子またはまとまり（負の番号）の名前。"""
        if node >= 0:
            return self.tab.model.names.get(node, str(node))
        units = self.tab.model.units
        return units[-node - 1].name if -node - 1 < len(units) else str(node)

    def _selected(self):
        rows = self.result_table.selectionModel().selectedRows()
        return self.results[rows[0].row()] if rows and rows[0].row() < len(self.results) else None

    def _show_selected(self) -> None:
        c = self._selected()
        if c is None:
            return
        names = self.tab.model.names
        kinds, changes = dict(KINDS), {"up": "↑", "down": "↓", "none": "→（変化なし）"}
        via = (f"。点数は下流の転写因子 {c.via_name}（{c.steps} 段先）の標的との重なりから"
               if c.via is not None else "")
        lines = [f"<b>{_label(c, names)}</b>（点数 {c.score:.2f}、標的の重なり {c.overlap}/{c.regulon}、"
                 f"偶然でも起きる確率 {c.p_value:.2g}、補正後 {c.q_value:.2g}{via}）"]
        color = {"match": "#2e7d32", "unknown": "#558b2f", "both": "#8d6e63", "self": "#2e7d32", "flat_ok": "#757575",
                 "contra": "#c62828", "flat_bad": "#c62828", "unreached": "#9e9e9e"}
        for v in sorted(c.verdicts, key=lambda v: (v.outcome in ("unreached", "flat_ok"), names[v.gene])):
            path = ""
            if v.edges:
                path = " ".join(f"{names[a]} {ARROW[s]}" for a, _b, s in v.edges) + f" {names[v.edges[-1][1]]}"
            lines.append(f"<span style='color:{color[v.outcome]}'>{names[v.gene]}（{kinds[v.kind]}{changes[v.change]}）: "
                         f"{OUTCOME_LABELS[v.outcome]}</span>" + (f"<br>&nbsp;&nbsp;{path}" if path else ""))
        if c.sim_check is not None:
            k = c.sim_check
            way = {1: "活性化した", -1: "不活性化した"}.get(k.direction, "")
            lines.append(f"<br><b>シミュレーションでの確認</b>" + (f"（候補が{way}として）" if way else "") +
                         f": 観測と一致 {k.agree}・逆 {k.disagree}・予測できない {k.silent}"
                         "（候補から観測した遺伝子への経路だけで、発現と活性を分けて計算）")
            arrow = {"up": "↑", "down": "↓"}
            for gene, kind, change, pred in sorted(k.genes, key=lambda x: (x[3] is None, x[3] != x[2], names[x[0]])):
                if pred is None:
                    continue
                col = "#2e7d32" if pred == change else "#c62828"
                lines.append(f"<span style='color:{col}'>{names[gene]}（{dict(KINDS)[kind]}）: 予測 {arrow[pred]}・観測 "
                             f"{arrow.get(change, change)}</span>")
        if c.consistency:
            ok = sum(x[2] is True for x in c.consistency)
            ng = sum(x[2] is False for x in c.consistency)
            lines.append(f"<br><b>経路の向きの一貫性</b>（{'活性化' if c.direction > 0 else '不活性化'}したとして）: "
                         f"動いた転写因子のうち 予測と一致 {ok}・不一致 {ng}")
            mark = {True: ("#2e7d32", "一致"), False: ("#c62828", "不一致"), None: ("#757575", "経路の符号が決まらない")}
            for t, d, res, edges in sorted(c.consistency, key=lambda x: (x[2] is not True, x[2] is None, x[1])):
                path = " ".join(f"{names.get(a, a)} {ARROW[s]}" for a, _b, s in edges) + \
                    (f" {names.get(edges[-1][1], edges[-1][1])}" if edges else "")
                col, text = mark[res]
                lines.append(f"<span style='color:{col}'>{self._unit_name(t)}: {text}</span><br>&nbsp;&nbsp;{path}")
        self.detail.setText("<br>".join(lines))
        self.window_button.setEnabled(True)
        pane = self.tab.active_pane
        if pane is not None:
            pane.show_search_result(c)

    def _open_window(self) -> None:
        c = self._selected()
        if c is None:
            return
        names = self.tab.model.names
        self.tab.open_relation_window(title=f"制御遺伝子探索: {_label(c, names)}")
