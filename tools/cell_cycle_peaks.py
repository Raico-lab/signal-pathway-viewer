"""細胞周期の同調培養の発現（SGD の SPELL）から、遺伝子ごとに「発現の山がどの時期か」を決める。

    .venv/bin/python tools/cell_cycle_peaks.py

時期のはっきりした目印の遺伝子（MARKERS）の平均の変化と、各遺伝子の変化の相関を、時系列ごとに求めて平均する。
相関が一番高い時期をその遺伝子の時期、その相関を周期性の目安（score）とする。時点の分の値や周期の長さは使わない
（時系列の列は時点の順に並んでいる）。4 本以上の時系列で測られた遺伝子だけを出す。

使う時系列（raw_data/SPELL/datasets/）: Spellman 1998（α 因子・cdc15・エルトリエーション）、Cho 1998（cdc28）、
Pramila 2006（α 因子 2 本）、Orlando 2008（野生型 2 本。サイクリン変異株は使わない）。

出力: raw_data/CELL_CYCLE/peak_phase.tsv（orf・name・phase・score・時期ごとの相関・時系列の数）。
転写制御に時期を付けるのは tools/import_sources.py（data/cell_cycle_tfs.csv の転写因子と時期が合う組だけ）。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SPELL = ROOT / "raw_data" / "SPELL" / "datasets"
OUT = ROOT / "raw_data" / "CELL_CYCLE" / "peak_phase.tsv"

# 時期の目印（Spellman 1998 の時期の群の代表的な遺伝子）。M/G1 は分裂期の終わり〜G1 の初め、G1 は G1 の後半（G1/S）、
# S は S 期（S/G2 の遺伝子もここに入る）、G2/M は G2 から分裂期
MARKERS = {
    "M/G1": "SIC1 EGT2 PIR1 ASH1 CTS1 DSE1 DSE2 CDC6 AMN1",
    "G1": "CLN1 CLN2 PCL1 RNR1 CLB5 CLB6 POL1 CDC21 SVS1",
    "S": "HTA1 HTB1 HTA2 HTB2 HHF1 HHT1 HHF2 HHT2 HHO1",
    "G2/M": "CLB1 CLB2 CDC5 CDC20 SWI5 ACE2 BUD4",
}
MIN_SERIES = 4


def read_pcl(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep="\t", index_col=0, low_memory=False)
    df = df.drop(index="EWEIGHT", errors="ignore").drop(columns=["NAME", "GWEIGHT"], errors="ignore")
    df.index = df.index.str.upper()
    return df.apply(pd.to_numeric, errors="coerce").groupby(level=0).mean()


def load_series() -> dict[str, pd.DataFrame]:
    def one(study: str, name: str = "*.pcl") -> Path:
        return next((SPELL / study / study).glob(name))

    series = {}
    for kind in ("alphaFactor", "cdc15", "elutriation"):
        series[f"Spellman 1998 {kind}"] = read_pcl(one("Spellman_1998_PMID_9843569", f"*_{kind}.*.pcl"))
    series["Cho 1998 cdc28"] = read_pcl(one("Cho_1998_PMID_9702192"))
    pramila = read_pcl(one("Pramila_2006_PMID_16912276"))   # α 因子の時系列 2 本（各 25 時点）
    series["Pramila 2006 (1)"], series["Pramila 2006 (2)"] = pramila.iloc[:, :25], pramila.iloc[:, 25:]
    orlando = read_pcl(one("Orlando_2008_PMID_18463633"))
    for rep in ("rep1", "rep2"):
        series[f"Orlando 2008 WT {rep}"] = orlando[[c for c in orlando.columns if "WildType" in c and rep in c]]
    return series


def correlations(df: pd.DataFrame, markers: dict[str, list[str]], exclude: str | None = None) -> pd.DataFrame:
    """各遺伝子の変化と、時期ごとの目印の平均の変化の相関（列 = 時期）。"""
    x = df.sub(df.mean(axis=1), axis=0)
    x = x.div(np.sqrt((x ** 2).sum(axis=1)), axis=0).fillna(0)
    out = {}
    for phase, genes in markers.items():
        genes = [g for g in genes if g in x.index and g != exclude]
        prof = x.loc[genes].mean()
        prof = prof - prof.mean()
        out[phase] = x @ (prof / np.sqrt((prof ** 2).sum()))
    return pd.DataFrame(out)


def main() -> None:
    sgd = pd.read_csv(ROOT / "raw_data" / "SGD_features.tab", sep="\t", header=None, dtype=str)
    sgd = sgd[sgd[1] == "ORF"]
    name_to_orf = {**dict(zip(sgd[3].str.upper(), sgd[3])), **dict(zip(sgd[4].dropna().str.upper(), sgd[3][sgd[4].notna()]))}
    orf_to_name = dict(zip(sgd[3], sgd[4].fillna(sgd[3])))
    markers = {p: [name_to_orf[g] for g in names.split()] for p, names in MARKERS.items()}
    series = load_series()

    tables = [correlations(df, markers) for df in series.values()]
    genes = sorted(set().union(*[t.index for t in tables]))
    stack = np.stack([t.reindex(genes).values for t in tables])
    n = (~np.isnan(stack[:, :, 0])).sum(axis=0)
    mean = pd.DataFrame(np.nanmean(stack, axis=0), index=genes, columns=list(markers))[n >= MIN_SERIES]

    # 目印の遺伝子を 1 つずつ外して当て直す（方法の確かめ）
    hit, total = 0, 0
    for phase, orfs in markers.items():
        for g in orfs:
            t = pd.concat([correlations(df, markers, g).loc[g] for df in series.values() if g in df.index], axis=1).mean(axis=1)
            total += 1
            hit += t.idxmax() == phase
            if t.idxmax() != phase:
                print(f"  目印 {orf_to_name.get(g, g)}（{phase}）は {t.idxmax()} になった（相関 {t.max():.2f}）")
    print(f"目印の遺伝子を外して当て直すと {hit}/{total} が同じ時期")

    out = mean.round(3)
    out.insert(0, "score", mean.max(axis=1).round(3))
    out.insert(0, "phase", mean.idxmax(axis=1))
    out.insert(0, "name", [orf_to_name.get(g, g) for g in out.index])
    out["n_series"] = n[n >= MIN_SERIES]
    out.index.name = "orf"
    OUT.parent.mkdir(exist_ok=True)
    out.sort_values("score", ascending=False).to_csv(OUT, sep="\t")
    for th in (0.4, 0.5):
        counts = out[out["score"] >= th]["phase"].value_counts().to_dict()
        print(f"score {th} 以上: {(out['score'] >= th).sum()} 遺伝子 {counts}")
    print(f"書き出しました: {OUT.relative_to(ROOT)}（{len(out)} 遺伝子・時系列 {len(series)} 本）")


if __name__ == "__main__":
    sys.exit(main())
