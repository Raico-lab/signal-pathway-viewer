"""多くの破壊株・刺激で一緒に変わる遺伝子（一般的なストレス応答）の一覧（data/common_response_genes.csv）。

tools/make_common_response.py が破壊株データ（Deleteome）の第 1 主成分から作る。制御遺伝子探索で、転写因子を見分ける手がかりから外す。
"""
import csv
from pathlib import Path

from .paths import resource_path

FILE = ("data", "common_response_genes.csv")


def load(name_to_id: dict[str, int], path: Path | None = None) -> set[int]:
    """一覧の遺伝子の番号。name_to_id は遺伝子名・Systematic 名（大文字）→ 番号。ファイルがなければ空。"""
    path = path or resource_path(*FILE)
    if not path.exists():
        return set()
    ids = set()
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(line for line in f if not line.startswith("#")):
            pid = name_to_id.get(row["gene"].strip().upper()) or name_to_id.get(row["systematic_name"].strip().upper())
            if pid is not None:
                ids.add(pid)
    return ids
