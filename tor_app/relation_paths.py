"""経路タブの経路の一覧（左の「経路」タブ）。チェックした遺伝子どうしが、どの経路でどう関わるかを 1 本ずつ並べる。

- 経路は段数がちょうど n 段（1〜3）のものだけ。経路の途中で同じ遺伝子を 2 度通らない。
- 地図の点線の枠の複合体（TORC1・PKA など）は 1 つの単位として扱う（複合体の中の受け渡しは段数に数えない）。
- 転写制御もたどる（A ← MSN2 → B のような、共通の転写因子によるつながりも見つける）。
- 経路の 1 行は上流（階層が上）を左に書く。行の並びは、左端・右端の単位の順（階層、同じ階層なら地図の配置の番号）。
  同じ両端の経路の中では、途中の単位の関係の数（ハブ）が少ない経路を先にする。根拠の確かさは並べ方に使わない。
  一覧の本数・共通の制御因子の数に上限は設けない（同じ両端の経路が ENUM_CAP 本に達したら、そこで数えるのをやめる）。

表示経路ごとの経路（基準の遺伝子を選んでいれば、基準の遺伝子が端にある経路だけ）:
- all: チェックした遺伝子どうしを結ぶ経路。各段の矢印の向きは問わない（A → X ← B のような共通の標的を経由する
  経路も含む）。左は階層の上の端。
- direct: チェックした遺伝子から別のチェックした遺伝子へ、矢印の向きにたどれる経路（途中はどの遺伝子でもよい）。
- regulators: チェックした遺伝子すべてに、ちょうど n 段で届く上流の単位（共通の制御因子）から、それぞれへの経路。
- targets: チェックした遺伝子すべてから、ちょうど n 段で届く下流の単位（共通の標的）への経路。
- toward / from: ほかのチェックした遺伝子から基準の遺伝子へ（基準の遺伝子から、ほかへ）の経路。
"""
from dataclasses import dataclass, field

from .subgraph import Adjacency

ENUM_CAP = 5000    # 同じ両端の経路を数える上限（計算が終わらなくなるのを防ぐ安全のため）
# 一覧全体の経路の本数の上限。ハブ（MSN2・CDC28 など）を選んで 3 段にすると百万本を超え、計算と一覧で
# 画面が十秒以上止まるため、ここで打ち切る（共通の制御因子・標的の数は打ち切らずに数える）
TOTAL_CAP = 20000


@dataclass
class PathRow:
    nodes: list[int]        # 単位（左 = 上流 → 右）
    forward: list[bool]     # 段ごとに、矢印が左から右へ向くか


@dataclass
class PairGroup:
    left: int
    right: int
    paths: list[PathRow]
    total: int              # 見つかった本数（ENUM_CAP で打ち切ったら ENUM_CAP）


@dataclass
class Result:
    groups: list[PairGroup] = field(default_factory=list)
    common: list[int] = field(default_factory=list)       # 共通の制御因子・標的（regulators / targets）
    missing: list[int] = field(default_factory=list)      # 経路の見つからなかったチェックした単位
    capped: int = 0                                       # 経路が ENUM_CAP 本に達して数えるのをやめた両端の組の数
    truncated: bool = False                               # 一覧全体が TOTAL_CAP 本に達して、残りの組を出さなかったか

    def rows(self) -> list[PathRow]:
        return [p for g in self.groups for p in g.paths]


class UnitGraph:
    """遺伝子の関係を、複合体を 1 つの単位にまとめたグラフ。単位の番号は、遺伝子ならその遺伝子の番号、複合体なら負の数。"""

    def __init__(self, adjacency: Adjacency, complexes: dict[str, list[int]], names: dict[int, str]):
        self.gene = adjacency
        self.unit_of: dict[int, int] = {}
        self.members: dict[int, list[int]] = {}
        self.names: dict[int, str] = dict(names)
        for i, (name, genes) in enumerate(sorted(complexes.items())):
            uid = -(i + 1)
            self.members[uid] = sorted(genes)
            self.names[uid] = name
            for g in genes:
                self.unit_of[g] = uid
        self.out: dict[int, set[int]] = {}
        self.inc: dict[int, set[int]] = {}
        for a, targets in adjacency.out.items():
            ua = self.unit(a)
            for b in targets:
                ub = self.unit(b)
                if ua != ub:
                    self.out.setdefault(ua, set()).add(ub)
                    self.inc.setdefault(ub, set()).add(ua)

    def unit(self, gene: int) -> int:
        return self.unit_of.get(gene, gene)

    def genes(self, unit: int) -> list[int]:
        return self.members.get(unit, [unit])

    def degree(self, unit: int) -> int:
        return len(self.out.get(unit, ())) + len(self.inc.get(unit, ()))

    def gene_edges(self, a: int, b: int) -> list[tuple[int, int]]:
        """単位 a → b の関係（遺伝子の組）。"""
        return [(x, y) for x in self.genes(a) for y in self.gene.out.get(x, ()) if self.unit(y) == b]

    def label(self, unit: int) -> str:
        return self.names.get(unit, str(unit))


