"""経路の提案: 観測した遺伝子の発現・活性の変化を説明する「起点 → 経路 → 転写因子 → 観測」の仮説を、点数の高い順に並べる。

正解の経路が 1 位である必要はなく、上位（30 件程度）に入ることを目指す。点数は観測との合い方だけで決める
（関係の確度 A〜D は、よく調べられた経路ほど上に来るだけなので順位に入れず、表示だけにする）。

1. 転写因子ごとの証拠 E(t, a): 転写因子 t が活性化（a = +1）・不活性化（a = −1）したとして、観測とつじつまが合う標的
   （作用 × a が観測の増減と合う。作用が向き不明の標的は、どちらの向きにもなりうるので合うものとして数える）が、
   偶然以上に多く含まれるか（超幾何分布の −log10 p）。活性の観測は、転写制御以外の関係の相手として同じように数える。
   E が MOVED 以上で、よく合う向きが決まる転写因子を「動いた転写因子」とする。
2. 起点 o の点数 S(o, d): o が d（+1 活性化 / −1 不活性化）に変わったとして、o から STEPS 段以内にたどれる
   動いた転写因子 t それぞれについて、経路の符号から予測した t の向きが合えば +E、逆なら −E（× DECAY^段数）。
   経路に向き不明の関係があれば、促進・抑制どちらにもなりうるので、合う向きを仮定する（向きの決まった関係と同じ重さ）。
   o 自身が動いた転写因子なら、自分の証拠も加える（0 段）。経路は転写因子から転写因子への段（転写の連鎖）を 1 回まで含めてよい。
3. 偶然との比較: 実際に動いた転写因子の証拠と向きはそのままで、どの転写因子が動いたかの割り当てを PERMUTATIONS 回
   入れ替えて同じ計算をし、
   起点ごとの z 値 =（S − 平均）/ √(標準偏差² + τ²) で並べる（τ は起点全体の標準偏差の中央値。偶然の点数がほぼ 0 の
   起点で z 値が極端になるのを防ぐ）。つながりの多い遺伝子（ハブ）や、向き不明の関係が多い経路が、
   選べる分だけ有利になるのを差し引く。
4. 同じ起点は MAX_PER_ORIGIN 件まで（主な経路の転写因子が違うもの）にして、上位 TOP 件を返す。

簡略化: 1 つの仮説の中で、向き不明の関係の向きは転写因子ごとに選ぶ（枝どうしで同じ関係を共有するときの一致は見ていない）。
"""
import math
from dataclasses import dataclass, field

import numpy as np

from .regulator_search import Observation, SignedGraph, _log_sf

STEPS = 3             # 起点から転写因子までのシグナル伝達の段数の上限
DECAY = 0.7           # 1 段あたりの割り引き
MOVED = 2.0           # 動いた転写因子とみなす証拠（−log10 p）
PERMUTATIONS = 200
MAX_PER_ORIGIN = 2
TOP = 30


@dataclass
class Proposal:
    origin: int                 # 起点（遺伝子、またはまとまり = 負の番号）
    origin_name: str
    direction: int              # +1 活性化 / −1 不活性化
    z: float                    # 偶然と比べた点数
    score: float                # S(o, d)
    tf: int                     # 主な経路の先の転写因子
    tf_name: str
    steps: int
    path: list                  # [(上流, 下流, 符号)]（実際に関係を持つ遺伝子に戻したもの）。符号 0 は向き不明
    assumed: dict = field(default_factory=dict)    # 向き不明の関係 (上流, 下流) → 仮定した符号
    explained: list = field(default_factory=list)  # 主な転写因子で説明できる観測 [(遺伝子, 種類, 増減)]
    support: list = field(default_factory=list)    # 予測と合う動いた転写因子 [(名前, 段数, 証拠)]
    conflict: list = field(default_factory=list)   # 予測と逆の動いた転写因子 [(名前, 段数, 証拠)]
    unknown_edges: int = 0
    tx_cascade: bool = False    # 転写の連鎖を含むか


