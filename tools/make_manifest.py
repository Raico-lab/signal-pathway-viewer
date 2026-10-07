"""サーバーに置く manifest.json（配信する DB の版）を作る。

使い方: .venv/bin/python tools/make_manifest.py 2026-10-15 "GLN3 の標的を追加" [--db data/tor_pathway.db] [--out manifest.json]
できた manifest.json と DB（同じ名前 tor_pathway.db）をサーバーの同じ場所に置く。方針は docs/EXTERNAL_DB_PLAN.md。
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tor_app.server import SCHEMA_VERSION  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("version", help="利用者に見せる版名（例: 2026-10-15）")
    ap.add_argument("notes", nargs="?", default="", help="変更点の一言")
    ap.add_argument("--db", default=str(ROOT / "data" / "tor_pathway.db"))
    ap.add_argument("--out", default="manifest.json")
    args = ap.parse_args()
    db = Path(args.db)
    manifest = {"data_version": args.version, "schema_version": SCHEMA_VERSION,
                "sha256": hashlib.sha256(db.read_bytes()).hexdigest(), "url": db.name, "notes": args.notes}
    Path(args.out).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{args.out}: {manifest['data_version']} ({db.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
