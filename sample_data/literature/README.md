# 文献で確認した関係

- `*.csv`（このフォルダの直下）: PubMed で PMID が実在し、要旨が関係を裏付けることを確かめた関係。DB の作り直し
  （`tools/import_sources.py`）で、公開データ・サンプルより優先して取り込む。列は `docs/curation/request_core_pathways.md` の
  形式に `verified_on`（確かめた日）・`verified_note`（要旨から読み取れたこと）を加えたもの。
- **論文の文の引用は置かない**（2026-10-07 から。出典は `pmids` で明記し、読み取れたことは `verified_note` に自分の言葉で書く）。
  `evidence`・`fill_evidence` の列は形のために残すが、空にする。照合（`tools/verify_literature.py`）のために写した一文は、
  Google Drive の TorPathway調査/ の下書きにだけ置き、照合が済んでから列を空にしてここへ移す。取り込み（`tools/import_sources.py`）も
  `evidence` の列を DB に入れない。それまでの引用と、別の AI から受け取った未確認の回答（以前の `unverified/`）は、
  Google Drive の TorPathway調査/2026-10-07_引用の退避/ に移した。

## 確かめ方
1. `.venv/bin/python tools/verify_literature.py ファイル.csv --out 結果.tsv` で、PMID が実在するか・酵母の論文か・題名と要旨に
   上流と下流の遺伝子（SGD の別名も含む）が出てくるかを一覧にする。
2. 「両方の遺伝子に言及」の行について、要旨を読んで関係の向き・作用を裏付けるかを確かめる。
3. 裏付けられた行だけを、`verified_on`・`verified_note` を付け、`evidence`・`fill_evidence` を空にしてこのフォルダ直下の CSV に移す。
4. DB に取り込んだら `.venv/bin/python tools/fetch_paper_list.py` で情報源タブの論文一覧（`data/papers.json`）を作り直す。
   使った論文（列 `pmids`・`fill_pmid`、`verified_note`・`note` に引いた PMID。reject の行も）はすべて情報源に残す。

## 記録
- 2026-10-02 1 回目（別の AI に依頼。`unverified/` の 7 ファイル・74 行）: PMID 56 件のうち 1 件は存在せず、ほとんどは関係のない
  論文（麻酔・ワクチンなど）だった（`docs/curation/pmid_check_2026-10-02_first_batch.tsv`）。題名・要旨で裏付けられたのは
  6 行だけで、`verified.csv` に入れた（SAK1・TOS3・ELM1 → SNF1、GLC7 ⊣ SNF1、SNF1 ⊣ CYR1、PHO85 ⊣ PHO4）。
  残りの 68 行は、関係の内容は教科書的なものが多いが、根拠の論文が合っていないので取り込んでいない。
- 2026-10-02 2 回目（`unverified/round2_matome_raw.csv`、71 行。列 `paper_title` を必須にして再依頼）: PMID は 1 回目と同じ誤りのまま
  で、`paper_title` に書かれた題名も 71 件すべて PubMed に見つからなかった（類似度 0.9 以上で検索）。取り込んでいない。
- 2026-10-02 3 回目（`unverified/round3_matome2_raw.csv`、72 行）: PMID と題名はすべて空欄（推測で書かないよう依頼したため）。
  関係ごとに PubMed を検索し、原著論文の要旨を読んで確かめた。裏付けられた 60 行と、同じ要旨から読み取った置き換え・追加 7 行
  （SCH9 ⊣ DOT6・TOD6、RIM15 → IGO1・IGO2 ⊣ CDC55、MID2 → ROM2）を `verified_round3.csv` に入れた。要旨に合わせて、
  作用（SLN1 → YPD1 を activate に）・種類（SLT2 → SWI4 は binding など）・直接かどうか（遺伝学的な根拠のものなど）を直した。
  除外した 12 行（取り込み済み 6、関係の誤り 4、裏付けなし・食い違い 2）は理由つきで `unverified/round3_rejected.csv` に置いた。
  検索結果・要旨・照合の一覧は Google Drive（TorPathway調査/2026-10-02_まとめ2照合/）に置いた。