def _reach(graph: SignedGraph, start: int, steps: int) -> dict[int, tuple[int, list]]:
    """start からシグナル伝達の関係で steps 段以内にたどれる転写因子 → (段数, 経路)。さらに転写因子から転写因子への
    段を 1 回だけ足したものも含める（段数 +1）。最短の経路を 1 本。"""
    seen, frontier, found = {start: []}, [start], {}
    if start in graph.tx:
        found[start] = (0, [])
    for d in range(1, steps + 1):
        nxt = []
        for u in frontier:
            for v, s in graph.sig.get(u, {}).items():
                key = graph.unit_of.get(v, v)
                if key in seen or key == start:
                    continue
                seen[key] = seen[u] + [(u, v, s, "sig")]
                nxt.append(key)
                if key in graph.tx and key not in found:
                    found[key] = (d, seen[key])
        frontier = nxt
    # 転写の連鎖（転写因子 → 転写因子）を 1 回
    for t, (d, path) in list(found.items()):
        if d >= steps + 1:
            continue
        for v, s in graph.tx.get(t, {}).items():
            key = graph.unit_of.get(v, v)
            if key in graph.tx and key not in found and key != start:
                found[key] = (d + 1, path + [(t, v, s, "tx")])
    return found


class PathwayIndex:
    """グラフごとに一度だけ作る、起点 × 転写因子 の到達の表（偶然との比較を行列でまとめて計算するため）。"""

    def __init__(self, graph: SignedGraph, steps: int = STEPS):
        self.graph = graph
        self.tfs = sorted(graph.tx)
        self.tf_index = {t: i for i, t in enumerate(self.tfs)}
        origins = sorted(set(graph.sig) | set(graph.tx))
        self.origins = origins
        self.paths: dict[int, dict[int, tuple[int, list]]] = {}
        rows_k, cols_k, vals_k, rows_u, cols_u, vals_u = [], [], [], [], [], []
        for oi, o in enumerate(origins):
            reach = _reach(graph, o, steps)
            self.paths[o] = reach
            for t, (d, path) in reach.items():
                w = DECAY ** d
                signs = [e[2] for e in path]
                if all(signs):
                    rows_k.append(oi); cols_k.append(self.tf_index[t]); vals_k.append(w * math.prod(signs))
                else:
                    rows_u.append(oi); cols_u.append(self.tf_index[t]); vals_u.append(w)
        # 起点ごとの (転写因子の番号, 重み, 経路の符号（0 は向き不明）) の配列（点数の形を変えて試すため）
        self.rows = {}
        for o, reach in self.paths.items():
            items = [(self.tf_index[t], DECAY ** d, math.prod(e[2] for e in path) if all(e[2] for e in path) else 0)
                     for t, (d, path) in reach.items()]
            self.rows[o] = tuple(np.array(x, dtype=float) for x in zip(*items)) if items else None
        from scipy.sparse import csr_matrix
        shape = (len(origins), len(self.tfs))
        self.K = csr_matrix((vals_k, (rows_k, cols_k)), shape=shape)   # 符号の決まった経路（重み × 符号）
        self.U = csr_matrix((vals_u, (rows_u, cols_u)), shape=shape)   # 向き不明の関係を含む経路（重み）
        # 標的の表（観測との照合を速くするため）
        self.genes = sorted({g for d in (graph.tx, graph.sig) for a, ts in d.items() for g in ts} |
                            {a for a in graph.tx if a >= 0})