def order_keys(graph: UnitGraph, levels: dict[int, int], layout: dict[int, int],
               tx: Adjacency | None = None) -> dict[int, tuple]:
    """単位の並び順のキー（階層, 配置の番号）。階層のない遺伝子（転写制御だけを受けるものなど）は、
    制御している転写因子の階層の 1 つ下とする。それもなければ一番下。複合体は構成要素のいちばん上。"""
    bottom = max(levels.values(), default=-1) + 1
    big = max(layout.values(), default=0) + 1

    def gene_level(g: int) -> int:
        if g in levels:
            return levels[g]
        regs = [levels[r] for r in (tx.inc.get(g, ()) if tx else ()) if r in levels]
        return max(regs) + 1 if regs else bottom

    keys = {}
    units = set(graph.out) | set(graph.inc) | set(graph.members)
    for u in units:
        keys[u] = min((gene_level(g), layout.get(g, big)) for g in graph.genes(u))
    return keys


def _directed(graph: UnitGraph, s: int, t: int, n: int, cap: int) -> tuple[list[PathRow], int]:
    """s から t へ、矢印の向きにちょうど n 段の経路。"""
    out, inc = graph.out, graph.inc
    found: list[list[int]] = []
    if n == 1:
        if t in out.get(s, ()):
            found.append([s, t])
    elif n == 2:
        found = [[s, x, t] for x in out.get(s, set()) & inc.get(t, set()) if x not in (s, t)]
    else:
        into_t = inc.get(t, set())
        for x in out.get(s, ()):
            if x == t:
                continue
            for y in out.get(x, set()) & into_t:
                if y not in (s, t, x):
                    found.append([s, x, y, t])
                    if len(found) >= cap:
                        break
            if len(found) >= cap:
                break
    return [PathRow(p, [True] * n) for p in found[:cap]], min(len(found), cap)


def _undirected(graph: UnitGraph, s: int, t: int, n: int, cap: int) -> tuple[list[PathRow], int]:
    """s と t を結ぶ、ちょうど n 段の経路（各段の矢印の向きは問わない。A → X ← B のような共通の標的を経由する経路も含む）。"""
    out, inc = graph.out, graph.inc

    def nb(x):
        return out.get(x, set()) | inc.get(x, set())

    candidates: list[list[int]] = []
    if n == 1:
        if t in nb(s):
            candidates.append([s, t])
    elif n == 2:
        candidates = [[s, x, t] for x in nb(s) & nb(t) if x not in (s, t)]
    else:
        near_t = nb(t)
        for x in nb(s):
            if x == t:
                continue
            for y in nb(x) & near_t:
                if y not in (s, t, x):
                    candidates.append([s, x, y, t])
            if len(candidates) >= cap:
                break
    # 段ごとの向き: 左から右への矢印があればそれ、なければ右から左（両方あれば左から右を表示する）
    rows = [PathRow(p, [b in out.get(a, ()) for a, b in zip(p, p[1:])]) for p in candidates[:cap]]
    return rows, len(rows)


def _exact_reach(graph: UnitGraph, start: int, n: int, upstream: bool) -> set[int]:
    """start からちょうど n 段で届く単位（upstream なら上流へさかのぼる）。同じ単位を 2 度通る道も含む（あとで確かめる）。"""
    step = graph.inc if upstream else graph.out
    frontier = {start}
    for _ in range(n):
        frontier = set().union(*(step.get(x, set()) for x in frontier)) if frontier else set()
    return frontier - {start}


def _flip(row: PathRow) -> PathRow:
    return PathRow(row.nodes[::-1], [not d for d in row.forward[::-1]])


