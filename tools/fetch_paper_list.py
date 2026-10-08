"""上部の「情報源」タブに並べる論文の一覧（data/papers.json）を作る。

参考にした論文 = 文献で確認した関係（根拠欄の「文献で確認…」の部分）の PMID
＋ 文献調査の記録（sample_data/literature/verified*.csv）で使った論文（列 pmids・fill_pmid と、verified_note・note に引いた PMID。
  記録の誤りと判断した reject の行も含む。探して見つからなかった記録 docs/curation/searched_not_found.csv の論文は含めない）
＋ data/sources.json の papers（公開データとして使った論文）と、sites の cite（サイトが引用を求める論文）
＋ 条件ごとの発現・リン酸化の測定の論文（data/expression.db の measures。tools/build_expression.py が作る）。書誌（著者・年・雑誌・題名・DOI）は NCBI の
E-utilities（esummary）から取る。DB を直したら・文献調査の回を終えたら、もう一度実行する。
（以前の手作業の複合体（archive/data/complexes.csv）の論文は、今は並べない。複合体の根拠に使うときに戻す）

使い方: .venv/bin/python tools/fetch_paper_list.py
"""
import csv
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "tor_pathway.db"
EXPRESSION_DB = ROOT / "data" / "expression.db"
CURATION = ROOT / "sample_data" / "literature"
OUT = ROOT / "data" / "papers.json"
ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
# 根拠欄で「文献で確認」の部分の終わり（次の出典の始まり）
NEXT_SOURCE = re.compile(r"; (?=公開データ|SGD|YEASTRACT|GO-CAM|UniProt|BioGRID|PhosphoGRID|作用の向き|⚠)")


def literature_pmids() -> set[str]:
    found = set()
    with sqlite3.connect(DB) as con:
        for (ev,) in con.execute("select evidence from interactions where evidence like '%文献で確認%'"):
            part = NEXT_SOURCE.split(ev[ev.index("文献で確認"):])[0]
            for m in re.finditer(r"PMID:? ?([\d, ]+)", part):
                found |= {x.strip() for x in m.group(1).split(",") if x.strip()}
    return found


def curation_pmids() -> set[str]:
    found = set()
    for path in sorted(CURATION.glob("verified*.csv")):
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                found |= set(re.findall(r"\d{6,9}", f"{row.get('pmids') or ''};{row.get('fill_pmid') or ''}"))
                # 要旨のメモに引いた PMID（GO:0005096 などの番号は除く）
                note = f"{row.get('verified_note') or ''} {row.get('note') or ''}"
                found |= set(re.findall(r"(?<![\d:.A-Za-z])\d{6,8}(?![\d.])", note))
    return found


def api_key() -> str:
    key = os.environ.get("NCBI_API_KEY", "")
    path = Path.home() / ".ncbi_api_key"
    if not key and path.exists():
        key = path.read_text().strip()
    return key


def expression_pmids() -> set[str]:
    if not EXPRESSION_DB.exists():
        return set()
    with sqlite3.connect(EXPRESSION_DB) as con:
        return {p for (p,) in con.execute("select distinct pmid from measures") if p}


def fetch(pmids: list[str]) -> dict:
    result = {}
    key = api_key()
    for i in range(0, len(pmids), 150):
        params = {"db": "pubmed", "id": ",".join(pmids[i:i + 150]), "retmode": "json"}
        if key:
            params["api_key"] = key
        data = urllib.parse.urlencode(params).encode()
        with urllib.request.urlopen(urllib.request.Request(ESUMMARY, data=data), timeout=60) as res:
            body = json.load(res)["result"]
        result.update({k: v for k, v in body.items() if k != "uids"})
        time.sleep(0.12 if key else 0.5)
    return result


def citation(s: dict) -> dict:
    authors = [a["name"] for a in s.get("authors", []) if a.get("authtype") == "Author"]
    if not authors:   # 著者が団体だけの論文（"UniProt Consortium" など）は団体名で
        authors = [a["name"] for a in s.get("authors", []) if a.get("authtype") == "CollectiveName"][:1]
    first = (authors[0] if " " in authors[0] and not authors[0].split(" ")[-1].isupper() else authors[0].split(" ")[0]) if authors else ""
    who = f"{first} et al." if len(authors) > 1 else first
    year = (s.get("pubdate") or "")[:4]
    doi = next((a["value"] for a in s.get("articleids", []) if a.get("idtype") == "doi"), "")
    url = f"https://doi.org/{doi}" if doi else f"https://pubmed.ncbi.nlm.nih.gov/{s['uid']}/"
    return {"pmid": s["uid"], "cite": f"{who} {year} {s.get('source', '')}".strip(),
            "title": (s.get("title") or "").rstrip("."), "url": url, "first": first, "year": year,
            "authors": ", ".join(authors)}   # 情報源タブで著者でも検索できるように、全著者（"Aalto MK, Keränen S" の形）


def main() -> None:
    sources = json.loads((ROOT / "data" / "sources.json").read_text(encoding="utf-8"))
    data_papers = {p["pmid"] for p in sources.get("papers", [])}
    data_papers |= {pmid for s in sources.get("sites", []) for pmid in s.get("cite", [])}
    lit = literature_pmids() | curation_pmids()
    expr = expression_pmids()
    pmids = sorted(lit | data_papers | expr, key=int)
    summaries = fetch(pmids)
    papers = []
    for pmid in pmids:
        if pmid not in summaries or "error" in summaries[pmid]:
            print(f"  書誌を取れなかった: PMID {pmid}")
            continue
        p = citation(summaries[pmid])
        p["used"] = [k for k, on in (("data", pmid in data_papers), ("literature", pmid in lit),
                                     ("expression", pmid in expr)) if on]
        papers.append(p)
    papers.sort(key=lambda p: (p["first"].lower(), p["year"], p["pmid"]))
    OUT.write_text(json.dumps({"papers": papers}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"論文 {len(papers)} 本: 文献で確認 {len(lit)}・公開データ {len(data_papers)}・発現とリン酸化 {len(expr)} → {OUT}")


if __name__ == "__main__":
    main()
