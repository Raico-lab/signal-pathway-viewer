"""制御遺伝子探索の確からしさを、遺伝子破壊株の発現データ（Deleteome, Kemmeren et al. 2014 Cell）で確かめる。

各破壊株について、発現が大きく変わった遺伝子（|log2 比| ≥ log2(1.7) かつ p < 0.05、論文の基準）を「発現 増／減」の
観測にして探索し、「壊した遺伝子の不活性化」が何位に来るかを集計する。壊した遺伝子そのものの発現（当然減る）は除く。

データ: raw_data/DELETEOME/deleteome_all_mutants_ex_wt_var_controls.txt
  （https://deleteome.holstegelab.nl の Downloads から取得。git では管理しない）

使い方:
  .venv/bin/python tools/evaluate_regulator_search.py [--depth 3] [--max-obs 60] [--workers 6] [--out 結果.tsv]
"""
import argparse
import csv
import math
import multiprocessing as mp
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tor_app.db.database import Database  # noqa: E402
from tor_app.graph_data import PathwayModel  # noqa: E402
from tor_app.regulator_search import Observation, SignedGraph, search  # noqa: E402

DATA = ROOT / "raw_data" / "DELETEOME" / "deleteome_all_mutants_ex_wt_var_controls.txt"
FC = math.log2(1.7)
P_MAX = 0.05
MIN_CHANGES = 4        # 論文の「応答のあった破壊株」の基準（4 遺伝子以上が変化）

_graph = None


def load_profiles(by_name: dict[str, int], by_systematic: dict[str, int], remove_pcs: int = 0, exclude_common: int = 0):
    """破壊株ごとに (壊した遺伝子, [(遺伝子, log2 比)]) を返す（大きく変わった遺伝子だけ）。

    remove_pcs: 全破壊株の log2 比の主成分のうち上位 k 個（多くの株に共通する変化。第 1 主成分は一般的なストレス応答）を、
                各株の変化から引いてから「大きく変わった」を判定する（p 値の基準は元のまま）。
    exclude_common: 第 1 主成分の重みの絶対値が大きい遺伝子 N 個を観測から外す（アプリでも使える形の補正）。"""
    import numpy as np
    with open(DATA, encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader)
        kinds = next(reader)
        # 列の組: (破壊株の名前, M の列, p 値の列)。対照の比較（wt どうしなど）は除く
        profiles = {}
        for i, (name, kind) in enumerate(zip(header, kinds)):
            if i < 3 or "-del" not in name:
                continue
            profiles.setdefault(name, {})[kind] = i
        cols = [(name, c["M"], c["p_value"]) for name, c in profiles.items() if "M" in c and "p_value" in c]

        def num(x: str) -> float:
            try:
                return float(x)
            except ValueError:
                return float("nan")

        pids, ms, ps = [], [], []
        for row in reader:
            if len(row) < 3:
                continue
            pids.append(by_systematic.get(row[1].strip().upper()) or by_name.get(row[2].strip().upper()))
            ms.append([num(row[mi]) if mi < len(row) else float("nan") for _, mi, _ in cols])
            ps.append([num(row[pi]) if pi < len(row) else float("nan") for _, _, pi in cols])
    M, P = np.array(ms), np.array(ps)
    known = ~np.isnan(M)
    M0 = np.where(known, M, 0.0)
    keep = np.ones(len(pids), dtype=bool)
    if remove_pcs or exclude_common:
        U, _, _ = np.linalg.svd(M0, full_matrices=False)
        if remove_pcs:
            B = U[:, :remove_pcs]
            M0 = M0 - B @ (B.T @ M0)
        if exclude_common:
            keep[np.argsort(-np.abs(U[:, 0]))[:exclude_common]] = False
    unmatched = sum(pid is None for pid in pids)
    big = known & (np.abs(M0) >= FC) & (np.nan_to_num(P, nan=1.0) < P_MAX) & keep[:, None]
    result = []
    for j, (name, _, _) in enumerate(cols):
        ch = [(pids[i], float(M0[i, j])) for i in np.nonzero(big[:, j])[0] if pids[i] is not None]
        gene = name.split("-del")[0].strip().upper()
        result.append((name, by_name.get(gene), ch))
    return result, unmatched


