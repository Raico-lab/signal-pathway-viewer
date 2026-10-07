"""転写因子を誘導した発現の時系列（IDEA: Hackett et al. 2020 Mol Syst Biol, PMID 32181581）から、転写制御の作用の向きを読む。

転写因子 X を誘導して EARLY_MINUTES 分以内に標的 Y が |log2 比| ≥ MIN_LOG2 で動けば、増えれば X → Y は「促進」、
減れば「抑制」とする。誘導の直後に動くものは直接の標的であることが多い。DB の向きとの一致率（2026-10-02、20 分以内）:
文献で確認した転写制御 100%（50/50）、破壊株データからの推定 95%、YEASTRACT+ の記録 71%。そのため YEASTRACT+ の記録より
優先する（tools/import_sources.py の apply_idea）。破壊株データ（Deleteome）・条件データとは独立なので、評価の水増しにならない。

データ: raw_data/IDEA/idea_kinetics.tsv（時系列ごとのあてはめ: 立ち上がり時刻 t_rise・変化量 v_inter など）
  https://storage.googleapis.com/calico-website-pin-public-bucket/datasets/idea_kinetics.zip
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "raw_data" / "IDEA" / "idea_kinetics.tsv"
EARLY_MINUTES = 20
MIN_LOG2 = 0.5
LABEL = "IDEA（Hackett et al. 2020、転写因子の誘導）"


def load(gene_of: callable) -> dict:
    """(転写因子, 標的) → ("activate" / "inhibit", 変化量, 立ち上がり時刻)。誘導の直後に大きく動いた組だけ。
    同じ組が複数の実験で逆向きに動いたものは除く。gene_of(名前) は DB の遺伝子名を返す関数。"""
    k = pd.read_csv(DATA, sep="\t")
    k = k[(k["t_rise"] <= EARLY_MINUTES) & (k["v_inter"].abs() >= MIN_LOG2)]
    out: dict = {}
    bad = set()
    for tf, gene, v, t in zip(k["TF"], k["GeneName"], k["v_inter"], k["t_rise"]):
        a, b = gene_of(str(tf)), gene_of(str(gene))
        if not a or not b or a == b:
            continue
        effect = "activate" if v > 0 else "inhibit"
        old = out.get((a, b))
        if old is not None and old[0] != effect:
            bad.add((a, b))
        if old is None or t < old[2]:
            out[(a, b)] = (effect, float(v), float(t))
    for pair in bad:
        out.pop(pair, None)
    return out


def describe(effect: str, v: float, t: float) -> str:
    word = "増加" if v > 0 else "減少"
    return (f"{LABEL}: 誘導 {t:.0f} 分で標的が{word}（log2 {v:+.2f}）→ {'促進' if effect == 'activate' else '抑制'}")
