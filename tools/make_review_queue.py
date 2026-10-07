"""文献で確かめるべき関係の一覧（docs/curation/review_queue.tsv）を、優先度の順に作る。

DB（data/tor_pathway.db）の関係のうち、まだ文献で確認していない（根拠欄が「文献で確認」で始まらない）ものを、
次の区分に分けて並べる。区分の中では、中心の経路の遺伝子（文献で確認した関係に出てくる遺伝子）を
両端に含むもの、根拠の論文（BioGRID の PMID）が多いものを上にする。

  1. 出典で食い違う: 公開データどうし、または破壊株データと記録で作用の向きが逆（根拠欄に ⚠）。
     下流が一般的なストレス応答の遺伝子（archive/data/common_response_genes.csv）のものは、破壊でストレス応答が起きただけの
     ことが多いので、区分の後ろに回し、列「ストレス応答」に印を付ける
  2・3. （以前の KEGG の図だけの区分。KEGG を使わなくなったので空）
  4. 向きを破壊株データから推定した転写制御
  5. 向きが不明のシグナル伝達（BioGRID の酵素活性。個別実験の記録があるもの）
  6. 向きが不明のシグナル伝達（網羅的な実験だけ。プロテインチップなど試験管内で起きただけのことも多いので後回し）

確かめた関係は sample_data/literature/ に加え、DB を作り直す（tools/import_sources.py）と一覧から消える。
探したが裏付けが見つからなかった組（docs/curation/searched_not_found.csv）は、区分の後ろに回し、列「探した日」を出す。
根拠の論文が網羅的な実験の論文だけの組（15 本以上の関係に根拠として出てくる PMID。プロテインチップ・大規模リン酸化解析など）も、
作用を書いた論文が見つかりにくいので、その次に回し、列「網羅的な論文だけ」に印を付ける。
最後に、関係の確度（tor_app/confidence.py の A〜D）ごとの本数を、全体と中心の経路の遺伝子どうしで出す（進み具合の目安）。

使い方: .venv/bin/python tools/make_review_queue.py [--out docs/curation/review_queue.tsv]
"""
import argparse
import csv
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tor_app import confidence  # noqa: E402

DB = ROOT / "data" / "tor_pathway.db"
COMMON = ROOT / "archive" / "data" / "common_response_genes.csv"
LITERATURE = ROOT / "sample_data" / "literature"
NOT_FOUND = ROOT / "docs" / "curation" / "searched_not_found.csv"
BULK_MIN = 15   # この本数以上の関係に根拠として出てくる PMID を網羅的な実験の論文とみなす

CATEGORIES = [
    (1, "出典で食い違う"),
    (4, "向きを破壊株データから推定した転写制御"),
    (5, "向きが不明のシグナル伝達（個別実験あり）"),
    (6, "向きが不明のシグナル伝達（網羅的な実験だけ）"),
]


def category(itype: str, effect: str, ev: str) -> int | None:
    if ev.startswith("文献で確認"):
        return None
    if "⚠" in ev:
        return 1
    if itype == "transcription":
        return 4 if "作用の向き: 破壊株データ" in ev else None   # YEASTRACT+ の向きはそのまま使う（量が多いので食い違いだけ見る）
    if effect in ("", "none"):
        return 6 if "網羅的な実験だけ" in ev else 5
    return None


