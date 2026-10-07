"""破壊株データから推定した転写制御の作用の向きで、制御遺伝子探索がどれだけ良くなるかを公平に確かめる。

転写因子 X の向きを X 自身の破壊株から推定して、X の破壊株で評価すると、答えを知ったうえで解くことになる。
そこで転写因子の破壊株は 5 組に分け、ある組を評価するときは、ほかの組の破壊株（と評価に使わない転写因子の破壊株）
だけから推定した向きを使う（交差検証）。転写因子以外の破壊株は、自分の破壊株を推定に使わないので、すべての向きを使う。

比べるやり方:
  なし         : 推定を使わない（今の DB のまま）
  埋める       : 作用不明の関係だけ、推定した向きで埋める
  埋める＋不明 : さらに、記録の向きと推定が食い違う関係を作用不明にする
  埋める＋反転 : さらに、記録の向きと推定が食い違う関係を推定の向きに変える

使い方: .venv/bin/python tools/evaluate_sign_inference.py [--depth 3] [--folds 5] [--workers 7]
"""
import argparse
import multiprocessing as mp
import random
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import deleteome  # noqa: E402
from evaluate_regulator_search import MIN_CHANGES, _init, _run, load_profiles  # noqa: E402
from tor_app.db.database import Database  # noqa: E402
from tor_app.db.vocab import resolve_effect  # noqa: E402
from tor_app.graph_data import PathwayModel  # noqa: E402
from tor_app.regulator_search import Observation, SignedGraph  # noqa: E402

POLICIES = ["なし", "埋める", "埋める＋不明", "埋める＋反転"]


def adjusted(interactions, changes: dict, policy: str, use: set) -> tuple[list, dict]:
    """推定した向き（use に入る転写因子の破壊株から）を、policy に従って当てはめた関係の一覧と、変えた本数。"""
    out, counts = [], {"埋めた": 0, "食い違い": 0}
    for it in interactions:
        effect = resolve_effect(it.interaction_type, it.effect)
        if policy != "なし" and it.interaction_type == "transcription" and it.source_id in use:
            inf = deleteome.inferred_effect(changes, it.source_id, it.target_id)
            if inf:
                if effect == "none":
                    effect = inf[0]
                    counts["埋めた"] += 1
                elif effect != inf[0]:
                    counts["食い違い"] += 1
                    if policy == "埋める＋不明":
                        effect = "none"
                    elif policy == "埋める＋反転":
                        effect = inf[0]
        out.append(SimpleNamespace(source_id=it.source_id, target_id=it.target_id,
                                   interaction_type=it.interaction_type, effect=effect))
    return out, counts


def run_jobs(graph, jobs, depth, workers):
    with mp.Pool(workers, initializer=_init, initargs=(graph,)) as pool:
        return pool.map(_run, [(n, g, o, depth, False) for n, g, o in jobs], chunksize=4)


def stats(rows) -> str:
    ranks = [r[4] for r in rows]
    n = len(ranks)
    if not n:
        return "（なし）"
    within = lambda k: 100 * sum(r is not None and r <= k for r in ranks) / n   # noqa: E731
    found = [r for r in ranks if r is not None]
    med = statistics.median(found) if found else float("nan")
    return (f"1 位 {within(1):5.1f}% | 10 位以内 {within(10):5.1f}% | 50 位以内 {within(50):5.1f}% | "
            f"候補に入らない {100 * (n - len(found)) / n:5.1f}% | 順位の中央値 {med:.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--max-obs", type=int, default=60)
    ap.add_argument("--fc", type=float, default=1.7, help="向きの推定に使う変化の大きさ（倍）。観測の基準は 1.7 倍のまま")
    ap.add_argument("--policies", default=",".join(POLICIES))
    ap.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 1))
    args = ap.parse_args()

    import math
    fc_obs = deleteome.FC
    deleteome.FC = math.log2(args.fc)
    db = Database(str(ROOT / "data" / "tor_pathway.db"))
    model = PathwayModel(db.proteins(), db.interactions(), db.categories())
    by_name = model.by_name
    by_sys = {p.standard_name.upper(): p.id for p in model.proteins.values() if p.standard_name}
    gene_of = lambda sysname, name: by_sys.get(sysname.upper()) or by_name.get(name.upper())   # noqa: E731
    interactions = deleteome.without_inferred(model.interactions.values())   # DB に入れた推定は外し、ここで推定し直す
    tfs = {it.source_id for it in interactions if it.interaction_type == "transcription"}

    t = time.time()
    changes = deleteome.load_changes(gene_of, wanted=tfs)
    deleteome.FC = fc_obs
    profiles, _ = load_profiles(by_name, by_sys)
    print(f"推定に使える転写因子の破壊株 {len(changes)} 件（読み込み {time.time() - t:.0f} 秒）")

    # 評価する破壊株（evaluate_regulator_search と同じ条件）
    base_graph = SignedGraph(interactions, False, {}, set())
    has_out = set(base_graph.tx) | set(base_graph.sig)
    jobs = []
    for name, gene, ch in profiles:
        ch = [(p, m) for p, m in ch if p != gene]
        if gene is None or len(ch) < MIN_CHANGES or gene not in has_out:
            continue
        ch.sort(key=lambda x: -abs(x[1]))
        jobs.append((name, gene, [Observation(p, "expr", "up" if m > 0 else "down") for p, m in ch[:args.max_obs]]))
    tf_jobs = [j for j in jobs if j[1] in tfs]
    other_jobs = [j for j in jobs if j[1] not in tfs]
    order = sorted({j[1] for j in tf_jobs})
    random.Random(0).shuffle(order)
    fold_of = {g: i % args.folds for i, g in enumerate(order)}
    print(f"評価する破壊株: 転写因子 {len(tf_jobs)}・転写因子以外 {len(other_jobs)}（交差検証 {args.folds} 組）\n")

    for policy in args.policies.split(","):
        t = time.time()
        tf_rows = []
        for f in range(args.folds):
            held = {g for g, k in fold_of.items() if k == f}
            inter, _ = adjusted(interactions, changes, policy, set(changes) - held)
            graph = SignedGraph(inter, False, {}, set())
            tf_rows += run_jobs(graph, [j for j in tf_jobs if j[1] in held], args.depth, args.workers)
        inter, counts = adjusted(interactions, changes, policy, set(changes))
        other_rows = run_jobs(SignedGraph(inter, False, {}, set()), other_jobs, args.depth, args.workers)
        changed = "" if policy == "なし" else f"（すべての推定を使うとき: 作用不明を埋める {counts['埋めた']} 本・記録と食い違う {counts['食い違い']} 本）"
        print(f"■ {policy}{changed}  [{time.time() - t:.0f} 秒]")
        print(f"   全体         {stats(tf_rows + other_rows)}")
        print(f"   転写因子     {stats(tf_rows)}")
        print(f"   転写因子以外 {stats(other_rows)}")


if __name__ == "__main__":
    main()