- 2026-10-02 4 回目（`verified_round4.csv`、14 行）: 構造確認用サンプル（`sample_data/interactions.csv`）の関係を PubMed で確かめて移した。
- 2026-10-02 5 回目（`verified_round5.csv`、40 行）: 確認待ちの一覧（`tools/make_review_queue.py`）の上位。HAP 複合体の標的の向きの誤り 29 本、
  KEGG の図だけの「リン酸化で促進」9 本を直した・確かめた。`status` が `reject` の 2 行（TOR2 → SCH9・TOR2 → TAP42）は、
  文献で誤りと分かった組として公開データからも取り込まない（`docs/source_conflicts.tsv` に「文献で誤りと確認」と出る）。
- 2026-10-02 6 回目（`verified_round6.csv`、47 行）: 確認待ちの一覧の続き。確認 28 行、取り込まない 19 組（KEGG の図の近道の矢印・
  同じまとまりの中の関係・TOR2 の関係として TORC2 の働きに数えてしまうもの）。
- 2026-10-02 7 回目（`verified_round7.csv`、40 行）: 向きが不明のシグナル伝達（BioGRID の酵素活性）のうち中心の経路のもの。
  BioGRID に記録された論文の要旨から向きを決めた 32 行と、向きが逆の記録・近道の矢印として取り込まない 8 組。
- 2026-10-04 10 回目（`verified_round10.csv`、10 行）: 中心の経路の遺伝子どうしで向きが不明の関係を、BioGRID が挙げる論文の要旨で確かめた。
  向きを決めた 9 行と、記録の誤り 1 組（PIB2 → SCH9、reject）。向きを決められなかった組は `docs/curation/searched_not_found.csv` に
  読んだ PMID と理由を書いた（取り込みには使わず、確かめる候補の一覧で後ろに回すだけ）。
  引用（evidence 列）が要旨にあるかも `tools/verify_literature.py` で確かめた（列 quote がなければ evidence を照合するようにした）。
- 2026-10-04 11 回目（`verified_round11.csv`、23 行）: 10 回目の続き。向きを決めた 15 行、取り込まない 6 組（reject）、注記 1 組（flag）。
  網羅的な実験（プロテインチップ）だけだった TPK2 → YAK1・TPK1 → SKO1 は、個別の論文で確かめた。
- 2026-10-04 12 回目（`verified_round12.csv`、63 行）: 確かめる候補の一覧の上位 120 本。別のエージェント 3 つが同じ規則（自分で PubMed を検索・
  要旨を読む・一文を写す）で下書きし、私が全行を `tools/verify_literature.py` で照合して、根拠の弱い 3 行を外した。向きを決めた 53 行・reject 6・flag 4。
- 2026-10-04 13 回目（`verified_round13.csv`、65 行）: 12 回目と同じやり方で一覧の次の 120 本。向きを決めた 59 行・reject 3・flag 3。
- 2026-10-04 14 回目（`verified_round14.csv`、76 行）: 一覧の次の 120 本。向きを決めた 76 行（reject・flag なし）。
- 2026-10-04 15 回目（`verified_round15.csv`、85 行）: 一覧の次の 120 本。向きを決めた 74 行・reject 8・flag 3。
- 2026-10-04 16 回目（`verified_round16.csv`、44 行）: 一覧の次の 120 本。向きを決めた 42 行・reject 2。
- 2026-10-04 17 回目（`verified_round17.csv`、62 行）: 一覧の次の 120 本。向きを決めた 57 行・reject 4・flag 1。

