"""決まった遺伝子の組を描く表示枠（GeneSetPane）と、表示枠を入れる別ウィンドウ（MapWindow）。

- GeneSetPane: 「経路」タブで強調している経路の遺伝子だけを描く枠。「強調中の遺伝子を新しい枠で開く」で、
  メインの表示枠に加える。開いたときの遺伝子の組と経路の強調をそのまま保ち、「再配置」を押すとその組に戻す。
  遺伝子は名前で覚えるので、次回起動時にも同じ組で開ける（state() の "gene_set"）。
- MapWindow: 表示枠を別ウィンドウに出す（表示枠の「別ウィンドウで開く」）。中の表示枠は、メインの地図と同じように
  検索・注目・拡張などができ、凡例・配置・色の設定はメインの地図と共通。
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from ..subgraph import Subgraph
from .graph_pane import GraphPane


class GeneSetPane(GraphPane):
    """決まった遺伝子の組（default_nodes）を描く表示枠。拡張などで加えたものは「再配置」で外れる。

    state の "gene_set"（遺伝子名）・"gene_set_edges"（遺伝子名の組）・"gene_set_ends"（端の遺伝子名）から作る。
    名前を遺伝子に戻すのは、モデルを読み込んでから（on_model_reloaded）。"""

    def __init__(self, tab, state: dict):
        self.default_nodes: set[int] = set()
        self._gene_set = {k: state.get(k, []) for k in ("gene_set", "gene_set_edges", "gene_set_ends")}
        self._gene_set_title = state.get("gene_set_title", "経路の遺伝子")
        super().__init__(tab, {"focus": []})
        self.relation = None
        # 注目・段数は、遺伝子の組が決まっているこの枠では使わない
        for w in (self.search, self.up_spin, self.down_spin):
            w.hide()
        for i in range(self.info_bar.count()):
            label = self.info_bar.itemAt(i).widget()
            if label is not None and label.__class__.__name__ == "QLabel" and label.text() in ("上流", "段 下流", "段"):
                label.hide()

    def set_title(self, index: int, closable: bool) -> None:
        super().set_title(index, closable)
        self.title.setText(f"枠{index}: {self._gene_set_title}" if index else self._gene_set_title)

    def on_model_reloaded(self, use_default: bool) -> None:
        by_name = self.model.by_name
        ids = lambda names: [by_name[n.upper()] for n in names if n.upper() in by_name]   # noqa: E731
        self.default_nodes = set(ids(self._gene_set["gene_set"]))
        edges = set()
        for a, b in self._gene_set["gene_set_edges"]:
            if a.upper() in by_name and b.upper() in by_name:
                edges.add((by_name[a.upper()], by_name[b.upper()]))
        ends = ids(self._gene_set["gene_set_ends"])
        self.relation = {"pids": ends, "found": [], "nodes": set(self.default_nodes), "edges": edges,
                         "anchor_node": None, "mode": "gene_set"}
        super().on_model_reloaded(False)

    def state(self) -> dict:
        state = super().state() if self.model else {}
        state.update({k: list(v) for k, v in self._gene_set.items()})
        state["gene_set_title"] = self._gene_set_title
        state["focus"] = []
        return state

    def _build_sub(self, with_relation: bool = True) -> Subgraph:
        if not self.model:
            return Subgraph(set(), set(), {}, {}, {})
        proteins = self.model.proteins
        nodes = {n for n in (self.default_nodes | self.extra) if n in proteins} - (self.excluded - self.default_nodes)
        adjacency = self._adjacency()
        # 経路の線は、ふだんは描かない線（転写因子どうし）でも描く
        relation = self.relation or {}
        return Subgraph(nodes, set(), {n: 0 for n in nodes},
                        {n: len(adjacency.out.get(n, set()) - nodes) for n in nodes},
                        {n: len(adjacency.inc.get(n, set()) - nodes) for n in nodes},
                        pinned=set(nodes), grown=self.grown & nodes,
                        allow=set(relation.get("edges", set())))

    def _on_page_ready(self):
        super()._on_page_ready()
        self._push_relation()

    def refresh_view(self, *args, **kwargs) -> None:
        super().refresh_view(*args, **kwargs)
        self._push_relation()

    # 「経路」タブの設定は、この枠には当てはめない（開いたときの経路をそのまま保つ）
    def show_relation(self, pids, mode, steps, anchor=None) -> str:
        return "この枠は経路を固定して表示しています" if mode != "off" else ""

    def clear_relation(self) -> None:
        pass

    def relayout(self) -> None:
        """再配置: 開いたときの遺伝子の組（経路タブで強調していた遺伝子）に戻して、配置し直す。"""
        self.extra, self.uncapped, self.excluded = set(), set(), set()
        self.grown, self.grown_order, self.grown_info = set(), [], {}
        self._record_history()
        self.refresh_view()
        self.statusMessage.emit("経路タブで強調していた遺伝子に戻して、配置し直しました")

    def set_focus(self, pid, center: bool = False) -> None:
        self.statusMessage.emit("この枠では「注目」は使えません。ほかの枠で注目してください")

    def _focus_clicked(self, pid: int) -> None:
        self.set_focus(pid)

    def update_count_label(self) -> None:
        if not self.model:
            return
        edges = self.model.count_edges(self.sub, self.tab.known_only(), self.tab.hidden_filters())
        self.focus_label.setText(f" 遺伝子 {len(self.sub.nodes)} 個　線 {edges} 本")


class MapWindow(QWidget):
    """表示枠を 1 つ入れた別ウィンドウ。新しい枠・閉じる・別ウィンドウのボタンは、この窓では使わない。"""

    def __init__(self, pane: GraphPane, title: str):
        super().__init__(None, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(title)
        self.pane = pane
        for w in (pane.new_pane_button, pane.close_button, pane.detach_button):
            w.hide()
        pane.set_title(0, closable=False)
        if pane.title.text() == "枠0":
            pane.title.setText("別ウィンドウ")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(pane)
        self.resize(1000, 750)
