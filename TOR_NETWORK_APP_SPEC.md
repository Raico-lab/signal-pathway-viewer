# 出芽酵母 遺伝子制御ネットワーク解析＆上流予測 デスクトップアプリ仕様書

> **前提の変更（2026-09-30）**: 当初は TOR 経路に特化していたが、TOR に限らず**全遺伝子間の関係性**を扱う前提に変更した。
> TOR 経路は代表例・サンプルデータとして引き続き使う。本文中の TOR の記述は例として読む。

## 1. プロジェクトの目的・前提

- 対象: 出芽酵母（Saccharomyces cerevisiae）の全遺伝子（約 6,000）間の制御関係を扱うローカルデスクトップアプリケーション。TOR 経路は代表例。
- ターゲットユーザー: プログラミングの知識がない研究室のメンバーや共同研究者。インストーラーや実行ファイルをダブルクリックするだけで誰でも直感的に使えるツールを目指す。
- 配布形態: 最終的に PyInstaller 等を用いて、Python環境がないPCでも単体で起動できる実行ファイル（Windowsなら .exe、Macなら .app）としてビルドできる構成にする。
- データの規模: タンパク質は約 6,000、制御関係は数万〜数十万本になりうる。すべての関係に作用の向き（活性化／抑制）や閾値が付くわけではなく、「関係があることだけ分かっている」ものが大半になる前提とする。

## 2. データベース設計の方針（シグナル強度・閾値制御の考慮）

単なるON/OFFの二値だけでなく、タンパク質の親和性や感度の違いによるシグナル強度の分岐（例: 「ある程度の活性低下ではAだけが活性化し、完全に不活性化するとAもBも不活性化する」といった挙動）を表現できるよう、タンパク質の性質や閾値ルールを保持できるスキーマ設計にする。

### ① 蛋白質テーブル (Proteins)

- `id`: 主キー
- `gene_name`: 遺伝子名（例: TOR1, SCH9, GLN3）
- `standard_name`: Systematic名（例: YJR066W）
- `complex_category`: 所属グループ（TORC1, TORC2, Downstream, Effector 等）
- `protein_properties`: タンパク質の性質・感度パラメータ（親和性、ドメイン構造、シグナル閾値の特性メモ）
- `description`: 機能説明

### ② 制御関係テーブル (Interactions)

- `id`: 主キー
- `source_id`: 上流タンパク質のID
- `target_id`: 下流タンパク質のID
- `interaction_type`: 制御の種類（activation[活性化], inhibition[抑制], phosphorylation[リン酸化] 等）
- `signal_threshold_rule`: 強度・条件分岐ルール（「通常時は抑制」「軽度な活性低下では非依存、重度でON」等の条件や閾値の定義）
- `evidence`: 文献情報や根拠

## 3. 初期データ構造（CSVインポート用フォーマットの例）

初期データはブラウジングせず、手動（CSV等）で用意したものを読み込める仕様にする。

### Proteins サンプルデータ

```csv
gene_name,standard_name,complex_category,protein_properties,description
TOR1,YJR066W,TORC1,Kinase_Core,TORC1 complex core kinase component
KOG1,YBR182C,TORC1,Scaffold,TORC1 regulatory subunit
SCH9,YML028W,Downstream,PI3P_Binding_Kinase,"AGC family kinase, phosphorylated by TORC1"
GLN3,YDL134C,Downstream,Transcription_Factor,GATA family transcription factor regulated by TORC1/Tap42
```

### Interactions サンプルデータ

```csv
source_protein,target_protein,interaction_type,signal_threshold_rule,evidence
TOR1,SCH9,phosphorylation,High_Signal_Required,Direct phosphorylation by TORC1
TOR1,GLN3,inhibition,Low_Threshold_Inhibition,TORC1 inhibits Gln3 localization via Tap42/Sit4 pathway
```

## 4. アプリケーションの機能要件（3タブ構成のGUI）

### 1. データベース管理タブ

- 登録されたタンパク質と制御関係の一覧表示、新規追加、編集、削除。
- CSVファイルからのデータ一括インポート／エクスポート。

### 2. ネットワーク可視化タブ

- PyQt の `QWebEngineView` 内で Cytoscape.js を動かし、インタラクティブ（ドラッグ・拡大縮小・クリックで詳細表示）にネットワークを描画。
- 活性化（矢印）と抑制（T字）の視覚的区別、およびシグナル強度や閾値ルールの表示。

### 3. 上流経路予測タブ

- ユーザーが発現変動遺伝子のリスト（CSV）をアップロード。
- 下流エフェクターの変動傾向とデータベース内の制御ルール（閾値・シグナル強度）を突き合わせ、上流の経路（TOR 経路に限らない）がどう制御されていたかをスコア順に予想し、ネットワーク上でハイライト表示。

## 5. 推奨技術スタック

- 言語: Python 3.10+
- GUI: PyQt6
- DB: SQLite (SQLAlchemy)
- ネットワーク解析: NetworkX, Pandas
- 描画: Cytoscape.js (`QWebEngineView` 埋め込み)
- ビルド・配布: PyInstaller（デスクトップアプリ化用）

## 6. 全遺伝子を扱うための留意事項（前提変更に伴う追記）

現時点ではコードを変更しない。データが全遺伝子規模になった段階で、次の点を検討する。

- **可視化**（2026-09-30 実装済み）: 全体は描かず、注目する遺伝子の周辺（上流・下流 n 段）だけを描く。先が多い遺伝子は 30 件まで描いて残りを件数で示し、ダブルクリックで展開する。縮小時は下流の枝先を省略して軽くする。
- **シミュレーション・予測**: 作用の向きが不明な関係（effect = none）は計算に使わず、表示だけに使う（現在の仕様どおり）。シミュレーションは表示中の遺伝子と 1 段外側の上流だけで計算する（2026-09-30 実装済み）。予測の対象範囲は、発現データに現れた遺伝子の上流に絞る。
- **起動時間**: 6 万本規模の架空データで、起動時の DB 読み込みに約 10 秒かかった。実データを入れた段階で必要なら短縮する。
- **カテゴリ**: 「TORC1」「Downstream」のような TOR 前提の分類に加えて、経路名（例: TOR、PKA、SNF1、HOG）や機能分類で分けられるようにする。1 つの遺伝子が複数の経路に属する場合の扱いも決める。
- **DB 管理タブ**: 数万行の一覧でも操作できるよう、絞り込みを前提にする。
- **データの配布**: DB のサイズが大きくなるため、同梱・外部配信のどちらでもサイズを確認する（docs/EXTERNAL_DB_PLAN.md）。