- 2026-10-04 細胞周期（`verified_cellcycle.csv`、101 行）: CDC28・PHO85・APC/C（CDC20・CDH1）・CDC14・CDC5 の基質について、
  組むサイクリン（partner）・時期（condition）・作用・部位を論文で確かめた（エージェント 4 つで下書き、`docs/curation/drafts/rules_cc_tx.md` の決まり）。
  サイクリンの特異性は Kõivomägi 2011（21658602）・Loog & Morgan 2005（15744308）・Faustova 2021（34502421）など。確認 40・作用を決めた 49・
  注記 7・新しい関係 3・reject 2（PHO85 → POL2 は RNA ポリメラーゼ II の取り違え、PHO85 → PCL7 は組む相手）。
  既存の文献の行と同じ組の 41 行は、根拠の上書きを避けて `docs/curation/drafts/cc_overlap_for_backfill.csv` に分けた。
- 2026-10-04 転写制御の向き（`verified_tx_direction.csv`、45 行）: 確かめる候補の一覧の「向きを破壊株データから推定した転写制御」313 本と
  「出典で食い違う」103 本（エージェント 6 つ）。論文で決められたのは 46 本だけで（確認 37・向きを直した 8、重なり 1）、残りは組を名指しした
  論文がなかった（`docs/curation/searched_not_found.csv` に追記）。決められた組では、破壊株データの向きが論文と逆のものが多かった
  （SFP1 → RPS12・RPS8A、STP1 → AGP1、SOK2 → SPI1 など。条件の違いや間接の作用のため）。破壊株データの向きだけで記録を直さない
  あとから MSN2/4 → RP 遺伝子 93 本（Kocik et al. 2026・41813847、抑制・浸透圧ストレス）と、31 回目の調べで見つかったオートファジーの遺伝子の転写の抑制 5 本（UME6 ⊣ ATG8、PHO23 ⊣ ATG9、PAF1 ⊣ ATG32、MIG1・MIG2 ⊣ ATG39）を加えた

- 2026-10-04 POL2 の取り違え（`verified_pol2_ctd.csv`、11 行）: BioGRID が RNA ポリメラーゼ II（RPO21）の CTD キナーゼの記録（30598543）を DNA ポリメラーゼ ε の POL2 に付けていた。YCK2・YCK3・CMK1・CMK2 → POL2 を reject にし、同じ論文の本文の一文で YCK2・YCK3・CMK1・CMK2・RIM11・SSK2・SLN1 → RPO21 を加えた（ほかのキナーゼの POL2 は 17・22・23 回目と細胞周期のファイルで reject 済み）

- 2026-10-04 並行の作業から渡された組（`verified_handoff_signaling.csv`）: 文献の調べ（33・35 回目）で「転写（並行の作業へ）」に回された組のうち、RIM15 ⊣ UME6（窒素飢餓、32240040）。同じ時に PHO85 ⊣ UGP1（via PHO4、11180457）・TPK1/2/3 ⊣ GSY2（7961723）を `verified_tx_direction.csv` に加えた。HOG1 → HXT1・GCN2 → ATG1 は論文に特定の遺伝子への作用がなく、決めていない

- 2026-10-04 中心の転写制御（`verified_tx_direction.csv` に追加）: (1) 中心の経路の転写因子 → 中心の経路の遺伝子の転写制御 260 本を確かめ、19 本を決めた（Hog1 → Sko1 → PTP3 ⊣ Hog1、Pho4 → PHO81、Msn2/4 → DOT6 など。URE2 → GLN3・GAT1 の転写制御は reject）。残りは網羅的なデータだけ。(2) 中心の転写因子 約 45 個の代表的な標的 147 本（確認 94・作用を決めた 32・新しい関係 20・注記 1。CRZ1 → PMC1・DAL80/GZF3 ⊣ NCR 遺伝子・DOT6/TOD6 ⊣ RiBi 遺伝子などを追加）。自己制御（MIG1・HAC1）は地図で扱わないので入れていない

