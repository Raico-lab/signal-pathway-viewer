# archive: 配布版に入れない試作（2026-10-02 にアプリから外した）

観測した発現変化から上流を探す機能の試作と、その評価の道具・データ。地図のシミュレーション（活性の予測・閾値・強度）。配布するアプリ（`tor_app/`）からは使っていない。
記録として残す。使えそうな考え方は、繋がり（複数の遺伝子に共通する上流と経路の発見）の作り直しで引用してよい。

| 場所 | 中身 |
|---|---|
| `tor_app/regulator_search.py` | 制御遺伝子探索（転写因子の標的の重なり・符号つき・因果の採点） |
| `tor_app/sim_verify.py` | 制御遺伝子探索の候補をシミュレーションで確かめる |
| `tor_app/pathways.py` | 経路の提案（起点 → 経路 → 転写因子の仮説の順位づけ。精度が出ずに中止） |
| `tor_app/common_response.py` | 一般的なストレス応答の遺伝子の一覧を読む |
| `tor_app/ui/regulator_panel.py` | 左の「制御遺伝子探索」タブの画面 |
| `tools/evaluate_regulator_search.py` | 破壊株データ（Deleteome）での評価 |
| `tools/evaluate_conditions.py` | 原因の分かっている条件（Gasch・Causton・Hardwick）での評価 |
| `tools/evaluate_pathways.py` | 経路の提案の評価 |
| `tools/evaluate_sign_inference.py` | 破壊株データから推定した向きの交差検証 |
| `tools/make_common_response.py` | `data/common_response_genes.csv` を作る |
| `data/common_response_genes.csv` | 一般的なストレス応答の遺伝子（点検の一覧 `tools/make_review_queue.py` の並べ方にも使っている） |
| `sample_data/regulator_search/` | 制御遺伝子探索の観測の例（熱ショック・ラパマイシン） |
| `sample_data/condition_eval/` | 条件データでの評価の正解 |
| `raw_data/CONDITIONS/` | 条件データの発現（git の管理外。取得元は各評価の道具の説明） |
| `tor_app/simulation.py` | 地図のシミュレーション（閾値ルールで下流の活性を予測。2026-10-02 にアプリから外した） |
| `tor_app/baseline.py`・`data/baseline.csv` | シミュレーションの基準の状態（富栄養） |
| `tor_app/ui/widgets.py` | 活性の固定・閾値・強度のスライダー |
| `data/complexes.csv`・`tor_app/complexes.py` | 手作業でまとめた複合体の構成と根拠（メモ・PMID）。2026-10-03 に外した（複合体は Complex Portal だけにした）。論文は複合体の根拠に使えるかもしれないので残す |

評価の数字と経緯は `docs/TODO.md` に残してある。

## 動かすとき
置き場所を移しただけで、中身は外したときのまま。import は元の場所（`tor_app.regulator_search` など）を前提にしているので、
動かすときは各ファイルを元の場所（この表の場所から `archive/` を除いたところ）に戻す。画面に戻すには、
`tor_app/ui/network_tab.py` に「制御遺伝子探索」タブを、`tor_app/ui/graph_pane.py` に候補の経路の強調（`show_search_result`）を
加え直す（git の履歴にある）。
シミュレーションを画面に戻すには、`tor_app/graph_data.py` の `network()`・`params()`・閾値の一時変更（overrides）、
`tor_app/ui/graph_pane.py` の `run_simulation`、`tor_app/ui/network_tab.py` の基準の状態・計算に使う関係・活性の固定・
閾値と強度のスライダー、`tor_app/web/network.js` の活性の色分け（`setActivities`）を加え直す（git の履歴にある）。
DB の列（閾値・強度・基底活性・閾値ルール）は残してある。閾値・強度・閾値ルール・基底活性は 2026-10-03 にデータベースの表・編集画面・説明欄・CSV の書き出しから外した。CSV の読み込みでは、これらの列があれば使う。
