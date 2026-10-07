"""遺伝子破壊株の発現データ（Deleteome, Kemmeren et al. 2014 Cell）を読み、転写制御の作用の向きを推定する。

転写因子 X の破壊株で標的 Y の発現が大きく変われば（|log2 比| ≥ log2(1.7) かつ p < 0.05、論文の基準）、
Y が減れば X → Y は「促進」、増えれば「抑制」と推定する。破壊の影響には間接的なもの（X → Z → Y）も混ざるので、
ここで推定した向きは文献の記録より弱い根拠として扱う。

データ: raw_data/DELETEOME/deleteome_all_mutants_ex_wt_var_controls.txt
  （https://deleteome.holstegelab.nl の Downloads から取得。git では管理しない）
"""
import csv
import math
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "raw_data" / "DELETEOME" / "deleteome_all_mutants_ex_wt_var_controls.txt"
FC = math.log2(1.7)
P_MAX = 0.05
LABEL = "Deleteome（Kemmeren et al. 2014）"


def load_changes(gene_of: callable, wanted: set | None = None) -> dict:
    """破壊株ごとに、大きく変わった遺伝子と log2 比・p 値を返す: 壊した遺伝子 → {遺伝子: (log2 比, p 値)}。

    gene_of(システマティック名, 遺伝子名) は遺伝子を返す関数（見つからなければ None）。
    wanted を渡すと、その遺伝子の破壊株だけを読む。同じ遺伝子の破壊株が複数あり、変化の向きが食い違う遺伝子は除く。"""
    with open(DATA, encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t")
        header, kinds = next(reader), next(reader)
        columns: dict[str, dict] = {}
        for i, (name, kind) in enumerate(zip(header, kinds)):
            if i >= 3 and "-del" in name:
                columns.setdefault(name, {})[kind] = i
        mutants = []
        for name, c in columns.items():
            if "M" not in c or "p_value" not in c:
                continue
            gene = gene_of("", name.split("-del")[0].strip())
            if gene is not None and (wanted is None or gene in wanted):
                mutants.append((gene, c["M"], c["p_value"]))
        changes: dict = {g: {} for g, _, _ in mutants}
        conflicting: dict = {g: set() for g, _, _ in mutants}
        for row in reader:
            if len(row) < 3:
                continue
            target = gene_of(row[1].strip(), row[2].strip())
            if target is None:
                continue
            for gene, mi, pi in mutants:
                try:
                    m, p = float(row[mi]), float(row[pi])
                except (ValueError, IndexError):
                    continue
                if abs(m) < FC or p >= P_MAX or target == gene:
                    continue
                old = changes[gene].get(target)
                if old is not None and (old[0] > 0) != (m > 0):
                    conflicting[gene].add(target)
                if old is None or abs(m) > abs(old[0]):
                    changes[gene][target] = (m, p)
    for gene, bad in conflicting.items():
        for t in bad:
            changes[gene].pop(t, None)
    return changes


def inferred_effect(changes: dict, tf, target) -> tuple[str, float, float] | None:
    """X の破壊株での Y の変化から推定した X → Y の作用: ("activate" / "inhibit", log2 比, p 値)。変化がなければ None。"""
    hit = changes.get(tf, {}).get(target)
    if hit is None:
        return None
    m, p = hit
    return ("activate" if m < 0 else "inhibit"), m, p


def describe(effect: str, m: float, p: float) -> str:
    word = "減少" if m < 0 else "増加"
    return (f"破壊株データ {LABEL}: 転写因子の破壊で標的が{word}（log2 比 {m:+.2f}, p={p:.2g}）→ "
            f"{'促進' if effect == 'activate' else '抑制'}と推定")


INFERRED_MARK = "作用の向き: 破壊株データ"


def without_inferred(interactions) -> list:
    """破壊株データから推定して付けた向きを作用不明に戻した関係の一覧（評価で、答えを知ったうえで解かないように）。"""
    out = []
    for it in interactions:
        effect = it.effect
        if INFERRED_MARK in (it.evidence or ""):
            effect = "none"
        out.append(SimpleNamespace(source_id=it.source_id, target_id=it.target_id,
                                   interaction_type=it.interaction_type, effect=effect, evidence=it.evidence))
    return out
