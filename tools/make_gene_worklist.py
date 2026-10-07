"""遺伝子ごとの文献の裏どりの作業一覧（docs/curation/gene_worklist.tsv）と進み具合。

遺伝子が「済み」とは、文献で確認した関係（確度 A。tor_app/confidence.py）に 1 本以上出てくるか、
探したが論文が見つからなかった記録（docs/curation/genes_not_found.csv）があること（2026-10-04 に決めた）。

まだの遺伝子を、手がかりの多い順に並べる:
  1. 個別実験の BioGRID の関係（PMID つき）がある
  2. ほかの関係（YEASTRACT+・GO 注釈など）がある
  3. 関係がない
区分の中では関係の多い順。列「候補」には、確かめやすい関係を最大 5 本（相手・種類・PMID）出す。
列「文献で済んだ組」には、文献の CSV で flag・reject にした組（A にならない）を出す。同じ組をまた書かないための目印。
網羅的な実験の論文（15 本以上の関係に根拠として出てくる PMID）は PMID に数えない。

使い方: .venv/bin/python tools/make_gene_worklist.py [--out docs/curation/gene_worklist.tsv]
"""
import argparse
import csv
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tor_app import confidence  # noqa: E402

DB = ROOT / "data" / "tor_pathway.db"
LITERATURE = ROOT / "sample_data" / "literature"
NOT_FOUND = ROOT / "docs" / "curation" / "genes_not_found.csv"
BULK_MIN = 15


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "curation" / "gene_worklist.tsv")
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    genes = {i: (g, s, d) for i, g, s, d in con.execute(
        "select id, gene_name, standard_name, description from proteins")}
    rows = con.execute("select source_id, target_id, interaction_type, evidence from interactions").fetchall()

    pmid_use = Counter()
    for *_, ev in rows:
        pmid_use.update(set(re.findall(r"PMID: (\d+)", ev or "")))

    done = set()
    for s, t, _, ev in rows:
        if confidence.level(ev) == "A":
            done |= {s, t}
    searched = set()
    if NOT_FOUND.exists():
        with open(NOT_FOUND, encoding="utf-8") as f:
            searched = {r["gene"] for r in csv.DictReader(f)}

    settled = defaultdict(list)   # 遺伝子 → 文献で flag・reject にした組
    for path in sorted(LITERATURE.glob("*.csv")):
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("status") in ("flag", "reject"):
                    pair = f"{r['source_protein']}→{r['target_protein']} {r['interaction_type']}（{r['status']}）"
                    settled[r["source_protein"]].append(pair)
                    settled[r["target_protein"]].append(pair)

    cands = defaultdict(list)   # 遺伝子 → [(優先, 相手, 種類, PMID)]
    for s, t, ity, ev in rows:
        lv = confidence.level(ev)
        pmids = [p for p in dict.fromkeys(re.findall(r"PMID: (\d+)", ev or "")) if pmid_use[p] < BULK_MIN]
        rank = 0 if (lv == "B" and "BioGRID" in ev and pmids) else 1 if pmids else 2
        for me, other, arrow in ((s, t, "→"), (t, s, "←")):
            cands[me].append((rank, f"{arrow}{genes[other][0]}", ity, ";".join(pmids[:3])))

    out = []
    for i, (g, std, desc) in genes.items():
        if i in done or g in searched:
            continue
        cs = sorted(cands[i], key=lambda c: (c[0], -len(c[3])))
        tier = 3 if not cs else 1 if cs[0][0] == 0 else 2
        text = " | ".join(f"{o} {ity}" + (f" [{p}]" if p else "") for _, o, ity, p in cs[:5])
        out.append((tier, -len(cs), g, std, len(cs), desc, text, " | ".join(settled[g])))
    out.sort()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["区分", "gene", "standard_name", "関係の数", "説明", "候補", "文献で済んだ組"])
        for tier, _, g, std, n, desc, text, done_pairs in out:
            w.writerow([tier, g, std, n, desc, text, done_pairs])

    n = len(genes)
    nd, ns = len(done), len(searched - {genes[i][0] for i in done})
    print(f"遺伝子 {n}: 文献で確認 {nd}（{nd / n:.1%}）・探して見つからず {ns}・まだ {len(out)}")
    for tier, label in ((1, "個別実験の BioGRID あり"), (2, "ほかの関係あり"), (3, "関係なし")):
        print(f"  区分 {tier}（{label}）: {sum(1 for r in out if r[0] == tier)}")
    print(f"→ {args.out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
