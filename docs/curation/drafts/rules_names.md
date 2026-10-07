# 下書きの決まり（名前の見直し names、2026-10-05）

目的: 論文が相手を**複合体の名前**や**タンパク質名**でしか書いていないため、これまで書けなかった関係を書く。
担当の一覧 `docs/curation/drafts/names_worklistK.tsv` の各行（遺伝子と、以前に書けなかった理由のメモ）について、もう一度確かめる。
基本の決まりは `docs/curation/drafts/rules_genes.md`（論文の探し方・列・引用の写し方・照合・条件の書き方）。この文書は、それに足す決まりだけを書く。

## 新しく書けるようになったもの
1. **複合体の名前**: `data/complex_groups.csv` の label（TORC1・calcineurin・CK2・PKA・HAP complex・26S proteasome・TFIIIC・RNA polymerase II・
   cytochrome c oxidase・ATP synthase・COPI・CCT・retromer・UTP-A complex・HACS など。**必ずファイルを開いて今の一覧を見る**）は、
   source_protein・target_protein にそのまま書いてよい（例: `TORC1,RPC82,sumoylation,...`）。取り込みで構成遺伝子に広がり、地図では複合体の枠から線が出る。
   論文がその複合体の名前（列 aliases にある書き方。TORC1・calcineurin・casein kinase II・protein kinase A など）で書いているときに使う。
   - PKA は触媒サブユニット（TPK1〜3）だけ。論文が「PKA」と書けば使ってよい
   - 表にない複合体（例: Rpd3L、SAGA、COPII、NuA4）を使いたいときは、行は書かずに提案のファイルに書く（下）
2. **1 つの遺伝子に決まるタンパク質名**: `data/protein_names.csv` にある名前（**ファイルを開いて今の一覧を見る**。iso-1-cytochrome c → CYC1、cytochrome c1 → CYT1、invertase → SUC2、
   arginyltransferase → ATE1、ALAS → HEM1、mtClpX → MCX1、acetoacetyl-CoA thiolase → ERG10、fructose-1,6-bisphosphatase → FBP1、
   AMP deaminase → AMD1、dihydrolipoamide dehydrogenase → LPD1、tau131 → TFC4 など）は、遺伝子名として書いてよい（行には遺伝子名を書く）。
   照合の道具がこの名前を遺伝子の言及として数える
   - 表にないが、S288C で 1 つの遺伝子にしか当たらないタンパク質名を使いたいときは、行は書かずに提案のファイルに書く（下）。
     同じ働きの遺伝子が 2 つ以上ある名前（enolase・aconitase・hexokinase・「eEF1A」など）は 1 つに決まらないので使わない

## まだ書かないもの
- 遺伝子の**群・ファミリー**の書き方（「PAU genes」「Cos proteins」「Ups proteins」「BNA genes」「THI genes」など）。名指しされた遺伝子だけ書く
- **A・B の写し**（RPL・RPS の A/B、TIF1/2、TEF1/2、ASP3-1〜4）を区別しない書き方。これは別の見直しで扱うので、今回は書かない
- 経路の名前だけ（「HOG pathway」「PKA pathway」「Ras/cAMP pathway」）で、どの遺伝子・複合体が働くか書いていないもの

## 出力（K は担当の番号）
- `docs/curation/drafts/names_partK.csv`: 書けた関係（`rules_genes.md` の下書きと同じ列）。note に「論文の書き方: TORC1」のように、使った名前を書く
- `docs/curation/drafts/names_not_found_partK.csv`: `gene,searched_on,read_pmids,note`。今回も書けなかった遺伝子（理由）
- `docs/curation/drafts/names_proposals_partK.csv`: `kind,name,genes,pmids,why`（kind は `complex` か `protein`）。
  表に足してほしい複合体・タンパク質名。genes は構成遺伝子（`;`）か 1 つの遺伝子。why には、Complex Portal の番号や SGD の説明など、その対応の根拠を書く
- 照合: `.venv/bin/python tools/verify_literature.py names_partK.csv --out names_partK.verify.tsv`
- 種類が「外した関係」の行は、メモにある関係そのものを確かめる（関係が書けたらその行、書けなければ not_found に）

## 触ってよいファイル
上の 4 つ（下書き・not_found・提案・照合の結果）と、スクラッチパッドの `names_partK/` の中だけ。`data/` の表やほかのファイルは触らない
