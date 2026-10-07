"""制御遺伝子探索の上位候補を、シミュレーションで壊して（活性 0）・活性化して（活性 1）確かめる。

候補ごとに、候補から観測した遺伝子への経路（作用の向きが分かっている関係で STEPS 段以内）の上にある遺伝子と観測した
遺伝子をシミュレーションの範囲にし、その範囲の外から入ってくる上流は働いていない（0）として固定する。発現と活性を分ける計算（simulation.simulate の
separate=True）で、候補の活性を 1 にしたときと 0 にしたときの差から、観測した遺伝子が増えるか減るかを予測する。
候補が活性化・不活性化のどちらに変わったとすると観測とよく合うかを選び、合った数 − 逆だった数 を観測の数で割った値
（一致度、−1〜1）で並べ直す。
"""
from dataclasses import dataclass, field

from .db.vocab import resolve_effect
from .regulator_search import Candidate, Observation
from .simulation import simulate

STEPS = 4          # 候補から下流へたどる段数
MIN_DELTA = 0.01   # これより小さい差は「予測できない」とする
BOUNDARY = 0.0     # 範囲の外から入ってくる上流の活性

_cache: dict[int, tuple[dict[int, set[int]], dict[int, set[int]]]] = {}


@dataclass
class Check:
    direction: int     # 観測とよく合う向き（+1 活性化 / −1 不活性化 / 0 決まらない）
    agree: int         # 予測と観測が合った数
    disagree: int      # 逆だった数
    silent: int        # 予測できなかった数（候補から届かない・差が小さい）
    genes: list = field(default_factory=list)   # (遺伝子, 観測の種類, 観測した増減, 予測した増減 "up"/"down"/None)

    @property
    def consistency(self) -> float:
        n = self.agree + self.disagree + self.silent
        return (self.agree - self.disagree) / n if n else 0.0


def _graph(model) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    """作用の向きが分かっている関係の 下流 → 上流 の対応（シミュレーションで信号を運ぶのはこれだけ）。"""
    key = id(model)
    if key not in _cache:
        out: dict[int, set[int]] = {}
        inc: dict[int, set[int]] = {}
        for it in model.interactions.values():
            if it.source_id != it.target_id and resolve_effect(it.interaction_type, it.effect) != "none":
                out.setdefault(it.source_id, set()).add(it.target_id)
                inc.setdefault(it.target_id, set()).add(it.source_id)
        _cache.clear()
        _cache[key] = (out, inc)
    return _cache[key]


def check(model, members: list[int], observations: list[Observation], levels: set[str] | None = None) -> Check:
    """members（候補の遺伝子、まとまりなら計算に入る構成要素）を 1 と 0 にしたときの差で、観測を予測する。"""
    out, inc = _graph(model)
    observed = [o for o in observations if o.change != "none"]
    targets = {o.gene for o in observed}

    def distances(starts, nbr) -> dict[int, int]:
        dist = {n: 0 for n in starts}
        frontier = list(starts)
        for d in range(1, STEPS + 1):
            nxt = []
            for u in frontier:
                for v in nbr.get(u, ()):
                    if v not in dist:
                        dist[v] = d
                        nxt.append(v)
            frontier = nxt
        return dist

    # 範囲は、候補から観測した遺伝子への経路（STEPS 段以内）の上にある遺伝子だけにする。広くとると、候補と関係のない
    # 遺伝子（上流がなければ既定で活性 1）の抑制が観測した遺伝子を押さえてしまい、候補の影響が見えなくなるため
    fwd, bwd = distances(members, out), distances(targets, inc)
    nodes = {n for n, d in fwd.items() if n in bwd and d + bwd[n] <= STEPS} | set(members)
    reach = nodes.copy()
    nodes |= targets
    boundary = {u for n in nodes for u in inc.get(n, ()) if u not in nodes}
    # 範囲の外の上流は働いていない（0）とする。既定の活性（上流のない転写因子は 1）にすると、観測した遺伝子がほかの
    # 転写因子だけで飽和し、候補の影響が見えなくなるため
    fixed = {b: BOUNDARY for b in boundary}
    net = model.network(nodes | boundary, levels)
    hi = simulate(net, {**fixed, **{m: 1.0 for m in members}}, separate=True)
    lo = simulate(net, {**fixed, **{m: 0.0 for m in members}}, separate=True)
    deltas = []
    for o in observed:
        if o.gene not in reach or o.gene in members:
            deltas.append(None)
            continue
        source_hi = hi.expression if o.kind == "expr" else hi.activity
        source_lo = lo.expression if o.kind == "expr" else lo.activity
        d = source_hi.get(o.gene, 0.0) - source_lo.get(o.gene, 0.0)
        deltas.append(d if abs(d) >= MIN_DELTA else None)
    best = Check(0, 0, 0, len(observed))
    for direction in (1, -1):
        agree = disagree = silent = 0
        for o, d in zip(observed, deltas):
            if d is None:
                silent += 1
            elif (d * direction > 0) == (o.change == "up"):
                agree += 1
            else:
                disagree += 1
        genes = [(o.gene, o.kind, o.change, None if d is None else ("up" if d * direction > 0 else "down"))
                 for o, d in zip(observed, deltas)]
        c = Check(direction, agree, disagree, silent, genes)
        if (c.agree - c.disagree, c.agree) > (best.agree - best.disagree, best.agree):
            best = c
    return best


def counted_members(model, c: Candidate) -> list[int]:
    """候補を壊すときに固定する遺伝子（まとまりなら、働きの計算に入る構成要素）。"""
    if c.gene >= 0:
        return [c.gene]
    unit = model.units[-c.gene - 1]
    return list(unit.counted)


def annotate(model, results: list[Candidate], observations: list[Observation], top: int = 20,
             levels: set[str] | None = None) -> None:
    """上位 top 件をシミュレーションで確かめ、候補の sim_check に結果を入れる（順位は変えない）。"""
    for c in results[:top]:
        c.sim_check = check(model, counted_members(model, c), observations, levels)


def rerank(model, results: list[Candidate], observations: list[Observation], top: int = 20,
           levels: set[str] | None = None, weight: float = 1.0) -> list[Candidate]:
    """上位 top 件をシミュレーションで確かめ、点数 ×（1 + weight × 一致度）で並べ直す。残りはそのまま後ろに置く。

    評価（2026-10-02）: 原因の分かっている条件のデータでは少し良くなる（上位 20 件・重み 10 で、正解の 10 位以内 38% → 45%）が、
    破壊株データでは下がる（10 位以内 21.9% → 19.4%）ので、画面では既定で並べ直さず、確かめた結果を表示するだけにする。"""
    annotate(model, results, observations, top, levels)
    head, tail = results[:top], results[top:]
    scored = []
    for i, c in enumerate(head):
        scored.append((c.score * (1.0 + weight * c.sim_check.consistency), -i, c))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [c for _s, _i, c in scored] + tail
