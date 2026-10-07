# Data sources and licenses / データの出典と利用条件

The source code of Pathways Viewer is licensed under GPLv3 (`LICENSE`). **The data bundled with it (`data/`) are NOT covered by
the GPL**; each data set remains under the terms of its original source listed below. Please cite the original sources when you
use the data.

Pathways Viewer のソースコードは GPLv3（`LICENSE`）です。**同梱のデータ（`data/`）には GPL は及びません。** 出典ごとの条件に従い、
使うときは元の出典を引用してください。アプリの「情報源」タブにも出典と論文を並べています。

## 地図の DB（`data/tor_pathway.db`）

| 出典 | 使っているもの | 利用条件 | 状態 |
|---|---|---|---|
| SGD（Saccharomyces Genome Database） | 遺伝子名・機能説明・GO 注釈・発現データの一覧（SPELL） | CC BY 4.0（とされている） | 要確認 |
| Gene Ontology（GO 注釈・GO-CAM） | 関係の向きの根拠 | CC BY 4.0 | |
| BioGRID（PhosphoGRID・BioGRID-PTM を含む） | 物理的相互作用・酵素と基質・修飾部位 | MIT | 確認済み |
| YEASTRACT+ | 転写制御（Documented の関係・作用の向き・環境条件） | YEASTRACT+ の利用条件（内容の全部・相当部分を、YEASTRACT+ の利益を損なう形や競合する形で再公開しない。Monteiro et al. 2020, Nucleic Acids Res 48:D642 を引用する） | 再配布について開発者に確認する |
| Complex Portal（EMBL-EBI） | 複合体の構成と説明 | CC0 | 確認済み |
| UniProt | 修飾された残基 | CC BY 4.0 | |
| Alliance of Genome Resources | パラログ | CC0（一部 CC BY 4.0） | |
| YGOB（Yeast Gene Order Browser） | パラログ | 明示の利用条件なし | 要確認 |
| IDEA（Hackett et al. 2020, Mol Syst Biol） | 転写制御の向き | 論文は CC BY 4.0。データの利用条件 | 要確認 |
| Deleteome（Kemmeren et al. 2014, Cell。GEO GSE42527） | 転写制御の向き | GEO に登録されたデータ | 要確認 |
| このプロジェクトでの文献調査（`sample_data/literature/`） | 文献で確かめた関係（PMID・日本語の要約・性質） | 論文の文は引用せず、出典（PMID）だけを示す。記録はこのリポジトリの一部 | |

## 発現・リン酸化・破壊株の実測（`data/expression.db`）

| 出典 | 使っているもの | 利用条件 | 状態 |
|---|---|---|---|
| SGD SPELL（GEO などの公開データを SGD がまとめたもの） | 条件ごとの mRNA（93 測定） | 元のデータの登録先の条件（多くは GEO） | 要確認 |
| Leutert et al. 2023（Nat Struct Mol Biol） | 条件ごとのリン酸化・タンパク質量 | 補足データ・Zenodo の利用条件 | 要確認 |
| Deleteome（Kemmeren et al. 2014） | 破壊株の mRNA | GEO GSE42527 | 要確認 |
| Bodenmiller et al. 2010（Sci Signal。BioGRID-PTM 経由） | 破壊株のリン酸化 | MIT（BioGRID） | 確認済み |

## そのほか

- `data/papers.json`: 論文の書誌（題名・著者・雑誌・年）。PubMed（NCBI E-utilities）から取得した書誌情報。
- KEGG は利用条件のため 2026-10-03 から使っていない（`archive/` には取得の道具のコードだけがある）。
- 機械翻訳は 2026-10-07 に廃止した。論文の文の引用は 2026-10-07 から置いていない。
