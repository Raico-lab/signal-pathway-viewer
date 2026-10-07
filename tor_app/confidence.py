"""関係の確度。根拠欄（tools/import_sources.py が書く）から決まった規則で判定する。

- A 文献で確認: PubMed で PMID と要旨を確かめた関係（sample_data/literature/）
- B 個別実験: BioGRID の個別実験（低スループット）の記録がある、YEASTRACT+ の DNA 結合と発現の両方の報告がある、
  または SGD の GO 注釈・GO-CAM（実験の証拠つき）がある
- C 網羅的な実験だけ: BioGRID の網羅的な実験（プロテインチップなど）の記録だけ
- D 推定だけ: 破壊株データからの推定だけ（KEGG は 2026-10-03 から使わない）
- U 根拠の書式なし: 手で加えた関係など（出典の書式で書かれていない）

複数の出典がある関係は、いちばん確かなものを取る。マップの表示と順位には使わず、関係の説明に使う。
"""

LEVELS = {
    "A": "文献で確認",
    "B": "個別実験",
    "C": "網羅的な実験だけ",
    "D": "推定だけ",
    "U": "根拠の書式なし",
}
ORDER = "ABCDU"

# 計算に使う関係の選び方（画面の選択肢）: 値 → (表示名, 使う確度)
CHOICES = {
    "all": ("すべての関係", set(ORDER)),
    "no_d": ("推定だけの関係を除く（A〜C）", set("ABC")),
    "ab": ("個別実験・文献で確認のみ（A・B）", set("AB")),
    "a": ("文献で確認のみ（A）", {"A"}),
}
DEFAULT_CHOICE = "all"


def level(evidence: str | None) -> str:
    ev = evidence or ""
    if ev.startswith("文献で確認"):
        return "A"
    found = []
    if "BioGRID" in ev:
        found.append("C" if "網羅的な実験だけ" in ev else "B")
    if "YEASTRACT" in ev or "SGD GO 注釈" in ev or "GO-CAM" in ev:
        found.append("B")
    if "破壊株データ" in ev:
        found.append("D")
    return min(found, key=ORDER.index) if found else "U"


def label(code: str) -> str:
    return f"{code} {LEVELS[code]}"


def allowed(choice: str) -> set[str]:
    return CHOICES.get(choice, CHOICES[DEFAULT_CHOICE])[1]


# 作用の向きが不明な理由（計算ではどれも「促進でも抑制でもありうる」として扱う。表示で区別する）
UNKNOWN_REASONS = {
    "flow": "信号の流れではない",
    "conflict": "出典で食い違う",
    "both": "促進と抑制の両方の報告がある",
    "none": "向きの記録がない",
}


def unknown_reason(evidence: str | None) -> str:
    """作用が不明な関係の理由のキー（UNKNOWN_REASONS）。"""
    ev = evidence or ""
    if "信号の流れではない" in ev:
        return "flow"
    if "出典で作用が食い違う" in ev or "出典で食い違う" in ev:
        return "conflict"
    if "activator と inhibitor の両方の報告あり" in ev:
        return "both"
    return "none"
