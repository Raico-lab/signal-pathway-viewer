"""注目する遺伝子の周辺だけを切り出す（全遺伝子規模でも描画を軽く保つため）。

- 起点: 注目した遺伝子。同じ複合体の構成要素は、直接相互作用する遺伝子と同じように表示する（そこから先はたどらない）。
- 起点から上流へ up 段、下流へ down 段たどる。
- 上流へたどるとき、1 つの遺伝子の上流が cap を超える場合は cap 件だけ含め、残りは「隠れている数」として表示する。
- 下流へたどるとき、1 つの遺伝子の下流が down_cap を超える場合（PHO85 のリン酸化の標的など）は、下流をすべて
  「まとめ」（bundles）として加える（線で描く下流を選ばない）。まとめの遺伝子はそこから先をたどらず、
  地図では制御の種類ごとの枠に並べて線を隠す。展開 (expand) した遺伝子は制限しない。
"""
from dataclasses import dataclass, field, replace

DEFAULT_CAP = 30         # 上流の上限
DOWN_CAP = 50            # 下流の上限（超えたら下流をすべてまとめの枠に入れる）


@dataclass
class ViewSpec:
    focus: list[int]
    up: int = 1
    down: int = 2
    extra: set[int] = field(default_factory=set)      # 展開で追加した遺伝子
    uncapped: set[int] = field(default_factory=set)   # 件数制限を外した遺伝子
    excluded: set[int] = field(default_factory=set)   # 隠した段の遺伝子（注目している遺伝子は除外しない）
    grown: set[int] = field(default_factory=set)      # 2 回目のクリックで上流・下流を追加した起点の遺伝子
    trf: set[int] = field(default_factory=set)        # 左の TFs 一覧でチェックした転写因子（一番上の段に表示する）
    cap: int = DEFAULT_CAP
    down_cap: int = DOWN_CAP


@dataclass
class Subgraph:
    nodes: set[int]
    seeds: set[int]
    down_dist: dict[int, int]     # 起点からの下流方向の段数（縮小時に枝先を隠すのに使う）
    hidden_down: dict[int, int]   # 表示されていない下流の数
    hidden_up: dict[int, int]     # 表示されていない上流の数
    pinned: set[int] = field(default_factory=set)   # 追加で表示した遺伝子（縮小しても省略しない）
    focus: set[int] = field(default_factory=set)    # 注目している遺伝子（複合体の他の構成要素は含まない）
    grown: set[int] = field(default_factory=set)    # 上流・下流を追加した起点の遺伝子
    tx_regs: set[int] = field(default_factory=set)  # TFs として加えたもの（一番上の専用の段に並べる）
    # 地図に描かない線（転写因子どうし）のうち、例外として描くもの（経路タブの経路）
    allow: set[tuple[int, int]] = field(default_factory=set)
    # 下流が多すぎる遺伝子 → その下流（枠にまとめ、線を隠して並べる）
    bundles: dict[int, set[int]] = field(default_factory=dict)


class Adjacency:
    def __init__(self, edges: list[tuple[int, int]]):
        self.out: dict[int, set[int]] = {}
        self.inc: dict[int, set[int]] = {}
        for s, t in edges:
            if s == t:
                continue
            self.out.setdefault(s, set()).add(t)
            self.inc.setdefault(t, set()).add(s)


def _walk(adj: dict[int, set[int]], starts: set[int], depth: int,
          spec: ViewSpec, names: dict[int, str], nodes: set[int],
          overflow: dict[int, set[int]] | None = None) -> None:
    """overflow を渡すと（下流へたどるとき）、先が down_cap を超える遺伝子の先をすべて overflow に入れ、たどらない。
    渡さないとき（上流へたどるとき）は、先が cap を超える遺伝子の先を cap 件だけたどる。"""
    frontier = set(starts)
    for _ in range(depth):
        nxt = set()
        for n in frontier:
            neighbors = adj.get(n, set())
            if n in spec.uncapped:
                pass
            elif overflow is not None:
                if len(neighbors) > spec.down_cap:
                    overflow.setdefault(n, set()).update(neighbors)
                    neighbors = set()
            elif len(neighbors) > spec.cap:
                # 先がさらに続く遺伝子（シグナル伝達の中継）を優先し、残りは名前順
                ranked = sorted(neighbors, key=lambda x: (not adj.get(x), names.get(x, "")))
                neighbors = ranked[:spec.cap]
            for m in neighbors:
                if m not in nodes:
                    nodes.add(m)
                    nxt.add(m)
        frontier = nxt


