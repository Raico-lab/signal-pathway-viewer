"""転写因子の条件ごとの活性から、標的の mRNA の上下を 1 段だけ予測し、条件ごとの発現と比べて正解率を出す。

予測: 条件 c で転写因子 T の活性が上がる（下がる）と目印の表（data/activity_markers.csv）にあれば、向きの付いた
転写制御 T → Y で Y の上下を予測する（促進なら T と同じ向き、抑制なら逆）。Y に入る予測がすべて同じ向きのときだけ
予測を出し、食い違えば「分からない」とする。一過性の上下（transient_up・transient_down）も上下として使う
（SPELL の測定の多くは 15〜60 分後）。

答え: data/expression.db の mRNA のまとめ（条件ごとの log2 比の中央値）を、条件ごとに全遺伝子の中央値・標準偏差で
標準化した値 z。|z| ≥ Z_CHANGED を「変わった」とする（tools/condition_expression.py と同じ z）。

出す数字:
- 変わった割合: 予測を出した標的のうち、実際に変わった割合（全遺伝子での割合と比べる）
- 正解率: 予測を出して実際に変わった標的のうち、向きが合った割合
- 多い方を答えた場合: 同じ標的に、その条件で変わった遺伝子の多い方の向き（上がる・下がる）を答えたときの正解率（基準）

向きの根拠ごとにも分ける。「条件ごとの発現」で付けた向き（tools/condition_expression.py）は、同じ発現データと
同じ目印から推定したので、既定では予測に使わない（--include-circular で使う。数字は別に出す）。
目印が標的の発現そのもの（「…に依存する遺伝子の発現」など）の行も同じデータを見ている恐れがあるので、
目印の種類（発現で見た目印・それ以外）でも分ける（言葉での粗い判定）。

使い方: .venv/bin/python tools/predict_tf_targets.py [--out docs/curation/tf_target_prediction.tsv] [--include-circular]
"""
import argparse
import csv
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKERS = ROOT / "data" / "activity_markers.csv"
EXPRESSION = ROOT / "data" / "expression.db"
DB = ROOT / "data" / "tor_pathway.db"
Z_CHANGED = 1.0
SIGN = {"up": 1, "transient_up": 1, "down": -1, "transient_down": -1}
EFFECT_SIGN = {"activate": 1, "inhibit": -1}
# 目印が標的の発現を見ているらしい言葉（粗い判定。HAC1 mRNA のスプライシングなど、標的でないものも少し混ざる）
EXPRESSION_MARKER = re.compile(r"標的|依存する遺伝子|依存する転写|レギュロン|の転写|遺伝子の発現|発現の誘導|の発現")
CIRCULAR = "条件ごとの発現"


def provenance(evidence: str) -> str:
    """向きの根拠の種類。後から付けた向き（「作用の向き: …」）を先に見る。"""
    m = re.search(r"作用の向き: (\S+)", evidence)
    if m:
        head = m.group(1)
        for key, name in [("条件ごとの発現", CIRCULAR), ("IDEA", "IDEA"), ("破壊株", "Deleteome"), ("SGD", "SGD GO")]:
            if head.startswith(key):
                return name
        return "その他"
    if evidence.startswith("文献で確認"):
        return "文献"
    if evidence.startswith("YEASTRACT"):
        return "YEASTRACT+"
    if evidence.startswith("SGD GO"):
        return "SGD GO"
    return "その他"


