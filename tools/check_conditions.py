"""文献の CSV の condition 列が、地図の条件（data/conditions.csv）に当てはまるかを確かめる。

    .venv/bin/python tools/check_conditions.py docs/curation/drafts/round17_part1.csv [...]
    .venv/bin/python tools/check_conditions.py --list      # 書ける言葉の一覧（群ごと）

行ごとに、当てはまった条件（細胞周期の時期も含む）を出し、当てはまらない行を最後にまとめる。
当てはめ方は取り込み（tools/import_sources.py）と同じ（tor_app/conditions.py の classify。"/" では分けない）。
"""
import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tor_app import conditions  # noqa: E402


def show_list() -> None:
    group = None
    for c in conditions.load():
        if not c.match:
            continue
        if c.group != group:
            group = c.group
            print(f"[{group}]")
        print(f"  {c.label}: {'・'.join(c.match)}")


def check(paths: list[Path]) -> int:
    labels = conditions.labels()
    missing = []
    for path in paths:
        print(f"== {path}")
        with open(path, encoding="utf-8") as f:
            for n, row in enumerate(csv.DictReader(f), 2):
                text = (row.get("condition") or "").strip()
                if not text:
                    continue
                keys = conditions.classify(text, split_slash=False)
                pair = f"{row.get('source_protein', '')} → {row.get('target_protein', '')}"
                print(f"  {n:>4} {pair}: {text}  →  [{'・'.join(labels[k] for k in keys) or '-'}]")
                if not keys:
                    missing.append((path.name, n, pair, text))
    if missing:
        print(f"\n当てはまらない行 {len(missing)} 件（地図の条件にならず、根拠欄に文字で残るだけ）:")
        for name, n, pair, text in missing:
            print(f"  {name}:{n} {pair}: {text}")
    return 1 if missing else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="*", type=Path)
    ap.add_argument("--list", action="store_true", help="書ける言葉の一覧を出す")
    args = ap.parse_args()
    if args.list or not args.csv:
        show_list()
        return
    sys.exit(check(args.csv))


if __name__ == "__main__":
    main()
