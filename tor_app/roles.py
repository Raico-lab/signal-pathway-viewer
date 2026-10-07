"""SGD の機能説明から、遺伝子のおおまかな役割を推定する（色分け用）。

機能説明の最初の一文（「;」まで）に主な役割が書かれていることが多いので、そこをキーワードで判定する。
上から順に当てはめ、最初に当たった役割にする。あくまで目安の自動分類。
"""
import re

# (キー, 表示名, 色, 判定パターン)
ROLES = [
    ("unknown", "機能不明", "#cfd8dc",
     r"unknown function|uncharacterized|dubious open reading frame|putative protein of unknown"),
    ("transcription", "転写制御因子", "#64b5f6",
     r"transcription(al)? (factor|activator|repressor|regulator|coactivator|corepressor)|"
     r"regulates transcription|dna-binding|zinc cluster|zinc finger|homeodomain|bzip|basic helix-loop-helix|"
     r"motif binding protein|element-binding protein|\)-binding protein"),
    ("phosphatase", "ホスファターゼ", "#ba68c8", r"phosphatase"),
    ("kinase", "キナーゼ", "#81c784", r"kinase"),
    ("gtpase", "GTPase・関連因子", "#4db6ac",
     r"gtpase|gtp-binding|guanine nucleotide exchange|gtpase-activating|\bgef\b|\brab\b"),
    ("ubiquitin", "ユビキチン・分解", "#f06292", r"ubiquitin|proteasom|sumo|e3 ligase|deubiquitinat"),
    ("transport", "輸送体・チャネル", "#ffb74d",
     r"permease|transporter|channel|symporter|antiporter|\bpump\b|importin|exportin|carrier"),
    ("translation", "リボソーム・翻訳", "#a1887f",
     r"ribosom|translation (initiation|elongation|termination)|trna synthetase|aminoacyl-trna"),
    ("enzyme", "代謝などの酵素", "#fff176",
     r"dehydrogenase|synthase|synthetase|reductase|transferase|isomerase|hydrolase|oxidase|lyase|"
     r"mutase|decarboxylase|esterase|peptidase|protease|nuclease|helicase|ligase|ase\b"),
    ("complex", "複合体の構成要素・足場", "#90a4ae", r"subunit|component|scaffold|adaptor|adapter"),
    ("other", "その他", "#e0e0e0", r""),
]
ROLE_INFO = {key: (label, color) for key, label, color, _ in ROLES}
# 最初の一文では分からなくても、説明全体にこう書かれていれば転写制御因子とみなす
_TF_ANYWHERE = re.compile(r"transcription(al)? (factor|activator|repressor)|myb-like hth transcrip|"
                          r"binds (\w+ ){1,4}motifs?", re.IGNORECASE)
_PATTERNS = [(key, re.compile(pattern, re.IGNORECASE)) for key, _, _, pattern in ROLES if pattern]


def classify(description: str, qualifier: str = "") -> str:
    """役割のキーを返す。qualifier は SGD の Verified / Uncharacterized / Dubious。"""
    if qualifier in ("Dubious", "Uncharacterized"):
        return "unknown"
    first = (description or "").split(";")[0]
    if not first.strip():
        return "unknown"
    role = next((key for key, pattern in _PATTERNS if pattern.search(first)), "other")
    if role in ("translation", "complex", "other") and _TF_ANYWHERE.search(description):
        return "transcription"
    return role


# 階層の色: 上流側（第 1 階層）の青から下流側の赤紫へ。最大階層数に合わせて等間隔に割り当てる
_LEVEL_STOPS = ["#90caf9", "#80deea", "#a5d6a7", "#e6ee9c", "#ffe082", "#ffcc80", "#ffab91", "#f48fb1", "#ce93d8"]
NO_LEVEL_COLOR = "#eeeeee"


def _mix(a: str, b: str, t: float) -> str:
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def level_color(level: int, max_level: int) -> str:
    """level は 0 始まり（第 1 階層 = 0）。max_level は最も下流の階層。"""
    if max_level <= 0:
        return _LEVEL_STOPS[0]
    pos = level / max_level * (len(_LEVEL_STOPS) - 1)
    i = min(int(pos), len(_LEVEL_STOPS) - 2)
    return _mix(_LEVEL_STOPS[i], _LEVEL_STOPS[i + 1], pos - i)


def level_label(level: int, max_level: int) -> str:
    if level == 0:
        return "第 1 階層・起点"
    if level == max_level:
        return f"第 {level + 1} 階層・最下流"
    return f"第 {level + 1} 階層"
