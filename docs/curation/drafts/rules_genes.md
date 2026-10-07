# 下書きの決まり（遺伝子の裏どり genes、2026-10-04）

目的: 担当の一覧（`docs/curation/drafts/genes_worklist_partK.tsv`）の**遺伝子ごとに**、論文で確かめた関係を **1 本以上**作る。
見つからなければ「見つからない」に記録する（どちらでもその遺伝子は済みになる）。全部の関係を確かめる必要はない。
**論文に書かれていないことは書かない。推測で埋めない**。PMID・題名・引用は必ず自分で PubMed から取る（記憶から書かない）。

## 触ってよいファイル
- 書く: `docs/curation/drafts/genes_partK.csv`（下書き）・`genes_not_found_partK.csv`（見つからない遺伝子）・照合の結果 `genes_partK.verify.tsv`
- それ以外（`sample_data/`・`data/`・DB・ほかの下書き・コード）は**触らない**。DB の作り直しもしない
- 作業用のスクリプト・一時ファイルは、ほかの担当と同時に動くので、スクラッチパッドの `genes_partK/` の中にだけ作る（`pm.py` は書き換えない）

## 論文の探し方
- PubMed: `.venv/bin/python /private/tmp/claude-501/-Users-rai-----Pathways/52a05473-dee7-4864-b132-2d08148b9983/scratchpad/pm.py search "検索式" 20`
  （題名の一覧）、`... pm.py abs PMID [PMID ...]`（題名と要旨）
- 本文（PMC にあるもの）: `curl -s "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC番号/fullTextXML"`
- 出芽酵母（Saccharomyces cerevisiae）の論文だけ使う

## 1 遺伝子の進め方（早いものから試す）
1. 列「候補」に PMID つきの関係があれば、その要旨を読む。要旨（か本文）に関係が書かれていれば、その関係を書く（status `confirm`、
   作用が記録と違えば `correct`、記録が誤りなら `reject`。reject も 1 本の確認に数える）
2. なければ、その遺伝子の働きを調べた原著を PubMed で探す（例: `"GENE"[tiab] AND (yeast OR cerevisiae)`、別名も）。
   要旨に、**ほかの出芽酵母の遺伝子との関係**（リン酸化する・結合して働く・転写を制御する・分解する・局在を決める など）が書かれていれば、
   その関係を書く（一覧にあれば `confirm`、なければ `new`）。説明の欄（SGD）に出てくる相手から探すと早い
3. 転写因子からの転写制御（YEASTRACT+）だけの遺伝子は、その遺伝子の発現を制御する転写因子を名指しした論文を探す
4. 見つからなければ `genes_not_found_partK.csv` に、読んだ PMID と理由を書く。目安は 1 遺伝子 5 分ほど。
   「Putative protein of unknown function」「Dubious open reading frame」などで論文がほとんどない遺伝子は、1 回検索して無ければすぐ not_found でよい

1 遺伝子につき 1〜2 本で十分。読んだ論文にほかの関係（担当の遺伝子どうしでも、外の遺伝子でも）がはっきり書かれていれば、それも書いてよい。

## 下書きの列（`sample_data/literature/verified_round46.csv` と同じ並び）
`source_protein,target_protein,interaction_type,effect,direct,condition,pmids,evidence,confidence,status,note,verified_on,verified_note,partner,site,evidence_kind,necessity,redundant_with,strength,layer,timing`

- `source_protein`・`target_protein`: 遺伝子名（標準名。DB の gene_name。無い遺伝子は系統名 YxxNNNx）。向きは作用する側 → 受ける側。
  DB の名前と論文の名前が違うことがある（例: GET5 は DB では MDY2）。書いたら
  `sqlite3 data/tor_pathway.db "select gene_name from proteins where gene_name='名前'"` で DB にあるか確かめる
- 一覧の列「文献で済んだ組」にある組（以前 flag・reject にしたもの）は書かない。書いても A にならないので、別の関係を探す
- `interaction_type`: `phosphorylation`・`dephosphorylation`・`ubiquitination`・`acetylation`・`methylation`・`sumoylation`・`proteolysis`・
  `transcription`・`activation`・`inhibition`・`binding`・`localization`・`enzymatic`・`other` から
- `effect`: 下流の**機能**への作用 `activate`・`inhibit`。論文から向きが言えないとき（複合体の構成要素どうしの結合など）は `none`
- 論文が「ある条件でだけ」記録と逆の向きを示すとき（例: 好気では促進・低酸素では抑制）は、DB は 1 組を 1 本で持つので、
  `effect` を `none` にして、条件ごとの向きを `note` に書く（記録の向きを条件付きの向きで上書きしない）
- `direct`: `direct` か `via 遺伝子名` か `直接かは不明`
- `condition`: 論文が条件を書いているときだけ。決まった日本語で（`docs/curation/request_core_pathways.md` の「条件の書き方」）。
  書いたら `.venv/bin/python tools/check_conditions.py 下書き.csv` で当てはまらない行を 0 に。働く場面は `note` へ
- `pmids`: 実際に読んだ論文だけ（`;` 区切り、最大 3）
- `evidence`: その論文の**要旨か本文から一文をそのまま写す**（英語。言い換えない。照合で文字列の一致を調べる）（照合のためだけ。2026-10-07 から、引用は sample_data/literature/ に入れない。下書きは Google Drive の TorPathway調査/ に置き、照合が済んだら evidence・fill_evidence を空にしてから sample_data/literature/ に移す。出典は pmids で明記する）
- `confidence`: `high`（直接の実験）・`medium`（間接・複数の弱い証拠）・`low`
- `status`: `confirm`・`correct`・`new`・`reject`・`flag`（記録は残すが注記。理由を note に）
- `verified_on`: `2026-10-04`。`verified_note`: 日本語で「要旨: …（PMID）」の形で読み取れたこと
- 性質の列（`docs/curation/literature_fields.md`）: 論文に書かれているときだけ。転写制御の `layer` は `expression`
- ヒストンを修飾する関係は `docs/curation/literature_fields.md` の「ヒストンの関係の向き」に従う

## 見つからない遺伝子
`genes_not_found_partK.csv`: `gene,searched_on,read_pmids,note`（`searched_on` は `2026-10-04`。note に検索式と、見つからなかった理由を日本語で短く）

## 仕上げ
1. `.venv/bin/python tools/verify_literature.py 下書き.csv --out 下書き.verify.tsv` で照合し、PMID が合わない・引用が見つからない・
   遺伝子が要旨に出てこない行を直すか消す（遺伝子が本文にだけ出てくる場合は、本文から引用していればよい）
2. 担当の遺伝子が全部、下書きの行か not_found のどちらかに入っていることを確かめる
3. 最後に、関係を書けた遺伝子の数・not_found の数・status ごとの行数・気づいたことを日本語で短く報告する
