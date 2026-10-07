"""以前 tools/import_sources.py で KEGG の経路図（KGML）を取り込んでいたコード（2026-10-03 に外した）。

KEGG は自由に使えるデータではないので、情報源として使わない。記録として残す（このままでは動かない）。
"""

# KEGG のまとまり（group）の箱から出る矢印を、箱の中のどの遺伝子に付けるか。
# 修飾（リン酸化など）の矢印で BioGRID に酵素の記録がなければ、機能説明の最初の句にこの語がある遺伝子だけを作用する側とする
KEGG_MODIFICATIONS = {
    "phosphorylation": ("kinase",),
    "dephosphorylation": ("phosphatase",),
    "ubiquitination": ("ubiquitin ligase", "ubiquitin-protein ligase", "cullin", "f-box", "ring finger", "e3"),
    "glycosylation": ("transferase",),
    "methylation": ("methyltransferase",),
}


def _group_actors(members: list[str], targets: list[str], subtypes: set[str], enzymes: set[tuple[str, str]],
                  descriptions: dict[str, str], enzyme_kinds: dict[str, set[str]]) -> tuple[list[str], str]:
    """まとまりの箱から出る矢印で、実際に作用する遺伝子と、そう決めた理由。

    1. BioGRID に、箱の中の遺伝子が相手の酵素だという記録があれば、その遺伝子だけ
    2. 修飾（リン酸化など）の矢印なら、BioGRID でその修飾の酵素として（ほかの基質で）記録のある遺伝子だけ
    3. それもなければ、その修飾の酵素だと機能説明の最初の句にある遺伝子だけ（いなければ付けない）
    4. 修飾のない促進・抑制の矢印は、複合体全体の働きとみなして全員に付ける
    """
    recorded = [a for a in members if any((a, b) in enzymes for b in targets)]
    if recorded:
        return recorded, "BioGRID に酵素の記録"
    mod = next((m for m in KEGG_MODIFICATIONS if m in subtypes), None)
    if mod:
        known = [a for a in members if mod in enzyme_kinds.get(a, set()) and a not in targets]
        if known:
            return known, f"BioGRID で{type_label(mod)}の酵素として記録"
        words = KEGG_MODIFICATIONS[mod]
        named = [a for a in members if any(w in descriptions.get(a, "").split(";")[0].lower() for w in words)]
        return named, f"機能説明が{'・'.join(words[:2])}"
    return members, "複合体全体"


