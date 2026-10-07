# 依頼: 出芽酵母の主要シグナル経路の制御関係を、文献付きで一覧にしてください

## 背景
出芽酵母（*Saccharomyces cerevisiae*）の遺伝子間の制御関係を可視化・シミュレーションするアプリのデータベースを整備しています。
公開データ（BioGRID・KEGG・YEASTRACT+）から作ったため、主要なシグナル経路で次の問題があります。

- 重要な関係が抜けている（例: BCY1 → TPK1/2/3、URE2 → GLN3、TCO89 などの TORC1 構成要素から下流への関係がない）
- 関係はあるが、促進か抑制か（作用）が分からないものが多い（下の対象遺伝子の間の 249 本のうち 129 本）
- 作用が逆に登録されているものがある（例: PHO85 → PHO4 が「促進」。実際は Pho80–Pho85 によるリン酸化で Pho4 は核外へ出て働かなくなる＝抑制）

そこで、文献を確認しながら、対象の遺伝子の間の制御関係を一覧にしてください。

## 作業は 2 つ
**A. 新しい関係の一覧**: 対象の遺伝子の間で、文献で裏付けのある制御関係を挙げる。
**B. 今の記録の点検**: 添付の `current_core_relations.csv`（今のデータベースの 249 本。転写制御は除く）の各行について、
文献で確かめて「正しい／作用を直す／関係として誤り／確かめられない」を判定する。特に作用（effect）が `none` の 129 本と、
出典が KEGG だけ、または KEGG+BioGRID で `phosphorylation` かつ `activate` の行を優先してください。

量が多いので、経路ごとに分けて、1 経路ずつ CSV を作ってもらって構いません（例: `TORC1.csv`）。

## 対象の遺伝子（SGD の標準名）
| 経路 | 遺伝子 |
|---|---|
| TORC1 | TOR1 TOR2 KOG1 LST8 TCO89 GTR1 GTR2 MEH1 EGO2 SLM4 PIB2 NPR2 NPR3 IML1 SEA4 FPR1 TAP42 TIP41 SIT4 PPH21 PPH22 SAP4 SAP155 SAP185 SAP190 SCH9 NPR1 GLN3 GAT1 URE2 SFP1 MAF1 DOT6 TOD6 RIM15 GIS1 ATG1 ATG13 GCN2 GCN4 RTG1 RTG2 RTG3 MKS1 IGO1 IGO2 CRF1 FHL1 IFH1 |
| TORC2 | AVO1 AVO2 TSC11 BIT61 SLM1 SLM2 YPK1 YPK2 PKH1 PKH2 ORM1 ORM2 FPK1 |
| PKA（Ras/cAMP） | RAS1 RAS2 CDC25 IRA1 IRA2 CYR1 BCY1 TPK1 TPK2 TPK3 GPR1 GPA2 PDE1 PDE2 YAK1 MSN2 MSN4 |
| SNF1 | SNF1 SNF4 GAL83 SIP1 SIP2 SAK1 TOS3 ELM1 REG1 GLC7 MIG1 MIG2 CAT8 ADR1 HXK2 |
| PHO | PHO85 PHO80 PHO81 PHO4 PHO2 PHO84 |
| HOG | SLN1 YPD1 SSK1 SSK2 SSK22 SHO1 MSB2 HKR1 STE20 STE50 STE11 PBS2 HOG1 PTP2 PTP3 HOT1 SKO1 SMP1 |
| 細胞壁（CWI） | SLG1 MID2 ROM2 RHO1 PKC1 BCK1 MKK1 MKK2 SLT2 RLM1 SWI4 SWI6 |

経路をまたぐ関係（例: SCH9 → RIM15、TPK1 → MSN2、SNF1 → MSN2）も含めてください。

## 出力の形式（CSV、UTF-8）
1 行に 1 つの関係（上流 → 下流）。列は次のとおり。

| 列 | 内容 |
|---|---|
| `source_protein` | 上流（作用する側）の遺伝子の SGD 標準名 |
| `target_protein` | 下流（作用を受ける側）の遺伝子の SGD 標準名 |
| `interaction_type` | 仕組み。次のどれか: `phosphorylation` `dephosphorylation` `ubiquitination` `transcription` `localization` `binding` `activation`（仕組みは不明だが促進） `inhibition`（仕組みは不明だが抑制） `other` |
| `effect` | 下流の**活性・機能**への作用: `activate` / `inhibit` / `none`（作用が決められない）。**矢印の向きではなく機能で判断してください**（例: リン酸化で核外に出されて働かなくなるなら `inhibit`） |
| `direct` | `direct`（直接の作用）か、`via 遺伝子名や分子名`（例: `via cAMP`、`via PKA`）か |
| `condition` | 作用がある条件（環境）と細胞周期の時期。いつもなら空欄。**決まった日本語の言葉で書く**（下の「条件の書き方」）。例: `ラパマイシン`、`窒素飢餓・G1 期`、`分裂期の終わり` |
| `pmids` | 根拠の PubMed ID（`;` 区切り。最大 3 つ。**実際に確認した論文だけ**） |
| `paper_title` | `pmids` の論文の題名（PubMed の英語の題名そのまま。複数なら `;` 区切りで同じ順） |
| `evidence` | 根拠の要約（英語で 1〜2 文。論文の内容の要約で、引用の書き写しでなくてよい） |
| `confidence` | `high`（直接の実験で示されている）/ `medium`（複数の間接的な証拠・総説での記述）/ `low`（推測を含む） |
| `status` | 作業 A の新しい関係は `new`。作業 B の点検は `confirm`（正しい）/ `correct`（作用や仕組みを直す。この行の値が正しいもの）/ `reject`（関係として誤り）/ `unverified`（確かめられない） |
| `note` | 補足（任意。食い違う報告がある、複合体として働く、など） |

