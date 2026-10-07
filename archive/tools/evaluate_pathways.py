"""経路の提案（tor_app/pathways.py）の評価: 正解の経路が上位 10 件・30 件に入る割合。

- 条件データ（sample_data/condition_eval/pathways.csv）: 正解は「起点:向き:転写因子|転写因子」。提案の起点が正解の起点と同じで、
  主な経路の転写因子か、予測と合う動いた転写因子のどれかが正解の転写因子なら当たり（起点だけ合うものも参考に数える）。
- 破壊株データ（Deleteome）: 壊した遺伝子（またはそれを含むまとまり）が起点の提案が何件目に来るか。
  破壊株データから推定した向きは使わない。

使い方: .venv/bin/python tools/evaluate_pathways.py [--null tf|obs|none] [--permutations 200] [--skip-deleteome]
"""
import argparse
import csv
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import deleteome  # noqa: E402
from evaluate_conditions import DATA, CONDITIONS, observations, read_column  # noqa: E402
from tor_app import pathways  # noqa: E402
from tor_app.db.database import Database  # noqa: E402
from tor_app.graph_data import PathwayModel  # noqa: E402
from tor_app.regulator_search import Observation, SignedGraph  # noqa: E402

ANSWERS = ROOT / "sample_data" / "condition_eval" / "pathways.csv"


def origin_matches(p, name: str, model) -> bool:
    pid = model.by_name.get(name.upper())
    if p.origin_name == name or p.origin_name.startswith(name + "（"):
        return True
    return pid is not None and p.origin == pid


def tf_names(p) -> set[str]:
    names = {p.tf_name} | {s[0] for s in p.support}
    return {n.split("（")[0] for n in names}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--null", default="tf", choices=["tf", "obs", "none"])
    ap.add_argument("--score", default="best_cons", choices=["best_cons", "sum", "union"])
    ap.add_argument("--permutations", type=int, default=pathways.PERMUTATIONS)
    ap.add_argument("--max-obs", type=int, default=60)
    ap.add_argument("--skip-deleteome", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    db = Database(str(ROOT / "data" / "tor_pathway.db"))
    model = PathwayModel(db.proteins(), db.interactions(), db.categories())
    lookup = {**model.by_name, **{p.standard_name.upper(): p.id for p in model.proteins.values() if p.standard_name}}

    # 条件データ（すべての関係を使う）
    graph = SignedGraph(list(model.interactions.values()), False, {}, set(), model.units, model.names)
    index = pathways.PathwayIndex(graph)
    conds = {r["name"]: r for r in csv.DictReader(open(CONDITIONS, encoding="utf-8"))}
    full, origin_only = [], []
    for row in csv.DictReader(open(ANSWERS, encoding="utf-8")):
        cond = conds[row["name"]]
        obs = observations(read_column(DATA / cond["file"], cond["column"], cond.get("baseline", "")), lookup, args.max_obs)
        res = pathways.propose(graph, obs, index, model.names, permutations=args.permutations, null_mode=args.null,
                                score_mode=args.score)
        answers = [a.split(":") for a in row["answers"].split(";") if a]
        r_full = r_origin = None
        for i, p in enumerate(res, 1):
            for origin, _sign, tfs in answers:
                if origin_matches(p, origin, model):
                    r_origin = r_origin or i
                    if tf_names(p) & set(tfs.split("|")):
                        r_full = r_full or i
        full.append(r_full); origin_only.append(r_origin)
        if not args.quiet:
            top = "、".join(f"{p.origin_name.split('（')[0]}{'↑' if p.direction > 0 else '↓'}→{p.tf_name.split('（')[0]}" for p in res[:5])
            print(f"{row['name']}: 起点＋転写因子 {r_full or '-'} 位・起点のみ {r_origin or '-'} 位 | 上位: {top}")
    pct = lambda xs, k: 100 * sum(x is not None and x <= k for x in xs) / len(xs)   # noqa: E731
    print(f"\n条件データ {len(full)} 条件: 起点＋転写因子 10 件以内 {pct(full, 10):.0f}%・30 件以内 {pct(full, 30):.0f}% | "
          f"起点のみ 10 件以内 {pct(origin_only, 10):.0f}%・30 件以内 {pct(origin_only, 30):.0f}%")

    if args.skip_deleteome:
        return
    # 破壊株データ（推定した向きは使わない）
    from evaluate_regulator_search import load_profiles, MIN_CHANGES
    graph_d = SignedGraph(deleteome.without_inferred(model.interactions.values()), False, {}, set(),
                          model.units, model.names)
    index_d = pathways.PathwayIndex(graph_d)
    by_sys = {p.standard_name.upper(): p.id for p in model.proteins.values() if p.standard_name}
    profiles, _ = load_profiles(model.by_name, by_sys)
    origins = set(index_d.origins)
    unit_of = graph_d.unit_of
    tx = {it.source_id for it in model.interactions.values() if it.interaction_type == "transcription"}
    ranks, ranks_tf, ranks_other = [], [], []
    for name, gene, changes in profiles:
        if gene is None or (gene not in origins and unit_of.get(gene) not in origins):
            continue
        changes = sorted([(p, v) for p, v in changes if p != gene], key=lambda x: -abs(x[1]))[:args.max_obs]
        if len(changes) < MIN_CHANGES:
            continue
        obs = [Observation(p, "expr", "up" if v > 0 else "down") for p, v in changes]
        res = pathways.propose(graph_d, obs, index_d, model.names, permutations=args.permutations, null_mode=args.null,
                                score_mode=args.score)
        r = next((i for i, p in enumerate(res, 1) if p.origin == gene or p.origin == unit_of.get(gene)), None)
        ranks.append(r)
        (ranks_tf if gene in tx else ranks_other).append(r)
    for label, xs in (("全体", ranks), ("転写因子", ranks_tf), ("それ以外", ranks_other)):
        if xs:
            print(f"破壊株データ {label} {len(xs)} 株: 10 件以内 {pct(xs, 10):.1f}%・30 件以内 {pct(xs, 30):.1f}%")


if __name__ == "__main__":
    main()
