# Third-party software / ほかの部品のライセンス

Pathways Viewer itself is licensed under the GNU General Public License version 3 (see `LICENSE`).
It uses the following third-party software. Each component remains under its own license.

Pathways Viewer 本体は GPLv3（`LICENSE`）です。次の部品を使っており、それぞれの部品にはそれぞれのライセンスが適用されます。

## デスクトップ版（同梱して配布）

| 部品 | 版 | ライセンス | 入手先 |
|---|---|---|---|
| PyQt6・PyQt6-WebEngine | 6.11 | GPL-3.0-only（Riverbank Computing） | https://www.riverbankcomputing.com/software/pyqt/ |
| Qt 6・Qt WebEngine（PyQt6-Qt6・PyQt6-WebEngine-Qt6 に含まれる。Chromium を含む） | 6.11 | LGPL-3.0（全文は `licenses/LGPL-3.0.txt`。LGPL-3.0 が引く GPL-3.0 の全文は `LICENSE`。Qt WebEngine に含まれる Chromium などは各ライセンス） | https://www.qt.io/ （Qt のソース: https://download.qt.io/official_releases/qt/ ） |
| SQLAlchemy | 2.1 | MIT | https://www.sqlalchemy.org/ |
| NetworkX | 3.6 | BSD-3-Clause | https://networkx.org/ |
| PyInstaller（実行ファイルを作るのに使用） | 6.22 | GPL-2.0-or-later（作ったプログラムの配布を認める例外つき） | https://pyinstaller.org/ |
| Python | 3 | PSF License | https://www.python.org/ |

デスクトップ版は Qt を別のファイル（共有ライブラリ）として同梱しています。LGPL-3.0 の条件のとおり、利用者は同じ版の Qt に差し替えられます。

The desktop version ships Qt as separate shared libraries, so users can replace them with their own build as permitted by LGPL-3.0.

## 地図の表示（デスクトップ版・ブラウザ版の両方。`tor_app/web/lib/`）

| 部品 | 版 | ライセンス | 入手先 |
|---|---|---|---|
| Cytoscape.js | 3.30.2 | MIT | https://js.cytoscape.org/ |
| dagre | 0.8.5 | MIT | https://github.com/dagrejs/dagre |
| cytoscape-dagre | 同梱の版 | MIT | https://github.com/cytoscape/cytoscape.js-dagre |
| cytoscape-svg | 0.4.0 | GPL-3.0 | https://github.com/kinimesi/cytoscape-svg |

## ブラウザ版（実行時にブラウザが読み込む）

| 部品 | 版 | ライセンス | 入手先 |
|---|---|---|---|
| Pyodide（ブラウザの中の Python） | 0.29.3 | MPL-2.0（含まれる CPython などは各ライセンス） | https://pyodide.org/ （cdn.jsdelivr.net から読み込む） |

ブラウザ版は PyQt6・Qt を含みません。代わりに `webapp/shim/PyQt6/`（このプロジェクトで書いた、Qt の部品を HTML で作る互換層。GPLv3）を使います。

The browser version does not include PyQt6 or Qt; it uses `webapp/shim/PyQt6/`, a compatibility layer written for this project (GPLv3).

各ライセンスの全文は、それぞれの入手先にあります。The full license texts are available from each project.
