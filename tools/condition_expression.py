"""条件ごとの発現（data/expression.db）と転写因子の条件ごとの活性（data/activity_markers.csv）から、転写制御の作用の向きを推定する。

転写因子 X の活性が上がる（下がる）条件で、標的 Y の mRNA がほかの遺伝子と比べて上がっていれば（下がっていれば）、
X → Y は「促進」と推定する（逆なら「抑制」）。条件ごとに全遺伝子の中央値・標準偏差で標準化した値（z）に、
X の活性の向き（上がる +1、下がる -1）を掛け、2 つ以上の条件で向きがそろい、平均の大きさが 1.0 以上のときだけ使う。

確かめ方（2026-10-04）: 文献で向きの分かっている転写制御 516 本のうち判定できた 55 本で正解 53 本（96%）。
いつも多い方（促進）を答えると 73%、転写因子の活性の条件を別の転写因子のものに入れ替えると 62% だったので、
転写因子に固有の情報を拾えている。ただし同じ条件で動く別の転写因子の作用（間接の作用）も混ざるので、
文献の記録より弱い根拠として扱う（作用不明の関係にだけ向きを付ける）。
"""
import csv
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPRESSION = ROOT / "data" / "expression.db"
MARKERS = ROOT / "data" / "activity_markers.csv"
MIN_CONDITIONS = 2
MIN_SCORE = 1.0
# 転写因子ごとの関門（tools/import_sources.py）: 記録の向きと GATE_MIN 本以上比べられて、一致が GATE_AGREE 未満なら使わない
GATE_MIN = 10
GATE_AGREE = 0.65
SIGN = {"up": 1, "transient_up": 1, "down": -1, "transient_down": -1}
# 目印の表の単位 → 転写因子（まとめて書かれた単位）
UNITS = {"MSN2/4": ["MSN2", "MSN4"], "RTG1/3": ["RTG1", "RTG3"], "DOT6/TOD6": ["DOT6", "TOD6"], "INO2/INO4": ["INO2", "INO4"]}
LABEL = "条件ごとの発現（SPELL）と転写因子の活性の目印"


def available() -> bool:
    return EXPRESSION.exists() and MARKERS.exists()


def load(gene_of: callable) -> tuple[dict, dict]:
    """(転写因子 → {条件: 活性の向き}, 遺伝子 → {条件: z}) を返す。gene_of(名前) は DB の遺伝子名（なければ None）。"""
    activity: dict[str, dict[str, int]] = defaultdict(dict)
    with open(MARKERS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["change"] in SIGN:
                for tf in UNITS.get(r["unit"], [r["unit"]]):
                    g = gene_of(tf)
                    if g:
                        activity[g][r["condition"]] = SIGN[r["change"]]
    con = sqlite3.connect(f"file:{EXPRESSION}?mode=ro", uri=True)
    values: dict[str, dict[str, float]] = defaultdict(dict)
    by_condition: dict[str, list[float]] = defaultdict(list)
    for cond, name, orf, value in con.execute(
            "select s.condition, g.name, g.orf, s.value from summary s join genes g on g.id = s.gene_id where s.kind = 'mrna'"):
        g = gene_of(name or "") or gene_of(orf or "")
        if g is None or value is None:
            continue
        values[g][cond] = value
        by_condition[cond].append(value)
    stats = {}
    for cond, vs in by_condition.items():
        vs = sorted(vs)
        median = vs[len(vs) // 2]
        mean = sum(vs) / len(vs)
        sd = (sum((v - mean) ** 2 for v in vs) / (len(vs) - 1)) ** 0.5 if len(vs) > 1 else 0
        stats[cond] = (median, sd)
    z = {g: {c: (v - stats[c][0]) / stats[c][1] for c, v in cs.items() if stats[c][1] > 0} for g, cs in values.items()}
    return dict(activity), z


def inferred_effect(activity: dict, z: dict, tf: str, target: str) -> tuple[str, float, list[str]] | None:
    """推定した向き（activate・inhibit）、平均の大きさ、使った条件。決められなければ None。"""
    conds = [(c, s) for c, s in activity.get(tf, {}).items() if c in z.get(target, {})]
    if len(conds) < MIN_CONDITIONS:
        return None
    scores = [s * z[target][c] for c, s in conds]
    mean = sum(scores) / len(scores)
    if abs(mean) < MIN_SCORE or not (all(x > 0 for x in scores) or all(x < 0 for x in scores)):
        return None
    return ("activate" if mean > 0 else "inhibit"), round(mean, 2), [c for c, _ in conds]


def describe(effect: str, score: float, conds: list[str], labels: dict) -> str:
    ja = "促進" if effect == "activate" else "抑制"
    names = "・".join(labels.get(c, c) for c in conds)
    way = "同じ向き" if score > 0 else "逆向き"
    return (f"{LABEL}: 転写因子の活性が変わる条件（{names}）で、標的が活性と{way}に動く（平均 z {score:+.2f}）→ {ja}と推定"
            "（間接の作用を含む）")