_model = None
_rerank = (0, 1.0)


def _init(graph, rerank=(0, 1.0)):
    global _graph, _model, _rerank
    _graph = graph
    _rerank = rerank
    if rerank[0]:
        # シミュレーションで確かめるときも、破壊株データから推定した向きは使わない（答えを知って解かないため）
        import deleteome
        db = Database(str(ROOT / "data" / "tor_pathway.db"))
        _model = PathwayModel(db.proteins(), db.interactions(), db.categories())
        for it in _model.interactions.values():
            if deleteome.INFERRED_MARK in (it.evidence or ""):
                it.effect = "none"


def _run(job):
    name, gene, observations, depth, flip = job
    if flip:   # 対照: 増減を逆にしたら、不活性化ではなく活性化が上に来るはず
        observations = [Observation(o.gene, o.kind, "down" if o.change == "up" else "up") for o in observations]
    res = search(_graph, observations, depth, permutations=0)
    if _model is not None:
        from tor_app import sim_verify
        res = sim_verify.rerank(_model, res, observations, _rerank[0], weight=_rerank[1])
    # 壊した遺伝子の順位（向きは問わない）と、表示する向き（-1 = 不活性化なら正しい）
    # 壊した遺伝子そのもの、またはそれを含むまとまり（複合体・役割が重なる組）の順位
    hit = next(((i + 1, c) for i, c in enumerate(res) if c.gene == gene or gene in c.members), None)
    rank = hit[0] if hit else None
    direction = hit[1].direction if hit else None
    reached = hit[1].overlap if hit else 0
    return name, gene, len(observations), len(res), rank, direction, reached