## 列 note の「フィードバック」
列 `note` に「フィードバック」と書いた行は、下流から上流への作用（PKA ⊣ RAS2 など）として根拠欄に
「フィードバック（下流から上流への作用）」の印が付く。経路をたどるときは使うが、遺伝子の階層（上下の順序）を
決める計算には使わない（標的の多いキナーゼが、自分の上流より上の階層に来るのを防ぐ）。2026-10-02 時点で 6 本。
- 2026-10-04 47 回目（`verified_round47.csv`、279 行）: 遺伝子の裏どり（`docs/curation/drafts/rules_genes.md`）の 1 回目。作業一覧（`tools/make_gene_worklist.py`）の
  区分 1（個別実験の BioGRID あり）の上位 240 遺伝子を、エージェント 6 つが下書きした。確認 126・新しい関係 131・直した 16・reject 3・flag 3。
  エージェントどうしで重なった 16 組は 1 行にし、以前 flag にした組と重なった 7 行と、相手をカルシニューリンとしか書いていない CNA1 → UBX7 は外した。
  見つからなかった 12 遺伝子は `docs/curation/genes_not_found.csv`。候補の PMID の要旨に相手の遺伝子が出てこない記録が多く（BioGRID の取り違えらしいもの）、
  そうした遺伝子は別の原著の関係で確かめた。UCC1 → CIT2 の PMID 27697924 はヒトの論文だった。文献で確認した遺伝子 1,499 → 1,757
- 2026-10-04 48 回目（`verified_round48.csv`、306 行）: 遺伝子の裏どりの 2 回目。区分 1 の残りと区分 2 の上位、計 320 遺伝子（エージェント 8 つ）。
  確認 77・新しい関係 199・直した 29・flag 1。見つからなかった 99 遺伝子は `docs/curation/genes_not_found.csv`（多くは機能不明・dubious の ORF）。
  HAP1 → ERG5 は好気で促進・低酸素で抑制（17785431、ヘムで切り替わる）なので、作用は不明（none）にした。文献で確認した遺伝子 1,757 → 2,047
- 2026-10-05 49 回目（`verified_round49.csv`、366 行）: 遺伝子の裏どりの 3 回目。400 遺伝子（エージェント 10 個）。確認 59・新しい関係 287・直した 15・flag 4・reject 1（STE12 → PRM4。
  要旨に「PRM4 は Ste12 に制御されない」）。見つからなかった 141 遺伝子は `docs/curation/genes_not_found.csv`。条件で向きが変わる DEP1 → NDT80・PHO2 → TRP4 は作用を不明（none）にした。
  文献で確認した遺伝子 2,047 → 2,395
- 2026-10-05 50 回目（`verified_round50.csv`、366 行）: 遺伝子の裏どりの 4 回目。400 遺伝子（エージェント 10 個）。確認 38・新しい関係 321・直した 8・flag 1（TAG1 → ATG1）。
  見つからなかった 129 遺伝子は `docs/curation/genes_not_found.csv`。AlphaFold の予測だけの MDM1–NVJ3 と、査読前（bioRxiv）の NVJ3 → DGA1 は外した。
  INO4 → EKI1 は要旨に向きがないので、公開データの促進を残した（note に記載）。文献で確認した遺伝子 2,395 → 2,749
- 2026-10-05 51 回目（`verified_round51.csv`、365 行）: 遺伝子の裏どりの 5 回目。400 遺伝子（エージェント 10 個）。確認 32・新しい関係 324・直した 8・flag 2（GCN4 → PDX3・GCN4 → TYR1）。
  見つからなかった遺伝子は `docs/curation/genes_not_found.csv`。要旨が酵素名でしか書いていない PRE3 → FBP1 は外した（PRE3 は次の回へ）。DBF2 → YJL213W の条件「試験管内」は evidence_kind に移した。
