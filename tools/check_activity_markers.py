"""条件ごとの活性の目印の表（data/activity_markers.csv）を、測定のデータ（data/expression.db のリン酸化）と照らし合わせる。

表の列 sites（「遺伝子:部位:符号」を ; で区切る。符号は、その単位の活性が上がるときにリン酸化が上がるなら +、下がるなら -）
について、同じ条件（expression.db の measures.conditions に表の condition を含むもの）の測定値を探し、
表の変化（up・transient_up は上がる、down・transient_down は下がる、no_change は変わらない）から期待される向きと比べる。

判定: 一致（調整済み p < 0.05 で期待どおりの向き）・逆（有意で逆向き）・有意でない・測れていない（その条件・部位の値がない）。
Leutert et al. 2023 は条件にさらして 5 分後の値なので、遅く動く目印（Slt2 の熱など）は「有意でない」になりやすい。

使い方: .venv/bin/python tools/check_activity_markers.py [--out docs/curation/activity_marker_check.tsv]
"""
import argparse
import csv
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKERS = ROOT / "data" / "activity_markers.csv"
EXPRESSION = ROOT / "data" / "expression.db"
PADJ = 0.05
EXPECT = {"up": 1, "transient_up": 1, "down": -1, "transient_down": -1, "no_change": 0}


def verdict(expected: int, value: float, padj: float | None) -> str:
    significant = padj is not None and padj < PADJ
    if expected == 0:
        return "逆" if significant else "一致"
    if not significant:
        return "有意でない"
    return "一致" if (value > 0) == (expected > 0) else "逆"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "curation" / "activity_marker_check.tsv")
    args = ap.parse_args()
    con = sqlite3.connect(f"file:{EXPRESSION}?mode=ro", uri=True)
    with open(MARKERS, encoding="utf-8") as f:
        markers = list(csv.DictReader(f))

    out, counts = [], Counter()
    for m in markers:
        if m["change"] not in EXPECT:   # 通常の増殖の状態（active・inactive）は比べる相手がないので飛ばす
            continue
        for spec in filter(None, (s.strip() for s in (m.get("sites") or "").split(";"))):
            gene, site, sign = spec.split(":")
            expected = EXPECT[m["change"]] * (1 if sign == "+" else -1)
            rows = con.execute(
                "select ms.label, p.value, p.padj from phospho p join genes g on g.id = p.gene_id "
                "join measures ms on ms.id = p.measure_id where g.name = ? and p.site = ? "
                "and (';' || ms.conditions || ';') like ?", (gene, site, f"%;{m['condition']};%")).fetchall()
            if not rows:
                counts["測れていない"] += 1
                out.append([m["unit"], m["condition"], m["change"], spec, "", "", "", "測れていない"])
                continue
            for label, value, padj in rows:
                v = verdict(expected, value, padj)
                counts[v] += 1
                out.append([m["unit"], m["condition"], m["change"], spec, label, f"{value:.2f}",
                            "" if padj is None else f"{padj:.2g}", v])

    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["単位", "条件", "表の変化", "部位", "測定", "log2 比", "調整済み p", "判定"])
        w.writerows(out)
    print(f"目印の部位と測定の組: {len(out)} → {args.out}")
    print("  " + "・".join(f"{k} {counts[k]}" for k in ("一致", "逆", "有意でない", "測れていない") if counts[k]))


if __name__ == "__main__":
    main()