def find(graph: UnitGraph, chosen: list[int], mode: str, n: int, anchor: int | None,
         keys: dict[int, tuple]) -> Result:
    """chosen はチェックした遺伝子（単位にまとめる）、anchor は基準の遺伝子（なければ None）。"""
    units = list(dict.fromkeys(graph.unit(g) for g in chosen))
    anchor_u = graph.unit(anchor) if anchor is not None else None
    big = (10 ** 9, 10 ** 9)
    key = lambda u: keys.get(u, big)   # noqa: E731
    result = Result()
    # 両端の組 → (経路, 本数)
    pairs: dict[tuple[int, int], tuple[list[PathRow], int]] = {}

    def full() -> bool:
        """これまでの組の経路が TOTAL_CAP 本に達したか（達したら、残りの組は探さない）。"""
        if sum(total for _, total in pairs.values()) >= TOTAL_CAP:
            result.truncated = True
            return True
        return False

    if mode in ("all", "direct"):
        for i, a in enumerate(units):
            for b in units[i + 1:]:
                if anchor_u is not None and anchor_u not in (a, b):
                    continue
                if full():
                    break
                if mode == "all":
                    left, right = sorted((a, b), key=key)
                    pairs[(left, right)] = _undirected(graph, left, right, n, ENUM_CAP)
                else:
                    for s, t in ((a, b), (b, a)):
                        pairs[(s, t)] = _directed(graph, s, t, n, ENUM_CAP)
    elif mode in ("toward", "from"):
        if anchor_u is None:
            return result
        for a in units:
            if a == anchor_u:
                continue
            if full():
                break
            s, t = (a, anchor_u) if mode == "toward" else (anchor_u, a)
            pairs[(s, t)] = _directed(graph, s, t, n, ENUM_CAP)
    elif mode in ("regulators", "targets"):
        upstream = mode == "regulators"
        if not units:
            return result
        common = set.intersection(*(_exact_reach(graph, u, n, upstream) for u in units)) - set(units)
        ends = [anchor_u] if anchor_u is not None else units
        listed = 0
        for x in sorted(common, key=key):
            # 共通かどうかは 1 本あれば分かる（経路を全部数えるのは、一覧に出す分だけ）
            if not all(_directed(graph, *((x, u) if upstream else (u, x)), n, 1)[1] for u in units):
                continue   # 同じ単位を 2 度通る道でしか届かない
            result.common.append(x)
            if listed >= TOTAL_CAP:
                result.truncated = True
                continue
            for u in ends:
                s, t = (x, u) if upstream else (u, x)
                pairs[(s, t)] = _directed(graph, s, t, n, ENUM_CAP)
                listed += pairs[(s, t)][1]
    else:
        return result

    reached = set()
    for (s, t), (rows, total) in pairs.items():
        if total:
            reached |= {s, t}
    if mode not in ("regulators", "targets"):
        result.missing = [u for u in units if u not in reached and (anchor_u is None or u != anchor_u)]

    listed = 0
    for (s, t) in sorted(pairs, key=lambda p: (key(p[0]), key(p[1]))):
        rows, total = pairs[(s, t)]
        if not total:
            continue
        if listed >= TOTAL_CAP:   # 一覧が多すぎる: 残りの組は出さない（並べ替えもしない）
            result.truncated = True
            continue
        listed += len(rows)
        rows = sorted(rows, key=lambda r: (sum(graph.degree(x) for x in r.nodes[1:-1]),
                                           [key(x) for x in r.nodes[1:-1]]))
        result.capped += total >= ENUM_CAP
        result.groups.append(PairGroup(s, t, rows, total))
    return result


def through(result: Result, unit: int) -> list[tuple[int, int]]:
    """unit を通る経路の位置（グループの番号, 経路の番号）。"""
    return [(gi, pi) for gi, g in enumerate(result.groups) for pi, p in enumerate(g.paths) if unit in p.nodes]


# 段の記号: 作用 → (左から右へ向くとき, 右から左へ向くとき)。◇ は作用不明（地図の白抜きの菱形）
ARROWS = {"activate": ("→", "←"), "inhibit": ("⊣", "⊢"), "none": ("◇", "◇")}


def row_text(graph: UnitGraph, row: PathRow, edge_marks: dict[tuple[int, int], set[tuple[str, str]]]) -> str:
    """経路 1 本の表示（例: TORC1 –P→ SCH9 –P⊣ MAF1）。edge_marks は遺伝子の組 → {(線上の記号, 作用)}。"""
    text = graph.label(row.nodes[0])
    for a, b, forward in zip(row.nodes, row.nodes[1:], row.forward):
        pairs = graph.gene_edges(a, b) if forward else graph.gene_edges(b, a)
        # 仕組み不明の関係（線上の記号 —）は記号を付けない
        marks = sorted({(sym if sym != "—" else "", eff) for e in pairs for sym, eff in edge_marks.get(e, ())})
        if forward:
            step = " –" + "/".join(f"{sym}{ARROWS[eff][0]}" for sym, eff in marks) + " "
        else:
            step = " " + "/".join(f"{ARROWS[eff][1]}{sym}" for sym, eff in marks) + "– "
        text += step + graph.label(b)
    return text
