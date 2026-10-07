"""遺伝子を壊したときに、地図の上で経路が切れるか（向きを使わない到達性）を計算し、破壊株のデータと比べる。

A. 1 遺伝子の破壊（Deleteome, Kemmeren et al. 2014）
   X を壊したときに発現が変わると予測する遺伝子 = X から MAX_STEPS 段以内でたどれて、最後の段が転写制御の遺伝子。
   パラログ Y（data/paralogs.tsv の accepted）が、X を通らずに同じ遺伝子に同じ段数以内で届けば「パラログが補う」とする。
   答え: 破壊株で 1.7 倍以上・p < 0.05 で変わった遺伝子（tools/deleteome.py の基準）。
   見るもの: 段数ごとの「変わった割合」（予測しなかった遺伝子の割合と比べる）と、パラログのある株の中で、
   パラログが補う標的と補わない標的の差（パラログのある株はもともと変化が少ないことがあるので、同じ株の中で比べる）。
   注意: YEASTRACT+ の転写制御には Kemmeren 2014 の発現データを根拠にしたものがあるので、転写因子の破壊の 1 段目は甘くなる。
   転写因子でない遺伝子（キナーゼなど）の破壊を分けて出す。

B. 2 遺伝子の破壊（BioGRID の遺伝的相互作用）
   地図の上で「片方を壊してももう片方で届く」組は、両方を壊すと強く効く（負の遺伝的相互作用が多い）と予測する。
   組の種類: パラログ・同じ複合体（Complex Portal）・並んだ経路（同じ上流 U と同じ下流 T を、それぞれ転写制御でない関係で
   U → X → T・U → Y → T と結ぶ）・一続きの経路（X → Y の転写制御でない関係）・でたらめな組（基準）。
   負: Negative Genetic・Synthetic Growth Defect・Synthetic Lethality。正: Positive Genetic。
   対象は、遺伝的相互作用の記録に一度でも出る遺伝子どうしの組。
   注意: パラログの表は「BioGRID の遺伝的相互作用か GO の重なり」で選んだので、genetic 列のある組は分けて出す。

C. 経路が切れるかを調べる: --check A B [--ko X,Y]
   X・Y を壊したときに A から B へ MAX_STEPS 段以内の経路が残るかと、残る経路の例を出す。
   DB には途中を省いた近道（TOR1 → GLN3 など。根拠に「間接の作用」）があり、そのままでは途中を壊しても切れないので、
   --direct-only でそれらを通らずに調べる（非転写の関係 10,843 本のうち 2,023 本、2026-10-06）。

使い方: .venv/bin/python tools/knockout_reach.py [--part A|B] [--check A B --ko X,Y]
"""
import argparse
import csv
import random
import sqlite3
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import deleteome  # noqa: E402

DB = ROOT / "data" / "tor_pathway.db"
PARALOGS = ROOT / "data" / "paralogs.tsv"
COMPLEXES = ROOT / "data" / "complex_portal.tsv"
BIOGRID = ROOT / "raw_data" / "BIOGRID-ORGANISM-LATEST" / "BIOGRID-ORGANISM-Saccharomyces_cerevisiae_S288c-5.0.261.tab3.txt"
MAX_STEPS = 3
NEGATIVE = {"Negative Genetic", "Synthetic Growth Defect", "Synthetic Lethality"}
POSITIVE = {"Positive Genetic"}


