# 依頼: 複合体・役割が重なる遺伝子の組の点検（文献の確認）

出芽酵母（*Saccharomyces cerevisiae*）のシグナル伝達・転写制御のモデルで、いくつかの遺伝子を「まとまり」（複合体や、役割が
重なるパラログの組）として扱っています。添付の `complexes_review.csv`（78 行・23 のまとまり）は、こちらの知識で作った案です。
各行が文献で裏付けられるかを確かめ、誤りを直し、足りないまとまりを加えてください。

## まとまりの扱い方（モデルの決まり）
まとまりの働き（0〜1）は、構成要素の働きから次のように計算します。

- **枠（slot）**: 同じ枠の遺伝子は「どれか 1 つが働けばよい」（OR）。違う枠どうしは「すべて必要」（AND）。
  例: PKA = 枠 `catalytic`（TPK1・TPK2・TPK3 のどれか）。SNF1 複合体 = SNF1 かつ SNF4 かつ（GAL83・SIP1・SIP2 のどれか）。
- **`accessory`（補助）**: 破壊しても働きが少し下がるだけ、または特定の条件でだけ要る。計算に入れない。
- **`inhibitor`（抑える側）**: まとまりの働きを抑えるサブユニット（PKA の BCY1）。計算に入れず、別の関係（BCY1 ⊣ TPK1 など）で表す。

つまり、各遺伝子について知りたいのは **「その遺伝子を破壊すると、まとまりの働きはどうなるか」** です。
- ほぼなくなる → 単独の枠（必須）
- パラログが残っていれば保たれる → パラログと同じ枠
- 少し下がる・ある条件でだけ下がる → `accessory`（条件は列 `condition` に）

## 作業 1: 今の 78 行の点検
`complexes_review.csv` の各行について、`verdict` に次のどれかを書いてください。

| verdict | 意味 |
|---|---|
| `keep` | 今の枠のままでよい（文献で確認できた） |
| `change` | 枠を変える（`proposed_slot` に新しい枠） |
| `remove` | このまとまりの構成要素ではない |
| `unsure` | 文献で決められなかった（`note` に分かったことを書く） |

特に確かめてほしい点:
1. **TORC1**: TCO89 は `accessory` でよいか（tco89Δ で TORC1 の働きはどの程度下がるか）。TOR1 と TOR2 は TORC1 の中で
   「どちらか一方でよい」と言えるか。
2. **TORC2**: AVO2・BIT61 は `accessory` でよいか。BIT2 は加えるべきか。AVO1・TSC11（AVO3）・LST8 は必須か。
3. **CK2**: 調節サブユニット CKB1・CKB2 は `accessory` でよいか（ckb1 ckb2 破壊株で CK2 の働きはどうなるか）。
4. **ATG1 複合体**: ATG29・ATG31 は、飢餓で誘導されるオートファジーに必須か（条件つきなら `condition` に書く）。
   ATG11 は加えるべきか。
5. **PP2A**: 今は触媒（PPH21・PPH22）と足場（TPD3）だけです。B サブユニット（CDC55・RTS1）を加えて、
   PP2A-Cdc55 と PP2A-Rts1 の 2 つに分けるべきか。
6. **SIT4 複合体**: SAP4・SAP155・SAP185・SAP190 は「どれか 1 つでよい」と言えるか（SAP4 は今の表にない）。
7. **EGO・SEACIT**: 構成要素はこれで全部か（EGO4 など）。SEACIT の IML1・NPR2・NPR3 は 3 つとも必須か。
8. **役割が重なる組**: RAS1/RAS2、IRA1/IRA2、YPK1/YPK2、PKH1/PKH2、YCK1/YCK2、MKK1/MKK2、IGO1/IGO2、MSN2/MSN4、
   TPK1/TPK2/TPK3 は、本当に「どちらか 1 つで足りる」か。片方の破壊で強い表現型が出る組（例: IRA2 と IRA1 の違い）は
   `note` に書いてください。なお TPK3 については「Bcy1 の結合で制御されない」という報告（PMID 38865242）があります。

## 作業 2: 転写因子の複合体の追加
転写の調節に、DNA に結合しない補助因子（SWI6・TUP1・SIN3 など）が関わるものがあります。モデルでは、補助因子を DNA に結合する
相手とまとめないと、その破壊の影響を説明できません。次のようなまとまりを、作業 1 と同じ列で加えてください（`verdict` は `add`）。

- SBF（SWI4・SWI6）、MBF（MBP1・SWI6）
- INO2・INO4（ヘテロ二量体）
- HAP 複合体（HAP2・HAP3・HAP5・HAP4）
- Cyc8（SSN6）–Tup1 と、それを呼ぶ DNA 結合因子（MIG1・NRG1・SKO1・ROX1 など）の関係
- そのほか、出芽酵母の主要な転写因子で、相手がいないと働かないもの（例: RTG1・RTG3、PHO4・PHO2、GCR1・GCR2）

## 出力
`complexes_review.csv` に列を埋めて返してください（列の順は変えないでください）。

`complex,gene,current_slot,current_note,verdict,proposed_slot,condition,pmids,paper_title,quote,confidence,note`

- `pmids`: 根拠の論文の PubMed ID（複数は `;` 区切り）。
- `paper_title`: その論文の、PubMed に表示される英語の題名そのまま（複数は `;` 区切りで `pmids` と同じ順）。
- `quote`: 根拠になる一文を、**PubMed の題名か要旨から一字一句そのまま写したもの**（英語）。複数の論文なら ` | ` で区切り、
  `pmids` と同じ順に並べる。
- `confidence`: high（直接の実験）／ medium（遺伝学的・間接）／ low（総説だけ）。
- 新しく加える行（作業 2）は、`current_slot`・`current_note` を空欄にしてください。

## 守ってほしいこと（大事）
これまでの依頼で、受け取った PMID の大半が関係のない論文（麻酔・ワクチンなど）を指し、題名も PubMed に存在しないものでした。

- **PubMed のページ（`https://pubmed.ncbi.nlm.nih.gov/<PMID>/`）を実際に開いて確かめた論文だけ**を書いてください。
- 開いて確かめられないときは、`pmids`・`paper_title`・`quote` を **空欄** にして、`verdict` を `unsure` にしてください。
  空欄は問題ありません。推測の番号よりずっと役に立ちます。
- 受け取った CSV は、こちらで自動で照合します（`tools/verify_literature.py`）。PMID が実在するか・題名が一致するか・
  `quote` が要旨にそのまま含まれるか・遺伝子名が出てくるか、を調べ、合わない行は使いません。

## 添付
- `complexes_review.csv`: 点検する 78 行（今の案の枠と説明つき）
