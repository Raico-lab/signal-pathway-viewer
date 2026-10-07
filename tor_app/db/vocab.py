"""制御の種類・カテゴリの既定値（表示名・色・作用の向き）。"""

# interaction_type -> (日本語名, 既定の作用, 線の色, 線上の記号)
INTERACTION_TYPES = {
    # 仕組み（修飾など）は分からず、作用（促進・抑制）だけ分かっている関係
    "activation": ("機構不明（促進）", "activate", "#455a64", "—"),
    "inhibition": ("機構不明（抑制）", "inhibit", "#455a64", "—"),
    "phosphorylation": ("リン酸化", "activate", "#ef6c00", "P"),
    "dephosphorylation": ("脱リン酸化", "activate", "#6a1b9a", "-P"),
    "transcription": ("転写制御", "activate", "#1565c0", "Tx"),
    "localization": ("局在制御", "activate", "#00838f", "Loc"),
    # 以下は BioGRID の酵素活性（Biochemical Activity）の修飾の種類。作用の向きは既定で不明
    "ubiquitination": ("ユビキチン化", "none", "#d81b60", "Ub"),
    "deubiquitination": ("脱ユビキチン化", "none", "#f48fb1", "-Ub"),
    "acetylation": ("アセチル化", "none", "#558b2f", "Ac"),
    "deacetylation": ("脱アセチル化", "none", "#9ccc65", "-Ac"),
    "methylation": ("メチル化", "none", "#5d4037", "Me"),
    "demethylation": ("脱メチル化", "none", "#a1887f", "-Me"),
    "sumoylation": ("SUMO 化", "none", "#3949ab", "SU"),
    "desumoylation": ("脱 SUMO 化", "none", "#9fa8da", "-SU"),
    "neddylation": ("NEDD 化（Rub1）", "none", "#00897b", "N8"),
    "deneddylation": ("脱 NEDD 化", "none", "#80cbc4", "-N8"),
    "proteolysis": ("タンパク質分解（プロセシング）", "none", "#b71c1c", "Cut"),
    "prenylation": ("プレニル化", "none", "#f9a825", "Pr"),
    "glycosylation": ("糖鎖付加", "none", "#827717", "Gly"),
    "enzymatic": ("酵素活性・修飾なし", "none", "#8d6e63", "E"),
    "binding": ("結合", "none", "#757575", "B"),
    "other": ("その他", "none", "#9e9e9e", ""),
}

EFFECTS = {
    "activate": "活性化方向（→）",
    "inhibit": "抑制方向（⊣）",
    "none": "作用不明",
}

# CSV の effect 列で受け付ける表記
EFFECT_ALIASES = {
    "activate": "activate", "activation": "activate", "+": "activate", "活性化": "activate", "正": "activate",
    "inhibit": "inhibit", "inhibition": "inhibit", "-": "inhibit", "抑制": "inhibit", "負": "inhibit",
    "none": "none", "なし": "none", "0": "none",
}

# パラログ（役割が重なりうる遺伝子の組）のカテゴリの出典と、地図の枠の色（tools/build_paralogs.py・import_sources.py）
PARALOG_SOURCE = "パラログ"
PARALOG_COLOR = "#78909c"

KNOWN_COMPLEXES = {"TORC1", "TORC2", "EGO", "SEACIT", "SEACAT", "PP2A", "GATOR"}

CATEGORY_PALETTE = [
    "#5c6bc0", "#26a69a", "#ffa726", "#ab47bc", "#66bb6a",
    "#ef5350", "#29b6f6", "#8d6e63", "#d4e157", "#ec407a",
]


def type_label(interaction_type: str) -> str:
    info = INTERACTION_TYPES.get(interaction_type)
    return info[0] if info else interaction_type


def default_effect(interaction_type: str) -> str:
    info = INTERACTION_TYPES.get(interaction_type)
    return info[1] if info else "none"


def resolve_effect(interaction_type: str, effect: str | None) -> str:
    return effect if effect in EFFECTS else default_effect(interaction_type)


def guess_is_complex(category: str) -> bool:
    upper = category.upper()
    return upper in KNOWN_COMPLEXES or "COMPLEX" in upper or "複合体" in category


def guess_threshold(rule: str) -> float:
    """閾値の数値が未指定のとき、ルールのラベルから仮の値を決める（後から自由に調整する前提）。"""
    text = rule.lower()
    if "high" in text or "高" in rule or "重度" in rule or "strong" in text:
        return 0.7
    if "low" in text or "低" in rule or "軽度" in rule or "weak" in text:
        return 0.2
    return 0.5


# 根拠欄にこの文字列がある関係は、信号の流れではない（逆向きのリン酸の受け渡しなど）。上流・下流をたどるときに使わない
NOT_SIGNAL_FLOW = "信号の流れではない"

# 根拠欄にこの文字列がある関係は、下流から上流へのフィードバック（文献で確かめたもの）。たどるときは使うが、
# 遺伝子の階層（上下の順序）を決める計算には使わない（PKA ⊣ RAS2 のような関係で上下が逆転しないようにする）
FEEDBACK = "フィードバック（下流から上流への作用）"


# 文献から拾った関係の性質（DB の interactions の列名と、画面に出す名前）。docs/curation/literature_fields.md
DETAILS = [("partner", "組む相手"), ("site", "修飾の部位"), ("evidence_kind", "根拠の実験"), ("necessity", "必要・十分"),
           ("redundant_with", "同じ働きの上流"), ("strength", "効く強さ"), ("layer", "変えるもの"), ("timing", "時間")]
# 1 つの値であるべき列（文献どうしで食い違えば両方を残して ⚠）
SINGLE_DETAILS = ("necessity", "strength")
# 決まった言葉の訳（それ以外の値はそのまま出す）
DETAIL_WORDS = {
    "in_vivo": "生体内", "in_vitro": "試験管内", "genetic": "遺伝学", "structural": "構造",
    "necessary": "必要（欠損で失われる）", "sufficient": "十分（上流だけで起こる）", "both": "必要かつ十分",
    "partial": "一部（欠損で一部だけ減る）",
    "strong": "強い", "moderate": "中くらい", "weak": "弱い",
    "expression": "発現", "activity": "活性", "stability": "分解", "localization": "局在",
    "transient": "一過性", "sustained": "持続", "adaptive": "適応すると戻る",
}


def detail_text(value: str) -> str:
    """性質の値を画面に出す形にする（";"・「・」で区切った決まった言葉を訳して「・」でつなぐ。例: "transient・5 分以内"）。"""
    words = [w.strip() for part in (value or "").split(";") for w in part.split("・")]
    return "・".join(DETAIL_WORDS.get(w, w) for w in words if w)