- 2026-10-05 52 回目（`verified_round52.csv`、349 行）: 遺伝子の裏どりの 6 回目。400 遺伝子（エージェント 10 個）。確認 29・新しい関係 316・直した 3・flag 2（HAP2・HAP3 → QCR2）。
  見つからなかった 152 遺伝子は `docs/curation/genes_not_found.csv`。HMRA1 → HO は論文が MAT 座の a1 の話（DB に MATA1 がないので HMRA1 で代用していた）なので外した。
  ZAP1 → RTC4 は mRNA は増えるが uORF で翻訳が止まりタンパク質が減るので抑制。RFX1 → FSH3 は要旨に向きがないので公開データの抑制を残した。
- 2026-10-05 53 回目（`verified_round53.csv`、340 行）: 遺伝子の裏どりの 7 回目。400 遺伝子（エージェント 10 個）。確認 15・新しい関係 325。見つからなかった 149 遺伝子は `docs/curation/genes_not_found.csv`。
  外した行: CTM1 → CYC1（要旨は「iso-1-cytochrome c」）、AFG1 → COX1（要旨は「ミトコンドリアがコードする CcO のサブユニット」で COX1〜3 のどれか決まらない）、YET1 → OPI1（要旨は「Yet 複合体」、観察は yet3Δ）。
  SRO77 → TOR1（via RHO1、25061043）は要旨が TOR1 を名指しするので残した。
- 2026-10-05 54 回目（`verified_round54.csv`、307 行）: 遺伝子の裏どりの 8 回目。400 遺伝子（エージェント 10 個）。確認 9・新しい関係 297・直した 1（RAD53 → CEP3 は via DUN1）。
  見つからなかった 167 遺伝子は `docs/curation/genes_not_found.csv`。照合で「酵母の論文」にならなかった 30 本は、PubMed の MeSH で全部 Saccharomyces cerevisiae と確かめた。
- 2026-10-05 55 回目（`verified_round55.csv`、444 行）: 遺伝子の裏どりの最後の回。残りの 587 遺伝子（エージェント 15 個）。新しい関係 437・確認 5・直した 2・reject 1（GCN4 → ARO7、
  2277632: 試験管内では結合するが生体内では制御しない）・flag 1（TPK1 → HUR1）。外した行: MDM35 → UPS3（要旨は「Ups proteins」）、ATE1–UBR1（酵母の文は「arginyltransferase」）、
  HOG1 → HFD1（経路全体の記述の当てはめ）。COQ4–COQ7 は DB の名前 CAT5 に直した。PPE1 → PPH21 は demethylation。
  これで全 6,613 遺伝子が、文献で確認した関係（4,291）か、探して見つからなかった記録（2,322、`docs/curation/genes_not_found.csv`）のどちらかを持つ
- 2026-10-05 名前の見直し（`verified_names1.csv` 43 行・`verified_names2.csv` 32 行）: 論文が相手を複合体の名前・タンパク質名でしか書かないため書けなかった関係を書き直した
  （`docs/curation/drafts/rules_names.md`）。遺伝子の欄に複合体の名前（`data/complex_groups.csv`、30 個）を書けるようにし、取り込みで構成遺伝子ごとに広げて
  根拠欄に「複合体 TORC1（上流）として」と書く。1 つの遺伝子に決まるタンパク質名（`data/protein_names.csv`）は照合で遺伝子の言及として数える。
  主な関係: TORC1 → RPC82・RPC53・RET1（SUMO 化）、calcineurin → ATC1、CYT2 → CYT1、PRE3 → FBP1、PDX1 → LPD1、MCX1 → HEM1、RPC10 → TFC4、
  RPN4 ↔ 26S proteasome、AP-2 → MID2、COG → KTD1、UTP-A・UTP-B → RRP36。外した行: HSP32・SNO4・HSP33 → TORC1（4 遺伝子をまとめて欠いた株だけ）、
  IRC25・POC4 → PUP2（要旨は「α5」だけ）。文献で確認した遺伝子 4,291 → 4,343
