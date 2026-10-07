# 論文から拾うもの（2026-10-04）

> 2026-10-07 から、論文の文の引用（`evidence`・`fill_evidence`）はリポジトリに入れない。照合のために写した一文は Google Drive の
> 下書きにだけ置き、照合が済んだら空にして `sample_data/literature/`・`data/activity_markers.csv` に移す（出典は `pmids` で明記する）。

関係の向きを確かめるために論文を読むとき、あわせて拾うもの。いずれシミュレーションを復活させるので、
その入力になる情報（入力の組み合わせ方・効く強さ・時間など）も拾う。**論文に書かれていないものは空欄にする。推測で埋めない**
（シミュレーションを誤らせるため）。

出力は 3 つ。

| 出力 | ファイル | 中身 |
|---|---|---|
| ① 関係 | `sample_data/literature/verified_roundN.csv`（下書きは `docs/curation/drafts/roundN_partK.csv`） | 調べている組の関係と、その性質 |
| ② 見つかった別の関係 | 下書き `docs/curation/drafts/side_partK.csv`（形は ① と同じ） | 読んだ論文に根拠の一文がある、調べている組以外の関係。照合して ① に加える |
| ③ 条件ごとの活性 | `data/activity_markers.csv`（下書き `docs/curation/drafts/markers_partK.csv`） | 「条件 X で遺伝子 Y の活性が上がる・下がる」。通常の増殖での状態と細胞周期の時期も含む |

## ① 関係の列

今までの列（`source_protein,target_protein,interaction_type,effect,direct,condition,pmids,evidence,confidence,status,note,verified_on,verified_note`）
の後ろに、次の列を足す。

| 列 | 書き方 | 例 | 使い道 |
|---|---|---|---|
| `condition` | 環境の条件と細胞周期の時期を、決まった日本語の言葉で（`docs/curation/request_core_pathways.md` の「条件の書き方」。`tools/check_conditions.py` で確かめる）。働く場面・仕組みは `note` へ | `窒素飢餓`・`G1 期`・`DNA 損傷・S 期` | 地図の「条件」タブ（細胞周期の時期も同じタブの 1 つの群） |
| `partner` | 働くときに組む相手（サイクリン・調節サブユニット・アダプター）。遺伝子名、複数は `;`。**CDK（CDC28・PHO85）の行は、論文がサイクリンを特定していれば必ず書く**（特定していなければ空欄。推測で書かない） | `CLB2`・`PCL1`・`MOB1` | 同じキナーゼでも相手で標的と時期が変わる。サイクリンからは細胞周期の時期も分かる（取り込みで「サイクリンからの推定」として付ける案） |
| `site` | 修飾の部位。`S582`・`T737` の形、複数は `;` | `S14;S15` | Leutert のリン酸化データとつなぐ（`tools/check_activity_markers.py` と同じ仕組み） |
| `evidence_kind` | 根拠の実験の種類。`in_vivo`・`in_vitro`・`genetic`（遺伝学だけ）・`structural`。複数は `;` | `in_vivo;in_vitro` | 確度を一定の基準で付ける |
| `necessity` | 上流が下流の作用に必要か・十分か。`necessary`（欠損で失われる）・`sufficient`（上流だけで起こる）・`both`・`partial`（欠損で一部だけ減る） | `necessary` | 入力の組み合わせ方（AND／OR）を決める |
| `redundant_with` | 同じ作用をもつ別の上流（重複して働く）。遺伝子名、複数は `;` | `TPK2;TPK3`・`PKH2` | 同じ働きのまとまりとして扱う |
| `strength` | 効く強さ。`strong`（完全に失われる・大きく変わる）・`moderate`（下がる・上がる）・`weak`（わずかに） | `strong` | 重み（weight）の目安 |
| `layer` | 下流の何を変えるか。`expression`（転写・mRNA 量）・`activity`（酵素活性・結合）・`stability`（分解）・`localization`（局在）。複数は `;` | `localization` | 発現と活性を分けて計算する |
| `timing` | 時間の性質。`transient`（一過性）・`sustained`（持続）・`adaptive`（適応すると戻る）と、書かれていれば時間（`5 分以内` など）を日本語で | `transient・5 分以内` | 一過性の応答・フィードバックの再現 |

## ② 見つかった別の関係

① と同じ列。読んだ論文に、調べている組とは別の関係が根拠の一文つきで書かれていれば拾う
（例: BUD32 → SCH9 を調べて、実際は SCH9 → BUD32 だと分かったとき、ELM1 → BFA1 を調べて ELM1 → KIN4 と分かったとき）。
① と同じ規則（自分で読んだ論文の一文をそのまま写す・推測しない）で書く。

## ③ 条件ごとの活性

`data/activity_markers.csv` と同じ列:
`unit,gene,genes,condition,change,marker,timing,pmids,evidence,confidence,verified_on,note,sites,sites_source`

- `condition` は条件の **キー**（`data/conditions.csv` の `key` 列）。環境は `nitrogen_starvation`・`rapamycin`・`osmotic` など、
  細胞周期の時期は `g1`・`s_phase`・`g2`・`m_phase`・`mitotic_exit`、通常の増殖は `unstressed`。1 行に 1 つ
- `change` は通常の増殖と比べて `up`・`down`・`transient_up`・`transient_down`・`no_change`。`unstressed` の行は `active`・`inactive`
  （通常の増殖での状態。シミュレーションの基準の状態になる）。細胞周期の時期の行は、ほかの時期と比べて `up`・`down`
- `marker` は何を見てそう言えるか（リン酸化の部位・局在・活性の測定など）。`sites` は `遺伝子:部位:符号`（符号は活性が上がるとき
  リン酸化が上がるなら `+`）
- 論文に「条件 X で Y が活性化する」のように書かれているときだけ拾う。関係を調べる論文に書かれていることは多くないので、見つかったときだけでよい

## ヒストンの関係の向き（2026-10-04 に決めた）

ヒストン（HHT1/2・HHF1/2・HTA1/2・HTB1/2・HTZ1・HHO1）を修飾する関係は、その印がヒストンのどの働き（転写・サイレンシング・修復など）に効くかではなく、
**印を付ける＝促進（activate）、印を外す＝抑制（inhibit）** で揃える。

- 付ける: リン酸化・アセチル化・メチル化・ユビキチン化・SUMO 化。外す: 脱リン酸化・脱アセチル化・脱メチル化・脱ユビキチン化、ヒストンの尾部の切断。
- interaction_type はその修飾にする。印（例: H3K4me3）と、その印の働き（例: 転写の活性化・サイレンシング）は note に「印: H3K4me3（…）」と書く。
- 触媒サブユニットでない構成要素（SAGA の ADA2 など）は、欠失で印が消える（増える）と論文に書かれていれば同じ向きにし、note に「触媒ではない（印に必要）」と書く。
- その酵素が生体内でそのヒストンの印を付ける（外す）と書いた一次論文の一文が要る（網羅的な実験・試験管内だけは決めない）。H3（HHT1/HHT2）・H4（HHF1/HHF2）などは
  同じタンパク質なので、1 本の論文で両方の遺伝子の行にしてよい。

## 確かめ方
- ①②: `.venv/bin/python tools/verify_literature.py <CSV>`（PMID・引用）と `tools/check_conditions.py <CSV>`（条件の言葉）
- ③: `tools/verify_literature.py` は列 `gene` で照合する。条件のキーは `data/conditions.csv` にあるものだけ
