# 下書きの決まり（細胞周期 cc・転写制御の向き tx、2026-10-04）

担当の一覧（`docs/curation/drafts/cc_worklist_partK.csv` か `tx_worklist_partK.csv`）の関係を、論文で確かめて下書きを作る。
**論文に書かれていないことは書かない。推測で埋めない**（シミュレーションと地図を誤らせるため）。決められなければ「見つからない」に回す。

## 触ってよいファイル
- 書く: `docs/curation/drafts/{cc|tx}_partK.csv`（下書き）・`{cc|tx}_not_found_partK.csv`（決められなかった組）・
  `markers_{cc|tx}_partK.csv`（条件ごとの活性。見つかったときだけ）・照合の結果 `*.verify.tsv`
- それ以外（`sample_data/`・`data/`・DB・ほかの下書き・コード）は**触らない**。DB の作り直しもしない

## 論文の探し方
- PubMed: `.venv/bin/python /private/tmp/claude-501/-Users-rai-----Pathways/f7929caf-1a19-46bc-a46b-2f1980fba19f/scratchpad/pm.py search "検索式" 20`
  （題名の一覧）、`... pm.py abs PMID [PMID ...]`（題名と要旨）
- 本文（PMC にあるもの）: `curl -s "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC番号/fullTextXML"`
- 一覧の列 `individual_pmids`・`根拠` にある PMID から読むと早い。出芽酵母の論文だけ使う

## 下書きの列（`sample_data/literature/verified_round18.csv` と同じ並び）
`source_protein,target_protein,interaction_type,effect,direct,condition,pmids,evidence,confidence,status,note,verified_on,verified_note,partner,site,evidence_kind,necessity,redundant_with,strength,layer,timing`

- `effect`: 下流の**機能**への作用 `activate`・`inhibit`・`none`（転写制御なら、転写因子が標的の発現を上げるなら activate）
- `direct`: `direct` か `via 遺伝子名`
- `condition`: 環境の条件と細胞周期の時期を決まった日本語で（`docs/curation/request_core_pathways.md` の「条件の書き方」）。
  時期は `G1 期`・`S 期`・`G2 期`・`分裂期`・`分裂期の終わり`。範囲は含む時期を全部並べる。働く場面は `note` へ。
  書いたら `.venv/bin/python tools/check_conditions.py 下書き.csv` で「当てはまらない行」が 0 になるようにする
- `pmids`: 実際に読んだ論文だけ（`;` 区切り、最大 3）
- `evidence`: その論文の**要旨か本文から一文をそのまま写す**（英語。言い換えない。照合で文字列が一致するか調べる）（照合のためだけ。2026-10-07 から、引用は sample_data/literature/ に入れない。下書きは Google Drive の TorPathway調査/ に置き、照合が済んだら evidence・fill_evidence を空にしてから sample_data/literature/ に移す。出典は pmids で明記する）
- `confidence`: `high`（直接の実験）・`medium`（間接・複数の弱い証拠）・`low`
- `status`: `confirm`（今の記録どおり）・`correct`（作用などを直す。この行が正しい値）・`new`（一覧にない関係を見つけた）・
  `reject`（関係として誤り）・`flag`（記録は残すが注記する。理由を note に）
- `verified_on`: `2026-10-04`。`verified_note`: 日本語で「要旨: …（PMID）」の形で、読み取れたこと
- 性質の列（`docs/curation/literature_fields.md`）: `partner`（組むサイクリン・相手。論文が特定しているときだけ）・`site`（S14 の形）・
  `evidence_kind`（in_vivo・in_vitro・genetic・structural）・`necessity`・`redundant_with`・`strength`・`layer`（転写制御は expression）・`timing`

## 見つからない組
`{cc|tx}_not_found_partK.csv`: `source_protein,target_protein,searched_on,read_pmids,note`（読んだ PMID と、決められなかった理由を日本語で）

## 条件ごとの活性（見つかったときだけ）
`markers_{cc|tx}_partK.csv`: `data/activity_markers.csv` と同じ列。`condition` は `data/conditions.csv` の key（`g1`・`m_phase`・`nitrogen_starvation` など）

