"""文献の関係で、相手を複合体の名前で書いたもの（論文が「TORC1」「カルシニューリン」のようにしか書かないとき）。

data/complex_groups.csv の名前（label）を、文献の CSV の source_protein・target_protein に書ける。
取り込み（tools/import_sources.py）では構成遺伝子ごとの関係に広げ、根拠欄に「複合体 TORC1（上流）として」と書く。
地図（graph_data.py）は、この印のある線の起点（終点）が同じ枠に入っていれば、枠から 1 本の線として描く。
"""
import csv
import re
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "complex_groups.csv"
MARK = re.compile(r"複合体 (.+?)（(上流|下流)）として")


@lru_cache(maxsize=1)
def groups() -> dict[str, dict]:
    """label（大文字）→ {label, genes, aliases}。"""
    if not PATH.exists():
        return {}
    with open(PATH, encoding="utf-8") as f:
        return {r["label"].strip().upper(): {"label": r["label"].strip(),
                                             "genes": [g.strip() for g in r["genes"].split(";") if g.strip()],
                                             "aliases": [a.strip() for a in r["aliases"].split("|") if a.strip()],
                                             "portal": [c.strip() for c in r.get("complex_portal", "").split(";") if c.strip()]}
                for r in csv.DictReader(f) if r.get("label")}


def expand(name: str, name_to_gene: dict[str, str]) -> tuple[list[str], str]:
    """文献の遺伝子の欄 → (遺伝子のリスト, 複合体の label。遺伝子なら空)。特定できなければ ([], "")。"""
    key = name.strip().upper()
    if key in name_to_gene:
        return [name_to_gene[key]], ""
    g = groups().get(key)
    if not g:
        return [], ""
    genes = [name_to_gene[x.upper()] for x in g["genes"] if x.upper() in name_to_gene]
    return list(dict.fromkeys(genes)), g["label"]


def mark(label: str, side: str) -> str:
    return f"複合体 {label}（{side}）として（論文は複合体の名前で書いている）"


def parse(evidence: str | None) -> list[tuple[str, str]]:
    """根拠欄の印 → [(label, 上流|下流)]。"""
    return MARK.findall(evidence or "")


def labels_for_portal(ref: str) -> list[str]:
    """Complex Portal の番号（CPX-…）→ その複合体に当たる label（地図の枠の説明欄で、複合体として書かれた関係を出すのに使う）。"""
    return [g["label"] for g in groups().values() if ref and ref in g["portal"]]
