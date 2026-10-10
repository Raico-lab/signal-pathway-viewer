"""DB の内容から Cytoscape.js の要素を組み立てる。"""
import re

import networkx as nx

from . import complex_groups, conditions, confidence
from .db import Category, Interaction, Protein
from .db.vocab import (FEEDBACK, INTERACTION_TYPES, NOT_SIGNAL_FLOW, PARALOG_COLOR, PARALOG_SOURCE, resolve_effect,
                       type_label)
from .roles import ROLE_INFO, classify
from .subgraph import Adjacency, Subgraph

UNKNOWN_COLOR = "#90a4ae"
# ほかの相互作用とは別扱いにする種類（上流・下流のたどり方と階層に使わない。表示はする）
SEPARATE_TYPES = {"transcription"}


class PathwayModel:
    """画面側で使うネットワーク。"""

    def __init__(self, proteins: list[Protein], interactions: list[Interaction], categories: list[Category],
                 members: dict[int, list[int]] | None = None):
        """members: CategoryMember の所属（カテゴリの id → 遺伝子）。Complex Portal の複合体（1 つの遺伝子が複数に入る）。"""
        self.proteins = {p.id: p for p in proteins}
        self.interactions = {i.id: i for i in interactions}
        self.categories = {c.name: c for c in categories}
        self.portal: dict[str, list[int]] = {
            c.name: sorted(m for m in (members or {}).get(c.id, []) if m in self.proteins)
            for c in categories if c.is_complex and (members or {}).get(c.id)}
        self._all_complexes: dict[str, list[int]] | None = None
        # パラログ（出典「パラログ」のカテゴリ。役割が重なりうる組）。複合体とは別に扱う（tools/import_sources.py）
        self.paralogs: dict[str, list[int]] = {
            c.name: sorted((m for m in (members or {}).get(c.id, []) if m in self.proteins),
                           key=lambda m: self.proteins[m].gene_name.upper())
            for c in categories if c.source == PARALOG_SOURCE and (members or {}).get(c.id)}
        # 上流・下流のたどり方・階層には転写制御を使わない（転写制御は別扱い）
        # 信号の流れではない関係（逆向きのリン酸の受け渡しなど）はたどらない
        flow = [i for i in interactions if i.interaction_type not in SEPARATE_TYPES
                and NOT_SIGNAL_FLOW not in (i.evidence or "")]
        self._flow = flow
        self._view_adj: dict = {}   # view_adjacency の結果（キー → 関係）。凡例の設定を変えるたびに増えるので数個までにする
        self.adjacency = Adjacency([(i.source_id, i.target_id) for i in flow])
        # (制御する側, される側) → 関係の種類（まとめの枠を種類ごとに分けるのに使う）
        self.pair_types: dict[tuple[int, int], list[str]] = {}
        for i in flow:
            self.pair_types.setdefault((i.source_id, i.target_id), []).append(i.interaction_type)
        # 転写制御（注目した遺伝子の転写因子の表示と、2 回目のクリックでの追加に使う）
        tx = [i for i in interactions if i.interaction_type in SEPARATE_TYPES]
        self._tx = tx
        self.tx_adjacency = Adjacency([(i.source_id, i.target_id) for i in tx])
        self.tfs = frozenset(self.tx_adjacency.out)   # 転写因子（転写制御の記録で制御する側にあるもの）
        self.tx_effect = {(i.source_id, i.target_id): resolve_effect(i.interaction_type, i.effect) for i in tx}
        self._tx_known = {pair for pair, effect in self.tx_effect.items() if effect != "none"}
        # 「作用が分かっている関係のみ」のときにたどる関係
        self.effect_adjacency = Adjacency([(i.source_id, i.target_id) for i in flow
                                           if resolve_effect(i.interaction_type, i.effect) != "none"])
        self.names = {p.id: p.gene_name for p in proteins}
        # 関係の確度（A 文献で確認 〜 D 推定だけ、U 書式なし）。説明に使う
        self.confidence = {i.id: confidence.level(i.evidence) for i in interactions}
        self.roles = {p.id: classify(p.description, p.protein_properties) for p in proteins}
        # ネットワーク全体での階層（遺伝子ごとに決まる値）。制御する側ができるだけ制御される側より上に来るようにする。
        # 結合は向きを持たないので、作用の向きが付いているものだけ使う。複合体は 1 つのまとまりとして数える。
        # 文献で確かめたフィードバック（下流から上流への作用）は使わない
        effects = {i.id: resolve_effect(i.interaction_type, i.effect) for i in interactions}
        directed = [i for i in flow if (i.interaction_type != "binding" or effects[i.id] != "none")
                    and FEEDBACK not in (i.evidence or "")]
        known = [(i.source_id, i.target_id) for i in directed if effects[i.id] != "none"]
        unknown = [(i.source_id, i.target_id) for i in directed if effects[i.id] == "none"]
        group = {m: f"c:{name}" for name, members in self.complexes().items() for m in members}
        self.levels_all = network_levels(known, unknown, group)
        self.levels_known = network_levels(known, [], group)
        self.by_name = {p.gene_name.upper(): p.id for p in proteins}
        self._layout_order: dict[bool, dict[int, int]] = {}

    def view_adjacency(self, known_only: bool, hidden: dict[str, list[str]], keep: set[int],
                       with_tx: bool = False) -> Adjacency:
        """表示する遺伝子を決めるときにたどる関係。凡例のチェックで隠した種類・作用の関係と、
        隠した役割の遺伝子（keep = 注目中の遺伝子は除く）につながる関係はたどらない。
        with_tx なら転写制御もたどる（左の「経路」タブ。共通の転写因子などを経由する経路も探す）。"""
        roles, types, effects = (frozenset(hidden.get(k, [])) for k in ("roles", "types", "effects"))
        blocked = frozenset(n for n, r in self.roles.items() if r in roles) - keep
        if not (roles or types or effects or with_tx):
            return self.effect_adjacency if known_only else self.adjacency
        key = (known_only, with_tx, types, effects, blocked)
        if key not in self._view_adj:
            if len(self._view_adj) >= 4:
                self._view_adj.clear()
            pairs = []
            for i in self._flow + (self._tx if with_tx else []):
                effect = resolve_effect(i.interaction_type, i.effect)
                if (known_only and effect == "none") or i.interaction_type in types or effect in effects:
                    continue
                if i.source_id in blocked or i.target_id in blocked:
                    continue
                pairs.append((i.source_id, i.target_id))
            self._view_adj[key] = Adjacency(pairs)
        return self._view_adj[key]

    def all_complexes(self) -> dict[str, list[int]]:
        """地図の枠（カテゴリ）と Complex Portal の複合体。注目・拡張で同じ複合体の構成要素を加えるのに使う。"""
        if self._all_complexes is None:
            self._all_complexes = {**self.portal, **self.complexes()}
        return self._all_complexes

    def frames(self, sub: Subgraph) -> dict[int, str]:
        """地図で遺伝子を囲む枠（遺伝子 → 枠の名前）。Cytoscape の枠は 1 つの遺伝子に 1 つだけなので、
        地図の枠（カテゴリ）を先に決め、残りの遺伝子を Complex Portal の複合体に割り当てる。
        表示中の構成要素の多い複合体から順に割り当て、2 つ以上が入るものだけを枠にする。まとめの遺伝子は除く。"""
        bundled = {g for genes in sub.bundles.values() for g in genes}
        frame: dict[int, str] = {}
        for name, members in self.complexes().items():
            for m in members:
                if m in sub.nodes and m not in bundled:
                    frame[m] = name
        free = sub.nodes - bundled - set(frame)
        shown = {name: [m for m in members if m in free] for name, members in self.portal.items()}
        for name in sorted((n for n, ms in shown.items() if len(ms) >= 2),
                           key=lambda n: (-len(shown[n]), len(self.portal[n]), n)):
            genes = [m for m in shown[name] if m not in frame]
            if len(genes) >= 2:
                for m in genes:
                    frame[m] = name
        return frame

    @staticmethod
    def _bundle_edge(source: int, target: int, sub: Subgraph) -> bool:
        """まとめの線（ふだん隠す）か: 起点 → まとめの遺伝子、または同じ起点のまとめの中の遺伝子どうし。"""
        for hub, genes in sub.bundles.items():
            if target in genes and (source == hub or source in genes):
                return True
        return False

    def paralog_frames(self, sub: Subgraph, framed: set[int], bundle_of: dict[int, str]) -> dict[int, str]:
        """パラログの枠（遺伝子 → 枠の id）。複合体と違い、構成要素を地図に加えない: 地図に出ている遺伝子のうち、
        複合体の枠に入っていないパラログどうしを囲む（つながった組は 1 つの枠）。同じ置き場所の遺伝子どうしだけを囲む:
        ふつうの段・左の TFs 一覧で加えた転写因子の段（一番上）・同じまとめの枠の中（枠の中に入れ子にする）。
        段はそろえないので、地図で別々の段になった組は、地図の側（network.js）で枠を描かない。
        枠の id は "g:" ＋ 組の名前を「・」でつないだもの。"""
        def place(g: int) -> str:
            return bundle_of.get(g) or ("tx" if g in sub.tx_regs else "")
        free = sub.nodes - framed
        group: dict[int, set[int]] = {}
        names: dict[int, list[str]] = {}
        for name, genes in self.paralogs.items():
            by_place: dict[str, list[int]] = {}
            for g in genes:
                if g in free:
                    by_place.setdefault(place(g), []).append(g)
            for shown in by_place.values():
                if len(shown) < 2:
                    continue
                merged = set(shown).union(*(group.get(g, set()) for g in shown))
                merged_names = sorted({name}.union(*(names.get(g, []) for g in merged)))
                for g in merged:
                    group[g], names[g] = merged, merged_names
        return {g: "g:" + "・".join(names[g]) for g in group}

    def bundle_type(self, hub: int, gene: int) -> str:
        """まとめの枠を分ける関係の種類（2 種類以上あれば、凡例の並びで先のもの）。"""
        order = list(INTERACTION_TYPES)
        types = self.pair_types.get((hub, gene), ["other"])
        return min(types, key=lambda t: order.index(t) if t in order else len(order))

    def complexes(self) -> dict[str, list[int]]:
        result: dict[str, list[int]] = {}
        for p in self.proteins.values():
            cat = self.categories.get(p.complex_category)
            if cat and cat.is_complex:
                result.setdefault(cat.name, []).append(p.id)
        return result

    def tx_priority(self, pid: int, other: int | None = None, regulator: bool = True):
        """転写制御の相手を絞るときの優先順。作用（促進・抑制）が分かっているもの → 他の関係（リン酸化など）も
        持つもの → 名前順。pid は相手、other は注目した遺伝子（regulator=True なら pid が other を制御する側）。"""
        pair = (pid, other) if regulator else (other, pid)
        known = other is not None and pair in self._tx_known
        relay = bool(self.adjacency.out.get(pid) or self.adjacency.inc.get(pid))
        return (not known, not relay, self.names.get(pid, ""))

    def category_color(self, name: str) -> str:
        cat = self.categories.get(name)
        return cat.color if cat else UNKNOWN_COLOR

    def edge_data(self, it: Interaction) -> dict:
        info = INTERACTION_TYPES.get(it.interaction_type)
        return {
            "id": f"e{it.id}",
            "source": f"p{it.source_id}",
            "target": f"p{it.target_id}",
            "type": it.interaction_type,
            "typeLabel": type_label(it.interaction_type),
            "symbol": info[3] if info else it.interaction_type[:3],
            "effect": resolve_effect(it.interaction_type, it.effect),
            # 向きがあるか（結合で作用も不明なものだけは向きがない）
            "directed": it.interaction_type != "binding" or resolve_effect(it.interaction_type, it.effect) != "none",
            # 線の色は修飾・制御の種類。促進・抑制の作用は終点の印の形で示す
            "color": info[2] if info else "#9e9e9e",
            # 働く条件（記録がなければ「条件の記録なし」）。左の「条件」タブの選択で地図を目立たせるのに使う
            "conds": conditions.split(it.conditions) or [conditions.NONE_KEY],
        }

    def _edge_shown(self, it: Interaction, sub: Subgraph, known_only: bool, tf_pairs: bool = False) -> bool:
        """地図に描く線か。tf_pairs なら、ふだんは隠す転写因子どうしの線も含める（地図に渡しておき、
        緑で選んだ遺伝子の線だけ地図の側で見せる）。"""
        if it.source_id not in sub.nodes or it.target_id not in sub.nodes:
            return False
        if not tf_pairs and self.tf_pair_hidden(it, sub):
            return False
        return not known_only or resolve_effect(it.interaction_type, it.effect) != "none"

    def tf_pair_hidden(self, it: Interaction, sub: Subgraph) -> bool:
        """転写因子どうしの転写制御の線なので、地図に描かないか。どちらかが注目・拡張している遺伝子の線と、
        sub.allow の線（経路タブの経路）は描く（TFs 一覧の「この遺伝子を制御する転写因子」の線は残す）。"""
        if it.interaction_type not in SEPARATE_TYPES or it.source_id not in self.tfs or it.target_id not in self.tfs:
            return False
        if {it.source_id, it.target_id} & (sub.focus | sub.grown) or (it.source_id, it.target_id) in sub.allow:
            return False
        return True

    def count_edges(self, sub: Subgraph, known_only: bool, hidden: dict[str, list[str]]) -> int:
        """表示される線の本数（凡例のチェックで隠した役割・種類・作用は数えない）。描画前の警告に使う。"""
        roles, types, effects = (set(hidden.get(k, [])) for k in ("roles", "types", "effects"))
        hidden_nodes = {n for n in sub.nodes if self.roles.get(n) in roles}
        frame = self.frames(sub)
        return sum(1 for it in self.interactions.values()
                   if self._edge_shown(it, sub, known_only) and it.interaction_type not in types
                   and not self._bundle_edge(it.source_id, it.target_id, sub)   # まとめの線はふだん隠す
                   and not (it.source_id in frame and frame.get(it.target_id) == frame[it.source_id])   # 複合体の中の線も
                   and resolve_effect(it.interaction_type, it.effect) not in effects
                   and it.source_id not in hidden_nodes and it.target_id not in hidden_nodes)

    def levels(self, known_only: bool) -> dict[int, int]:
        return self.levels_known if known_only else self.levels_all

    def layout_order(self, known_only: bool) -> dict[int, int]:
        """配置の番号（遺伝子 → 1 始まりの番号）。ネットワーク全体で一度だけ決めるので、どのマップでも同じ。
        マップの段の中では番号の小さい順に左から並べる（「A は常に B の左」がどのマップでも成り立つ）。"""
        if known_only not in self._layout_order:
            adjacency = self.effect_adjacency if known_only else self.adjacency
            self._layout_order[known_only] = layout_numbers(list(self.proteins), self.levels(known_only), adjacency,
                                                            self.names)
        return self._layout_order[known_only]

    def elements(self, sub: Subgraph, known_only: bool = False) -> list[dict]:
        elements = []
        frame = self.frames(sub)
        for name in sorted(set(frame.values())):
            elements.append({"data": {"id": f"c:{name}", "label": name, "isComplex": True,
                                      "color": self.category_color(name)}})
        # 下流が多すぎる遺伝子のまとめ: 制御の種類ごとの枠に入れ、線はふだん隠す（緑で選ぶ・枠を押すと見せる）
        bundle_of: dict[int, str] = {}
        for hub, genes in sorted(sub.bundles.items()):
            by_type: dict[str, list[int]] = {}
            for g in genes:
                by_type.setdefault(self.bundle_type(hub, g), []).append(g)
            for t, gs in sorted(by_type.items()):
                bid = f"b:{hub}:{t}"
                info = INTERACTION_TYPES.get(t)
                elements.append({"data": {"id": bid, "isComplex": True, "isBundle": True, "hub": f"p{hub}",
                                          "label": f"{self.names[hub]} の下流 {_short_type(t)} {len(gs)}",
                                          "color": info[2] if info else "#9e9e9e"}})
                for g in gs:
                    bundle_of[g] = bid
        # パラログの枠（組ごとの色の二重線。複合体に入っていない組だけ。まとめの中の組は、まとめの枠の中に入れ子にする）
        paralog = self.paralog_frames(sub, set(frame), bundle_of)
        for fid in sorted(set(paralog.values())):
            first = fid[2:].split("・")[0]   # 組がつながったときは、名前順で最初の組の色
            data = {"id": fid, "isComplex": True, "isParalog": True,
                    "label": fid[2:], "color": self.category_color(first) if first in self.categories else PARALOG_COLOR}
            inside = {bundle_of.get(g) for g, f in paralog.items() if f == fid}
            if inside != {None}:
                data["parent"] = inside.pop()
            elements.append({"data": data})
        for pid in sorted(sub.nodes):
            p = self.proteins[pid]
            data = {
                "id": f"p{p.id}",
                "label": p.gene_name,
                "gene": p.gene_name,
                "category": p.complex_category,
                "color": self.category_color(p.complex_category),
                "role": self.roles[pid],
                "roleColor": ROLE_INFO[self.roles[pid]][1],
                "level": self.levels(known_only).get(pid),
                "order": self.layout_order(known_only)[pid],
                "depth": sub.down_dist.get(pid, 0),
                "more": sub.hidden_down.get(pid, 0),
                "txReg": pid in sub.tx_regs,
                "seed": pid in sub.seeds,
                "pinned": pid in sub.pinned,
                "focus": pid in sub.focus,
                "grown": pid in sub.grown,
            }
            mates = sorted({g for genes in self.paralogs.values() if pid in genes for g in genes} - {pid} & sub.nodes)
            if mates:
                data["paralogs"] = [f"p{g}" for g in mates]   # 地図にあるパラログの相手（条件で薄くするとき、線のない方は相手に合わせる）
            if pid in bundle_of:
                data["parent"] = paralog.get(pid, bundle_of[pid])
                data["bundled"] = True
            elif pid in frame:
                data["parent"] = f"c:{frame[pid]}"
            elif pid in paralog:
                data["parent"] = paralog[pid]
            elements.append({"data": data})
        grouped: dict[tuple, list[Interaction]] = {}   # 複合体の名前で書かれた文献の関係（複合体 → 相手の組ごと）
        for it in self.interactions.values():
            if self._edge_shown(it, sub, known_only, tf_pairs=True):
                marks = complex_groups.parse(it.evidence)
                if marks:
                    label, side = marks[0]
                    end = it.target_id if side == "上流" else it.source_id
                    grouped.setdefault((label, side, end, it.interaction_type), []).append(it)
                    continue
                data = self.edge_data(it)
                if self.tf_pair_hidden(it, sub):
                    data["tfPair"] = True   # ふだんは隠し、どちらかの端を緑で選んだときだけ見せる
                # まとめの起点からまとめの遺伝子への線と、同じ起点のまとめの中の遺伝子どうしの線は隠す
                # （枠を押す・緑で選ぶと見せる）。まとめの遺伝子と地図のほかの遺伝子との線はふつうに描く
                if self._bundle_edge(it.source_id, it.target_id, sub):
                    data["tfPair"] = True
                    data["bundle"] = bundle_of[it.target_id]
                    # 起点からまとめへの線は、起点を緑で選んでも出さない（まとめの遺伝子を選んだとき・枠を押したときは出す）
                    if it.source_id in sub.bundles:
                        data["hubEdge"] = True
                elif it.source_id in frame and frame.get(it.target_id) == frame[it.source_id]:
                    # 同じ複合体の枠の中どうしの線も、まとめと同じくふだん隠す（緑で選ぶ・枠を押すと見せる）
                    data["tfPair"] = True
                    data["frame"] = f"c:{frame[it.source_id]}"
                elements.append({"data": data})
        elements += self._group_edges(grouped, frame)
        return elements

    def _group_edges(self, grouped: dict[tuple, list[Interaction]], frame: dict[int, str]) -> list[dict]:
        """複合体の名前で書かれた文献の関係（tor_app/complex_groups.py）。構成遺伝子ごとの関係を、複合体の側の遺伝子が
        いちばん多く入っている地図の枠から 1 本の線にまとめる（id は代表の関係。説明欄はその根拠を出す）。
        複合体には型違い（TORC1 の TOR1 型・TOR2 型など）があり、1 つの遺伝子は 1 つの枠にしか入らないので、
        枠の外の構成遺伝子（TOR2 など）の線もこの 1 本に含める。どの構成遺伝子も枠に入っていなければ、遺伝子ごとの線のまま描く。
        どちらも data の pairs に元の遺伝子の組を持たせる（経路の強調に使う）"""
        out = []
        for (label, side, end, _), its in sorted(grouped.items(), key=lambda kv: kv[0][:3]):
            ends = [it.source_id if side == "上流" else it.target_id for it in its]
            counts: dict[str, int] = {}
            for g in ends:
                if frame.get(g):
                    counts[frame[g]] = counts.get(frame[g], 0) + 1
            pairs = [f"p{it.source_id}>p{it.target_id}" for it in its]
            if counts:
                best = max(sorted(counts), key=lambda f: counts[f])
                data = self.edge_data(min(its, key=lambda it: it.id))
                data["source" if side == "上流" else "target"] = f"c:{best}"
                data.update(group=label, pairs=pairs, groupEdges=[f"e{it.id}" for it in its])
                out.append({"data": data})
            else:
                for it in its:
                    data = self.edge_data(it)
                    data.update(group=label, pairs=[f"p{it.source_id}>p{it.target_id}"])
                    out.append({"data": data})
        return out

    def _all_types(self) -> list[str]:
        order = list(INTERACTION_TYPES)
        return sorted({it.interaction_type for it in self.interactions.values()},
                      key=lambda t: order.index(t) if t in order else len(order))

    def legend(self, sub: Subgraph, known_only: bool = False) -> dict:
        shown = [it for it in self.interactions.values() if self._edge_shown(it, sub, known_only)]
        used_types = sorted({it.interaction_type for it in shown},
                            key=lambda t: list(INTERACTION_TYPES).index(t) if t in INTERACTION_TYPES else 99)
        used_categories = {self.proteins[n].complex_category for n in sub.nodes}
        roles = [key for key in ROLE_INFO if any(self.roles[n] == key for n in sub.nodes)]
        return {
            "categories": [{"name": c.name, "color": c.color, "isComplex": c.is_complex}
                           for c in self.categories.values() if c.name in used_categories],
            "roles": [{"key": k, "name": ROLE_INFO[k][0], "color": ROLE_INFO[k][1]} for k in roles],
            # DB にあるすべての種類を並べ、今の表示に出ていないものは薄く表示する（表示する前に絞り込めるように）
            "types": _type_legend(self._all_types(), set(used_types)),
        }


