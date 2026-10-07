"""ブラウザ版のための PyQt6 の互換層（Pyodide の中で使う）。

デスクトップ版の tor_app/ui のコードを、そのままブラウザで動かすために、使っている Qt の部品・仕組みだけを
HTML の要素で作り直したもの。本物の PyQt6 の一部だけを、見た目と動きが近くなるように真似ている。
足りない機能に当たったら、ここに足す（tor_app/ui の側をブラウザ向けに変えない）。

- _core.py: 列挙・QObject・シグナル・タイマー・イベント・座標・項目のモデル
- _dom.py: HTML の要素の出し入れ（js の呼び出しをまとめたもの）
- _gui.py: 色・フォント・キー・ショートカット・外部のリンク
- _widgets.py: 部品と並び（レイアウト）
- _items.py: 一覧・木・表
- _web.py: 地図を表示する iframe（QWebEngineView）と、地図のページとのやりとり（QWebChannel）
"""
PYQT_VERSION_STR = "6.web"
