"""左の「登録」タブ: 注目・拡張・選択・TFs・経路・条件・凡例の設定を、名前を付けて登録し、あとで呼び出す。

登録するときに、どの項目を含めるかを選べる。呼び出すと、登録した項目だけを操作中の表示枠（と全体の凡例）に当てはめる。
遺伝子は名前で保存するので、DB を作り直しても同じ遺伝子に戻せる（DB にない遺伝子は無視する）。
"""
import json
from datetime import datetime

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import (QApplication, QCheckBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..db.vocab import type_label
from ..paths import user_data_dir
from ..roles import ROLE_INFO

SAVE_FILE = user_data_dir() / "saved_views.json"

# 登録できる項目: キー → (表示名, 説明)
PARTS = {
    "focus": ("注目", "注目している遺伝子・上流/下流の段数"),
    "grown": ("拡張", "拡張と、それで加えた遺伝子・隠した段"),
    "picked": ("選択", "地図で緑に選んでいる遺伝子"),
    "trf": ("TFs", "左の TFs 一覧でチェックした、マップの一番上の段に表示する転写因子"),
    "relation": ("経路", "「経路」タブのチェックした遺伝子・表示経路・経路の段数・基準の遺伝子"),
    "conditions": ("条件", "左の「条件」タブで外した条件と、最も上流の経路のチェック"),
    "legend": ("凡例", "凡例の役割・制御の種類・作用のチェックと「表示」欄"),
}
EXPANSION_KEYS = ("extra", "uncapped", "excluded", "grown", "grown_info", "grown_order")
EFFECT_NAMES = {"activate": "促進", "inhibit": "抑制", "none": "作用不明"}


def _load() -> list[dict]:
    try:
        data = json.loads(SAVE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [v for v in data.get("views", []) if isinstance(v, dict) and v.get("name")]


def _store(views: list[dict]) -> None:
    SAVE_FILE.write_text(json.dumps({"views": views}, ensure_ascii=False, indent=1), encoding="utf-8")


class SavedViewsPanel(QWidget):
    def __init__(self, tab):
        super().__init__()
        self.tab = tab
        self.views = _load()
        layout = QVBoxLayout(self)
        save_box = QGroupBox("今の表示を登録")
        save_layout = QVBoxLayout(save_box)
        self.part_checks: dict[str, QCheckBox] = {}
        for key, (label, desc) in PARTS.items():
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setToolTip(desc)
            self.part_checks[key] = cb
            save_layout.addWidget(cb)
        name_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("名前 例: TOR1 から MSN2 への経路")
        self.name_edit.returnPressed.connect(self.save_current)
        save_button = QPushButton("登録")
        save_button.clicked.connect(self.save_current)
        name_row.addWidget(self.name_edit, 1)
        name_row.addWidget(save_button)
        save_layout.addLayout(name_row)

        list_box = self.list_box = QGroupBox("登録した表示")
        list_layout = QVBoxLayout(list_box)
        self.list = QListWidget()
        self.list.setMinimumHeight(160)
        self.list.currentItemChanged.connect(lambda *_: self._show_summary())
        self.list.itemDoubleClicked.connect(lambda _item: self.load_selected())
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.summary.setStyleSheet("color:#37474f;")
        buttons = QHBoxLayout()
        load_button = QPushButton("呼び出す")
        load_button.setToolTip("選んだ表示を、操作中の表示枠に当てはめます。ダブルクリックでも呼び出せます")
        load_button.clicked.connect(self.load_selected)
        delete_button = QPushButton("削除")
        delete_button.clicked.connect(self.delete_selected)
        buttons.addWidget(load_button)
        buttons.addWidget(delete_button)
        # 登録の中身は、呼び出す・削除のボタンの下に出す
        list_layout.addWidget(self.list)
        list_layout.addLayout(buttons)
        list_layout.addWidget(self.summary)

        for w in (save_box, list_box):
            layout.addWidget(w)
        layout.addStretch(1)
        layout.addWidget(tab.help_button("saved"))
        self._refresh_list()
        # 一覧の外（呼び出す・削除のボタンを除く）や、一覧の空いたところをクリックしたら選択を外す
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress and self.list.currentItem() is not None:
            target = QApplication.widgetAt(event.globalPosition().toPoint())
            inside = target is not None and (target is self.list_box or self.list_box.isAncestorOf(target))
            on_empty = target is self.list.viewport() and \
                self.list.itemAt(self.list.viewport().mapFromGlobal(event.globalPosition().toPoint())) is None
            if not inside or on_empty:
                self.clear_selection()
        return super().eventFilter(obj, event)

    def clear_selection(self) -> None:
        self.list.clearSelection()
        self.list.setCurrentItem(None)
        self._show_summary()

    # ================= 登録 =================
    def _capture(self, parts: list[str]) -> dict:
        """操作中の表示枠と、全体の凡例から、選んだ項目を取り出す（遺伝子は名前で持つ）。"""
        tab = self.tab
        pane = tab.active_pane
        names = tab.model.names
        state = pane.state()
        data: dict = {}
        if "focus" in parts:
            data["focus"] = {k: state[k] for k in ("focus", "up", "down")}
        if "trf" in parts:
            data["trf"] = list(state["trf"])
        if "grown" in parts:
            data["grown"] = {k: state[k] for k in EXPANSION_KEYS}
        if "picked" in parts:
            data["picked"] = list(state["picked"])
        if "relation" in parts:
            anchor = tab.rel_anchor.currentData()
            data["relation"] = {"genes": [names[p] for p in tab._rel_checked], "mode": tab._relation_mode(),
                                "steps": tab.rel_steps.value(),
                                "anchor": names.get(anchor) if anchor is not None else None}
        if "legend" in parts:
            data["legend"] = {"hidden": tab.hidden_filters(), "display": dict(tab.display_options)}
        if "conditions" in parts:
            data["conditions"] = {"off": sorted(tab.conditions_off), "top": tab.cond_top.isChecked()}
        return data

    def save_current(self) -> None:
        tab = self.tab
        if tab.active_pane is None or not tab.model:
            return
        name = self.name_edit.text().strip()
        parts = [k for k, cb in self.part_checks.items() if cb.isChecked()]
        if not name:
            QMessageBox.information(self, "登録", "名前を入力してください。")
            return
        if not parts:
            QMessageBox.information(self, "登録", "登録する項目を 1 つ以上チェックしてください。")
            return
        same = [i for i, v in enumerate(self.views) if v["name"] == name]
        if same and QMessageBox.question(self, "上書きの確認", f"「{name}」はすでに登録されています。上書きしますか？") \
                != QMessageBox.StandardButton.Yes:
            return
        view = {"name": name, "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "parts": self._capture(parts)}
        if same:
            self.views[same[0]] = view
        else:
            self.views.append(view)
        try:
            _store(self.views)
        except OSError as e:
            QMessageBox.critical(self, "エラー", f"ファイルに書き込めませんでした。\n{e}")
            return
        self.name_edit.clear()
        self._refresh_list(select=name)
        tab.statusMessage.emit(f"「{name}」を登録しました: {'・'.join(PARTS[k][0] for k in parts)}")

    # ================= 呼び出し =================
    def load_selected(self) -> None:
        view = self._selected()
        tab = self.tab
        if view is None or tab.active_pane is None or not tab.model:
            return
        parts = view["parts"]
        pane = tab.active_pane
        by_name = tab.model.by_name
        ids = lambda names: [by_name[n.upper()] for n in names if n.upper() in by_name]   # noqa: E731

        # 凡例（全表示枠に共通）
        if "conditions" in parts:
            tab.set_conditions_off(set(parts["conditions"].get("off", [])), record=False)   # 履歴には下の load_state で残す
            tab.set_condition_top(parts["conditions"].get("top", False))
        if "legend" in parts:
            tab._set_hidden_filters(parts["legend"].get("hidden", {}))
            display = parts["legend"].get("display", {})
            tab.display_options.update({k: bool(v) for k, v in display.items() if k in tab.display_options})
            for view_pane in tab._views():
                view_pane.push_label_options()

        # 注目・拡張・緑の選択（操作中の表示枠）。登録していない項目は今のまま。
        # 注目だけを呼び出すときは、拡張は外す（別の遺伝子に注目したときと同じ）
        pane.clear_relation()
        state = pane.state()
        if "focus" in parts:
            state.update({k: v for k, v in parts["focus"].items() if k != "trf"})
            if "grown" not in parts:
                state.update({"extra": [], "uncapped": [], "excluded": [], "grown": [], "grown_info": {},
                              "grown_order": []})
        if "grown" in parts:
            state.update(parts["grown"])
        # TFs の表示。TFs の項目がなく注目だけを呼び出すときは外す（別の遺伝子に注目したときと同じ）。
        # 以前の形式（TFs を注目の項目に含めていた）も読めるようにする
        if "trf" in parts:
            state["trf"] = parts["trf"]
        elif "focus" in parts:
            state["trf"] = parts["focus"].get("trf", [])
        if "picked" in parts:
            state["picked"] = parts["picked"]
        pane.load_state(state)

        # 経路（地図を描き直してから、地図にある遺伝子だけチェックする）
        if "relation" in parts:
            r = parts["relation"]
            tab.set_relation_settings(ids(r.get("genes", [])), r.get("mode", "off"), int(r.get("steps", r.get("lo", 1))),
                                      by_name.get((r.get("anchor") or "").upper()))
        else:
            tab.apply_relation()
        tab._save_view()
        tab.statusMessage.emit(f"「{view['name']}」を呼び出しました")

    def delete_selected(self) -> None:
        view = self._selected()
        if view is None:
            return
        if QMessageBox.question(self, "削除の確認", f"「{view['name']}」を削除しますか？") != QMessageBox.StandardButton.Yes:
            return
        self.views = [v for v in self.views if v is not view]
        try:
            _store(self.views)
        except OSError as e:
            QMessageBox.critical(self, "エラー", f"ファイルに書き込めませんでした。\n{e}")
        self._refresh_list()

    # ================= 一覧 =================
    def _selected(self) -> dict | None:
        item = self.list.currentItem()
        if item is None:
            return None
        name = item.data(Qt.ItemDataRole.UserRole)
        return next((v for v in self.views if v["name"] == name), None)

    def _refresh_list(self, select: str | None = None) -> None:
        self.list.clear()
        for v in sorted(self.views, key=lambda v: v.get("saved_at", ""), reverse=True):
            item = QListWidgetItem(v["name"])
            item.setData(Qt.ItemDataRole.UserRole, v["name"])
            item.setToolTip(self._describe(v))
            self.list.addItem(item)
            if v["name"] == select:
                self.list.setCurrentItem(item)
        self._show_summary()

    def _show_summary(self) -> None:
        view = self._selected()
        self.summary.setText(self._describe(view) if view else "")

    @staticmethod
    def _describe(view: dict) -> str:
        parts = view["parts"]
        lines = [f"登録: {view.get('saved_at', '')}"]
        if "focus" in parts:
            f = parts["focus"]
            lines.append(f"注目: {'、'.join(f.get('focus', [])) or 'なし'}、上流 {f.get('up')}・下流 {f.get('down')} 段")
        if "grown" in parts:
            g = parts["grown"]
            lines.append(f"拡張: {'、'.join(g.get('grown', [])) or 'なし'}"
                         + (f"、加えた遺伝子 {len(g.get('extra', []))} 個" if g.get("extra") else ""))
        trf = parts["trf"] if "trf" in parts else parts.get("focus", {}).get("trf")
        if trf is not None:
            lines.append(f"TFs: {'、'.join(trf) or 'なし'}")
        if "picked" in parts:
            lines.append(f"緑の選択: {'、'.join(parts['picked']) or 'なし'}")
        if "relation" in parts:
            from .graph_pane import GraphPane
            r = parts["relation"]
            mode = GraphPane.RELATION_MODES.get(r.get("mode"), ("?",))[0]
            steps = r.get("steps", r.get("lo"))   # 以前の形式（lo〜hi）で登録したものは最小の段数
            span = "" if r.get("mode") == "off" or steps is None else f" {steps} 段"
            anchor = f"、基準 {r['anchor']}" if r.get("anchor") else ""
            lines.append(f"経路: {mode}{span}: {'、'.join(r.get('genes', [])) or '遺伝子なし'}{anchor}")
        if "legend" in parts:
            h = parts["legend"].get("hidden", {})
            hidden = ([ROLE_INFO[k][0] for k in h.get("roles", []) if k in ROLE_INFO]
                      + [type_label(k) for k in h.get("types", [])]
                      + [EFFECT_NAMES.get(k, k) for k in h.get("effects", [])])
            lines.append("凡例: " + ("隠す項目 " + "、".join(hidden) if hidden else "すべて表示"))
        if "conditions" in parts:
            from .. import conditions
            names = conditions.labels()
            off = parts["conditions"].get("off", [])
            lines.append("条件: " + ("外す条件 " + "、".join(names.get(k, k) for k in off) if off else "すべて選択")
                         + ("、最も上流の経路" if parts["conditions"].get("top") else ""))
        return "\n".join(lines)