def network_levels(known: list[tuple[int, int]], unknown: list[tuple[int, int]],
                   group: dict[int, str] | None = None) -> dict[int, int]:
    """ネットワーク全体での階層（0 始まり）。制御する側ができるだけ、制御される側より上の階層に来るようにする。

    - 複合体の構成要素（group）と、直接互いに作用し合う組（A⇄B）は 1 つのまとまりとして同じ階層にする。
      ただし A⇄B のうち片方の向きだけ作用が分かっている組はまとめず、分かっている向きで上下を決める。
    - それ以外の循環は、上向きになる関係ができるだけ少なくなるよう一部の関係を例外にして断つ。
      まず作用の向きが分かっている関係（known）だけで上下の順序を決め、次に向き不明の関係（unknown）を、
      その順序と矛盾しない（循環を作らない）ものだけ加える。向きの分かる関係が向き不明の関係に押し負けないようにするため。
    - 上流を持たないものを 0 とし、最も長い経路の段数で数える。向きのある関係を持たないものは含まない。
    """
    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for member, rep_name in (group or {}).items():
        parent[find(member)] = find(rep_name)
    known_set = set(known)
    pairs = known_set | set(unknown)
    for a, b in pairs:
        if (b, a) in pairs and ((a, b) in known_set) == ((b, a) in known_set):
            parent[find(a)] = find(b)

    known_graph = nx.DiGraph()
    known_graph.add_edges_from((find(a), find(b)) for a, b in known if find(a) != find(b))
    order = _feedback_order(known_graph)
    dag = nx.DiGraph((a, b) for a, b in known_graph.edges if order[a] < order[b])
    dag.add_nodes_from(known_graph)
    for a, b in unknown:
        ga, gb = find(a), find(b)
        if ga == gb or dag.has_edge(ga, gb):
            continue
        if ga in dag and gb in dag and nx.has_path(dag, gb, ga):
            continue   # 循環を作る関係は例外にする
        dag.add_edge(ga, gb)
    if not dag:
        return {}
    depth: dict = {}
    for n in nx.topological_sort(dag):
        preds = list(dag.predecessors(n))
        depth[n] = max(depth[p] for p in preds) + 1 if preds else 0
    nodes = {x for e in pairs for x in e} | set(group or {})
    return {n: depth[find(n)] for n in nodes if not isinstance(n, str) and find(n) in depth}