def _tf_evidence(graph: SignedGraph, tfs: list[int], expr: dict[int, int], act: dict[int, int], total: int):
    """転写因子ごとの、活性化・不活性化それぞれの証拠（−log10 p）と、つじつまの合う標的。"""
    plus, minus = np.zeros(len(tfs)), np.zeros(len(tfs))
    for i, t in enumerate(tfs):
        for targets, changed in ((graph.tx.get(t, {}), expr), (graph.sig.get(t, {}), act)):
            if not changed or not targets:
                continue
            hit = targets.keys() & changed.keys()
            if not hit:
                continue
            kp = sum(1 for g in hit if targets[g] == 0 or targets[g] == changed[g])
            km = sum(1 for g in hit if targets[g] == 0 or targets[g] == -changed[g])
            plus[i] += -_log_sf(kp, total, len(targets), len(changed))
            minus[i] += -_log_sf(km, total, len(targets), len(changed))
    return plus, minus


ALPHA = 0.5   # score_mode="best_cons" で、他の動いた転写因子とのつじつまに掛ける重み


def _origin_scores_best(index: PathwayIndex, plus: np.ndarray, minus: np.ndarray, alpha: float = ALPHA):
    """起点ごとの S(o, +1)・S(o, −1)。いちばんよく説明できる経路の点数 ×（1 + alpha × つじつま）。
    つじつま =（予測と合う動いた転写因子の点数の和 − 逆の和）/ 全体の和（−1〜1）。向き不明の経路は合う向きを仮定する。"""
    best = np.maximum(plus, minus)
    b = np.where(plus >= minus, 1.0, -1.0)
    m = np.where(best >= MOVED, best, 0.0)
    up, down = np.zeros(len(index.origins)), np.zeros(len(index.origins))
    for oi, o in enumerate(index.origins):
        r = index.rows.get(o)
        if r is None:
            continue
        idx, w, sg = r
        idx = idx.astype(int)
        val = w * m[idx]
        if not val.any():
            continue
        for d, outv in ((1, up), (-1, down)):
            ok = (sg == 0) | (d * sg == b[idx])
            cons, incons = val[ok].sum(), val[~ok].sum()
            top = val[ok].max() if ok.any() else 0.0
            outv[oi] = top * (1 + alpha * (cons - incons) / (cons + incons)) if top > 0 else 0.0
    return up, down


BETA = 1.0   # score_mode="union" で、予測と逆の動いた転写因子の証拠に掛ける重み


def _origin_scores_union(index: PathwayIndex, plus, minus, expr, act, total, beta: float = BETA):
    """起点ごとの S(o, ±1): 予測と合う動いた転写因子（向き不明の経路は合う向きを仮定）の標的を合わせ、そのうちつじつまの
    合う観測の数が偶然以上に多いか（−log10 p）× DECAY^(最短の段数) − beta × 予測と逆の動いた転写因子の証拠（× DECAY^段数）。
    複数の転写因子をまとめて説明できる上流の起点が、1 つの転写因子より高くなりうる。"""
    graph = index.graph
    best = np.maximum(plus, minus)
    b = np.where(plus >= minus, 1, -1)
    moved = best >= MOVED
    n_obs = len(expr) + len(act)
    cache: dict = {}

    def tf_sets(i):
        if i not in cache:
            t = index.tfs[i]
            tg = set(graph.tx.get(t, {})) | set(graph.sig.get(t, {}))
            cache[i] = (tg, set(_explained(graph, t, int(b[i]), expr, act)))
        return cache[i]
    up, down = np.zeros(len(index.origins)), np.zeros(len(index.origins))
    for oi, o in enumerate(index.origins):
        r = index.rows.get(o)
        if r is None:
            continue
        idx, w, sg = r
        idx = idx.astype(int)
        sel = moved[idx]
        if not sel.any():
            continue
        for d, outv in ((1, up), (-1, down)):
            ok = sel & ((sg == 0) | (d * sg == b[idx]))
            bad = sel & ~ok
            if not ok.any():
                continue
            targets, explained = set(), set()
            for i in idx[ok]:
                tg, ex = tf_sets(int(i))
                targets |= tg
                explained |= ex
            h = -_log_sf(len(explained), total, len(targets), n_obs)
            outv[oi] = h * float(w[ok].max()) - beta * float((best[idx[bad]] * w[bad]).sum())
    return up, down


