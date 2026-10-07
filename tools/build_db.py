"""CSV から同梱用の DB (data/tor_pathway.db) を作り直す。

    python tools/build_db.py [proteins.csv] [interactions.csv]

引数を省略すると sample_data/ の CSV を使う。アプリの「CSV エクスポート」で書き出したファイルもそのまま使える。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tor_app.db import Database  # noqa: E402
from tor_app.db import csv_io  # noqa: E402


def main() -> None:
    proteins = sys.argv[1] if len(sys.argv) > 1 else ROOT / "sample_data" / "proteins.csv"
    interactions = sys.argv[2] if len(sys.argv) > 2 else ROOT / "sample_data" / "interactions.csv"
    out = ROOT / "data" / "tor_pathway.db"
    out.parent.mkdir(exist_ok=True)
    out.unlink(missing_ok=True)
    db = Database(str(out))
    print("遺伝子:", csv_io.import_proteins(db, csv_io.read_csv(str(proteins)), False).summary())
    print("制御関係:", csv_io.import_interactions(db, csv_io.read_csv(str(interactions)), False).summary())
    db.engine.dispose()
    print(f"作成しました: {out}")


if __name__ == "__main__":
    main()