class Graph:
    def __init__(self, direct_only: bool = False):
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        self.names = {}
        for gene, orf in con.execute("select gene_name, standard_name from proteins"):
            self.names[gene.upper()] = gene.upper()
            if orf:
                self.names[orf.upper()] = gene.upper()
        self.out: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.inn: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for a, b, kind, evidence in con.execute(
                "select a.gene_name, b.gene_name, i.interaction_type, i.evidence from interactions i "
                "join proteins a on a.id = i.source_id join proteins b on b.id = i.target_id"):
            a, b = a.upper(), b.upper()
            # 根拠に「間接の作用」とある関係は途中を省いた近道なので、direct_only なら通らない（文献の照合で付けた印）
            if direct_only and "間接の作用" in (evidence or ""):
                continue
            if a != b:
                self.out[a].append((b, kind))
                self.inn[b].append((a, kind))

    def gene(self, name: str) -> str | None:
        return self.names.get(name.strip().upper())

    def distances(self, start: str, removed: set[str] = frozenset(), steps: int = MAX_STEPS - 1) -> dict[str, int]:
        """start から steps 段以内で届く遺伝子 → 段数（start は 0）。removed は通らない。"""
        dist = {start: 0}
        q = deque([start])
        while q:
            x = q.popleft()
            if dist[x] >= steps:
                continue
            for y, _ in self.out.get(x, []):
                if y not in dist and y not in removed:
                    dist[y] = dist[x] + 1
                    q.append(y)
        return dist

    def expression_targets(self, start: str, removed: set[str] = frozenset()) -> dict[str, int]:
        """start から MAX_STEPS 段以内で、最後の段が転写制御で届く遺伝子 → いちばん短い段数。"""
        out: dict[str, int] = {}
        for x, d in self.distances(start, removed).items():
            for y, kind in self.out.get(x, []):
                if kind == "transcription" and y != start and y not in removed:
                    if y not in out or d + 1 < out[y]:
                        out[y] = d + 1
        return out

    def path(self, a: str, b: str, removed: set[str]) -> list[str] | None:
        """a から b への MAX_STEPS 段以内の経路の 1 つ（なければ None）。"""
        prev = {a: None}
        depth = {a: 0}
        q = deque([a])
        while q:
            x = q.popleft()
            if x == b:
                p = []
                while x:
                    p.append(x)
                    x = prev[x]
                return p[::-1]
            if depth[x] >= MAX_STEPS:
                continue
            for y, _ in self.out.get(x, []):
                if y not in prev and y not in removed:
                    prev[y] = x
                    depth[y] = depth[x] + 1
                    q.append(y)
        return None


