"""破壊株データ（Deleteome）の全破壊株に共通する発現変化（第 1 主成分 = 一般的なストレス応答・生育の遅れ）に強く乗る遺伝子を、
data/common_response_genes.csv に書き出す。

制御遺伝子探索では、これらの遺伝子の変化は多くの破壊・刺激で一緒に起きるので、転写因子を見分ける手がかりにならない。
転写因子の点数はこれらを除いて計算し、シグナル伝達の遺伝子（PKA・TORC1 など、本来の出力がストレス応答のもの）は
除かない点数を受け継ぐ（tor_app/regulator_search.py）。評価: tools/evaluate_regulator_search.py --common。

使い方: .venv/bin/python tools/make_common_response.py [--top 400]
"""
import argparse
import csv
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "raw_data" / "DELETEOME" / "deleteome_all_mutants_ex_wt_var_controls.txt"
OUT = ROOT / "data" / "common_response_genes.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=400, help="第 1 主成分の重みの絶対値が大きい順に何遺伝子を書き出すか")
    args = ap.parse_args()
    with open(DATA, encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader)
        kinds = next(reader)
        cols = [i for i, (n, k) in enumerate(zip(header, kinds)) if i >= 3 and "-del" in n and k == "M"]
        systematic, symbol, rows = [], [], []
        for row in reader:
            if len(row) < 3:
                continue
            values = []
            for i in cols:
                try:
                    values.append(float(row[i]))
                except (ValueError, IndexError):
                    values.append(0.0)
            systematic.append(row[1].strip())
            symbol.append(row[2].strip())
            rows.append(values)
    M = np.nan_to_num(np.array(rows))
    U, S, _ = np.linalg.svd(M, full_matrices=False)
    pc = U[:, 0]
    # 符号を「ストレス遺伝子（HSP26・CTT1 など）が正」にそろえる
    stress = [i for i, s in enumerate(symbol) if s.upper() in ("HSP26", "CTT1", "HSP12", "PGM2", "ALD3")]
    if pc[stress].mean() < 0:
        pc = -pc
    share = S[0] ** 2 / (S ** 2).sum()
    order = np.argsort(-np.abs(pc))[:args.top]
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        f.write(f"# Deleteome（Kemmeren et al. 2014）の全 {len(cols)} 破壊株の log2 比の第 1 主成分（変化の {100 * share:.1f}%）。"
                f"重みの絶対値が大きい {args.top} 遺伝子。tools/make_common_response.py で作成\n")
        w = csv.writer(f)
        w.writerow(["systematic_name", "gene", "weight", "in_stress_response"])
        for i in order:
            w.writerow([systematic[i], symbol[i], f"{pc[i]:.4f}", "up" if pc[i] > 0 else "down"])
    up = sum(pc[i] > 0 for i in order)
    print(f"第 1 主成分（変化の {100 * share:.1f}%）の上位 {args.top} 遺伝子（ストレス応答で増える {up}・減る {args.top - up}）→ {OUT}")


if __name__ == "__main__":
    main()