def _distances(adj: dict[int, set[int]], seeds: set[int], nodes: set[int]) -> dict[int, int]:
    """seeds から adj の向きにたどったときの段数（nodes の中だけ）。"""
    dist = {s: 0 for s in seeds}
    frontier = list(seeds)
    while frontier:
        nxt = []
        for n in frontier:
            for m in adj.get(n, ()):
                if m in nodes and m not in dist:
                    dist[m] = dist[n] + 1
                    nxt.append(m)
        frontier = nxt
    return dist


def build_subgraph(adjacency: Adjacency, complexes: dict[str, list[int]], names: dict[int, str],
                   spec: ViewSpec, paralogs: dict[str, list[int]] | None = None) -> Subgraph:
    member_of = {m: members for members in complexes.values() for m in members}
    seeds = set(spec.focus)
    nodes = set(seeds)
    overflow: dict[int, set[int]] = {}
    _walk(adjacency.out, seeds, spec.down, spec, names, nodes, overflow)
    _walk(adjacency.inc, seeds, spec.up, spec, names, nodes)
    # 注目した遺伝子と同じ複合体の構成要素も表示する（相互作用する遺伝子と同じ扱い）
    mates = {m for f in spec.focus for m in member_of.get(f, [])}
    nodes.update(mates)
    for n in spec.uncapped:
        if n in nodes or n in spec.extra:
            nodes.update(adjacency.out.get(n, set()))
            nodes.update(adjacency.inc.get(n, set()))
    nodes.update(spec.extra)
    # 左の TFs 一覧でチェックした転写因子（他の関係で既に表示しているものは、その位置のまま）
    tx_regs = spec.trf - nodes
    nodes.update(spec.trf)
    # パラログ: 組の一方が地図にあれば、もう一方も加える（1 回だけ。加えたものの組はたどらない）
    for genes in (paralogs or {}).values():
        if any(g in nodes for g in genes):
            nodes.update(genes)
    # 下流が多すぎて省いた分は、まとめとしてすべて加える（ほかの経路で表示しているものは除く。2 つの遺伝子の
    # まとめに入るものは名前順で先の方だけ）
    bundles: dict[int, set[int]] = {}
    taken = set(nodes)
    for hub in sorted(overflow, key=lambda n: (names.get(n, ""), n)):
        rest = overflow[hub] - taken
        if rest:
            bundles[hub] = rest
            taken |= rest
    nodes = taken
    nodes -= spec.excluded - seeds
    bundles = {h: g & nodes for h, g in bundles.items() if h in nodes and g & nodes}

    # 表示範囲内で、起点から下流方向に何段目か（縮小時に枝先を隠すのに使う）。
    # 注目した遺伝子と同じ複合体の構成要素は起点と同じ 0 段目とし、縮小しても隠さない
    down_reach = _distances(adjacency.out, seeds | (mates & nodes), nodes)
    down_dist = {n: down_reach.get(n, 0) for n in nodes}

    hidden_down = {n: len(adjacency.out.get(n, set()) - nodes) for n in nodes}
    hidden_up = {n: len(adjacency.inc.get(n, set()) - nodes) for n in nodes}
    return Subgraph(nodes, seeds, down_dist, hidden_down, hidden_up, spec.extra & nodes, set(spec.focus) & nodes,
                    spec.grown & nodes, tx_regs & nodes, bundles=bundles)


def grow_targets(adjacency: Adjacency, complexes: dict[str, list[int]], pid: int) -> set[int]:
    """拡張（2 回目のクリック）で加える遺伝子: 直接の上流・下流と、同じ複合体の構成要素。"""
    linked = adjacency.out.get(pid, set()) | adjacency.inc.get(pid, set())
    for members in complexes.values():
        if pid in members:
            linked |= set(members) - {pid}
    return linked - {pid}


def link_path(adjacency: Adjacency, pid: int, shown: set[int], depth: int, cap: int,
              names: dict[int, str]) -> tuple[set[int], list[int], set[int]]:
    """地図にない遺伝子 pid から、shown の遺伝子までの最短の経路（向きは問わず depth 段まで）の途中の遺伝子。
    多くの経路が通るものを優先して cap 個まで選ぶ。返り値: (届いた shown の遺伝子, 途中の遺伝子すべて, 選んだもの)"""
    hits, paths = path_nodes(adjacency, pid, shown, depth)
    middle = sorted((n for n in paths if n not in shown and n != pid), key=lambda n: (-paths[n], names.get(n, "")))
    return hits, middle, set(middle[:cap])


