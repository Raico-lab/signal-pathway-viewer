# PyInstaller 設定。 Mac: .app / Windows: フォルダ形式の .exe を生成する。
#   pyinstaller PathwaysViewer.spec
# インストーラーはこの後に作る（Mac: installer/mac/make_dmg.sh、Windows: installer/windows/PathwaysViewer.iss）
import os
import sys

VERSION = os.environ.get("PATHWAYS_VERSION", "0.0.0")   # 配布物の版（GitHub Actions がタグや日付から入れる）

# 同梱するのはアプリが読むデータだけ（*_sources.csv などは取り込みの道具用なので入れない）
DATA_FILES = ["tor_pathway.db", "expression.db", "server.json", "conditions.csv", "complex_portal.tsv", "papers.json", "sources.json"]

a = Analysis(
    ["main.py"],
    # LICENSE などは GPLv3 で配るために同梱する（アプリの「ヘルプ > このアプリについて」からも案内する）
    datas=[("tor_app/web", "tor_app/web")] + [(f"data/{name}", "data") for name in DATA_FILES]
          + [(name, ".") for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "DATA_LICENSES.md")],
    hiddenimports=["sqlalchemy.dialects.sqlite"],
    # pandas・numpy・scipy はアプリでは使わない（CSV の読み込みは取り込みの道具だけ。networkx は numpy なしで動く）
    excludes=["tkinter", "matplotlib", "IPython", "pandas", "numpy", "scipy"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PathwaysViewer",
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PathwaysViewer")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="PathwaysViewer.app",
        bundle_identifier="jp.lab.pathwaysviewer",
        version=VERSION,
        info_plist={"NSHighResolutionCapable": True, "CFBundleDisplayName": "Pathways viewer for Saccharomyces cerevisiae"},
    )