## 仕上げ
1. `.venv/bin/python tools/verify_literature.py 下書き.csv --out 下書き.verify.tsv` で照合し、PMID が合わない・引用が見つからない行を直すか消す
2. `tools/check_conditions.py` で当てはまらない行を 0 に
3. 最後に、確かめた行の数（status ごと）・見つからない組の数・気づいたことを日本語で短く報告する

## 目印の表（markers_matrix_partK.csv、2026-10-04 追加）
担当の単位 × 条件について、「その条件で、その単位の活性が通常の増殖と比べて上がるか・下がるか」を論文で確かめて、
`data/activity_markers.csv` と同じ列で書く:
`unit,gene,genes,condition,change,marker,timing,pmids,evidence,confidence,verified_on,note,sites,sites_source`
- `unit`: 単位の名前（例: `TORC1`・`PKA`・`SNF1`・`HOG1`。既存の行と同じ書き方に合わせる）。`gene`: 代表の遺伝子、`genes`: 含む遺伝子（`;`）
- `condition`: `data/conditions.csv` の **key**（1 行に 1 つ）。`unstressed` の行は通常の増殖での状態（`active`・`inactive`）
- `change`: `up`・`down`・`transient_up`・`transient_down`・`no_change`（通常の増殖と比べて。一過性なら transient_、何分かは timing に）
- `marker`: 何を見てそう言えるか（日本語で。例: 「Sch9 T737 のリン酸化」「Msn2 の核移行」「Hog1 の T174/Y176 のリン酸化」）
- `sites`: 部位が分かれば `遺伝子:部位:符号`（活性が上がるとリン酸化が上がるなら `+`、下がるなら `-`）。`sites_source` にその出典
- `evidence`: 論文の要旨か本文の一文をそのまま写す（照合のためだけ。data/activity_markers.csv に入れるときは空にする）。`pmids`: 読んだ論文（最大 3）
- **すでに `data/activity_markers.csv` にある unit × condition の組は書かない**（重複を避ける。先に読んで確かめる）
- 論文に書かれていない組は書かない（空いた枠を推測で埋めない）。決められなかった枠は `markers_matrix_not_found_partK.csv`
  （`unit,condition,read_pmids,note`）に
- 照合: `.venv/bin/python tools/verify_literature.py 下書き.csv --out 下書き.verify.tsv`（列 gene で照合する）

## ヒストンを修飾する酵素 → ヒストン（2026-10-04、docs/curation/literature_fields.md に合わせる）
印を付ける（アセチル化・メチル化・リン酸化など）＝ `activate`、外す（脱アセチル化・脱メチル化など）＝ `inhibit`。
印の種類と、その印が転写にどう働くか（例: H3K4me3 は活性化、H4K16 の脱アセチル化はサイレンシング）は `note` に書く。

## 転写因子の活性の目印（markers_tf_partK.csv、2026-10-04 追加）
目的: 転写因子が「どの条件で働く（活性が上がる・下がる）か」を集める。これで、その転写因子の向き不明の転写制御に、
条件ごとの発現から向きが付く（tools/condition_expression.py。2 つ以上の条件が要る）。
- 列と書き方は「目印の表」と同じ（`data/activity_markers.csv` の列）。unit は転写因子の名前（まとめるなら `INO2/INO4` の形）
- **condition は次の中から**（発現のデータがある条件。これ以外は推定に使われない）:
  adenine_limitation・alkaline・alt_carbon・alt_nitrogen・amino_acid_starvation・carbon_starvation・cell_wall・cold・diauxic_shift・
  drug・er・genotoxic・glucose_limitation・glucose_repletion・heat・hypoxia・iron_limitation・lipid・metal_stress・nitrogen_starvation・
  nonfermentable・osmotic・oxidative・rapamycin・reductive・stationary・weak_acid（と、通常の増殖の状態 unstressed）
- 1 つの転写因子につき 2 条件以上を目指す。「その条件で転写因子が活性化・核移行・リン酸化・発現が上がって標的を誘導する」と
  論文に書かれていること（標的 1 つの誘導だけでなく、転写因子の働きとして書かれているもの）
- 条件で活性が変わらない転写因子（いつも働く一般の転写因子など）は、無理に書かず not_found に「条件で変わらない」と書く
- すでに `data/activity_markers.csv` にある unit × condition は書かない
