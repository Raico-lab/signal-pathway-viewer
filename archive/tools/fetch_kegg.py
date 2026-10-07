"""KEGG の出芽酵母（sce）の経路図データ（KGML）を、学術目的で無料の REST API から取得して raw_data/KEGG/ に保存する。

    python tools/fetch_kegg.py

促進・抑制の向きが載っている制御系の経路図（番号が sce03・sce04 で始まるもの）だけを取得する。
代謝経路の図には向きがないので取得しない。取得日は raw_data/KEGG/FETCHED.txt に記録する。
KEGG は随時更新されるので、取り込み直すときはこのスクリプトで取得し直す。
"""
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "raw_data" / "KEGG"
API = "https://rest.kegg.jp"
WAIT = 0.4   # KEGG に負担をかけないよう、1 件ごとに待つ（秒）


def get(path: str) -> bytes:
    with urllib.request.urlopen(f"{API}/{path}", timeout=60) as response:
        return response.read()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    listing = get("list/pathway/sce").decode("utf-8")
    (OUT / "pathway_list.txt").write_text(listing, encoding="utf-8")
    ids = [line.split("\t")[0] for line in listing.splitlines() if line.startswith(("sce03", "sce04"))]
    failed = []
    for i, pathway in enumerate(ids, 1):
        try:
            (OUT / f"{pathway}.xml").write_bytes(get(f"get/{pathway}/kgml"))
        except OSError as e:
            failed.append(f"{pathway}: {e}")
        print(f"\r{i}/{len(ids)} {pathway}", end="", flush=True)
        time.sleep(WAIT)
    print()
    (OUT / "FETCHED.txt").write_text(
        f"KEGG REST API ({API}) から取得: {date.today().isoformat()}\n"
        f"経路図 {len(ids) - len(failed)} 枚（sce03・sce04）\n", encoding="utf-8")
    if failed:
        sys.exit("取得できなかった経路図:\n" + "\n".join(failed))
    print(f"{OUT} に保存しました")


if __name__ == "__main__":
    main()
