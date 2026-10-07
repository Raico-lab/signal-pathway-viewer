# 原因の分かっている条件での評価

`tools/evaluate_conditions.py` が、`conditions.csv` の各条件の発現変化（`raw_data/CONDITIONS/`）を観測として制御遺伝子探索を行い、
その条件で働くことが確立した制御因子（列 `expected`、`:+` は活性化・`:-` は不活性化）が何位に来るか・向きが正しいかを数える。

- データ: SGD の発現データの保管庫（SPELL 用に整えた log2 比、`https://s3-us-west-2.amazonaws.com/sgd-archive.yeastgenome.org/expression/microarray/`）。
  Gasch et al. 2000（PMID 11102521、環境ストレス）、Causton et al. 2001（PMID 11179418）、Hardwick et al. 1999（PMID 10611304、ラパマイシン・アミノ酸飢餓）。
  `raw_data/` は git で管理しないので、取得し直すときは上の保管庫から同じフォルダ名で置く。
- 時点: 応答の早い時点（15〜60 分。窒素飢餓は 1 時間、定常期へは 12 時間）。列 `baseline` があれば、その列との差を変化とする。
- 正解（`expected`）は教科書的に確立した制御因子で、こちらで決めたもの:
  熱ショック = HSF1・MSN2/4 の活性化、PKA の不活性化 / 高浸透圧 = HOG1・PBS2・HOT1・MSN2/4 / 酸化 = YAP1・SKN7・MSN2/4 /
  窒素飢餓 = GLN3・GAT1・MSN2/4 の活性化、TORC1 の不活性化 / アミノ酸飢餓 = GCN4・GCN2 / ラパマイシン = TORC1 の不活性化、
  GLN3・GAT1・MSN2/4 の活性化、SFP1 の不活性化 / 定常期へ = SNF1 の活性化、MIG1・PKA の不活性化、CAT8・ADR1・RIM15 の活性化 /
  DTT = HAC1・IRE1 の活性化。