例:
```
source_protein,target_protein,interaction_type,effect,direct,condition,pmids,paper_title,evidence,confidence,status,note
PHO85,PHO4,phosphorylation,inhibit,direct,,9732266,Phosphorylation regulates association of the transcription factor Pho4 with its import receptor Pse1/Kap121.,"Pho80-Pho85 phosphorylates Pho4, promoting its nuclear export via Msn5 and blocking its import, which inactivates PHO gene transcription.",high,correct,Pho80 is the cyclin partner
BCY1,TPK1,inhibition,inhibit,direct,,<PMID>,<題名>,"Bcy1 is the regulatory subunit of PKA and binds and inhibits the catalytic subunits Tpk1-3; cAMP binding releases them.",high,new,also TPK2 and TPK3
CYR1,BCY1,inhibition,inhibit,via cAMP,,<PMID>,<題名>,"Adenylate cyclase Cyr1 produces cAMP, which binds Bcy1 and releases the catalytic PKA subunits.",high,new,small-molecule mediated
```

## 条件（`condition`）の書き方
地図では、この列から左の「条件」タブの条件（環境の条件と、細胞周期の時期）を付けます。
**決まった言葉を含む書き方だけが地図に載ります**。当てはまらない書き方は根拠欄に文字で残るだけで、絞り込みには使えません。

- **環境の条件**: `data/conditions.csv` の条件名を日本語で書く（一覧は `.venv/bin/python tools/check_conditions.py --list`）。
  例: `窒素飢餓`・`アミノ酸飢餓`・`グルコース飢餓`・`グルコース制限`・`非発酵性炭素源`・`定常期`・`浸透圧ストレス`・`熱ショック`・
  `酸化ストレス`・`DNA 損傷`・`細胞壁ストレス`・`小胞体ストレス`・`ラパマイシン`（「薬剤・化学物質」になる）・`通常の増殖`。
  英語（`glucose starvation` など）は当てはまらないので使わない
- **細胞周期の時期**: `G1 期`・`S 期`・`G2 期`・`分裂期`・`分裂期の終わり`（細胞質分裂を含む）。
  範囲は含む時期を全部並べる（`S 期〜分裂期` ではなく `S 期・G2 期・分裂期`。`G2/M` は `G2 期・分裂期`）
- **両方あれば並べる**: `窒素飢餓・G1 期`。いくつもあれば「・」で区切る
- **働く場面・仕組みは `note` に書く**: `ペキソファジー`・`テロメアのサイレンシング`・`Clb2 と組んだとき`・`紡錘体チェックポイント`
  などは条件ではないので、`condition` には入れず `note` に書く（時期が分かれば時期だけを `condition` に書く）
- いつも働く関係は空欄
- 書いたら `.venv/bin/python tools/check_conditions.py <CSV>` で確かめる。「当てはまらない行」が出たら、言葉を直すか `note` に移す。
  一覧にない環境の条件が要るときは、`data/conditions.csv` に行を足すので相談する

## 守ってほしいこと
- **論文番号を作らないでください。** 実際に開いて内容を確かめた論文の PMID だけを書き、確かめられなければ `pmids` を空欄にして `confidence` を `low` にしてください。
  受け取った CSV は PubMed で機械的に照合します（`tools/verify_literature.py`）。1 回目の依頼では PMID のほとんどが関係のない論文を指していたため、
  **列 `paper_title` に、その PMID の論文の題名（PubMed に表示される英語の題名そのまま）を必ず書いてください。**
  題名が PubMed の記録と一致しない行は取り込みません。
- 論文を見つけられない関係は、無理に PMID を書かず `pmids` を空欄にしてください（空欄の行は、こちらで文献を探します）。
- 対象は出芽酵母だけです。ほかの生物（ヒト・分裂酵母など）の知見から類推した関係は含めないでください。
- 遺伝子名は SGD の標準名を使ってください（別名で書かれた論文は標準名に直す。例: Ego1 → MEH1、Ego3 → SLM4、Wsc1 → SLG1、Avo3 → TSC11）。
- 小分子（cAMP など）や複合体を介する関係は、遺伝子 → 遺伝子の形にして、`direct` に `via cAMP` のように書いてください。
- 複合体全体が作用する場合（例: TORC1 → SCH9）は、触媒サブユニット（TOR1・TOR2 など）を上流にし、`note` に複合体名を書いてください。
- 条件によって作用が逆になる関係は、条件ごとに別の行にしてください。
- 作業 B で `correct` にするときは、その行に正しい値を書き、`note` に元の値からどこを直したかを書いてください。

## 添付
- `current_core_relations.csv`: 今のデータベースの、対象の遺伝子の間の関係（転写制御を除く 249 本）。列 `current_sources` は今の出典（BioGRID は酵素と基質の記録で作用は不明、KEGG は経路図の矢印から作用を付けたもの、サンプルは構造確認用で根拠が弱い）。