def regrow(adjacency: Adjacency, complexes: dict[str, list[int]], names: dict[int, str], spec: ViewSpec,
           link_depth: int, link_cap: int, paralogs: dict[str, list[int]] | None = None) -> dict[int, set[int]]:
    """注目・段数・TFs と拡張の起点（spec.grown）だけから、拡張で加える遺伝子を決め直す（再配置で使う）。
    操作の順序によらず、同じ設定なら同じ結果になる。返り値: 拡張の起点 → その起点で加えた遺伝子。

    - 地図にある起点は、直接の上流・下流と同じ複合体の構成要素を加える（加えた中に別の起点があれば、それも広げる）。
    - 地図にない起点（検索欄から拡張したもの）は、その遺伝子と、地図につながる経路の途中の遺伝子だけを加える。
    """
    base = build_subgraph(adjacency, complexes, names, replace(spec, extra=set(), uncapped=set(), excluded=set()), paralogs)
    nodes = set(base.nodes)
    order = sorted(spec.grown, key=lambda n: (names.get(n, ""), n))
    added: dict[int, set[int]] = {}
    while len(added) < len(order):
        ready = [g for g in order if g in nodes and g not in added]
        for g in ready:
            added[g] = grow_targets(adjacency, complexes, g) - nodes
            nodes |= added[g]
        if ready:
            continue
        g = next(g for g in order if g not in added)
        _, _, chosen = link_path(adjacency, g, nodes, link_depth, link_cap, names)
        added[g] = ({g} | chosen) - nodes
        nodes |= added[g]
    return added


def path_nodes(adjacency: Adjacency, start: int, targets: set[int], max_depth: int,
               no_collider: bool = False, direction: str = "both") -> tuple[set[int], dict[int, int]]:
    """start から targets のどれかまでの最短の経路（向きは問わず max_depth 段まで）をすべて求める。
    返り値: (届いた targets, 経路上の遺伝子 → その遺伝子を通る経路の本数)。経路の途中で targets は通らない。

    no_collider=True なら、途中の遺伝子が「制御を受けているだけ」になる経路（A → X ← B のように、経路の
    両側から矢印が向かってくるだけの X）を使わない。途中の遺伝子は、経路上の次の遺伝子を制御する側でなければならない。
    direction="out" なら矢印の向き（制御する → される）にだけ、"in" なら逆向きにだけたどる。
    """
    # 状態 = (遺伝子, 直前の関係の矢印がこの遺伝子に向かってきたか)
    def moves(state):
        n, into = state
        if direction != "in":
            for m in adjacency.out.get(n, ()):
                yield (m, True)
        if direction != "out" and not (no_collider and into):   # 制御を受けて来た遺伝子からは、さらに受ける向きには進めない
            for m in adjacency.inc.get(n, ()):
                yield (m, False)

    root = (start, False)
    parents: dict[tuple, set[tuple]] = {root: set()}
    seen_nodes = {start}
    frontier, hits = {root}, set()
    for _ in range(max_depth):
        nxt: dict[tuple, set[tuple]] = {}
        for st in frontier:
            for mv in moves(st):
                if mv[0] in seen_nodes or mv in parents:
                    continue
                nxt.setdefault(mv, set()).add(st)
        parents.update(nxt)
        seen_nodes |= {mv[0] for mv in nxt}
        hit_states = {mv for mv in nxt if mv[0] in targets}
        if hit_states:
            hits = {mv[0] for mv in hit_states}
            break
        frontier = {mv for mv in nxt if mv[0] not in targets}
    else:
        hit_states = set()
    # 届いた状態から start へさかのぼり、途中の遺伝子が何本の経路に乗るか数える
    counts_st: dict[tuple, int] = {h: 1 for h in hit_states}
    layer = set(hit_states)
    while layer:
        prev: dict[tuple, int] = {}
        for st in layer:
            for q in parents.get(st, ()):
                prev[q] = prev.get(q, 0) + counts_st[st]
        for q, c in prev.items():
            counts_st[q] = counts_st.get(q, 0) + c
        layer = set(prev) - {root}
    counts: dict[int, int] = {}
    for (n, _), c in counts_st.items():
        counts[n] = counts.get(n, 0) + c
    return hits, counts


