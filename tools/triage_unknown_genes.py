"""働きの分からない ORF を、PubMed の検索 1 回でふるい分ける（遺伝子の裏どりの下ごしらえ、2026-10-04）。

作業一覧（docs/curation/gene_worklist.tsv）のうち、説明が「Dubious open reading frame」か「unknown function」の遺伝子について、
遺伝子名・系統名・SGD の別名のどれかが題名か要旨に出てくる出芽酵母の論文を PubMed で数える。
0 件の遺伝子は、論文で関係を確かめようがないので docs/curation/genes_not_found.csv に「探して見つからず」として加える。
1 件以上の遺伝子はそのまま一覧に残す（エージェントが読む）。

使い方: .venv/bin/python tools/triage_unknown_genes.py [--dry-run]
"""
import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKLIST = ROOT / "docs" / "curation" / "gene_worklist.tsv"
NOT_FOUND = ROOT / "docs" / "curation" / "genes_not_found.csv"
SGD = ROOT / "raw_data" / "SGD_features.tab"
ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
KEY = (Path.home() / ".ncbi_api_key").read_text().strip()


def aliases() -> dict[str, list[str]]:
    out = {}
    with open(SGD, encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="\t"):
            if len(row) > 5 and row[1] == "ORF":
                names = [n for n in [row[4], row[3], *row[5].split("|")] if n]
                for n in names[:2]:
                    out[n] = names
    return out


def query(names: list[str]) -> str:
    terms = " OR ".join(f'"{n}"[tiab]' for n in dict.fromkeys(names))
    return f"({terms}) AND (yeast OR cerevisiae)"


def count(q: str) -> int:
    url = f"{ESEARCH}?db=pubmed&retmode=json&retmax=0&term={urllib.parse.quote(q)}&api_key={KEY}"
    for k in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                time.sleep(0.11)   # api_key つきで 1 秒 10 回まで
                return int(json.loads(r.read())["esearchresult"]["count"])
        except Exception:
            time.sleep(2 * (k + 1))
    raise RuntimeError(q)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    al = aliases()
    with open(WORKLIST, encoding="utf-8") as f:
        targets = [r for r in csv.DictReader(f, delimiter="\t")
                   if "Dubious open reading frame" in r["説明"] or "unknown function" in r["説明"]]

    zero, kept = [], 0
    for i, r in enumerate(targets, 1):
        names = al.get(r["gene"], [r["gene"], r["standard_name"]])
        q = query(names)
        if count(q) == 0:
            zero.append({"gene": r["gene"], "searched_on": date.today().isoformat(), "read_pmids": "",
                         "note": f"機械のふるい分け（tools/triage_unknown_genes.py）: PubMed で 0 件（{q}）"})
        else:
            kept += 1
        if i % 200 == 0:
            print(f"{i}/{len(targets)}: 0 件 {len(zero)}")

    print(f"対象 {len(targets)}: 論文 0 件 {len(zero)} → 見つからずに記録 / 論文あり {kept} → 一覧に残す")
    if not args.dry_run and zero:
        with open(NOT_FOUND, "a", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=["gene", "searched_on", "read_pmids", "note"]).writerows(zero)


if __name__ == "__main__":
    main()