def load_edges() -> list[tuple[str, str, int, str]]:
    """向きの付いた転写制御: (転写因子, 標的, +1/-1, 根拠の種類)。名前は大文字の遺伝子名。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        "select a.gene_name, b.gene_name, i.effect, i.evidence from interactions i "
        "join proteins a on a.id = i.source_id join proteins b on b.id = i.target_id "
        "where i.interaction_type = 'transcription' and i.effect in ('activate', 'inhibit')")
    return [(a.upper(), b.upper(), EFFECT_SIGN[e], provenance(ev or "")) for a, b, e, ev in rows]


def load_activity() -> dict[tuple[str, str], tuple[int, str, str]]:
    """(転写因子, 条件) → (+1/-1, 目印の種類, 目印)。同じ組で向きが食い違えば外す。"""
    out: dict = {}
    bad = set()
    with open(MARKERS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["change"] not in SIGN:
                continue
            kind = "発現で見た目印" if EXPRESSION_MARKER.search(r["marker"]) else "それ以外の目印"
            for g in (r["genes"] or r["unit"]).split(";"):
                key = (g.strip().upper(), r["condition"])
                s = SIGN[r["change"]]
                if key in out and out[key][0] != s:
                    bad.add(key)
                elif key not in out or kind == "それ以外の目印":
                    out[key] = (s, kind, r["marker"])
    return {k: v for k, v in out.items() if k not in bad}


def load_z() -> dict[str, dict[str, float]]:
    """条件 → {遺伝子: z}。"""
    con = sqlite3.connect(f"file:{EXPRESSION}?mode=ro", uri=True)
    values: dict[str, dict[str, float]] = defaultdict(dict)
    for cond, name, orf, value in con.execute(
            "select s.condition, g.name, g.orf, s.value from summary s join genes g on g.id = s.gene_id "
            "where s.kind = 'mrna' and s.value is not null"):
        values[cond][(name or orf).upper()] = value
    z = {}
    for cond, vs in values.items():
        xs = sorted(vs.values())
        median = xs[len(xs) // 2]
        mean = sum(xs) / len(xs)
        sd = (sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
        z[cond] = {g: (v - median) / sd for g, v in vs.items()}
    return z


def predict(edges, activity, z, use) -> list[dict]:
    """条件 × 標的ごとの予測。use(根拠の種類) が真の関係だけ使う。"""
    votes: dict[tuple[str, str], list] = defaultdict(list)
    for tf, target, sign, prov in edges:
        if not use(prov):
            continue
        for cond in z:
            act = activity.get((tf, cond))
            if act and target in z[cond]:
                votes[(cond, target)].append((tf, act[0] * sign, prov, act[1]))
    rows = []
    for (cond, target), vs in votes.items():
        signs = {v[1] for v in vs}
        zt = z[cond][target]
        rows.append({
            "condition": cond, "target": target, "z": zt,
            "pred": vs[0][1] if len(signs) == 1 else 0,
            "tfs": vs,
        })
    return rows


def majority(z: dict) -> dict[str, int]:
    """条件ごとに、変わった遺伝子の多い方の向き。"""
    out = {}
    for cond, vs in z.items():
        up = sum(1 for v in vs.values() if v >= Z_CHANGED)
        down = sum(1 for v in vs.values() if v <= -Z_CHANGED)
        out[cond] = 1 if up >= down else -1
    return out


def score(rows, z, major) -> dict:
    """予測の行の集まりの数字。"""
    pred = [r for r in rows if r["pred"] != 0]
    changed = [r for r in pred if abs(r["z"]) >= Z_CHANGED]
    correct = sum(1 for r in changed if (r["z"] > 0) == (r["pred"] > 0))
    base = sum(1 for r in changed if (r["z"] > 0) == (major[r["condition"]] > 0))
    rate = {c: sum(1 for v in vs.values() if abs(v) >= Z_CHANGED) / len(vs) for c, vs in z.items()}
    background = sum(rate[r["condition"]] for r in pred) / len(pred) if pred else 0
    return {
        "予測": len(pred), "食い違い": sum(1 for r in rows if r["pred"] == 0),
        "変わった": len(changed), "変わった割合": len(changed) / len(pred) if pred else 0,
        "全遺伝子で変わる割合": background or 0,
        "正解": correct, "正解率": correct / len(changed) if changed else 0,
        "多い方を答えた場合": base / len(changed) if changed else 0,
    }


def fmt(s: dict) -> str:
    return (f"予測 {s['予測']:>6}（食い違い {s['食い違い']}）  変わった {s['変わった']:>5}"
            f"（{s['変わった割合']:.1%}、全遺伝子では {s['全遺伝子で変わる割合']:.1%}）  "
            f"正解率 {s['正解率']:.1%}（多い方を答えると {s['多い方を答えた場合']:.1%}）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "curation" / "tf_target_prediction.tsv")
    ap.add_argument("--include-circular", action="store_true",
                    help="「条件ごとの発現」で付けた向きも予測に使う（同じデータで答え合わせになる）")
    args = ap.parse_args()
    edges, activity, z = load_edges(), load_activity(), load_z()
    major = majority(z)
    tfs_with_activity = {tf for tf, c in activity if c in z}
    print(f"向きの付いた転写制御 {len(edges)} 本、活性の分かる (転写因子, 条件) {len(activity)} 組"
          f"（mRNA のある条件で、転写因子 {len({e[0] for e in edges} & tfs_with_activity)} 個）")
    print(f"判定: |z| ≥ {Z_CHANGED} を変わったとする\n")

    use_main = (lambda p: True) if args.include_circular else (lambda p: p != CIRCULAR)
    rows = predict(edges, activity, z, use_main)
    print("■ 全体" + ("（「条件ごとの発現」の向きも使う）" if args.include_circular else "（「条件ごとの発現」の向きは使わない）"))
    print("  " + fmt(score(rows, z, major)))

    print("\n■ 向きの根拠ごと（その根拠の関係だけで予測）")
    for prov in ["文献", "YEASTRACT+", "IDEA", "Deleteome", "SGD GO", CIRCULAR]:
        s = score(predict(edges, activity, z, lambda p, prov=prov: p == prov), z, major)
        if s["予測"]:
            print(f"  {prov:<12}" + fmt(s) + ("  ※同じデータで推定した向き" if prov == CIRCULAR else ""))

    print("\n■ 目印の種類ごと（予測を出した転写因子の目印がすべてその種類の行）")
    for kind in ["それ以外の目印", "発現で見た目印"]:
        sub = [r for r in rows if all(v[3] == kind for v in r["tfs"])]
        print(f"  {kind:<10}" + fmt(score(sub, z, major)))

    print("\n■ 条件ごと")
    by_cond = defaultdict(list)
    for r in rows:
        by_cond[r["condition"]].append(r)
    for cond in sorted(by_cond, key=lambda c: -len(by_cond[c])):
        s = score(by_cond[cond], z, major)
        if s["変わった"]:
            print(f"  {cond:<22}" + fmt(s))

    print("\n■ 転写因子ごと（その転写因子 1 つで決まった予測。変わった標的が 10 以上）")
    per_tf = defaultdict(lambda: Counter())
    for r in rows:
        if r["pred"] == 0 or abs(r["z"]) < Z_CHANGED:
            continue
        ok = (r["z"] > 0) == (r["pred"] > 0)
        for tf, s, prov, kind in r["tfs"]:
            per_tf[(tf, r["condition"])]["ok" if ok else "ng"] += 1
    by_tf = defaultdict(Counter)
    for (tf, cond), c in per_tf.items():
        by_tf[tf]["ok"] += c["ok"]
        by_tf[tf]["ng"] += c["ng"]
    for tf, c in sorted(by_tf.items(), key=lambda kv: kv[1]["ok"] / max(1, sum(kv[1].values()))):
        n = c["ok"] + c["ng"]
        if n >= 10:
            conds = sorted({cond for (t, cond) in per_tf if t == tf})
            print(f"  {tf:<8} {c['ok']:>5}/{n:<5} {c['ok'] / n:.0%}  条件: {'・'.join(conds)}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["condition", "target", "z", "predicted", "verdict", "tfs"])
        for r in sorted(rows, key=lambda r: (r["condition"], r["target"])):
            if r["pred"] == 0:
                verdict = "食い違い"
            elif abs(r["z"]) < Z_CHANGED:
                verdict = "変わらず"
            else:
                verdict = "正解" if (r["z"] > 0) == (r["pred"] > 0) else "逆"
            w.writerow([r["condition"], r["target"], f"{r['z']:.2f}",
                        {1: "上がる", -1: "下がる", 0: ""}[r["pred"]], verdict,
                        "; ".join(f"{tf}({'+' if s > 0 else '-'},{prov})" for tf, s, prov, _ in r["tfs"])])
    print(f"\n予測の一覧: {args.out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