def _origin_scores(index: PathwayIndex, plus: np.ndarray, minus: np.ndarray):
    """起点ごとの S(o, +1)・S(o, −1)。"""
    best = np.maximum(plus, minus)
    b = np.where(plus >= minus, 1.0, -1.0)
    m = np.where(best >= MOVED, best, 0.0)
    known = index.K @ (m * b)       # Σ 重み × 経路の符号 × 証拠 × よく合う向き
    unknown = index.U @ m           # 向き不明の関係を含む経路は、合う向きを仮定する
    return known + unknown, -known + unknown


def propose(graph: SignedGraph, observations: list[Observation], index: PathwayIndex | None = None,
            names: dict[int, str] | None = None, permutations: int = PERMUTATIONS, top: int = TOP,
            seed: int = 0, progress=None, null_mode: str = "tf", score_mode: str = "best_cons") -> list[Proposal]:
    """null_mode: 偶然との比較の取り方。"tf" = 動いた転写因子の割り当てを入れ替える、"obs" = 観測を別の遺伝子に入れ替える、
    "none" = 比較しない（点数 S で並べる）。"""
    names = names or {}
    index = index or PathwayIndex(graph)
    sign = {"up": 1, "down": -1}
    expr = {o.gene: sign[o.change] for o in observations if o.kind == "expr" and o.change in sign}
    act = {o.gene: sign[o.change] for o in observations if o.kind == "act" and o.change in sign}
    if not expr and not act:
        return []
    total = max(graph.n_genes, 1)
    plus, minus = _tf_evidence(graph, index.tfs, expr, act, total)
    if score_mode == "union":
        scorer = lambda ix, p_, m_: _origin_scores_union(ix, p_, m_, expr, act, total)   # noqa: E731
    else:
        scorer = _origin_scores_best if score_mode == "best_cons" else _origin_scores
    s_up, s_down = scorer(index, plus, minus)
    # 偶然との比較: 実際に動いた転写因子の証拠と向きはそのままで、「どの転写因子が動いたか」の割り当てだけを入れ替える。
    # 多くの転写因子に届く起点（ハブ）や、向き不明の関係が多い起点は、入れ替えても点数が高いので z 値が下がる
    rng = np.random.default_rng(seed)
    best0 = np.maximum(plus, minus)
    mb = np.where(best0 >= MOVED, best0, 0.0) * np.where(plus >= minus, 1.0, -1.0)
    mm = np.where(best0 >= MOVED, best0, 0.0)
    null = np.zeros((max(permutations, 2), len(index.origins)))
    if null_mode == "tf":
        for k in range(permutations):
            perm = rng.permutation(len(index.tfs))
            if score_mode in ("best_cons", "union"):
                u, d = scorer(index, plus[perm], minus[perm])
                null[k] = np.maximum(u, d)
            else:
                known, unknown = index.K @ mb[perm], index.U @ mm[perm]
                null[k] = np.maximum(known + unknown, -known + unknown)
            if progress and k % 20 == 0:
                progress(k, permutations)
    elif null_mode == "obs":
        pool = np.array(index.genes)
        for k in range(permutations):
            picked = rng.choice(pool, size=len(expr) + len(act), replace=False)
            e2 = dict(zip(picked[:len(expr)].tolist(), expr.values()))
            a2 = dict(zip(picked[len(expr):].tolist(), act.values()))
            p2, m2 = _tf_evidence(graph, index.tfs, e2, a2, total)
            u, d = scorer(index, p2, m2)
            null[k] = np.maximum(u, d)
    if null_mode == "none":
        null[:] = 0.0
    mean, sd = null.mean(axis=0), null.std(axis=0)
    # 偶然の点数がほぼ 0 の起点は標準偏差も 0 に近く、z 値が極端になる。全体の典型的なばらつきを下限として加える
    tau = float(np.median(sd[sd > 0])) if np.any(sd > 0) else 1.0
    sd = np.sqrt(sd ** 2 + tau ** 2)
    z_up, z_down = (s_up - mean) / sd, (s_down - mean) / sd
    best_dir = np.where(z_up >= z_down, 1, -1)
    z = np.maximum(z_up, z_down)
    s = np.where(best_dir > 0, s_up, s_down)

    best = np.maximum(plus, minus)
    tf_dir = {t: (1 if plus[i] >= minus[i] else -1) for i, t in enumerate(index.tfs)}
    moved = {t for i, t in enumerate(index.tfs) if best[i] >= MOVED}
    ev = {t: float(best[i]) for i, t in enumerate(index.tfs)}
    order = np.argsort(-z)
    out: list[Proposal] = []
    for oi in order:
        if len(out) >= top or z[oi] <= 0 or s[oi] <= 0:
            break
        o, d = index.origins[oi], int(best_dir[oi])
        reach = index.paths[o]
        # 起点 d から予測した向きと、動いた転写因子の向きを比べる
        rows = []
        for t, (steps, path) in reach.items():
            if t not in moved:
                continue
            signs = [e[2] for e in path]
            w = ev[t] * DECAY ** steps
            if all(signs):
                ok = d * math.prod(signs) == tf_dir[t]
                rows.append((ok, w, t, steps, path))
            else:
                rows.append((True, w, t, steps, path))
        support = sorted([r for r in rows if r[0]], key=lambda r: -r[1])
        conflict = sorted([r for r in rows if not r[0]], key=lambda r: -r[1])
        explained_by: set = set()
        added = 0
        for ok, w, t, steps, path in support:
            if added >= MAX_PER_ORIGIN:
                break
            ex = _explained(graph, t, tf_dir[t], expr, act)
            if added and len(set(ex) - explained_by) < max(1, len(ex) // 2):
                continue   # 前の経路と説明する観測がほとんど同じなら、別の経路として出さない
            explained_by |= set(ex)
            assumed = {graph.gene_edge(a, b, 0)[:2]: v for (a, b), v in _assume(path, d, tf_dir[t]).items()}
            out.append(Proposal(
                o, graph.name(o, names), d, float(z[oi]), float(s[oi]), t, graph.name(t, names), steps,
                [graph.gene_edge(*e[:3]) for e in path], assumed,
                [(g, "expr" if g in expr else "act", "up" if (expr.get(g) or act.get(g)) > 0 else "down") for g in ex],
                [(graph.name(x[2], names), x[3], round(x[1], 2)) for x in support],
                [(graph.name(x[2], names), x[3], round(x[1], 2)) for x in conflict],
                sum(1 for e in path if not e[2]), any(e[3] == "tx" for e in path)))
            added += 1
            if len(out) >= top:
                break
    return out


def _explained(graph: SignedGraph, t: int, a: int, expr: dict, act: dict) -> list[int]:
    """転写因子 t が a に変わったとして、つじつまの合う観測（向き不明の標的はどちらとも合う）。"""
    out = []
    for targets, changed in ((graph.tx.get(t, {}), expr), (graph.sig.get(t, {}), act)):
        for g in targets.keys() & changed.keys():
            if targets[g] == 0 or targets[g] * a == changed[g]:
                out.append(g)
    return out


def _assume(path: list, d: int, want: int) -> dict:
    """向き不明の関係に仮定する符号: 経路全体で起点の向き d から転写因子の向き want になるように、最初の向き不明の関係で合わせる。"""
    unknown = [i for i, e in enumerate(path) if not e[2]]
    if not unknown:
        return {}
    known = math.prod(e[2] for e in path if e[2])
    first = 1 if d * known == want else -1
    return {(path[i][0], path[i][1]): (first if j == 0 else 1) for j, i in enumerate(unknown)}