def pmids(ev: str) -> set[str]:
    """根拠欄に出てくる PMID（「PMID: 1, 2」と「BioGRID（PMID 1, 2）」の形）。"""
    out = set()
    for m in re.finditer(r"PMID:? ([\d, ]+)", ev):
        out |= {x.strip() for x in m.group(1).split(",") if x.strip()}
    for m in re.finditer(r"BioGRID（PMID ([^）]+)）", ev):
        out |= {x for x in re.split(r"[, ]+", m.group(1)) if x.isdigit()}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "curation" / "review_queue.tsv")
    args = ap.parse_args()
    con = sqlite3.connect(DB)
    name = dict(con.execute("select id, gene_name from proteins"))
    rows = con.execute("select source_id, target_id, interaction_type, effect, evidence from interactions").fetchall()

    core = set()
    for a, b, _t, _e, ev in rows:
        if (ev or "").startswith("文献で確認"):
            core |= {name[a], name[b]}
    core = {g.upper() for g in core}
    common = set()
    if COMMON.exists():
        with open(COMMON, encoding="utf-8") as f:
            common = {r["gene"].strip().upper() for r in csv.DictReader(line for line in f if not line.startswith("#"))}

    checked = set()
    for path in sorted(LITERATURE.glob("*.csv")):
        with open(path, encoding="utf-8") as f:
            checked |= {(r["source_protein"].strip().upper(), r["target_protein"].strip().upper())
                        for r in csv.DictReader(f) if (r.get("status") or "").strip() == "flag"}

    searched = {}
    if NOT_FOUND.exists():
        with open(NOT_FOUND, encoding="utf-8") as f:
            searched = {(r["source_protein"].strip().upper(), r["target_protein"].strip().upper()): r["searched_on"].strip()
                        for r in csv.DictReader(f)}

    freq = Counter(p for _a, _b, t, _e, ev in rows if t != "transcription" for p in pmids(ev or ""))
    bulk = {p for p, n in freq.items() if n >= BULK_MIN}

    queue = []
    for a, b, itype, effect, ev in rows:
        ev = ev or ""
        cat = category(itype, effect or "", ev)
        if cat is None or (name[a].upper(), name[b].upper()) in checked:   # 文献で確かめて残した関係（flag）は点検しない
            continue
        sa, sb = name[a], name[b]
        n_core = (sa.upper() in core) + (sb.upper() in core)
        m = re.search(r"BioGRID（PMID ([^）]+)）", ev) or re.search(r"Biochemical Activity \([^)]*\); PMID: ([\d, ]+(?: ほか \d+ 報)?)", ev)
        n_papers = 0
        if m:
            n_papers = len(m.group(1).split(", "))
            extra = re.search(r"ほか (\d+) 報", m.group(1))
            n_papers += int(extra.group(1)) - 1 if extra else 0
        stress = cat == 1 and sb.upper() in common
        done = searched.get((sa.upper(), sb.upper()), "")
        ids = pmids(ev)
        bulk_only = cat in (5, 6) and bool(ids) and ids <= bulk
        queue.append((cat, bool(done), bulk_only, stress, -n_core, -n_papers, sa, sb, itype, effect or "none", n_core, n_papers,
                      stress, done, ev))
    queue.sort()
    labels = dict(CATEGORIES)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["順位", "区分", "上流", "下流", "種類", "今の作用", "中心の経路の遺伝子の数", "BioGRID の論文数", "ストレス応答",
                    "探した日", "根拠", "網羅的な論文だけ"])
        for i, (cat, _, bulk_only, _, _, _, sa, sb, itype, effect, n_core, n_papers, stress, done, ev) in enumerate(queue, 1):
            w.writerow([i, labels[cat], sa, sb, itype, effect, n_core, n_papers, "○" if stress else "", done, ev[:300],
                        "○" if bulk_only else ""])
    by_cat = Counter(q[0] for q in queue)
    both = Counter(q[0] for q in queue if q[10] == 2)
    stress = sum(q[12] for q in queue)
    print(f"文献で確かめるべき関係: {len(queue)} 本 → {args.out}")
    for cat, label in CATEGORIES:
        print(f"  {cat}. {label}: {by_cat[cat]} 本（うち両端が中心の経路の遺伝子 {both[cat]} 本）"
              + (f"。下流がストレス応答の遺伝子 {stress} 本" if cat == 1 else ""))
    print(f"  探したが見つからなかった組（後ろに回した）: {sum(q[1] for q in queue)} 本")
    print(f"  根拠が網羅的な実験の論文だけの組（その次に回した。PMID {len(bulk)} 報）: "
          f"{sum(q[2] and not q[1] for q in queue)} 本")

    levels, levels_core = Counter(), Counter()
    for a, b, _t, _e, ev in rows:
        lv = confidence.level(ev)
        levels[lv] += 1
        if name[a].upper() in core and name[b].upper() in core:
            levels_core[lv] += 1
    for title, c in (("全体", levels), ("両端が中心の経路の遺伝子", levels_core)):
        print(f"関係の確度（{title}、{sum(c.values())} 本）: "
              + "・".join(f"{confidence.label(k)} {c[k]}" for k in confidence.ORDER if c[k]))


if __name__ == "__main__":
    main()
