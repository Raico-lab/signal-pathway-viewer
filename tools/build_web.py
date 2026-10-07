"""ブラウザ版（GitHub Pages に置く静的なファイル一式）を web_dist/ に作る。

ブラウザの中の Python（Pyodide）で、デスクトップ版と同じ tor_app のコードを動かす。PyQt6 の代わりに、
webapp/shim/PyQt6/ の互換層（Qt の部品を HTML で作る）を読み込ませる。

作るもの（web_dist/）:
- index.html・boot.js・qt.css: 読み込みの画面と、Pyodide を起動する JavaScript
- app.zip: tor_app の .py・互換層・networkx・data/ の小さいファイル（Pyodide の中で /app に展開する）
- data/*.db.gz: 地図の DB と発現の DB（GitHub Pages は 1 ファイル 100MB まで。圧縮して置き、ブラウザで展開する）
- tor_app/web/: 地図のページ（表示枠ごとに iframe で開く。qrc:// の qwebchannel.js は互換のものに差し替える）
- version.json: 版（ファイルの取り直しの判定に使う）

使い方: python tools/build_web.py            （web_dist/ を作る）
        python tools/build_web.py --serve    （作ってから http://localhost:8765/ で開けるようにする）
公開は tools/publish_web.py。
"""
import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web_dist"
WEBAPP = ROOT / "webapp"
BIG_DATA = ("tor_pathway.db", "expression.db")   # 圧縮して別に置く
SMALL_DATA_SUFFIXES = (".csv", ".tsv", ".json")


def _networkx_dir() -> Path:
    import networkx
    return Path(networkx.__file__).resolve().parent


def _add_tree(zf: zipfile.ZipFile, src: Path, arc: str) -> None:
    for path in sorted(src.rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts or path.suffix in (".pyc",):
            continue
        if any(part.startswith(".") for part in path.relative_to(src).parts):
            continue
        zf.write(path, f"{arc}/{path.relative_to(src).as_posix()}")


def build_app_zip(dest: Path) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        # tor_app の Python（地図のページ tor_app/web/ は iframe で開くので zip には入れない）
        for path in sorted((ROOT / "tor_app").rglob("*.py")):
            if "__pycache__" not in path.parts:
                zf.write(path, path.relative_to(ROOT).as_posix())
        _add_tree(zf, WEBAPP / "shim", "shim")
        # networkx は純 Python。Pyodide の networkx は matplotlib などを一緒に読み込むので、手元のものを入れる
        nx = _networkx_dir()
        for path in sorted(nx.rglob("*.py")):
            rel = path.relative_to(nx.parent)
            if "__pycache__" in rel.parts or "tests" in rel.parts:
                continue
            zf.write(path, f"site/{rel.as_posix()}")
        for path in sorted((ROOT / "data").iterdir()):
            if path.name == "server.json":
                # ブラウザ版は開くたびに最新の DB を読むので、サーバーとの照らし合わせはしない
                zf.writestr("data/server.json", json.dumps({"base_url": ""}))
            elif path.suffix in SMALL_DATA_SUFFIXES:
                zf.write(path, f"data/{path.name}")


def build(out: Path = OUT) -> dict:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    for name in ("index.html", "boot.js", "qt.css"):
        shutil.copy2(WEBAPP / name, out / name)
    # GPLv3 の本文・ほかの部品とデータの利用条件も置く（公開先の一番上。ソースの場所は「このアプリについて」で案内する）
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "DATA_LICENSES.md"):
        shutil.copy2(ROOT / name, out / name)
    build_app_zip(out / "app.zip")
    (out / "data").mkdir()
    sizes = {}
    for name in BIG_DATA:
        src = ROOT / "data" / name
        raw = src.read_bytes()
        with gzip.open(out / "data" / f"{name}.gz", "wb", compresslevel=9) as f:
            f.write(raw)
        sizes[name] = len(raw)
    # 地図のページ（表示枠ごとの iframe）。Qt の qwebchannel.js は互換のものに差し替える
    web = out / "tor_app" / "web"
    shutil.copytree(ROOT / "tor_app" / "web", web, ignore=shutil.ignore_patterns(".*"))
    html = (web / "network.html").read_text(encoding="utf-8")
    html = html.replace("qrc:///qtwebchannel/qwebchannel.js", "qwebchannel.js")
    (web / "network.html").write_text(html, encoding="utf-8")
    shutil.copy2(WEBAPP / "qwebchannel.js", web / "qwebchannel.js")
    (out / ".nojekyll").write_text("")

    digest = hashlib.sha256()
    for path in sorted(out.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(out).as_posix().encode())
            digest.update(path.read_bytes())
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = ""
    version = {"version": digest.hexdigest()[:12], "commit": commit,
               "built_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "sizes": sizes}
    # 公開先（デスクトップアプリのタブのダウンロードの URL に使う）。版と大きさは tools/publish_web.py が足す
    version.update(json.loads((WEBAPP / "site.json").read_text(encoding="utf-8")))
    (out / "version.json").write_text(json.dumps(version, ensure_ascii=False, indent=1), encoding="utf-8")
    return version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--serve", action="store_true", help="作ってから http://localhost:8765/ で開けるようにする")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    version = build()
    total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"web_dist/ を作りました: 版 {version['version']}（{total / 1e6:.1f} MB）")
    if args.serve:
        import functools
        import http.server
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OUT))
        print(f"http://localhost:{args.port}/ で開けます（Ctrl+C で止める）")
        http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
