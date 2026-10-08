# Data sources and licenses / データの出典と利用条件

The source code of Pathways Viewer is licensed under GPLv3 (`LICENSE`). **The data bundled with it (`data/`) are NOT covered by
the GPL**; each data set remains under the terms of its original source listed below. The data were processed (selected,
merged, summarized) for this tool. Please cite the original sources when you use the data.

Pathways Viewer のソースコードは GPLv3（`LICENSE`）です。**同梱のデータ（`data/`）には GPL は及びません。** それぞれ元の出典の条件のままです。
データはこのツールのために加工（選択・統合・要約）しています。使うときは元の出典を引用してください。アプリの「情報源」タブにも、
サイトごとの利用条件と、サイトが引用を求める論文、参考にした論文を並べています。

（2026-10-07 に各出典の公式の記載を確かめた。下の「根拠」は公式のページの記載）

## 地図の DB（`data/tor_pathway.db`）

| 出典 | 使っているもの | 利用条件と根拠 | 必要な表記・引用 |
|---|---|---|---|
| SGD（Saccharomyces Genome Database） | 遺伝子名・機能説明・GO 注釈 | **CC BY 4.0**（"SGD operates under the Creative Commons Attribution 4.0 International license" — https://sites.google.com/view/yeastgenome-help/about ） | SGD・CC BY 4.0・加工したこと。Engel SR et al. 2025 Genetics（PMID 39530598）。データの元の論文も |
| Gene Ontology（GO 注釈・GO-CAM） | 関係の向きの根拠 | **CC BY 4.0**（https://geneontology.org/docs/go-citation-policy/ ） | GO の論文（Aleksander et al. 2023 Genetics、PMID 36866529・Ashburner et al. 2000 Nat Genet、PMID 10802651）と GO のリリース日 |
| BioGRID（PhosphoGRID・BioGRID-PTM を含む） | 物理的相互作用・酵素と基質・修飾部位・破壊株のリン酸化（Bodenmiller 2010 の記録） | **MIT**（https://wiki.thebiogrid.org/doku.php/terms_and_conditions ）。許諾文は `licenses/BioGRID-MIT.txt` | 著作権表示と許諾文の同梱（済み）。BioGRID の論文（Oughtred et al. 2021 Protein Sci、PMID 33070389・Stark et al. 2006 NAR、PMID 16381927）。PhosphoGRID は Stark et al. 2010 Database（PMID 20428315） |
| YEASTRACT+ | 転写制御（Documented の関係・作用の向き・環境条件） | 利用条件（https://www.yeastract-plus.org/termsusage.php ）: 内容の全部・相当部分を、YEASTRACT+ の利益を損なう形や競合する形で再公開しない。データの利用・再配布には元のデータの持ち主の条件のほかに制限はない | Monteiro PT et al. 2020 Nucleic Acids Res 48:D642（PMID 31586406）。**再配布について開発者に確認する（未）** |
| Complex Portal（EMBL-EBI） | 複合体の構成と説明 | **CC0**（Complex Portal 2022, NAR, https://pmc.ncbi.nlm.nih.gov/articles/PMC8689886/ ） | 義務なし（引用を推奨。Meldal et al. 2022 NAR、PMID 34718729） |
| UniProt | 修飾された残基 | **CC BY 4.0**（https://www.uniprot.org/help/license ） | UniProt の論文（UniProt Consortium 2025 NAR、PMID 39552041）・CC BY 4.0 |
| Alliance of Genome Resources | パラログ | **CC BY 4.0**（Alliance の Terms of Use） | Alliance・CC BY 4.0（Alliance of Genome Resources Consortium 2024 Genetics、PMID 38552170） |
| YGOB（Yeast Gene Order Browser, v7 Aug 2012） | パラログ | **明示の許諾なし。** 引用の依頼だけ（"Please cite this article if using data" — http://ygob.ucd.ie/browser/info.html ）。使うのは相同の組という事実情報 | Byrne KP & Wolfe KH 2005 Genome Res 15:1456（PMID 16169922） |
| IDEA（Hackett et al. 2020） | 転写制御の向き | 論文は **CC BY 4.0**。同じデータが **GEO GSE142864**（下の GEO の方針）。Calico の配布ファイル自体には明文なし | Hackett SR et al. 2020 Mol Syst Biol 16:e9174・GSE142864 |
| Deleteome（Kemmeren et al. 2014） | 転写制御の向き | **GEO GSE42527**（下の GEO の方針）。Holstege lab のサイトには明文なし | Kemmeren P et al. 2014 Cell 157:740（PMID 24766815）・GSE42527 |
| このプロジェクトの文献調査（`sample_data/literature/`） | 文献で確かめた関係（PMID・日本語の要約・性質） | 論文の文は引用せず、出典（PMID）だけを示す。記録はこのリポジトリの一部 | |

## 発現・リン酸化・破壊株の実測（`data/expression.db`）

| 出典 | 使っているもの | 利用条件と根拠 | 必要な表記・引用 |
|---|---|---|---|
| SGD SPELL（GEO などの公開データを SGD がまとめたもの） | 条件ごとの mRNA（93 測定の中央値） | SGD（CC BY 4.0）。元のデータは GEO（下の方針） | SPELL の論文（Hibbs et al. 2007 Bioinformatics、PMID 17724061）と、測定ごとの元の論文（アプリの説明欄と情報源タブに PMID） |
| Leutert et al. 2023 Nat Struct Mol Biol | 条件ごとのリン酸化・タンパク質量（要約値）・キナーゼと基質の一覧 | 同じデータが **Zenodo（doi:10.5281/zenodo.10016524、CC BY 4.0）** と **PRIDE（PXD035050 ほか、CC0）** にある。論文と Supplementary Tables は Springer Nature が権利を持つ（CC ではない） | Leutert M et al. 2023（doi:10.1038/s41594-023-01115-3）・Zenodo DOI・CC BY 4.0・加工したこと |
| Deleteome（Kemmeren et al. 2014） | 破壊株の mRNA（p < 0.05 かつ 1.4 倍以上の値） | **GEO GSE42527**（下の方針） | Kemmeren 2014・GSE42527 |
| Bodenmiller et al. 2010 Sci Signal（BioGRID-PTM 経由） | 破壊株のリン酸化 | **MIT**（BioGRID） | Bodenmiller B et al. 2010（PMID 21177495）・BioGRID |

### GEO の方針
"NCBI places no restrictions on the use or distribution of the GEO data. However, some submitters may claim patent, copyright, or
other intellectual property rights in all or a portion of the data they have submitted."
— https://www.ncbi.nlm.nih.gov/geo/info/disclaimer.html

## そのほか

- `data/papers.json`: 論文の書誌（題名・著者・雑誌・年）。PubMed（NCBI E-utilities）から取得した書誌情報。
- KEGG は利用条件のため 2026-10-03 から使っていない（`archive/` には取得の道具のコードだけがある）。
- 機械翻訳は 2026-10-07 に廃止した。論文の文の引用は 2026-10-07 から置いていない。

## 残っている確認
- YEASTRACT+ の再配布の了承（開発者に問い合わせる）。
- Leutert 2023 の要約値は Supplementary Tables から作っている。根拠をはっきり CC BY にするため、Zenodo（CC BY 4.0）のファイルから作るように取り込みを変える。
- YGOB は明示の許諾がない。気になるなら開発者（ygob@ucd.ie）に一言確認する。
