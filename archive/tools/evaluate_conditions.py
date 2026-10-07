"""原因の分かっている条件（熱ショック・浸透圧・酸化・窒素飢餓・アミノ酸飢餓・ラパマイシンなど）の発現データで、
制御遺伝子探索が「その条件で働く制御因子」を何位に挙げるかを数える（sample_data/condition_eval/）。

各条件の時点の log2 比のうち、|log2 比| ≥ 1（2 倍）の遺伝子を変化の大きい順に最大 60 個とり、「発現 増／減」の観測にする。
正解の制御因子（遺伝子名、または data/complexes.csv のまとまりの名前）ごとに、候補の順位と表示した向きを出す。

使い方: .venv/bin/python tools/evaluate_conditions.py [--depth 3] [--max-obs 60] [--signal-mode best|causal] [--rerank N]
"""
import argparse
import csv
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tor_app.db.database import Database  # noqa: E402
from tor_app.graph_data import PathwayModel  # noqa: E402
from tor_app.regulator_search import Observation, SignedGraph, search  # noqa: E402

DATA = ROOT / "raw_data" / "CONDITIONS"
CONDITIONS = ROOT / "sample_data" / "condition_eval" / "conditions.csv"
MIN_LOG2 = 1.0


def read_column(path: Path, column: str, baseline: str = "") -> dict[str, float]:
    """PCL ファイルの 1 列（baseline があればその列との差）。キーは Systematic 名と遺伝子名（大文字）。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader)
        def find(name: str) -> int:   # Gasch のデータは列名の後ろに「; src: …」が付く
            exact = [i for i, h in enumerate(header) if h.strip() == name]
            return exact[0] if exact else next(i for i, h in enumerate(header) if h.strip().startswith(name))
        ci = find(column)
        bi = find(baseline) if baseline else None
        out = {}
        for row in reader:
            if not row or row[0] == "EWEIGHT" or len(row) <= ci:
                continue
            try:
                v = float(row[ci]) - (float(row[bi]) if bi is not None else 0.0)
            except (ValueError, IndexError):
                continue
            for key in (row[0].strip().upper(), row[1].strip().upper().split("|")[0]):
                if key:
                    out.setdefault(key, v)
    return out


def observations(values: dict[str, float], lookup: dict[str, int], max_obs: int) -> list[Observation]:
    changes = {}
    for key, v in values.items():
        pid = lookup.get(key)
        if pid is not None and abs(v) >= MIN_LOG2 and abs(v) > abs(changes.get(pid, 0.0)):
            changes[pid] = v
    top = sorted(changes.items(), key=lambda x: -abs(x[1]))[:max_obs]
    return [Observation(p, "expr", "up" if v > 0 else "down") for p, v in top]


def rank_of(results, target: str, model: PathwayModel) -> tuple[int | None, int | None]:
    """正解（遺伝子名またはまとまりの名前）の順位と表示した向き。"""
    pid = model.by_name.get(target.upper())
    for i, c in enumerate(results, 1):
        if c.name == target or c.name.startswith(target + "（") or (pid is not None and (c.gene == pid or pid in c.members)):
            return i, c.direction
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--max-obs", type=int, default=60)
    ap.add_argument("--signal-mode", choices=["best", "causal"], default=None)
    ap.add_argument("--rerank", type=int, default=0, help="上位 N 件をシミュレーションで検証して並べ直す（0 なら行わない）")
    ap.add_argument("--rerank-weight", type=float, default=1.0, help="並べ直しで一致度に掛ける重み")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    db = Database(str(ROOT / "data" / "tor_pathway.db"))
    model = PathwayModel(db.proteins(), db.interactions(), db.categories())
    lookup = {**model.by_name, **{p.standard_name.upper(): p.id for p in model.proteins.values() if p.standard_name}}
    graph = SignedGraph(list(model.interactions.values()), False, {}, set(), model.units, model.names)
    if args.signal_mode:
        graph.signal_mode = args.signal_mode
    rerank = None
    if args.rerank:
        from tor_app import sim_verify
        rerank = sim_verify

    with open(CONDITIONS, encoding="utf-8") as f:
        conds = list(csv.DictReader(f))
    all_ranks, best_ranks, right_dir, total = [], [], 0, 0
    for cond in conds:
        values = read_column(DATA / cond["file"], cond["column"], cond.get("baseline", ""))
        obs = observations(values, lookup, args.max_obs)
        res = search(graph, obs, args.depth, names=model.names)
        if rerank:
            res = rerank.rerank(model, res, obs, args.rerank, weight=args.rerank_weight)
        answers = [a.split(":") for a in cond["expected"].split(";") if a]
        cells, ranks = [], []
        for name, sign in answers:
            r, d = rank_of(res, name, model)
            want = 1 if sign == "+" else -1
            total += 1
            if r is not None:
                ranks.append(r)
                right_dir += d == want
            mark = "" if r is None else ("○" if d == want else ("×" if d == -want else "？"))
            cells.append(f"{name} {r if r is not None else '-'}{mark}")
            all_ranks.append(r)
        best_ranks.append(min(ranks) if ranks else None)
        if not args.quiet:
            print(f"{cond['name']}（観測 {len(obs)}）: " + "、".join(cells))
    n = len(all_ranks)
    pct = lambda k: 100 * sum(r is not None and r <= k for r in all_ranks) / n   # noqa: E731
    best_pct = lambda k: 100 * sum(r is not None and r <= k for r in best_ranks) / len(best_ranks)   # noqa: E731
    found = [r for r in all_ranks if r is not None]
    print(f"\n正解 {n} 件（{len(conds)} 条件）: 1 位 {pct(1):.0f}% | 5 位以内 {pct(5):.0f}% | 10 位以内 {pct(10):.0f}% | "
          f"20 位以内 {pct(20):.0f}% | 候補に入らない {100 * (n - len(found)) / n:.0f}% | 順位の中央値 "
          f"{statistics.median(found) if found else '-'} | 向きが正しい {100 * right_dir / max(len(found), 1):.0f}%（順位が付いたもの）")
    print(f"条件ごとの、いちばん上の正解: 1 位 {best_pct(1):.0f}% | 5 位以内 {best_pct(5):.0f}% | 10 位以内 {best_pct(10):.0f}%")


if __name__ == "__main__":
    main()
