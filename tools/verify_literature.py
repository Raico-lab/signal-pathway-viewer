"""文献で整理した関係の CSV（docs/curation/request_core_pathways.md の形式）の PMID を PubMed で確かめる。

各行の PMID について、論文が実在するか、列 paper_title（あれば）が PubMed の題名と一致するか、列 quote（あれば。論文から
写した一文）が題名・要旨、なければ PMC の本文にそのまま含まれるか、酵母の論文か、題名・要旨に上流と下流の遺伝子（SGD の別名も含む。複合体の名前（data/complex_groups.csv）ならその別名）が出てくるかを
調べ、一覧（TSV）にする。複合体の点検（docs/curation/request_complexes.md の形式）のように上流・下流の列がない CSV では、
列 gene の遺伝子が出てくるかを調べる。
「両方の遺伝子に言及」以外の行は、内容を読んで確かめるまで取り込まない。

使い方: .venv/bin/python tools/verify_literature.py 確かめる.csv [...] --out 結果.tsv
（NCBI E-utilities を使う。API キーは環境変数 NCBI_API_KEY か ~/.ncbi_api_key から読み、あれば 1 秒に 10 回、
なければ 1 秒に 3 回までに抑える）
"""
import argparse
import csv
import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(ROOT))
from tor_app import complex_groups  # noqa: E402

SGD = ROOT / "raw_data" / "SGD_features.tab"
PROTEIN_NAMES = ROOT / "data" / "protein_names.csv"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


def ncbi_api_key() -> str:
    """NCBI の API キー（環境変数 NCBI_API_KEY、なければ ~/.ncbi_api_key）。なければ空。"""
    key = os.environ.get("NCBI_API_KEY", "").strip()
    path = Path.home() / ".ncbi_api_key"
    if not key and path.exists():
        key = path.read_text(encoding="utf-8").strip()
    return key


API_KEY = ncbi_api_key()
WAIT = 0.11 if API_KEY else 0.4   # 要求の間隔（キーありは 1 秒に 10 回、なしは 3 回まで）