def range_paths(adjacency: Adjacency, start: int, targets: set[int], lo: int, hi: int, direction: str = "both",
                no_collider: bool = False, via: int | None = None
                ) -> tuple[set[int], dict[int, int], set[tuple[int, int]]]:
    """start から targets のどれかまでの、長さが lo〜hi 段の経路をすべて求める（最短に限らない）。
    たとえば lo=2 なら、直接の関係（1 段）だけの経路は含めない。経路の途中で start と targets は通らない。
    via を指定すると、via を通る経路だけ（via が start・targets なら、そこから出る・そこに着く経路）にする。

    direction・no_collider は path_nodes と同じ。
    返り値: (届いた targets, 遺伝子 → その遺伝子を通る経路の本数, 経路が通る関係 {(制御する側, される側)})
    """
    inf = float("inf")

    def moves(node: int, into: bool):
        if direction != "in":
            for m in adjacency.out.get(node, ()):
                yield m, True, (node, m)
        if direction != "out" and not (no_collider and into):
            for m in adjacency.inc.get(node, ()):
                yield m, False, (m, node)

    def distances(goal: set[int]) -> dict[int, int]:
        """goal までの最短の段数（向き・no_collider の条件を緩めた下限。枝刈りに使う）。"""
        dist = {g: 0 for g in goal}
        frontier = list(goal)
        for d in range(1, hi + 1):
            nxt = []
            for n in frontier:
                back = set()
                if direction != "in":
                    back |= adjacency.inc.get(n, set())
                if direction != "out":
                    back |= adjacency.out.get(n, set())
                for m in back:
                    if m not in dist:
                        dist[m] = d
                        nxt.append(m)
            frontier = nxt
        return dist

    to_target = distances(targets)
    to_via = distances({via}) if via is not None else {}
    via_to_target = to_target.get(via, inf) if via is not None else 0

    def needed(node: int, passed: bool) -> float:
        """ここから経路を完成させるのに最低限必要な段数。"""
        if passed:
            return to_target.get(node, inf)
        return to_via.get(node, inf) + via_to_target

    # 前から: layers[i] = {(遺伝子, 矢印が向かってきたか, via を通ったか): start からそこまでの経路の本数}
    root = (start, False, via is None or start == via)
    layers = [{root: 1}]
    for i in range(1, hi + 1):
        nxt: dict[tuple, int] = {}
        for (node, into, passed), count in layers[-1].items():
            if i > 1 and node in targets:
                continue   # targets に着いたら先へは進まない
            for m, m_into, _ in moves(node, into):
                if m == start:
                    continue
                m_passed = passed or m == via
                if i + needed(m, m_passed) > hi:
                    continue
                state = (m, m_into, m_passed)
                nxt[state] = nxt.get(state, 0) + count
        layers.append(nxt)

    hits: set[int] = set()
    counts: dict[int, int] = {}
    edges: set[tuple[int, int]] = set()
    for length in range(max(lo, 1), hi + 1):
        # 後ろから: ちょうど length 段で targets に着く経路の、残りの部分の本数
        back = {s: 1 for s in layers[length] if s[0] in targets and s[2]}
        if not back:
            continue
        hits |= {s[0] for s in back}
        for s, c in back.items():
            counts[s[0]] = counts.get(s[0], 0) + layers[length][s] * c
        for i in range(length - 1, -1, -1):
            prev: dict[tuple, int] = {}
            for state, count in layers[i].items():
                node, into, passed = state
                if i > 0 and node in targets:
                    continue
                total = 0
                for m, m_into, edge in moves(node, into):
                    c = back.get((m, m_into, passed or m == via), 0)
                    if c:
                        total += c
                        edges.add(edge)
                if total:
                    prev[state] = total
                    counts[node] = counts.get(node, 0) + count * total
            back = prev
    return hits, counts, edges


def range_reach(adjacency: Adjacency, start: int, lo: int, hi: int, direction: str) -> set[int]:
    """start から矢印の向き（"out"）または逆向き（"in"）に、ちょうど lo〜hi 段でたどり着ける遺伝子
    （最短に限らない。start には戻らない）。"""
    adj = adjacency.out if direction == "out" else adjacency.inc
    found: set[int] = set()
    frontier = {start}
    for d in range(1, hi + 1):
        frontier = {m for n in frontier for m in adj.get(n, ()) if m != start}
        if d >= lo:
            found |= frontier
    return found


def reach(adjacency: Adjacency, start: int, max_depth: int, direction: str) -> dict[int, int]:
    """start から矢印の向き（"out"）または逆向き（"in"）に max_depth 段までたどれる遺伝子と、その段数。"""
    adj = adjacency.out if direction == "out" else adjacency.inc
    dist = {start: 0}
    frontier = [start]
    for d in range(1, max_depth + 1):
        nxt = []
        for n in frontier:
            for m in adj.get(n, ()):
                if m not in dist:
                    dist[m] = d
                    nxt.append(m)
        frontier = nxt
    return dist


def ancestors(adjacency: Adjacency, nodes: set[int]) -> set[int]:
    """nodes の上流すべて（シミュレーションで表示中の遺伝子の活性を決めるのに必要な範囲）。"""
    seen = set(nodes)
    stack = list(nodes)
    while stack:
        n = stack.pop()
        for m in adjacency.inc.get(n, ()):
            if m not in seen:
                seen.add(m)
                stack.append(m)
    return seen
