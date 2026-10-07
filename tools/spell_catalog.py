"""SGD の発現データ（SPELL、raw_data/SPELL/datasets/）の目録を作る。条件（data/conditions.csv）への対応付けの下調べ用。

研究ごとの README（引用・PMID・PCL ごとの説明・分類の札・チャンネル数）と、各 PCL の列名を 1 行 1 ファイルにまとめ、
説明・札・列名に出てくる英語の言葉から条件の候補（suggested）を付ける。候補は目安で、採用するかどうかは
data/expression_sources.csv（tools/build_expression.py が読む対応表）で決める。

使い方: .venv/bin/python tools/spell_catalog.py [出力先.tsv]（既定は Google Drive の調査フォルダ）
"""
import csv
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASETS = ROOT / "raw_data" / "SPELL" / "datasets"
# 調べ物の中間ファイルの置き場（環境変数 PATHWAYS_RESEARCH_DIR。なければ手元の scratch/）
DEFAULT_OUT = (Path(os.environ.get("PATHWAYS_RESEARCH_DIR", ROOT / "scratch")) / "条件別の発現と活性" / "spell_catalog.tsv")

# 条件のキー → 英語の言葉（正規表現。説明・札・列名を小文字にして探す）
KEYWORDS = {
    "heat": r"heat[ -]?shock|\bhs\d|\b3[7-9] ?°?c\b|\b4[0-2] ?°?c\b|temperature shift|thermal",
    "cold": r"cold|\b1[0-5] ?°?c\b|low temperature",
    "osmotic": r"osmo|nacl|sorbitol|\bkcl\b|salt stress",
    "oxidative": r"oxidative|h2o2|hydrogen peroxide|menadione|diamide|paraquat|tert-butyl|\btbhp\b|cumene",
    "reductive": r"\bdtt\b|dithiothreitol|reductive",
    "er": r"tunicamycin|unfolded protein|\bupr\b|\bdtt\b",
    "genotoxic": r"\bmms\b|methyl methanesulfonate|hydroxyurea|\bhu\b|ultraviolet|\buv\b|ionizing|gamma|dna damage|bleomycin|camptothecin|4-?nqo",
    "nitrogen_starvation": r"nitrogen (starvation|depletion|limitation|limited)|\-n\b|no nitrogen|nitrogen-free",
    "alt_nitrogen": r"proline|urea|allantoin|glutamate as|nitrogen source|ammonium|\bgaba\b",
    "amino_acid_starvation": r"amino acid starvation|amino acid limitation|3-?at\b|aminotriazole|histidine starvation|leucine starvation|sulfometuron",
    "amino_acid_repletion": r"amino acid (addition|repletion)",
    "adenine_limitation": r"adenine",
    "carbon_starvation": r"glucose (starvation|depletion|removal)|carbon starvation|no glucose|glucose-free",
    "glucose_limitation": r"glucose[- ]limit|low glucose|chemostat",
    "glucose_repletion": r"glucose (addition|pulse|repletion|re-?addition)|added glucose",
    "alt_carbon": r"galactose|raffinose|sucrose|maltose|fructose",
    "nonfermentable": r"glycerol|ethanol|acetate|lactate|respirat|non-?fermentable",
    "diauxic_shift": r"diauxic",
    "stationary": r"stationary|quiescen",
    "phosphate_limitation": r"phosphate (starvation|limitation|depletion)|low phosphate|no phosphate|low pi\b",
    "sulfur_limitation": r"sulfur (starvation|limitation)|sulfate limitation",
    "zinc_limitation": r"zinc (deficien|limitation|starvation)|low zinc",
    "iron_limitation": r"iron (deficien|limitation|starvation)|low iron|\bbps\b|bathophenanthroline",
    "copper_limitation": r"copper (deficien|limitation)",
    "metal_stress": r"cadmium|arsen|\bas\(iii\)|cobalt|nickel|copper excess|mercury|chromium",
    "hypoxia": r"anaerob|hypox|anoxi|oxygen (limitation|deprivation)",
    "alkaline": r"alkalin|high ph|ph 8",
    "ph_stress": r"\bacid(ic)? stress|low ph|ph [2-4]\b",
    "weak_acid": r"acetic acid|sorbic|benzoic|propionic|weak acid|lactic acid",
    "cell_wall": r"cell wall|calcofluor|congo red|zymolyase|caspofungin|echinocandin",
    "calcium": r"calcium|cacl2",
    "drug": r"rapamycin|caffeine|fluconazole|ketoconazole|cycloheximide|drug|inhibitor|antifungal|\bfk506\b|wortmannin",
    "mating": r"alpha[- ]factor|pheromone|mating",
    "sporulation": r"sporulat|meiosis|meiotic",
    "cell_cycle_sync": r"cell cycle|synchron|elutriation|cdc15|cdc28-",
    "pseudohyphal": r"pseudohyph|filament|invasive",
    "lipid": r"oleate|oleic|inositol|fatty acid|ergosterol",
    "mitochondrial": r"rho0|rho-0|petite|mitochondrial dysfunction",
    "unstressed": r"unstressed|untreated|log[- ]phase|mid-?log|steady[- ]state|reference",
}
_RE = {k: re.compile(v) for k, v in KEYWORDS.items()}
# 遺伝子を壊した・変えた株の目印（列名に出ていれば変異株の比較）
MUTANT = re.compile(r"Δ|delta|\bdel\b|mutant|knock-?out|deletion|\b[a-z]{3}\d{1,3}(-\d+)?\b|overexpress|\bko\b", re.I)
WILD = re.compile(r"\bwt\b|wild[- ]?type|\bby47\d\d|\bs288c|\bw303|parental", re.I)