def load_kegg(folder: Path, name_to_gene: dict[str, str], enzymes: set[tuple[str, str]] = frozenset(),
              descriptions: dict[str, str] | None = None,
              enzyme_kinds: dict[str, set[str]] | None = None) -> tuple[dict, list, list]:
    """KEGG の KGML から、促進・抑制の向きが分かる遺伝子の組を集める。

    経路図の 1 つの箱に同じ働きの遺伝子が並んでいるとき（TPK1/TPK2/TPK3 など）は、すべてに同じ向きを付ける。
    複合体などのまとまり（group）から出る矢印は、_group_actors で決めた作用する遺伝子にだけ付ける
    （以前は全員に付けていたため、TORC1 と一緒に描かれた脱リン酸化酵素 PPH21 が「TAP42 をリン酸化する」などとなっていた）。
    返り値: {(上流, 下流): {"effect", "type", "pathways", "notes"}}、経路図によって向きが食い違う組の一覧、
            作用する遺伝子が決められず取り込まなかった矢印の一覧
    """
    descriptions = descriptions or {}
    enzyme_kinds = enzyme_kinds or {}
    found: dict[tuple[str, str], dict] = {}
    skipped: list[tuple[str, str, str]] = []
    for path in sorted(folder.glob("sce*.xml")):
        root = ET.parse(path).getroot()
        genes, group_ids = {}, set()
        for e in root.findall("entry"):
            if e.get("type") == "gene":
                genes[e.get("id")] = [name_to_gene[n.split(":")[1].upper()] for n in e.get("name").split()
                                      if n.startswith("sce:") and n.split(":")[1].upper() in name_to_gene]
        for e in root.findall("entry"):
            if e.get("type") == "group":
                genes[e.get("id")] = [g for c in e.findall("component") for g in genes.get(c.get("id"), [])]
                group_ids.add(e.get("id"))
        for r in root.findall("relation"):
            if r.get("type") not in ("PPrel", "GErel"):
                continue
            subtypes = {st.get("name") for st in r.findall("subtype")}
            if subtypes & {"activation", "expression"}:
                effect = "activate"
            elif subtypes & {"inhibition", "repression"}:
                effect = "inhibit"
            else:
                continue
            if r.get("type") == "GErel":
                kind = "transcription"
            elif "phosphorylation" in subtypes:
                kind = "phosphorylation"
            elif "dephosphorylation" in subtypes:
                kind = "dephosphorylation"
            elif "ubiquitination" in subtypes:
                kind = "ubiquitination"
            else:
                kind = "activation" if effect == "activate" else "inhibition"
            sources, targets = genes.get(r.get("entry1"), []), genes.get(r.get("entry2"), [])
            note = ""
            if r.get("entry1") in group_ids and len(sources) > 1:
                actors, reason = _group_actors(sources, targets, subtypes, enzymes, descriptions, enzyme_kinds)
                box = "+".join(sources)
                if not actors:
                    skipped.append((box, "/".join(targets), f"{path.stem} {'・'.join(sorted(subtypes))}"))
                    continue
                if len(actors) < len(sources):
                    note = f"KEGG の枠 {box} のうち {'・'.join(actors)}（{reason}）"
                sources = actors
            for a in sources:
                for b in targets:
                    if a == b:
                        continue
                    item = found.setdefault((a, b), {"effects": set(), "type": kind, "pathways": set(), "notes": set()})
                    item["effects"].add(effect)
                    item["pathways"].add(path.stem)
                    if note:
                        item["notes"].add(note)
    conflicts = sorted(pair for pair, item in found.items() if len(item["effects"]) > 1)
    result = {pair: {"effect": next(iter(item["effects"])), "type": item["type"],
                     "pathways": ", ".join(sorted(item["pathways"])), "notes": " / ".join(sorted(item["notes"]))}
              for pair, item in found.items() if len(item["effects"]) == 1}
    conflict_info = [(a, b, ", ".join(sorted(found[(a, b)]["pathways"]))) for a, b in conflicts]
    return result, conflict_info, skipped




def merge_kegg_yeastract(kegg_rows: pd.DataFrame, tx_rows: pd.DataFrame,
                         report: list) -> tuple[pd.DataFrame, pd.DataFrame]:
    """KEGG と YEASTRACT+ の両方にある転写制御を 1 本にまとめ、根拠欄に両方を残す。
    作用は、一致すればそれ、片方が作用不明なら分かっている方、食い違えば作用不明にして印を付ける。"""
    if not len(kegg_rows) or not len(tx_rows):
        return kegg_rows, tx_rows
    index = {(a.upper(), b.upper()): i for i, (a, b) in enumerate(zip(tx_rows["source_protein"], tx_rows["target_protein"]))}
    used, merged, disagree = set(), 0, 0
    kegg_rows = kegg_rows.copy()
    for i, row in kegg_rows.iterrows():
        if row["interaction_type"] != "transcription":
            continue
        j = index.get((row["source_protein"].upper(), row["target_protein"].upper()))
        if j is None:
            continue
        y = tx_rows.iloc[j]
        k_eff, y_eff = row["effect"], y["effect"]
        if k_eff == y_eff or y_eff == "none":
            effect, warn = k_eff, ""
        elif k_eff == "none":
            effect, warn = y_eff, ""
        else:
            effect = "none"
            warn = f"; ⚠ 出典で作用が食い違う（KEGG {EFFECT_JA[k_eff]} / YEASTRACT+ {EFFECT_JA[y_eff]}）ため作用不明"
            disagree += 1
            report.append(["KEGG と YEASTRACT+", row["source_protein"], row["target_protein"], "転写制御",
                           EFFECT_JA[effect], f"KEGG {EFFECT_JA[k_eff]} / YEASTRACT+ {EFFECT_JA[y_eff]}"])
        kegg_rows.at[i, "effect"] = effect
        kegg_rows.at[i, "evidence"] = f"{row['evidence']}; {y['evidence']}{warn}"
        used.add(j)
        merged += 1
    print(f"KEGG と YEASTRACT+ の両方にある転写制御 {merged} 組を 1 本にまとめた（作用が食い違う {disagree} 組は作用不明）")
    keep = [j not in used for j in range(len(tx_rows))]
    return kegg_rows, tx_rows[keep].reset_index(drop=True)