def aliases() -> dict[str, set[str]]:
    cols = ["sgdid", "type", "qualifier", "systematic", "gene", "alias", "parent", "sgdid2", "chrom", "start", "stop",
            "strand", "gp", "cv", "sv", "desc"]
    raw = pd.read_csv(SGD, sep="\t", header=None, names=cols, dtype=str, keep_default_na=False, quoting=3)
    out = {}
    for _, r in raw[raw["type"] == "ORF"].iterrows():
        names = {r["gene"], r["systematic"]} | {a for a in r["alias"].split("|") if a}
        out[(r["gene"] or r["systematic"]).upper()] = {n for n in names if len(n) >= 3}
    # 複合体の名前（data/complex_groups.csv）は、その別名（TORC1・calcineurin など）が出てくれば言及とみなす
    for key, g in complex_groups.groups().items():
        out[key] = set(g["aliases"]) | {g["label"]}
    # 1 つの遺伝子に決まるタンパク質名（data/protein_names.csv。例: iso-1-cytochrome c → CYC1）も、その遺伝子の言及とみなす
    if PROTEIN_NAMES.exists():
        with open(PROTEIN_NAMES, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out.setdefault(r["gene"].strip().upper(), set()).add(r["name"].strip())
    return out


def fetch(url: str, retries: int = 4) -> str:
    """NCBI は混んでいると一時的なエラー（429・5xx）を返すので、間をおいて取り直す。"""
    if API_KEY:
        url += ("&" if "?" in url else "?") + "api_key=" + API_KEY
    request = urllib.request.Request(url, headers={"User-Agent": "PathwaysViewer/1.0"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            if attempt == retries or e.code not in (429, 500, 502, 503, 504):
                raise
        except urllib.error.URLError:
            if attempt == retries:
                raise
        time.sleep(2 * (attempt + 1))


def pubmed(pmids: list[str]) -> dict[str, dict]:
    info: dict[str, dict] = {}
    for i in range(0, len(pmids), 150):
        batch = pmids[i:i + 150]
        summary = json.loads(fetch(EUTILS + "esummary.fcgi?db=pubmed&retmode=json&id=" + ",".join(batch)))["result"]
        time.sleep(WAIT)
        xml = fetch(EUTILS + "efetch.fcgi?db=pubmed&rettype=abstract&retmode=xml&id=" + ",".join(batch))
        time.sleep(WAIT)
        abstracts = {}
        for art in re.findall(r"<PubmedArticle>.*?</PubmedArticle>", xml, re.S):
            pid = re.search(r"<PMID[^>]*>(\d+)</PMID>", art).group(1)
            parts = re.findall(r"<ArticleTitle>(.*?)</ArticleTitle>", art, re.S) + \
                re.findall(r"<AbstractText[^>]*>(.*?)</AbstractText>", art, re.S)
            abstracts[pid] = html.unescape(re.sub(r"<[^>]+>", "", " ".join(parts)))
        for p in batch:
            s = summary.get(p, {})
            info[p] = {"exists": bool(s) and "error" not in s, "title": s.get("title", ""),
                       "year": s.get("pubdate", "")[:4], "journal": s.get("source", ""), "text": abstracts.get(p, "")}
    return info


_pmc_cache: dict[str, str] = {}


def pmc_text(pmid: str) -> str:
    """PubMed の論文の PMC 本文（あれば）。引用が要旨にないとき、本文にあるかを確かめるために使う。"""
    if pmid in _pmc_cache:
        return _pmc_cache[pmid]
    text = ""
    try:
        links = json.loads(fetch(EUTILS + f"elink.fcgi?dbfrom=pubmed&db=pmc&retmode=json&id={pmid}"))
        ids = [l for ls in links.get("linksets", []) for db in ls.get("linksetdbs", []) if db.get("linkname") == "pubmed_pmc"
               for l in db["links"]]
        time.sleep(WAIT)
        if ids:
            xml = fetch(EUTILS + f"efetch.fcgi?db=pmc&id={ids[0]}&rettype=xml")
            text = html.unescape(re.sub(r"<[^>]+>", " ", xml))
            time.sleep(WAIT)
    except Exception:  # noqa: BLE001 - 本文が取れなければ要旨だけで判定する
        text = ""
    _pmc_cache[pmid] = text
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    names = aliases()
    rows = []
    for f in args.files:
        with open(f, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                r["file"] = f.name
                rows.append(r)
    pmids = sorted({p.strip() for r in rows for p in (r.get("pmids") or "").split(";") if p.strip().isdigit()})
    info = pubmed(pmids) if pmids else {}

    def mentioned(gene: str, text: str) -> bool:
        return any(re.search(r"\b" + re.escape(n) + r"p?\b", text, re.I) for n in names.get(gene.upper(), {gene}))

    def norm(t: str) -> str:
        return re.sub(r"[^a-z0-9]", "", t.lower())

    out = []
    for r in rows:
        given = [t.strip() for t in (r.get("paper_title") or "").split(";")]
        # 引用は列 quote、なければ確認済みの CSV（sample_data/literature/）の列 evidence（論文から写した一文）
        quotes = [q.strip() for q in (r.get("quote") or r.get("evidence") or "").split(" | ")]
        source = r.get("source_protein") or r.get("gene", "")
        target = r.get("target_protein") or source
        for k, p in enumerate([x.strip() for x in (r.get("pmids") or "").split(";") if x.strip()] or [""]):
            i = info.get(p, {"exists": False, "title": "", "year": "", "journal": "", "text": ""})
            text = f"{i['title']} {i['text']}"
            s_ok, t_ok = mentioned(source, text), mentioned(target, text)
            title = given[k] if k < len(given) else ""
            title_ok = not title or norm(title) == norm(i["title"]) or norm(title) in norm(i["title"])
            quote = quotes[k] if k < len(quotes) else ""
            quote_ok = not quote or norm(quote) in norm(text)
            in_body = False
            if quote and not quote_ok and i["exists"]:
                in_body = norm(quote) in norm(pmc_text(p))   # 要旨になければ PMC の本文を見る
                quote_ok = in_body
            verdict = ("PMID なし" if not i["exists"] else
                       "題名が一致しない" if not title_ok else
                       "引用が要旨にない" if not quote_ok else
                       "両方の遺伝子に言及" if s_ok and t_ok else
                       "片方だけ言及" if s_ok or t_ok else "どちらにも言及なし")
            out.append([r["file"], source, r.get("target_protein") or r.get("complex", ""), r.get("interaction_type", ""),
                        r.get("effect", ""), p, i["year"], i["journal"],
                        "yes" if re.search(r"yeast|cerevisiae", text, re.I) else "no",
                        verdict + ("（引用は本文）" if in_body else ""), i["title"][:200]])
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["file", "source", "target", "type", "effect", "pmid", "year", "journal", "酵母の論文", "判定", "title"])
        w.writerows(out)
    print(f"PMID {len(pmids)} 件・{len(out)} 行:", dict(Counter(o[9] for o in out)), f"→ {args.out}")


if __name__ == "__main__":
    main()