def read_readme(path: Path) -> dict:
    """README → {"citation", "pmid", "geo", "pcls": {PCL 名: (説明, 条件数, 札, チャンネル数)}}。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    info = {"citation": "", "pmid": "", "pcls": {}}
    m = re.search(r"Citation:\s*(.*?)\n\s*\n", text, re.S)
    if m:
        info["citation"] = " ".join(m.group(1).split())
    m = re.search(r"PMID:\s*(\d+)", text)
    if m:
        info["pmid"] = m.group(1)
    for line in text.splitlines():
        p = line.split("\t")
        if len(p) >= 5 and p[0].endswith(".pcl"):
            info["pcls"][p[0].strip()] = (p[1].strip(), p[2].strip(), p[3].strip(), p[4].strip())
    return info


def pcl_columns(path: Path) -> list[str]:
    with open(path, encoding="utf-8", errors="replace") as f:
        return [c.strip() for c in f.readline().rstrip("\r\n").split("\t")[3:]]


def suggest(text: str) -> list[str]:
    low = text.lower()
    return [k for k, r in _RE.items() if r.search(low)]


def main(out: Path) -> None:
    rows = []
    for study in sorted(p for p in DATASETS.iterdir() if p.is_dir()):
        readmes = list(study.rglob("*.README"))
        info = read_readme(readmes[0]) if readmes else {"citation": "", "pmid": "", "pcls": {}}
        for pcl in sorted(study.rglob("*.pcl")):
            desc, _n, tags, channels = info["pcls"].get(pcl.name, ("", "", "", ""))
            cols = pcl_columns(pcl)
            wild = sum(bool(WILD.search(c)) for c in cols)
            mut = sum(bool(MUTANT.search(c)) and not WILD.search(c) for c in cols)
            rows.append({
                "study": study.name, "pmid": info["pmid"], "pcl": str(pcl.relative_to(DATASETS)),
                "description": desc, "tags": tags, "channels": channels, "n_columns": len(cols),
                "wild_cols": wild, "mutant_cols": mut,
                "suggested": ";".join(suggest(" ".join([desc, tags, pcl.name] + cols))),
                "columns": " || ".join(cols)[:1500], "citation": info["citation"][:300],
            })
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter="\t")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} 個の PCL → {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT)