def layout_numbers(ids: list[int], levels: dict[int, int], adjacency: Adjacency, names: dict[int, str],
                   sweeps: int = 8) -> dict[int, int]:
    """全遺伝子の配置の番号。階層の上から順に、同じ階層の中では左から順に 1, 2, 3, … と振る。

    同じ階層の中の順序は、線の交差が少なくなるよう重心法で決める: まず名前順に並べ、上流（下向きの回）・
    下流（上向きの回）の相手の位置（階層の中での割合 0〜1）の平均の順に並べ替えることを交互に sweeps 回くり返す。
    平均が同じものは名前順。入力が同じなら必ず同じ番号になる。階層のない遺伝子は一番下の階層として扱う。
    """
    bottom = max(levels.values(), default=-1) + 1
    level = {n: levels.get(n, bottom) for n in ids}
    key_name = lambda n: (names.get(n, ""), n)   # noqa: E731
    rows: dict[int, list[int]] = {}
    for n in sorted(ids, key=key_name):
        rows.setdefault(level[n], []).append(n)
    pos: dict[int, float] = {}

    def place(row: list[int]) -> None:
        for i, n in enumerate(row):
            pos[n] = (i + 0.5) / len(row)

    for row in rows.values():
        place(row)
    order = sorted(rows)
    for sweep in range(sweeps):
        down = sweep % 2 == 0
        neighbors = adjacency.inc if down else adjacency.out
        for lv in (order[1:] if down else order[-2::-1]):
            def barycenter(n, lv=lv):
                ps = [pos[m] for m in neighbors.get(n, ()) if m in pos and level[m] != lv]
                return sum(ps) / len(ps) if ps else pos[n]
            rows[lv] = sorted(rows[lv], key=lambda n: (barycenter(n), key_name(n)))
            place(rows[lv])
    numbers: dict[int, int] = {}
    for lv in order:
        for n in rows[lv]:
            numbers[n] = len(numbers) + 1
    return numbers