def load_paralogs(g: Graph) -> tuple[dict[str, set[str]], set[frozenset], set[frozenset]]:
    """(遺伝子 → パラログ, 組（遺伝的相互作用を根拠にしていない）, 組（根拠にした）)。"""
    by_gene: dict[str, set[str]] = defaultdict(set)
    independent, genetic = set(), set()
    with open(PARALOGS, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            a, b = g.gene(r["gene_a"]), g.gene(r["gene_b"])
            if r["accepted"] != "yes" or not a or not b:
                continue
            by_gene[a].add(b)
            by_gene[b].add(a)
            (genetic if r["genetic"] else independent).add(frozenset((a, b)))
    return by_gene, independent, genetic


def part_a(g: Graph) -> None:
    paralogs, _, _ = load_paralogs(g)
    regulators = {x for x in g.out}
    changes = deleteome.load_changes(lambda sysname, name: g.gene(sysname) or g.gene(name), wanted=regulators)
    universe = {t for ch in changes.values() for t in ch} | set(g.names.values())
    print(f"A. 1 遺伝子の破壊: Deleteome の破壊株のうち、地図で下流のある遺伝子 {len(changes)} 個\n")
    tally = defaultdict(Counter)   # (区分, 段数 or '補う' など) → 数
    per_mutant = []
    for x, changed in changes.items():
        is_tf = any(k == "transcription" for _, k in g.out.get(x, []))
        group = "転写因子" if is_tf else "転写因子でない"
        targets = g.expression_targets(x)
        covered: set[str] = set()
        for y in paralogs.get(x, ()):
            alt = g.expression_targets(y, removed={x})
            covered |= {t for t, d in targets.items() if t in alt and alt[t] <= d}
        for t in universe - {x}:
            if t in targets:
                key = f"{targets[t]} 段" + ("（パラログが補う）" if t in covered
                                             else "（パラログはあるが補わない）" if x in paralogs else "")
            else:
                key = "届かない"
            tally[(group, key)]["n"] += 1
            tally[(group, key)]["changed"] += t in changed
        per_mutant.append((x, group, len(targets), len(changed), len(set(targets) & set(changed))))
    for group in ["転写因子", "転写因子でない"]:
        n_mut = sum(1 for p in per_mutant if p[1] == group)
        print(f"■ {group}の破壊（{n_mut} 株）: 段数ごとの、変わった遺伝子の割合")
        keys = sorted({k for gr, k in tally if gr == group}, key=lambda k: (k == "届かない", k))
        for k in keys:
            c = tally[(group, k)]
            print(f"  {k:<16} {c['changed']:>7} / {c['n']:<9} {c['changed'] / c['n']:.2%}")
        print()


def load_genetic(g: Graph) -> tuple[set[frozenset], set[frozenset], set[str]]:
    neg, pos, genes = set(), set(), set()
    with open(BIOGRID, encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        next(reader)
        for r in reader:
            if r[12] != "genetic":
                continue
            a, b = g.gene(r[5]) or g.gene(r[7]), g.gene(r[6]) or g.gene(r[8])
            if not a or not b or a == b:
                continue
            genes |= {a, b}
            if r[11] in NEGATIVE:
                neg.add(frozenset((a, b)))
            elif r[11] in POSITIVE:
                pos.add(frozenset((a, b)))
    return neg, pos, genes


def part_b(g: Graph) -> None:
    neg, pos, genes = load_genetic(g)
    _, para_ind, para_gen = load_paralogs(g)
    complex_pairs = set()
    with open(COMPLEXES, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            members = sorted({x for x in (g.gene(n) for n in r["genes"].split(";")) if x})
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    complex_pairs.add(frozenset((a, b)))
    sig_out = {x: {y for y, k in es if k != "transcription"} for x, es in g.out.items()}
    sig_in = {x: {y for y, k in es if k != "transcription"} for x, es in g.inn.items()}
    series = {frozenset((x, y)) for x, ys in sig_out.items() for y in ys}
    # 並んだ経路: 同じ U から入り、同じ T へ出る X・Y
    parallel = set()
    by_up_down: dict[tuple[str, str], set[str]] = defaultdict(set)
    for x in set(sig_in) & set(sig_out):
        if len(sig_in[x]) * len(sig_out[x]) > 400:   # ハブ（CDC28 など）は組の数が膨らむので、両側に 20 を超えるものは外す
            continue
        for u in sig_in[x]:
            for t in sig_out[x]:
                if u != t:
                    by_up_down[(u, t)].add(x)
    for xs in by_up_down.values():
        xs = sorted(xs)
        for i, a in enumerate(xs):
            for b in xs[i + 1:]:
                parallel.add(frozenset((a, b)))
    parallel -= para_ind | para_gen | complex_pairs | series
    in_universe = lambda pairs: {p for p in pairs if p <= genes}   # noqa: E731
    rng = random.Random(0)
    pool = sorted(genes & set(g.names.values()))
    rand = set()
    while len(rand) < 200000:
        a, b = rng.sample(pool, 2)
        rand.add(frozenset((a, b)))
    print(f"B. 2 遺伝子の破壊: 遺伝的相互作用に出る遺伝子 {len(genes)} 個、負の組 {len(neg)}・正の組 {len(pos)}\n")
    base_neg = len(rand & neg) / len(rand)
    base_pos = len(rand & pos) / len(rand)
    rows = [("パラログ（遺伝的相互作用を根拠にしていない）", para_ind), ("パラログ（遺伝的相互作用を根拠に選んだ）", para_gen),
            ("同じ複合体", complex_pairs), ("並んだ経路", parallel), ("一続きの経路（X → Y）", series), ("でたらめな組", rand)]
    print(f"  {'組の種類':<28}{'組':>8}  {'負':>14}  {'正':>14}")
    for name, pairs in rows:
        ps = in_universe(pairs)
        if not ps:
            continue
        n_neg, n_pos = len(ps & neg), len(ps & pos)
        print(f"  {name:<26}{len(ps):>8}  {n_neg:>5} {n_neg / len(ps):>6.1%}（×{n_neg / len(ps) / base_neg:>4.1f}）"
              f"  {n_pos:>5} {n_pos / len(ps):>6.1%}（×{n_pos / len(ps) / base_pos:>4.1f}）")
    print("\n  ×: でたらめな組と比べた倍率")


def check(g: Graph, a: str, b: str, ko: list[str]) -> None:
    ga, gb = g.gene(a), g.gene(b)
    removed = {g.gene(x) for x in ko if g.gene(x)}
    if not ga or not gb:
        print("遺伝子が見つかりません:", a if not ga else b)
        return
    before = g.path(ga, gb, set())
    after = g.path(ga, gb, removed)
    print(f"{ga} → {gb}（{MAX_STEPS} 段以内）")
    print(f"  壊す前: {' → '.join(before) if before else '経路なし'}")
    print(f"  {'・'.join(sorted(removed))} を壊した後: {' → '.join(after) if after else '経路が切れる'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=["A", "B"])
    ap.add_argument("--check", nargs=2, metavar=("A", "B"))
    ap.add_argument("--ko", default="")
    ap.add_argument("--direct-only", action="store_true", help="根拠に「間接の作用」とある関係（近道）を通らない")
    args = ap.parse_args()
    g = Graph(args.direct_only)
    if args.check:
        check(g, *args.check, [x for x in args.ko.split(",") if x])
        return
    if args.part in (None, "A"):
        part_a(g)
    if args.part in (None, "B"):
        part_b(g)


if __name__ == "__main__":
    main()
