# 依頼（2 回目）: 根拠の論文の付け直しと、残りの作業

前回は、出芽酵母の主要シグナル経路の関係を 74 行まとめていただき、ありがとうございました。
受け取った CSV の PMID を PubMed で機械的に照合したところ、**ほとんどの PMID が関係のない論文を指していました**
（56 件中 1 件は存在せず、題名・要旨に該当する遺伝子が出てくる論文は 5 件だけでした）。

例:
- ROM2 → RHO1（PMID 10364858）→ 実際の論文は「Peri-operative silent myocardial ischaemia ...」（*Anaesthesia*）
- BCK1 → MKK1（PMID 9393988）→ 実際の論文は「Female scientists wanted -- apply to UK research councils.」（*Nature*）
- BCY1 → TPK1（PMID 3082572）→ 実際の論文は「Massive eosinophilia in rheumatoid arthritis」（*Clin Rheumatol*）

関係の内容そのものは妥当なものが多いと考えています。根拠の論文を正しく付け直してください。
基本の決まり・列の意味は、前回の依頼文 `request_core_pathways.md` と同じです。今回から **列 `paper_title` を必須** にしました。

## 作業 1: 根拠の付け直し（添付 `round2_needs_citation.csv`、67 行）
各行の関係について、それを直接示す出芽酵母の論文を探し、PMID と題名を付けてください。

- 見つからない関係は、`pmids` と `paper_title` を空欄のままにしてください（**推測で番号を書かないでください**）。
- 関係の内容が論文と合わないと分かったものは、正しい内容に直すか、`status` を `reject` にしてください。
- 次の行は、内容にも問題があるので見直してください。
  - TCO89 → SCH9: TORC1 全体の作用は、触媒サブユニット（TOR1・TOR2）を上流にする決まりです。TCO89 からの行は不要です。
  - GCN2 → GCN4: Gcn2 がリン酸化するのは eIF2α（SUI2）で、GCN4 の翻訳が増えるのはその結果です。
    `GCN2 → SUI2`（phosphorylation、eIF2 の働きは inhibit）と、`SUI2 → GCN4`（translation の制御。interaction_type は other、
    effect は inhibit、note に「eIF2 の活性が下がると GCN4 の翻訳が増える」）の 2 行に分けてください。
  - PDE1 → BCY1・PDE2 → BCY1（effect が none）: ホスホジエステラーゼは cAMP を分解し、cAMP が減ると BCY1 が PKA を抑えます。
    `effect` は activate、`direct` は `via cAMP` にしてください。
  - SLN1 → YPD1・YPD1 → SSK1（effect が inhibit）: リン酸の受け渡し（phosphorelay）の各段の作用を、下流の**働き**で判断してください
    （SSK1 はリン酸化されると働かない、など）。
  - RTG1 → RTG3（binding）: RTG1 と RTG3 はこちらで 1 つのまとまり（二量体）として扱うので、この行は不要です。
    同じように、**同じ複合体の構成要素どうしの関係は不要**です（TORC1・TORC2・PKA・SNF1 複合体・SIT4 複合体・EGO・SEACIT など）。

## 作業 2: 今の記録の点検（前回の作業 B。添付 `current_core_relations.csv`、249 本）
前回は手が付いていなかったので、お願いします。特に次を優先してください。
- `effect` が `none` の行（129 本）
- `current_sources` が KEGG または KEGG+BioGRID で、`phosphorylation` かつ `activate` の行（図の矢印で、機能としては抑制のものがあるため）

## 作業 3: まだ取り上げられていない遺伝子（46 個）
前回の CSV に一度も出てこなかった遺伝子です。これらが関わる主な制御関係を加えてください。

AVO1 AVO2 BIT61 CRF1 DOT6 EGO2 FHL1 FPR1 GAL83 HKR1 IFH1 IGO1 IGO2 IML1 KOG1 LST8 MAF1 MEH1 MID2 MIG2 MSB2 NPR2 NPR3 PIB2
PPH22 RTG2 SAP155 SAP185 SAP190 SAP4 SEA4 SFP1 SIP1 SIP2 SLM2 SLM4 SMP1 SNF4 STE20 STE50 SWI6 TAP42 TIP41 TOD6 TSC11 URE2

特に重要なもの: URE2 → GLN3、TAP42・TIP41 と SIT4・PP2A、SEACIT（IML1・NPR2・NPR3）→ GTR1、PIB2 → TORC1、
FPR1 → TOR1（`condition` は ラパマイシン。条件の書き方は `request_core_pathways.md` の「条件の書き方」）、SFP1・IFH1・FHL1・CRF1 とリボソームタンパク質遺伝子、MAF1、DOT6・TOD6、IGO1・IGO2。
複合体の構成要素から外への作用は、触媒サブユニットを上流にしてください（例: SEACIT なら IML1 → GTR1、note に SEACIT）。

## 出力
- 経路ごとに 1 つの CSV（例: `round2_TORC1.csv`）。列は前回の 11 列に `paper_title` を加えた 12 列:
  `source_protein,target_protein,interaction_type,effect,direct,condition,pmids,paper_title,evidence,confidence,status,note`
- `paper_title` には、`pmids` の論文の題名を **PubMed に表示される英語の題名そのまま** 書いてください（複数なら `;` 区切りで同じ順）。

## 提出前に確かめてください
各 PMID について PubMed のページ（`https://pubmed.ncbi.nlm.nih.gov/<PMID>/`）を開き、
1. 題名が `paper_title` と一致すること
2. 出芽酵母の論文であること
3. 題名か要旨に、上流と下流の両方のタンパク質（または遺伝子）が出てくること

を確かめてください。受け取った CSV は、こちらで同じ照合を自動で行い（`tools/verify_literature.py`）、
題名が一致しない行・どちらの遺伝子も出てこない行は取り込みません。

## 添付
- `round2_needs_citation.csv`: 作業 1 の 67 行（前回の PMID と照合結果つき）
- `current_core_relations.csv`: 作業 2 の 249 本
- `request_core_pathways.md`: 前回の依頼文（対象の遺伝子・列の意味・決まり）