def _feedback_order(graph: nx.DiGraph) -> dict:
    """循環のあるグラフで、後ろ向きになる関係が少なくなる並び順（Eades–Lin–Smyth の貪欲法）。"""
    rest = graph.copy()
    head, tail = [], []
    while rest:
        changed = True
        while changed:
            changed = False
            for n in [n for n in rest if rest.out_degree(n) == 0]:
                tail.append(n)
                rest.remove_node(n)
                changed = True
            for n in [n for n in rest if rest.in_degree(n) == 0]:
                head.append(n)
                rest.remove_node(n)
                changed = True
        if rest:
            n = max(rest, key=lambda x: rest.out_degree(x) - rest.in_degree(x))
            head.append(n)
            rest.remove_node(n)
    return {n: i for i, n in enumerate(head + tail[::-1])}


# 作用だけ分かっていて仕組みが不明な関係は、凡例の「制御・修飾の種類」では 1 行にまとめる
# （促進か抑制かは作用の欄で示すので、リン酸化などの仕組みと同列には並べない）
_MECHANISM_UNKNOWN = ("activation", "inhibition")


def _short_type(interaction_type: str) -> str:
    """まとめの枠の名札に使う種類の名前（長い補足の括弧は外す: 酵素活性（修飾なし・作用は不明） → 酵素活性）。"""
    label = type_label(interaction_type)
    return re.sub(r"（.*?）", "", label) if len(label) > 10 else label


def _type_legend(all_types: list[str], shown: set[str]) -> list[dict]:
    rows = []
    unknown = [t for t in all_types if t in _MECHANISM_UNKNOWN]
    if unknown:
        rows.append({"key": "|".join(unknown), "label": "機構不明", "symbol": "—",
                     "color": INTERACTION_TYPES["activation"][2], "shown": bool(shown & set(unknown))})
    for t in all_types:
        if t not in _MECHANISM_UNKNOWN:
            info = INTERACTION_TYPES.get(t, ("", "", "#9e9e9e", t[:3]))
            rows.append({"key": t, "label": type_label(t), "symbol": info[3], "color": info[2], "shown": t in shown})
    return rows
