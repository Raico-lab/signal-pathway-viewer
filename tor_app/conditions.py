"""関係が働く条件の一覧（data/conditions.csv）。YEASTRACT+ の環境条件（群・小群）に合わせて組んである。

関係ごとに、条件のキーを ";" でつないで DB の interactions.conditions に入れる（tools/import_sources.py）。
条件のない（記録のない）関係は空。画面では、線の説明に条件を出し、左の「条件」タブで選んだ条件の関係と遺伝子を
地図で目立たせる。細胞周期の時期（G1・S・G2・分裂期など）も条件の 1 つの群（細胞周期）として同じように扱う。

data/conditions.csv の列（行の順が画面の並び）:
- key: 条件のキー（DB に入れる値）
- group: 群（画面で見出しにする。YEASTRACT+ の群の訳）
- label: 画面に出す名前（YEASTRACT+ の小群の訳）
- yeastract: YEASTRACT+ の「群 / 小群」（tools/fetch_yeastract_conditions.py で取った表をこの条件にする）
  空なら YEASTRACT+ の表は使わない（unstressed は、対照の小群がほぼ全部の転写制御にあって絞れないので空にしてある）
- description: Help に出す説明
- match: 文献の CSV の condition 列（日本語の自由な書き方）を、この条件に当てはめるための言葉（";" 区切り。
  英数字は語として当てる）。label も当てはめに使う（match が空なら使わない）
"""
import csv
import re
from dataclasses import dataclass

from .paths import resource_path

NONE_KEY = "_none"   # 条件の記録がない関係（左の「条件」タブの選択肢）
NONE_LABEL = "条件の記録なし"


@dataclass(frozen=True)
class Condition:
    key: str
    group: str
    label: str
    yeastract: str
    match: tuple[str, ...]
    description: str = ""


_CACHE: list[Condition] | None = None


def load() -> list[Condition]:
    global _CACHE
    if _CACHE is None:
        with open(resource_path("data", "conditions.csv"), encoding="utf-8") as f:
            _CACHE = [Condition(r["key"].strip(), r["group"].strip(), r["label"].strip(), (r.get("yeastract") or "").strip(),
                                _match(r), (r.get("description") or "").strip())
                      for r in csv.DictReader(f) if r.get("key", "").strip()]
    return _CACHE


def _match(row: dict) -> tuple[str, ...]:
    """当てはめる言葉: match 列と、画面の名前（label）。match が空の条件（ラパマイシンなど、関係には付けないもの）は空のまま。"""
    words = [m.strip() for m in (row.get("match") or "").split(";") if m.strip()]
    label = row["label"].strip()
    return tuple(words + [label] if words and label not in words else words)


def labels() -> dict[str, str]:
    """キー → 画面の名前（「記録なし」のキーも含む）。"""
    return {**{c.key: c.label for c in load()}, NONE_KEY: NONE_LABEL}


def classify(text: str, split_slash: bool = True) -> list[str]:
    """出典の条件の書き方（例: "Stress / Heat"、"熱・フェロモン"）を条件のキーの並びにする（当てはまらなければ空）。
    小群（"/" より後）の言葉を先に見て、当てはまらなければ全体で見る（"Stress / Heat" → 熱、"Stress" → その他のストレス）。
    いくつも当てはまれば全部（"熱・フェロモン" → 熱ショック・接合）。通常時（"ストレスのない通常時"）に当てはまれば、
    ほかは見ない（「通常時。高浸透圧では…」のような補足を拾わないため）。"""
    text = (text or "").strip()
    if not text:
        return []
    parts = [p for p in (text.split("/", 1)[1] if split_slash and "/" in text else "", text) if p]
    # 長い言葉から当て、当たった所は短い言葉では当てない（「分裂期の終わり」の中の「分裂期」を拾わない）。
    # 同じ言葉を持つ条件にはまとめて当てる（「G2/M」は G2 期と分裂期の両方の言葉）
    words: dict[str, list[str]] = {}
    for c in load():
        for m in c.match:
            words.setdefault(m.lower(), []).append(c.key)
    order = [c.key for c in load()]
    for part in parts:
        low = part.lower()
        hits: set[str] = set()
        for word in sorted(words, key=len, reverse=True):
            if _hit(word, low):
                hits.update(words[word])
                low = _blank(word, low)
        found = sorted(hits, key=order.index)
        if "unstressed" in found:
            return ["unstressed"]
        if found:
            return found
    return []


def classify_yeastract(group: str, subgroup: str) -> list[str]:
    """YEASTRACT+ の環境条件（群・小群）を条件のキーにする（data/conditions.csv の yeastract 列と同じものだけ）。"""
    name = f"{group} / {subgroup}".lower()
    return [c.key for c in load() if c.yeastract and c.yeastract.lower() == name]


def _hit(word: str, text: str) -> bool:
    """英数字の言葉は語として当てる（"ph" が "phosphate"・"log-phase" に当たらないように）。日本語はそのまま含むかで見る。"""
    word = word.lower()
    if word.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text) is not None
    return word in text


def _blank(word: str, text: str) -> str:
    """text の中の word（_hit と同じ当て方）を空白にする。"""
    if word.isascii():
        return re.sub(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", " ", text)
    return text.replace(word, " ")


def split(value: str | None) -> list[str]:
    """DB の conditions 列の値（"heat;osmotic"）をキーの並びにする。"""
    return [k for k in (value or "").split(";") if k]


def join(keys) -> str:
    order = [c.key for c in load()]
    return ";".join(sorted(set(keys), key=lambda k: order.index(k) if k in order else len(order)))
