"""パラログ（役割が重なりうる遺伝子の組）の一覧 data/paralogs.tsv を作る。

候補（raw_data/PARALOGS/）:
- Pillars.tab: YGOB（Yeast Gene Order Browser, v7-Aug2012）。出芽酵母の全ゲノム重複でできた組（オーノログ）。
  S. cerevisiae は 12 列目（A）と 22 列目（B）。両方が埋まっている行が 1 組
- alliance_paralogy_sgd.json.gz: Alliance of Genome Resources の出芽酵母のパラログ（SGD の ID。全ゲノム重複以外も含む）

裏付け（手元のデータ）:
- BioGRID の遺伝的相互作用で、両方を壊すと片方より強く悪くなる記録（合成致死・生育の低下・負の遺伝的相互作用・
  表現型の増強）。「どちらか一方があれば働く」ことの証拠になる
- SGD の GO 注釈（生物学的過程・分子機能）の重なり（Jaccard 係数）

採用の基準（列 accepted）: 遺伝的相互作用の記録が 1 件以上、または GO の重なりが GO_MIN 以上。
根拠のない組は、パラログでも働きが分かれていることがあるので入れない。

使い方: .venv/bin/python tools/build_paralogs.py
"""
import csv
import gzip
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw_data"
PILLARS = RAW / "PARALOGS" / "Pillars.tab"
ALLIANCE = RAW / "PARALOGS" / "alliance_paralogy_sgd.json.gz"
FEATURES = RAW / "SGD_features.tab"
OUT = ROOT / "data" / "paralogs.tsv"
REDUNDANCY = {"Synthetic Lethality": "合成致死", "Synthetic Growth Defect": "生育の低下",
              "Negative Genetic": "負の遺伝的相互作用", "Phenotypic Enhancement": "表現型の増強"}
GO_MIN = 0.5   # GO の重なりだけで採用するときの下限


def gene_names() -> tuple[dict[str, str], dict[str, str]]:
    """SGD の ID → 遺伝子名、systematic 名 → 遺伝子名（遺伝子名がなければ systematic 名）。"""
    by_id, by_sys = {}, {}
    with open(FEATURES, encoding="utf-8") as f:
        for c in csv.reader(f, delimiter="\t"):
            if len(c) > 4 and c[1] == "ORF" and c[3]:
                name = c[4] or c[3]
                by_id[c[0]], by_sys[c[3].upper()] = name, name
    return by_id, by_sys


def candidates(by_id, by_sys) -> dict[tuple[str, str], dict]:
    pairs: dict[tuple[str, str], dict] = {}

    def add(a, b, source, **info):
        if not a or not b or a == b:
            return
        key = tuple(sorted((a, b), key=str.upper))
        pairs.setdefault(key, {"sources": set()})["sources"].add(source)
        pairs[key].update(info)

    with open(PILLARS, encoding="utf-8") as f:
        for c in csv.reader(f, delimiter="\t"):
            if len(c) >= 22 and c[11] != "---" and c[21] != "---":
                add(by_sys.get(c[11].upper()), by_sys.get(c[21].upper()), "YGOB")
    for row in json.load(gzip.open(ALLIANCE))["data"]:
        a, b = (by_id.get(g.split(":")[-1]) for g in (row["gene1"], row["gene2"]))
        add(a, b, "Alliance", confidence=row.get("confidence", ""), identity=row.get("identity", ""))
    return pairs


def genetic_evidence() -> dict[tuple[str, str], dict]:
    """BioGRID の遺伝的相互作用のうち、機能の重なりを示す記録（組 → 種類ごとの件数と PMID）。"""
    path = next(iter(sorted(RAW.glob("**/BIOGRID-ORGANISM-Saccharomyces_cerevisiae_S288c-*.tab3.txt"))))
    found: dict[tuple[str, str], dict] = defaultdict(lambda: {"types": defaultdict(int), "pmids": set()})
    with open(path, encoding="utf-8") as f:
        next(f)
        for c in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(c) > 14 and c[12] == "genetic" and c[11] in REDUNDANCY:
                key = tuple(sorted((c[7], c[8]), key=str.upper))
                found[key]["types"][c[11]] += 1
                if c[14].startswith("PUBMED:"):
                    found[key]["pmids"].add(c[14].split(":")[1])
    return found


def go_terms() -> dict[str, set[str]]:
    gaf = next(iter(sorted((RAW / "SGD_GO").glob("gene_association.sgd.*.gaf"), reverse=True)))
    terms: dict[str, set[str]] = defaultdict(set)
    with open(gaf, encoding="utf-8") as f:
        for line in f:
            if line.startswith("!"):
                continue
            c = line.rstrip("\n").split("\t")
            if len(c) > 8 and c[8] in ("P", "F") and "NOT" not in c[3]:
                terms[c[2].upper()].add(c[4])
    return terms


def main() -> None:
    by_id, by_sys = gene_names()
    pairs = candidates(by_id, by_sys)
    genetic = genetic_evidence()
    terms = go_terms()
    rows = []
    for (a, b), info in sorted(pairs.items(), key=lambda kv: (kv[0][0].upper(), kv[0][1].upper())):
        ev = genetic.get((a, b)) or genetic.get((b, a)) or {"types": {}, "pmids": set()}
        ta, tb = terms.get(a.upper(), set()), terms.get(b.upper(), set())
        jaccard = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
        accepted = bool(ev["types"]) or jaccard >= GO_MIN
        rows.append([a, b, "・".join(sorted(info["sources"])), info.get("confidence", ""), info.get("identity", ""),
                     "・".join(f"{REDUNDANCY[t]} {n}" for t, n in sorted(ev["types"].items())),
                     ",".join(sorted(ev["pmids"], key=int)), f"{jaccard:.2f}", "yes" if accepted else ""])
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["gene_a", "gene_b", "sources", "alliance_confidence", "identity", "genetic", "pmids",
                    "go_overlap", "accepted"])
        w.writerows(rows)
    accepted = [r for r in rows if r[-1]]
    by_genetic = sum(1 for r in rows if r[5])
    print(f"候補 {len(rows)} 組（YGOB {sum('YGOB' in r[2] for r in rows)}・Alliance {sum('Alliance' in r[2] for r in rows)}・"
          f"両方 {sum(r[2] == 'Alliance・YGOB' for r in rows)}）")
    print(f"採用 {len(accepted)} 組（遺伝的相互作用の記録あり {by_genetic}・GO の重なりだけ {len(accepted) - by_genetic}）→ {OUT}")


if __name__ == "__main__":
    main()
