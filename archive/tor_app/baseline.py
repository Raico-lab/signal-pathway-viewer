"""シミュレーションの基準の状態（data/baseline.csv）。富栄養（YPD・30℃）で、栄養・ストレスのセンサーなど入力にあたる遺伝子の活性。

マップのシミュレーションでは、計算範囲に入る遺伝子のうち一覧にあるものをこの値に固定し、その上に利用者の固定を重ねる。
一覧にない遺伝子は、これまでどおり上流から計算する（範囲の外の上流は既定の活性）。
既定の活性（活性化の入力があれば 0）のままだと、TORC1 の上流（GTR1・PIB2）や PKA の上流（GPR1・GPA2・CDC25）が 0 になり、
多くの経路が最初から止まって、遺伝子を壊しても何も変わらなかった（2026-10-02 の評価: 破壊株データで予測できたのは 1.4%）。
"""
import csv
from pathlib import Path

from .paths import resource_path

PRESETS = {"rich": "富栄養（YPD・30℃）", "none": "なし（既定の活性）"}
DEFAULT_PRESET = "rich"


def load(by_name: dict[str, int], path: Path | None = None) -> dict[int, float]:
    path = path or resource_path("data", "baseline.csv")
    if not Path(path).exists():
        return {}
    out = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pid = by_name.get(row["gene"].strip().upper())
            if pid is not None:
                out[pid] = float(row["value"])
    return out


def notes(path: Path | None = None) -> dict[str, str]:
    path = path or resource_path("data", "baseline.csv")
    if not Path(path).exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return {row["gene"].strip().upper(): row["note"] for row in csv.DictReader(f)}