def summarize(label: str, rows: list[tuple], key: int) -> None:
    ranks = [r[key] for r in rows]
    n = len(ranks)
    found = [r for r in ranks if r is not None]
    pct = lambda k: 100 * sum(r is not None and r <= k for r in ranks) / n if n else 0   # noqa: E731
    # でたらめに並べたときの期待値（候補の中の位置が一様なら）
    rand = lambda k: 100 * statistics.mean(min(1.0, k / r[3]) if r[3] else 0 for r in rows) if n else 0   # noqa: E731
    print(f"{label}: {n} 株")
    print(f"  1 位 {pct(1):5.1f}%（でたらめなら {rand(1):4.1f}%） | 10 位以内 {pct(10):5.1f}%（{rand(10):4.1f}%）"
          f" | 50 位以内 {pct(50):5.1f}%（{rand(50):4.1f}%） | 候補に入らない {100 * (n - len(found)) / n:5.1f}%")
    if found:
        print(f"  順位の中央値 {statistics.median(found):.0f}（候補の数の中央値 {statistics.median(r[3] for r in rows):.0f}）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--max-obs", type=int, default=60, help="1 株あたりの観測の上限（変化の大きい順）")
    ap.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 1))
    ap.add_argument("--limit", type=int, default=0, help="評価する株の数の上限（試すとき用）")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--remove-pcs", type=int, default=0, help="全株に共通する変化（主成分の上位 k 個）を各株から引く")
    ap.add_argument("--exclude-common", type=int, default=0, help="第 1 主成分の重みが大きい遺伝子 N 個を観測から外す")
    ap.add_argument("--common", action="store_true",
                    help="アプリと同じ扱い: data/common_response_genes.csv の遺伝子を、転写因子自身の点数でだけ外す")
    ap.add_argument("--common-full-factor", type=float, default=None,
                    help="転写因子自身の点数で、ストレス応答の遺伝子を含めた点数をこの率で使ってよい（既定は探索の既定値）")
    ap.add_argument("--confidence", default="all", help="計算に使う関係の確度（tor_app/confidence.py の CHOICES のキー）")
    ap.add_argument("--rerank", type=int, default=0, help="上位 N 件をシミュレーションで確かめて並べ直す（tor_app/sim_verify.py）")
    ap.add_argument("--rerank-weight", type=float, default=1.0)
    ap.add_argument("--unsigned", action="store_true", help="転写因子の点数で増減の向きを使わない（以前の並べ方）")
    ap.add_argument("--unsigned-inherit", action="store_true", help="シグナル伝達の遺伝子が受け継ぐ点数では向きを使わない")
    ap.add_argument("--step-factor", type=float, default=None, help="シグナル伝達の遺伝子が受け継ぐときの 1 段あたりの掛け率")
    ap.add_argument("--signal-mode", choices=["best", "causal"], default=None, help="シグナル伝達の遺伝子の点数の付け方")
    ap.add_argument("--causal-threshold", type=float, default=None)
    ap.add_argument("--causal-unknown", type=float, default=None)
    ap.add_argument("--causal-fallback", type=float, default=None)
    ap.add_argument("--causal-penalty", type=float, default=None)
    ap.add_argument("--signed-fallback", type=float, default=None,
                    help="向きを無視した点数をこの率で使ってよい（既定は探索の既定値。0 なら向きだけ）")
    ap.add_argument("--common-exclude-all", action="store_true",
                    help="シグナル伝達の遺伝子が受け継ぐ点数でも、ストレス応答の遺伝子を外す")
    args = ap.parse_args()

    db = Database(str(ROOT / "data" / "tor_pathway.db"))
    model = PathwayModel(db.proteins(), db.interactions(), db.categories())
    by_name = model.by_name
    by_systematic = {p.standard_name.upper(): p.id for p in model.proteins.values() if p.standard_name}
    t = time.time()
    profiles, unmatched = load_profiles(by_name, by_systematic, args.remove_pcs, args.exclude_common)
    print(f"データ: 破壊株 {len(profiles)} 件（読み込み {time.time() - t:.0f} 秒、DB にない遺伝子の行 {unmatched}）")

    # DB には破壊株データから推定した向きも入っている。それを使うと、転写因子の破壊株で答えを知ったうえで解くことになるので外す
    # （推定した向きの効き目は tools/evaluate_sign_inference.py で交差検証して確かめる）
    import deleteome
    from tor_app import confidence
    levels = confidence.allowed(args.confidence)
    graph = SignedGraph(deleteome.without_inferred([it for it in model.interactions.values()
                                                    if model.confidence[it.id] in levels]), False, {}, set(),
                        model.units, model.names)
    out_degree = {}
    for it in model.interactions.values():
        if it.source_id != it.target_id:
            out_degree[it.source_id] = out_degree.get(it.source_id, 0) + 1
    if args.unsigned:
        graph.tf_signed = False
    if args.signed_fallback is not None:
        graph.signed_fallback = args.signed_fallback
    if args.unsigned_inherit:
        graph.signed_inherit = False
    if args.step_factor is not None:
        graph.step_factor = args.step_factor
    if args.signal_mode:
        graph.signal_mode = args.signal_mode
    if args.causal_threshold is not None:
        graph.causal_threshold = args.causal_threshold
    if args.causal_unknown is not None:
        graph.causal_unknown = args.causal_unknown
    if args.causal_fallback is not None:
        graph.causal_fallback = args.causal_fallback
    if args.causal_penalty is not None:
        graph.causal_penalty = args.causal_penalty
    common: set[int] = set()
    if args.common:
        from tor_app import common_response
        common = common_response.load({**by_name, **by_systematic})
        graph.common = common
        if args.common_full_factor is not None:
            graph.common_full_factor = args.common_full_factor
        if args.common_exclude_all:
            graph.common_inherit_full = False
        print(f"一般的なストレス応答の遺伝子: {len(common)} 個（転写因子自身の点数では観測から外す、"
              f"含めた点数を使う率 {graph.common_full_factor}）")
    unit_only: set[str] = set()
    jobs, skipped = [], {"DB にない": 0, "変化が 4 遺伝子未満": 0, "下流の関係がない（候補になれない）": 0}
    for name, gene, changes in profiles:
        if gene is None:
            skipped["DB にない"] += 1
            continue
        changes = [(p, m) for p, m in changes if p != gene]
        if len(changes) < MIN_CHANGES:
            skipped["変化が 4 遺伝子未満"] += 1
            continue
        in_unit = any(gene in u.counted for u in model.units)
        if not out_degree.get(gene) and not in_unit:
            skipped["下流の関係がない（候補になれない）"] += 1
            continue
        if not out_degree.get(gene):
            unit_only.add(name)   # 自分の関係はないが、複合体・役割が重なる組の候補として探せる
        changes.sort(key=lambda x: -abs(x[1]))
        if common:   # ストレス応答の遺伝子で上限が埋まらないよう、それ以外とは別に数える
            changes = [c for c in changes if c[0] not in common][:args.max_obs] + \
                [c for c in changes if c[0] in common][:args.max_obs]
        obs = [Observation(p, "expr", "up" if m > 0 else "down") for p, m in changes[:2 * args.max_obs if common else args.max_obs]]
        jobs.append((name, gene, obs))
    if args.limit:
        jobs = random.Random(0).sample(jobs, min(args.limit, len(jobs)))
    print("除いた株: " + "、".join(f"{k} {v}" for k, v in skipped.items()))
    print(f"評価する株: {len(jobs)}（さかのぼる段数 {args.depth}、観測は 1 株 {args.max_obs} 個まで）")

    t = time.time()
    with mp.Pool(args.workers, initializer=_init, initargs=(graph, (args.rerank, args.rerank_weight))) as pool:
        rows = pool.map(_run, [(n, g, o, args.depth, False) for n, g, o in jobs], chunksize=4)
    print(f"計算 {time.time() - t:.0f} 秒\n")

    if unit_only:
        summarize("（追加）自分の関係はないが、まとまりの構成要素として探せる破壊株", [r for r in rows if r[0] in unit_only], 4)
        rows = [r for r in rows if r[0] not in unit_only]   # 以下は以前と同じ株で比べる
    summarize("壊した遺伝子の順位", rows, 4)
    shown = [r for r in rows if r[4] is not None]
    if shown:
        def share(v): return 100 * sum(r[5] == v for r in shown) / len(shown)
        print(f"  表示する向き（候補に入った {len(shown)} 株）: 不活性化 {share(-1):.0f}%（正しい）／活性化 {share(1):.0f}%"
              f"／向きは不明 {share(0):.0f}%")
    tfs = {it.source_id for it in model.interactions.values() if it.interaction_type == "transcription"}
    tf = [r for r in rows if r[1] in tfs]
    summarize("うち転写因子（転写制御の関係を持つ）", tf, 4)
    other = [r for r in rows if r not in tf]
    summarize("うち転写因子以外", other, 4)

    names = model.names
    best = sorted((r for r in rows if r[4]), key=lambda r: r[4])[:15]
    print("\n上位に来た例: " + "、".join(f"{names[r[1]]}({r[4]})" for r in best))
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(["破壊株", "遺伝子", "観測の数", "候補の数", "順位", "表示した向き", "重なった観測の数"])
            for r in rows:
                w.writerow([r[0], names[r[1]], r[2], r[3], r[4] or "", {1: "活性化", -1: "不活性化", 0: "不明"}.get(r[5], ""), r[6]])
        print(f"株ごとの結果: {args.out}")


if __name__ == "__main__":
    main()
