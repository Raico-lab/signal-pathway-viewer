"""計算上の複合体・役割が重なる遺伝子の組（data/complexes.csv）。

図の枠（遺伝子のカテゴリ）とは別に、「まとまりとしてどう働くか」を記録する（複合体の詳細の表示に使う。シミュレーションは archive/）。

- 同じ枠（slot）の遺伝子は、どれか 1 つが働けばよい（役割が重なる。例: TPK1・TPK2・TPK3、RAS1・RAS2）
- 必須の枠はすべてそろって働く（複合体。働きはいちばん弱い枠に揃う。例: TORC1 = TOR1/TOR2・KOG1・LST8）
- slot が accessory（補助）・inhibitor（抑える側。PKA の BCY1 など）の遺伝子は、まとまりの働きの計算に入れない
- 列 pmids は根拠の論文（PubMed で要旨を確かめたもの。; 区切り）。空欄の行は、まだ文献で確かめていない
- 1 つの遺伝子が複数のまとまりに入ってよい（TOR2 は TORC1 と TORC2）。その遺伝子自身の関係（TOR2 → YPK1 など）を
  どのまとまりの働きとして計算するか（主なまとまり）は、遺伝子のカテゴリ（図の枠）と同じ名前のまとまり、なければ最初に書いたまとまり
"""
import csv
from dataclasses import dataclass, field
from pathlib import Path

from .paths import resource_path

NOT_COUNTED = {"accessory", "inhibitor"}


@dataclass
class ComplexUnit:
    name: str
    slots: dict[str, list[int]] = field(default_factory=dict)   # 必須の枠 → その枠の遺伝子（どれか 1 つでよい）
    accessory: list[int] = field(default_factory=list)
    inhibitors: list[int] = field(default_factory=list)
    notes: dict[int, str] = field(default_factory=dict)
    pmids: dict[int, list[str]] = field(default_factory=dict)    # 構成要素 → 根拠の論文（PubMed ID）
    primary: list[int] = field(default_factory=list)            # このまとまりを「主なまとまり」とする遺伝子

    @property
    def counted(self) -> list[int]:
        return [g for members in self.slots.values() for g in members]

    @property
    def members(self) -> list[int]:
        return self.counted + self.accessory + self.inhibitors


def load_units(by_name: dict[str, int], categories: dict[int, str], path: Path | None = None) -> list[ComplexUnit]:
    """data/complexes.csv を読む。DB にない遺伝子は無視する。by_name は大文字の遺伝子名 → 遺伝子、categories は遺伝子 → カテゴリ名。"""
    path = path or resource_path("data", "complexes.csv")
    if not Path(path).exists():
        return []
    units: dict[str, ComplexUnit] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name, gene, slot = (row.get(k, "").strip() for k in ("complex", "gene", "slot"))
            pid = by_name.get(gene.upper())
            if not name or pid is None:
                continue
            unit = units.setdefault(name, ComplexUnit(name))
            if slot == "accessory":
                unit.accessory.append(pid)
            elif slot == "inhibitor":
                unit.inhibitors.append(pid)
            else:
                unit.slots.setdefault(slot or gene, []).append(pid)
            if row.get("note"):
                unit.notes[pid] = row["note"].strip()
            if row.get("pmids"):
                unit.pmids[pid] = [x.strip() for x in row["pmids"].split(";") if x.strip()]
    result = [u for u in units.values() if u.slots]
    # 主なまとまり: 遺伝子のカテゴリと同じ名前のまとまり、なければ最初に書いたまとまり
    chosen: dict[int, ComplexUnit] = {}
    for unit in result:
        for g in unit.counted:
            if g not in chosen or (categories.get(g) == unit.name and categories.get(g) != chosen[g].name):
                chosen[g] = unit
    for g, unit in chosen.items():
        unit.primary.append(g)
    return result
