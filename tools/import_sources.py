"""公開データベースの元データから同梱用 DB (data/tor_pathway.db) を作る。

    python tools/import_sources.py

取り込む内容:
- SGD_features.tab           : 全 ORF の遺伝子名・Systematic 名・機能説明（遺伝子テーブル）
- BioGRID (出芽酵母 TAB3)     : 酵素と基質の関係（Biochemical Activity）だけ。修飾の種類（リン酸化・ユビキチン化など）
                               ごとに登録し、作用の向きは不明 (none) とする。注記（Qualifications）は根拠欄に残す
- YEASTRACT+（raw_data/YEASTRACT/）: 文献で裏付けのある転写因子 → 標的遺伝子（documented_and_*.tsv）。転写制御として登録し、
                               促進・抑制は act_*.tsv / inh_*.tsv（あれば）から付ける。両方にある・どちらにもない組は作用不明
- sample_data/literature/*.csv: 文献で確認した関係（PubMed で PMID と要旨を確かめたもの。unverified/ の中は使わない）。
                               遺伝子の欄に複合体の名前（data/complex_groups.csv）があれば構成遺伝子ごとに広げる。
                               いちばん優先し、同じ組の公開データ・サンプルの記録は根拠欄に残す。
                               status が reject の行は、文献で誤りと分かった組と、近道・複合体の重複の矢印として、
                               公開データからも取り込まない。status が flag の行は、公開データの記録を残したまま
                               作用の上書きと注記を加える（向きが食い違う・信号の流れではない、など）
- sample_data/proteins.csv   : 図のカテゴリ・遺伝子の既定の活性（TOR 経路など）。こちらを優先
- sample_data/interactions.csv: 構造確認用のサンプルの関係。文献で確かめたものは sample_data/literature/ に移したので、
                               既定では取り込まない（--curated-interactions で指定したときだけ使う）
- IDEA（raw_data/IDEA/、あれば）: 転写因子を誘導した時系列。誘導直後に動いた組の向きを採る（YEASTRACT+ の記録より優先。
                               逆だった記録は直して一覧に出す。tools/idea.py）
- SGD の GO（raw_data/SGD_GO/、あれば）: GO アノテーションのうち相手（has_input）と向きの分かる記録（活性化因子・阻害因子・
                               GEF・GAP、転写の正負の制御）。作用不明に向きを付け、ない組は新しい関係として加える
- GO-CAM（raw_data/GO_CAM/yeast_models.json.gz、あれば）: SGD が作った出芽酵母の GO の因果モデル。活性どうしの
                               「正に制御・負に制御」から、担う遺伝子産物どうしの向きのある関係を作り、SGD の GO と同じく重ねる
- Deleteome（raw_data/DELETEOME/、あれば）: 転写因子の破壊株の発現データ。破壊で標的が大きく変わる転写制御について、
                               作用不明なら向きを推定して付け、記録の向きと食い違えば ⚠ を付ける（記録の向きは変えない）

BioGRID の遺伝学的相互作用・物理的相互作用は「制御関係」ではないため取り込まない。
元データは raw_data/ に置く（容量が大きいためアプリには同梱しない）。

同じ組が複数の出典にあるときは、片方を捨てずに根拠欄に両方を残す（精査済みと公開データなど）。
KEGG は自由に使えるデータではないので、2026-10-03 から使わない（以前の取り込みのコードは archive/tools/kegg_import.py）。
出典によって作用（促進・抑制）が食い違う組は、根拠欄に「⚠ 出典で作用が食い違う」と書き、docs/source_conflicts.tsv に一覧を出す。
"""
import argparse
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import condition_expression  # noqa: E402
import deleteome  # noqa: E402
import idea  # noqa: E402
from tor_app import complex_groups  # noqa: E402
from tor_app.db import Database  # noqa: E402
from tor_app.db import csv_io  # noqa: E402
from tor_app.db.models import Category, CategoryMember, Interaction, Protein  # noqa: E402
from tor_app.db.vocab import (FEEDBACK, PARALOG_COLOR, PARALOG_SOURCE, guess_is_complex, resolve_effect,  # noqa: E402
                              type_label)
from sqlalchemy import select  # noqa: E402

RAW = ROOT / "raw_data"
CONFLICT_REPORT = ROOT / "docs" / "source_conflicts.tsv"
EFFECT_JA = {"activate": "促進", "inhibit": "抑制", "none": "作用不明"}
YEAST_TAXON = "559292"
BIOCHEMICAL_ACTIVITY = "Biochemical Activity"
MAX_PMIDS = 5


def load_sgd(path: Path) -> pd.DataFrame:
    cols = ["sgdid", "type", "qualifier", "systematic", "gene", "alias", "parent", "sgdid2", "chrom",
            "start", "stop", "strand", "genetic_pos", "coord_ver", "seq_ver", "description"]
    df = pd.read_csv(path, sep="\t", header=None, names=cols, dtype=str, keep_default_na=False, quoting=3)
    orfs = df[df["type"] == "ORF"].copy()
    orfs["gene_name"] = orfs["gene"].where(orfs["gene"] != "", orfs["systematic"])
    return pd.DataFrame({
        "gene_name": orfs["gene_name"],
        "standard_name": orfs["systematic"],
        "complex_category": "",
        "protein_properties": orfs["qualifier"],   # Verified / Uncharacterized / Dubious
        "description": orfs["description"],
        "basal_activity": "",
    }).drop_duplicates("gene_name")


def standardize_curated(sgd: pd.DataFrame, curated_p: pd.DataFrame,
                        curated_i: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """精査済みデータの遺伝子名を、Systematic 名を手がかりに SGD の標準名へそろえる（例: AVO3 → TSC11）。"""
    by_systematic = dict(zip(sgd["standard_name"].str.upper(), sgd["gene_name"]))
    rename = {}
    for _, row in curated_p.iterrows():
        standard = by_systematic.get(row.get("standard_name", "").upper())
        if standard and standard.upper() != row["gene_name"].upper():
            rename[row["gene_name"].upper()] = standard
    for old, new in rename.items():
        print(f"  精査済みデータの {old} は SGD の標準名 {new} として登録")
    curated_p = curated_p.copy()
    curated_i = curated_i.copy()
    curated_p["gene_name"] = [rename.get(g.upper(), g) for g in curated_p["gene_name"]]
    for col in ("source_protein", "target_protein"):
        curated_i[col] = [rename.get(g.upper(), g) for g in curated_i[col]]
    return curated_p, curated_i


LITERATURE = ROOT / "sample_data" / "literature"


def kind_class(interaction_type: str) -> str:
    """公開データを外すときの種類の分け方: 転写制御（tx）とそれ以外（sig。リン酸化・結合など）。
    文献の組と種類の違う公開データの関係（例: 文献はリン酸化、YEASTRACT+ は転写制御）は外さない。"""
    return "tx" if str(interaction_type).strip().lower() == "transcription" else "sig"


def literature_pairs(r, name_to_gene: dict[str, str]) -> list[tuple[str, str, str]]:
    """文献の CSV の 1 行 → [(上流の遺伝子, 下流の遺伝子, 根拠欄に足す印)]。source_protein・target_protein に
    複合体の名前（data/complex_groups.csv）が書いてあれば構成遺伝子ごとに広げ、「複合体 TORC1（上流）として」の印を付ける。
    遺伝子を特定できなければ空。"""
    a_genes, a_label = complex_groups.expand(str(r["source_protein"]), name_to_gene)
    b_genes, b_label = complex_groups.expand(str(r["target_protein"]), name_to_gene)
    marks = "; ".join(m for m in (a_label and complex_groups.mark(a_label, "上流"),
                                   b_label and complex_groups.mark(b_label, "下流")) if m)
    return [(a, b, marks) for a in a_genes for b in b_genes if a != b]


def load_rejected(folder: Path, name_to_gene: dict[str, str]) -> dict[tuple[str, str, str], str]:
    """文献で誤りと分かった組（status が reject の行）→ 理由（verified_note）。キーは (上流, 下流, 種類の分け方)。
    同じ種類の公開データからも取り込まない（種類が空の行は、どちらの種類も取り込まない）。"""
    out = {}
    for path in sorted(folder.glob("*.csv")):
        table = pd.read_csv(path, dtype=str, keep_default_na=False)
        for _, r in table.iterrows():
            if r.get("status", "") != "reject":
                continue
            for a, b, _ in literature_pairs(r, name_to_gene):
                pmids = ", ".join(p.strip() for p in r.get("pmids", "").split(";") if p.strip())
                why = (r.get("verified_note", "") or r.get("note", "")) + (f"（PMID: {pmids}）" if pmids else "")
                kinds = [kind_class(r["interaction_type"])] if r.get("interaction_type", "").strip() else ["tx", "sig"]
                for k in kinds:
                    out[(a.upper(), b.upper(), k)] = why
    return out


def load_flags(folder: Path, name_to_gene: dict[str, str]) -> dict[tuple[str, str], tuple[str, str]]:
    """文献の CSV で status が flag の行 → (上書きする作用（空なら変えない）, 根拠欄に加える注記)。
    関係は公開データの記録のまま残し、作用の上書きと注記だけを加える（文献で誤りとは示されていないが、
    向きが食い違う・信号の流れではない、などを記録するため）。"""
    out = {}
    for path in sorted(folder.glob("*.csv")):
        table = pd.read_csv(path, dtype=str, keep_default_na=False)
        for _, r in table.iterrows():
            if r.get("status", "") != "flag":
                continue
            for a, b, _ in literature_pairs(r, name_to_gene):
                pmids = ", ".join(p.strip() for p in r.get("pmids", "").split(";") if p.strip())
                out[(a, b)] = (r.get("effect", "").strip(), (r.get("verified_note", "") or r.get("note", "")) +
                               (f"（PMID: {pmids}）" if pmids else ""))
    return out


def apply_flags(frames: list[pd.DataFrame], flags: dict, report: list, done: set | None = None, last: bool = True) -> set:
    """status が flag の組について、公開データの記録の作用を上書きし、根拠欄に注記を加える（frames をその場で書き換える）。
    表ができる順に 2 回に分けて呼ぶ（BioGRID・転写制御 → SGD GO・PTM・UniProt・GO-CAM）。done に当てた組をためて、
    last のときだけ、どの表にもなかった組を知らせる。"""
    done = set() if done is None else done
    for frame in frames:
        for i, row in frame.iterrows():
            key = (row["source_protein"], row["target_protein"])
            if key not in flags:
                continue
            effect, note = flags[key]
            if effect:
                frame.at[i, "effect"] = effect
            frame.at[i, "evidence"] = f"{row['evidence']}; {note}"
            done.add(key)
            report.append(["文献で注記", key[0], key[1], row["interaction_type"], EFFECT_JA.get(effect or row["effect"], ""), note])
    if last:
        for key in set(flags) - done:
            print(f"  注記する組が公開データにない: {key[0]} → {key[1]}")
        print(f"文献で注記した組: {len(done)} 組")
    return done


def load_literature(folder: Path, name_to_gene: dict[str, str]) -> pd.DataFrame:
    """文献で確認した関係（docs/curation/request_core_pathways.md の形式に verified_on・verified_note を加えたもの）。
    status が reject の行は登録しない。遺伝子名は SGD の標準名にそろえる。"""
    rows = []
    for path in sorted(folder.glob("*.csv")):
        table = pd.read_csv(path, dtype=str, keep_default_na=False)
        for _, r in table.iterrows():
            if r.get("status", "") in ("reject", "flag"):   # 取り込まない組・公開データに注記するだけの組
                continue
            pairs = literature_pairs(r, name_to_gene)
            if not pairs:
                print(f"  文献の関係で遺伝子を特定できない行: {r['source_protein']} → {r['target_protein']}（{path.name}）")
                continue
            pmids = ", ".join(p.strip() for p in r.get("pmids", "").split(";") if p.strip())
            # 論文の文の引用（列 evidence）は DB に入れない（2026-10-07 から出典の明記だけにした。照合用の一文は Google Drive の
            # 作業用の下書きにだけ置き、この CSV では空にする）。日本語の要約（verified_note）は自分たちの言葉なので残す
            parts = [f"文献で確認（{r.get('verified_on', '') or '日付なし'}、PubMed で PMID と要旨を確認）"]
            if pmids:
                parts.append(f"PMID: {pmids}")
            if r.get("verified_note"):
                parts.append(r["verified_note"])
            if r.get("direct") and r["direct"] != "direct":
                parts.append(f"間接の作用（{r['direct']}）")
            if r.get("condition"):
                parts.append(f"条件: {r['condition']}")
            if r.get("confidence"):
                parts.append(f"確度: {r['confidence']}")
            if "フィードバック" in r.get("note", ""):   # 列 note に「フィードバック」と書いた関係は、階層の計算に使わない
                parts.append(FEEDBACK)
            for a, b, marks in pairs:
                rows.append([a, b, r.get("interaction_type", "") or "other", r.get("effect", "") or "none", "", "", "",
                             "; ".join(parts + ([marks] if marks else []))])
    return pd.DataFrame(rows, columns=csv_io.INTERACTION_COLUMNS)


def literature_problems(folder: Path, name_to_gene: dict[str, str]) -> list[list[str]]:
    """文献の CSV のうち、取り込めない・画面で困る行（SGD で特定できない遺伝子名、知らない interaction_type）を一覧にする。"""
    from tor_app.db.vocab import INTERACTION_TYPES
    out = []
    for path in sorted(folder.glob("*.csv")):
        table = pd.read_csv(path, dtype=str, keep_default_na=False)
        for _, r in table.iterrows():
            a, b, kind = r["source_protein"].strip(), r["target_protein"].strip(), r.get("interaction_type", "").strip()
            for name in (a, b):
                if name and name.upper() not in name_to_gene and name.upper() not in complex_groups.groups():
                    out.append(["文献の遺伝子名", a, b, kind, "", f"SGD で特定できない名前 {name}（標準名に直す）: {path.name}"])
            if kind and kind.lower() not in INTERACTION_TYPES:
                out.append(["文献の種類", a, b, kind, "", f"知らない interaction_type（画面に英語のまま出る）: {path.name}"])
    return out


def merge_curated(sgd: pd.DataFrame, curated: pd.DataFrame) -> pd.DataFrame:
    """精査済みデータの基底活性を優先し、機能説明・ORF の確度（protein_properties）は SGD を使う。
    （以前はカテゴリ（Upstream など）と性質のメモも上書きしていたが、2026-10-03 に外した）"""
    sgd = sgd.set_index(sgd["gene_name"].str.upper())
    for _, row in curated.iterrows():
        key = row["gene_name"].upper()
        if key in sgd.index:
            for col in ("basal_activity",):
                if row.get(col, ""):
                    sgd.at[key, col] = row[col]
        else:
            sgd.loc[key] = {c: row.get(c, "") for c in sgd.columns}
    return sgd.reset_index(drop=True)


# BioGRID の Modification 欄 → 制御の種類（tor_app/db/vocab.py の INTERACTION_TYPES）
MODIFICATION_TYPES = {
    "Phosphorylation": "phosphorylation", "Dephosphorylation": "dephosphorylation",
    "Ubiquitination": "ubiquitination", "Deubiquitination": "deubiquitination",
    "Acetylation": "acetylation", "Deacetylation": "deacetylation",
    "Methylation": "methylation", "Demethylation": "demethylation",
    "Sumoylation": "sumoylation", "Desumoylation": "desumoylation",
    "Nedd(Rub1)ylation": "neddylation", "Deneddylation": "deneddylation",
    "Proteolytic Processing": "proteolysis", "Prenylation": "prenylation", "Glycosylation": "glycosylation",
    "No Modification": "enzymatic",
}
MAX_NOTES = 3


def load_biogrid_enzymatic(path: Path, name_to_gene: dict[str, str]) -> pd.DataFrame:
    """BioGRID TAB3 の酵素活性（Biochemical Activity）の行から、酵素 (Interactor A) → 基質 (Interactor B) の関係を作る。

    修飾の種類ごとに 1 本にまとめる。作用の向きは分からないので none とする。
    根拠欄に実験の規模を書く（低スループットの記録が 1 つでもあれば「個別実験あり」、なければ「網羅的な実験だけ」）。
    同じ組に修飾ありの記録があれば、修飾なし（No Modification）の記録は登録しない。
    """
    groups: dict[tuple[str, str, str], dict] = {}
    skipped = 0
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            c = line.rstrip("\n").split("\t")
            if c[11] != BIOCHEMICAL_ACTIVITY or c[15] != YEAST_TAXON or c[16] != YEAST_TAXON:
                continue
            a = name_to_gene.get(c[5].upper()) or name_to_gene.get(c[7].upper())
            b = name_to_gene.get(c[6].upper()) or name_to_gene.get(c[8].upper())
            if not a or not b or a == b:
                skipped += 1
                continue
            kind = MODIFICATION_TYPES.get(c[19], "enzymatic")
            g = groups.setdefault((a, b, kind), {"modification": c[19], "pmids": [], "notes": [], "low": False})
            g["low"] = g["low"] or c[17].startswith("Low")
            pmid = c[14].split(":")[-1]
            if pmid not in g["pmids"]:
                g["pmids"].append(pmid)
            note = c[20].strip()
            if note not in ("", "-") and note not in g["notes"]:
                g["notes"].append(note)
    modified = {(a, b) for a, b, kind in groups if kind != "enzymatic"}
    version = re.search(r"(\d+\.\d+\.\d+)", path.name)
    label = f"BioGRID {version.group(1) if version else ''} Biochemical Activity"
    rows = []
    for (a, b, kind), g in groups.items():
        if kind == "enzymatic" and (a, b) in modified:
            continue
        ids = g["pmids"]
        shown = ", ".join(ids[:MAX_PMIDS]) + (f" ほか {len(ids) - MAX_PMIDS} 報" if len(ids) > MAX_PMIDS else "")
        evidence = f"{label} ({g['modification']}); PMID: {shown}; 実験の規模: {'個別実験あり' if g['low'] else '網羅的な実験だけ'}"
        if g["notes"]:
            notes = " / ".join(n[:120] for n in g["notes"][:MAX_NOTES])
            evidence += f"; 注記: {notes}" + (f" ほか {len(g['notes']) - MAX_NOTES} 件" if len(g["notes"]) > MAX_NOTES else "")
        rows.append([a, b, kind, "none", "", "", "", evidence])
    pairs = {(a, b) for a, b, _ in groups}
    print(f"BioGRID 酵素活性: {len(pairs)} 組 → 修飾の種類ごとに {len(rows)} 本（遺伝子を特定できず除外 {skipped} 行）")
    return pd.DataFrame(rows, columns=csv_io.INTERACTION_COLUMNS)


def _read_pairs(path: Path) -> list[tuple[str, str]]:
    """「転写因子<TAB>標的」または YEASTRACT+ の「転写因子;標的」の 2 列の表を読む。"""
    pairs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = re.split(r"[\t;]", line.strip())
        if len(parts) >= 2 and parts[0] and parts[1]:
            pairs.append((parts[0].strip(), parts[1].strip()))
    return pairs


SGD_GO = RAW / "SGD_GO"


def load_sgd_go(folder: Path, name_to_gene: dict[str, str]) -> list[tuple[str, str, str, str, str]]:
    """SGD の GO アノテーション（gene_association.sgd.*.gaf）から、相手（has_input）と向きの分かる記録を読む。
    分子の働きが活性化因子・GEF なら促進、阻害因子・GAP・GDI なら抑制（シグナル伝達）。生物学的過程が
    「RNA ポリメラーゼ II による転写の正・負の制御」なら、相手への転写の促進・抑制。NOT の記録は使わない。
    返り値: [(上流, 下流, 種類, 作用, 根拠)]。用語の名前は go_term_names.json（QuickGO で引いたもの）。"""
    import json
    gafs = sorted(folder.glob("gene_association.sgd*.gaf"))
    if not gafs:
        return []
    names = json.loads((folder / "go_term_names.json").read_text(encoding="utf-8"))
    sgdid_to_gene = {}
    with open(RAW / "SGD_features.tab", encoding="utf-8", errors="replace") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) > 4 and c[1] == "ORF":
                gene = name_to_gene.get((c[4] or c[3]).upper())
                if gene:
                    sgdid_to_gene[c[0]] = gene
    out = []
    with open(gafs[-1], encoding="utf-8") as f:
        for line in f:
            if line.startswith("!"):
                continue
            r = line.rstrip("\n").split("\t")
            if len(r) < 16 or "NOT" in r[3] or "has_input" not in r[15]:
                continue
            src = name_to_gene.get(r[2].upper())
            targets = [sgdid_to_gene.get(x) for x in re.findall(r"has_input\(SGD:(S\d+)\)", r[15])]
            if not src:
                continue
            term = names.get(r[4], "")
            low = term.lower()
            if r[8] == "F":
                if "gtpase activator" in low or "gdp-dissociation inhibitor" in low or "inhibitor activity" in low:
                    kind, effect = "inhibition", "inhibit"
                elif "exchange factor" in low or "activator activity" in low:
                    kind, effect = "activation", "activate"
                else:
                    continue
            elif r[4] in ("GO:0045944", "GO:0000122"):
                kind, effect = "transcription", ("activate" if r[4] == "GO:0045944" else "inhibit")
            else:
                continue
            pmid = r[5].split("PMID:")[-1] if "PMID:" in r[5] else r[5]
            for t in targets:
                if t and t != src:
                    out.append((src, t, kind, effect, f"SGD GO 注釈: {term}（{r[4]}、{r[6]}、PMID: {pmid}）"))
    return out


def apply_sgd_go(frames: list[pd.DataFrame], records: list, taken: set, report: list,
                 label: str = "SGD の GO") -> pd.DataFrame:
    """SGD の GO（や GO-CAM）の記録を公開データに重ねる。既存の組で作用不明なら向きを付け、同じなら根拠を足し、逆なら ⚠ を付ける
    （向きは変えない）。どの出典にもない組は、新しい関係として返す（文献で確認した組・取り込まない組は除く）。"""
    by_pair: dict = {}
    for a, b, kind, effect, ev in records:
        key = (a, b, kind == "transcription")
        old = by_pair.get(key)
        if old and old[1] != effect:
            by_pair[key] = (kind, "none", old[2] + "; " + ev + "（GO の記録どうしで向きが食い違う）")
        elif old:
            by_pair[key] = (kind, effect, old[2] + "; " + ev)
        else:
            by_pair[key] = (kind, effect, ev)
    used, filled, agreed, disagreed = set(), 0, 0, 0
    for frame in frames:
        for i, row in frame.iterrows():
            key = (row["source_protein"], row["target_protein"], row["interaction_type"] == "transcription")
            if key not in by_pair:
                continue
            kind, effect, ev = by_pair[key]
            used.add(key)
            current = _effect(row["interaction_type"], row.get("effect", ""))
            if current == "none" and effect != "none":
                frame.at[i, "effect"] = effect
                frame.at[i, "evidence"] = f"{row['evidence']}; 作用の向き: {ev}"
                filled += 1
            elif current == effect:
                frame.at[i, "evidence"] = f"{row['evidence']}; {ev}（記録の向きと一致）"
                agreed += 1
            else:
                frame.at[i, "evidence"] = f"{row['evidence']}; ⚠ 出典で作用が食い違う: {ev}（記録の{EFFECT_JA[current]}のまま）"
                disagreed += 1
                report.append([f"{label}と記録", key[0], key[1], kind, EFFECT_JA[current], ev])
    new = [[a, b, kind, effect, "", "", "", ev] for (a, b, _tx), (kind, effect, ev) in by_pair.items()
           if (a, b, _tx) not in used and (a.upper(), b.upper(), kind_class(kind)) not in taken]
    print(f"{label}: 相手と向きの分かる記録 {len(records)} 件 → 作用不明に向きを付けた {filled} 本 / 一致 {agreed} 本 / "
          f"逆 {disagreed} 本（⚠）/ 新しい関係 {len(new)} 本")
    return pd.DataFrame(new, columns=csv_io.INTERACTION_COLUMNS)


GO_CAM = RAW / "GO_CAM" / "yeast_models.json.gz"
# GO-CAM の活性どうしの因果関係 → (作用, 直接か)。「入力を与える」（RO:0002413）は代謝の流れなども含み、制御とは限らないので使わない
GOCAM_RELATIONS = {
    "RO:0002629": ("activate", True, "直接正に制御"),
    "RO:0002630": ("inhibit", True, "直接負に制御"),
    "RO:0002213": ("activate", False, "正に制御"),
    "RO:0002212": ("inhibit", False, "負に制御"),
    "RO:0002304": ("activate", False, "因果的に正の作用"),
    "RO:0002305": ("inhibit", False, "因果的に負の作用"),
}
# 上流の活性がこれらなら、転写制御として扱う（DNA に結合する転写因子の活性）
GOCAM_TF_ACTIVITIES = {"GO:0003700", "GO:0000981", "GO:0001228", "GO:0001227", "GO:0003712", "GO:0003713", "GO:0003714"}


def load_gocam(path: Path, name_to_gene: dict[str, str]) -> list[tuple[str, str, str, str, str]]:
    """GO-CAM（SGD が作った出芽酵母の公開モデル）から、遺伝子産物どうしの向きの分かる因果関係を集める。
    活性 A を担う遺伝子産物（enabled by）→ 活性 B を担う遺伝子産物、の組にする。返り値は load_sgd_go と同じ形
    [(上流, 下流, 種類, 作用, 根拠)]。複合体が担う活性（遺伝子産物でないもの）は使わない。"""
    import gzip
    import json
    sgd_to_gene = {}
    for line in (RAW / "SGD_features.tab").read_text(encoding="utf-8", errors="replace").splitlines():
        cols = line.split("\t")
        if len(cols) > 4 and cols[1] == "ORF":
            gene = name_to_gene.get((cols[4] or cols[3]).upper())
            if gene:
                sgd_to_gene[cols[0]] = gene
    records, models_used = [], 0
    for model in json.load(gzip.open(path, "rt", encoding="utf-8")):
        ann = {a["key"]: a["value"] for a in model.get("annotations", [])}
        if ann.get("state") != "production":
            continue
        individuals = {i["id"]: i for i in model.get("individuals", [])}
        types = {iid: {t.get("id") for t in i.get("type", []) if t.get("id")} for iid, i in individuals.items()}
        sources = {iid: [a["value"] for a in i.get("annotations", []) if a["key"] == "source"]
                   for iid, i in individuals.items()}
        enabled: dict[str, set[str]] = {}
        for f in model.get("facts", []):
            if f["property"] == "RO:0002333":   # enabled by
                genes = {sgd_to_gene[t.split(":", 1)[1]] for t in types.get(f["object"], set())
                         if t.startswith("SGD:") and t.split(":", 1)[1] in sgd_to_gene}
                enabled.setdefault(f["subject"], set()).update(genes)
        used = False
        for f in model.get("facts", []):
            rel = GOCAM_RELATIONS.get(f["property"])
            if not rel:
                continue
            ups, downs = enabled.get(f["subject"], set()), enabled.get(f["object"], set())
            if not ups or not downs:
                continue
            effect, direct, word = rel
            pmids = sorted({s.split(":", 1)[1] for a in f.get("annotations", []) if a["key"] == "evidence"
                            for s in sources.get(a["value"], []) if s.startswith("PMID:")})
            kind = "transcription" if types.get(f["subject"], set()) & GOCAM_TF_ACTIVITIES else \
                ("activation" if effect == "activate" else "inhibition")
            title = ann.get("title", "").strip().replace(";", ",")
            ev = (f"GO-CAM「{title}」: {word}" + ("" if direct else "（間接）")
                  + (f"（PMID: {', '.join(pmids)}）" if pmids else ""))
            for a in ups:
                for b in downs:
                    if a != b:
                        records.append((a, b, kind, effect, ev))
                        used = True
        models_used += used
    print(f"GO-CAM: 出芽酵母の公開モデルのうち {models_used} 件から、向きの分かる遺伝子どうしの関係 {len(records)} 件")
    return records


BIOGRID_PTM = RAW / "BIOGRID-PTM"
PTM_TYPES = {"kinase": "phosphorylation", "phosphatase": "dephosphorylation"}


def load_biogrid_ptm(folder: Path, name_to_gene: dict[str, str]) -> pd.DataFrame:
    """BioGRID の翻訳後修飾（PhosphoGRID を含む）: 修飾部位を担うキナーゼ・ホスファターゼ（触媒）→ 基質の関係。
    組と種類ごとに 1 本にまとめ、部位と文献を根拠欄に書く（作用の向きは不明）。"""
    ptm_file = next(folder.glob("BIOGRID-PTM-*.yeast.tsv"), None)
    rel_file = next(folder.glob("BIOGRID-PTM-RELATIONSHIPS-*.yeast.tsv"), None)
    if ptm_file is None or rel_file is None:
        return pd.DataFrame(columns=csv_io.INTERACTION_COLUMNS)
    version = re.search(r"(\d+\.\d+\.\d+)", rel_file.name).group(1)
    gene = lambda symbol, systematic: name_to_gene.get(symbol.upper()) or name_to_gene.get(systematic.upper())  # noqa: E731
    sites = {}
    for line in ptm_file.read_text(encoding="utf-8").splitlines()[1:]:
        c = line.split("\t")
        if len(c) > 12:
            sites[c[0]] = (gene(c[4], c[3]), f"{c[10]}{c[8]}", c[12])
    groups: dict = {}
    for line in rel_file.read_text(encoding="utf-8").splitlines()[1:]:
        c = line.split("\t")
        if len(c) < 13 or c[6] not in PTM_TYPES or c[7] != "catalytic" or c[0] not in sites:
            continue
        enzyme = gene(c[4], c[3])
        target, site, _ = sites[c[0]]
        if not enzyme or not target or enzyme == target:
            continue
        g = groups.setdefault((enzyme, target, PTM_TYPES[c[6]]), {"sites": set(), "pmids": set(), "db": set()})
        g["sites"].add(site)
        g["pmids"].add(c[9])
        g["db"].add(c[12])
    rows = []
    for (a, b, kind), g in groups.items():
        sites_text = "・".join(sorted(g["sites"])[:8]) + (f" ほか {len(g['sites']) - 8} か所" if len(g["sites"]) > 8 else "")
        pmids = sorted(p for p in g["pmids"] if p.isdigit())
        rows.append([a, b, kind, "none", "", "", "",
                     f"BioGRID PTM {version}（{'・'.join(sorted(g['db']))}）: 部位 {sites_text}; PMID: {', '.join(pmids)}"])
    print(f"BioGRID PTM: 修飾を担う酵素 → 基質 {len(rows)} 組")
    return pd.DataFrame(rows, columns=csv_io.INTERACTION_COLUMNS)


def merge_ptm(enzymatic: pd.DataFrame, ptm: pd.DataFrame, taken: set) -> pd.DataFrame:
    """BioGRID PTM の組を BioGRID の酵素活性の関係に重ねる。同じ組・種類があれば根拠欄に足し、なければ新しい関係にする。"""
    index = {(a, b, t): i for i, (a, b, t) in enumerate(zip(enzymatic["source_protein"], enzymatic["target_protein"],
                                                             enzymatic["interaction_type"]))}
    new, merged = [], 0
    for row in ptm.itertuples(index=False):
        key = (row.source_protein, row.target_protein, row.interaction_type)
        if key in index:
            i = index[key]
            enzymatic.at[i, "evidence"] = f"{enzymatic.at[i, 'evidence']}; {row.evidence}"
            merged += 1
        elif (row.source_protein.upper(), row.target_protein.upper(), kind_class(row.interaction_type)) not in taken:
            new.append(list(row))
    print(f"  BioGRID の酵素活性と同じ組 {merged}（根拠に足した）/ 新しい関係 {len(new)}")
    return pd.DataFrame(new, columns=csv_io.INTERACTION_COLUMNS)


UNIPROT = RAW / "UNIPROT"
_ACTIVATE = re.compile(r"\b(activat\w*|stimulat\w*|enhanc\w*|increas\w*|promot\w*|positively regulat\w*)", re.I)
_INHIBIT = re.compile(r"\b(inhibit\w*|inactivat\w*|repress\w*|reduc\w*|decreas\w*|antagoniz\w*|suppress\w*"
                      r"|negatively regulat\w*)", re.I)
# 向きの語に見えるが、作用ではなく部位・状態の名前（「auto-inhibitory domain」「activation loop」など）。向きの判定では読まない
_NOT_VERB = re.compile(r"(auto-?)?inhibitory (domain|region|sequence|segment|motif|subunit)|autoinhibit\w*"
                       r"|activation (loop|domain|segment|lip)|activating mutation", re.I)
_GENE_TOKEN = re.compile(r"\b([A-Z][A-Za-z]{1,4}\d{1,3}[A-Z]?)\b")
_MOD_TYPES = (("phospho", "phosphorylation"), ("methyl", "methylation"), ("acetyl", "acetylation"))
# 修飾の「by …」に出る、遺伝子名でない呼び名 → 触媒サブユニット
UNIPROT_ENZYME_ALIASES = {"PKA": ("TPK1", "TPK2", "TPK3"), "TORC1": ("TOR1", "TOR2"), "TORC2": ("TOR2",),
                          "CK2": ("CKA1", "CKA2"), "PKC": ("PKC1",), "CDK1": ("CDC28",), "CK1": ("YCK1", "YCK2")}


def uniprot_genes(path: Path, name_to_gene: dict[str, str]) -> dict[str, str]:
    """UniProt の番号（Entry）→ 遺伝子名（DB の名前）。"""
    out = {}
    for r in pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False).itertuples(index=False):
        for name in str(r[1]).split():
            g = name_to_gene.get(name.upper())
            if g:
                out[r[0]] = g
                break
    return out


def load_uniprot(folder: Path, name_to_gene: dict[str, str]) -> tuple[list, pd.DataFrame]:
    """UniProt の出芽酵母の確認済みタンパク質から、
    - 活性の調節（ACTIVITY REGULATION）の文: 「X によって活性化・阻害される」の X（遺伝子名）→ そのタンパク質の向きのある関係
    - 修飾された残基（MOD_RES）の「…; by X」: X → そのタンパク質の修飾（リン酸化・メチル化・アセチル化。向きは不明）
    を集める。実験の証拠（ECO:0000269 などと PubMed）のある記載だけを使う（他の生物からの類推 ECO:0000250 は使わない）。
    返り値: (向きのある記録 [(上流, 下流, 種類, 作用, 根拠)], 修飾の関係の表)"""
    path = next(iter(sorted(folder.glob("uniprot_yeast_*.tsv"), reverse=True)), None)
    if path is None:
        return [], pd.DataFrame(columns=csv_io.INTERACTION_COLUMNS)
    fetched = re.search(r"(\d{4}-\d{2}-\d{2})", path.name).group(1)
    table = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    records, mods = [], {}

    def enzymes(name: str) -> list[str]:
        name = name.strip().upper()
        if name in UNIPROT_ENZYME_ALIASES:
            return [g for g in (name_to_gene.get(n) for n in UNIPROT_ENZYME_ALIASES[name]) if g]
        g = name_to_gene.get(name)
        return [g] if g else []

    for r in table.itertuples(index=False):
        entry, names, regulation, modres = r[0], r[1], r[2], r[4]
        target = next((name_to_gene[n.upper()] for n in names.split() if n.upper() in name_to_gene), None)
        if not target:
            continue
        # 活性の調節
        text = regulation.replace("ACTIVITY REGULATION:", " ")
        for block in re.finditer(r"([^{}]*)\{([^}]*)\}", text):   # 文のまとまりと、その後ろの {証拠}
            body, evidence = block.group(1), block.group(2)
            pmids = re.findall(r"ECO:00(?:00269|00314|00315|07744)\|PubMed:(\d+)", evidence)
            if not pmids:
                continue
            for sentence in re.split(r"(?<=\.)\s+", body):
                # 名前の一部の「activating」（GTPase-activating protein など）は、活性化の動詞として読まない
                scan = re.sub(r"(GTPase|GAP)[- ]activating|activating protein|exchange factor", lambda m: "x" * len(m.group(0)),
                              sentence, flags=re.I)
                scan = _NOT_VERB.sub(lambda m: "x" * len(m.group(0)), scan)
                verbs = [(m.start(), "activate") for m in _ACTIVATE.finditer(scan)] + \
                        [(m.start(), "inhibit") for m in _INHIBIT.finditer(scan)]
                if not verbs:
                    continue
                verbs.sort()
                for m in _GENE_TOKEN.finditer(sentence):
                    gene = name_to_gene.get(m.group(1).upper())
                    if not gene or gene == target:
                        continue
                    # 「phosphorylation of X」の X は調節される側なので使わない
                    if re.search(r"\bof\s+(?:both\s+)?(?:[\w-]+,?\s+(?:and\s+)?){0,3}$", sentence[:m.start()]):
                        continue
                    # 「PAB1-binding protein PBP1」の PAB1 は名前の一部で、調節する側ではない
                    if re.match(r"-(binding|interacting|associated)", sentence[m.end():]):
                        continue
                    before = [v for v in verbs if v[0] < m.start()]
                    verb = before[-1] if before else verbs[0]
                    effect = verb[1]
                    # GTPase の「活性（GTP の分解）が GAP で正に制御される」は、GTPase の働きを止める（機能としては抑制）
                    if effect == "activate" and re.match(r"positively regulat", scan[verb[0]:], re.I) and \
                            re.search(r"GTPase[- ]activating|\bGAP\b", sentence):
                        effect = "inhibit"
                    records.append((gene, target, "activation" if effect == "activate" else "inhibition", effect,
                                    f"UniProt {entry} 活性の調節（{fetched} 取得）: {sentence.strip()}（PMID: {', '.join(pmids)}）"))
        # 修飾された残基
        for entry_text in modres.split("MOD_RES ")[1:]:
            note = re.search(r'/note="([^"]*)"', entry_text)
            evidence = re.search(r'/evidence="([^"]*)"', entry_text)
            if not note or "; by " not in note.group(1) or not evidence:
                continue
            pmids = re.findall(r"ECO:00(?:00269|07744)\|PubMed:(\d+)", evidence.group(1))
            if not pmids:
                continue
            what, by = note.group(1).split("; by ", 1)
            kind = next((t for w, t in _MOD_TYPES if w in what.lower()), None)
            if not kind:
                continue
            site = f"{entry_text.split(';')[0].strip()}（{what}）"
            by = by.split(";")[0]
            for name in re.split(r"\s+(?:and|or)\s+|,\s*", by):
                for enzyme in enzymes(name):
                    if enzyme == target:
                        continue
                    g = mods.setdefault((enzyme, target, kind), {"sites": set(), "pmids": set()})
                    g["sites"].add(site)
                    g["pmids"].update(pmids)
    rows = [[a, b, kind, "none", "", "", "",
             f"UniProt 修飾された残基（{fetched} 取得）: {'・'.join(sorted(g['sites'])[:6])}; PMID: {', '.join(sorted(g['pmids']))}"]
            for (a, b, kind), g in mods.items()]
    print(f"UniProt: 活性の調節から向きのある記録 {len(records)} 件・修飾を担う酵素 → 基質 {len(rows)} 組")
    return records, pd.DataFrame(rows, columns=csv_io.INTERACTION_COLUMNS)


COMPLEX_PORTAL = RAW / "COMPLEX_PORTAL" / "559292.tsv"
COMPLEX_PORTAL_OUT = ROOT / "data" / "complex_portal.tsv"


PORTAL_SOURCE = "Complex Portal"
PORTAL_COLOR = "#b0bec5"   # 仮の色（入れたあとに color_complexes で塗り分ける）


def complex_color(k: int) -> str:
    """k 番目の複合体の色。色相を黄金角ずつ回し、隣どうしの番号でも見分けやすくする（明るさは 3 段で変える）。"""
    import colorsys
    hue = (k * 0.381966) % 1.0
    light = (0.42, 0.50, 0.36)[k % 3]
    r, g, b = colorsys.hls_to_rgb(hue, light, 0.62)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def color_complexes(db: Database) -> int:
    """複合体のカテゴリ（地図の枠・Complex Portal）を、名前順に 1 つずつ別の色で塗る。複合体でないカテゴリは変えない。"""
    with db.session() as s, s.begin():
        cats = sorted(s.scalars(select(Category).where(Category.is_complex)), key=lambda c: c.name.upper())
        for k, cat in enumerate(cats):
            cat.color = complex_color(k)
    return len(cats)


def add_complex_portal_categories(db: Database) -> int:
    """data/complex_portal.tsv の複合体を、すべてカテゴリとして DB に入れる（出典 Complex Portal、所属は CategoryMember。
    1 つの遺伝子が複数の複合体に入れる）。前に入れた Complex Portal のカテゴリは消してから入れ直す。
    地図の枠（出典が空のカテゴリ・遺伝子の complex_category）は変えない。名前が重なるものは末尾に番号を付ける。"""
    if not COMPLEX_PORTAL_OUT.exists():
        return 0
    table = pd.read_csv(COMPLEX_PORTAL_OUT, sep="\t", dtype=str, keep_default_na=False)
    with db.session() as s, s.begin():
        for cat in s.scalars(select(Category).where(Category.source == PORTAL_SOURCE)):
            s.delete(cat)
        s.flush()
        taken = {c.name for c in s.scalars(select(Category))}
        ids = {p.gene_name.upper(): p.id for p in s.scalars(select(Protein))}
        added = 0
        for r in table.itertuples(index=False):
            members = [ids[g.upper()] for g in r.genes.split(";") if g.upper() in ids]
            if not members:
                continue
            name = r.name if r.name not in taken else f"{r.name} ({r.complex_ac})"
            taken.add(name)
            cat = Category(name=name, is_complex=True, color=PORTAL_COLOR, source=PORTAL_SOURCE,
                           ref=r.complex_ac, description=getattr(r, "description", "") or "")
            s.add(cat)
            s.flush()
            s.add_all(CategoryMember(category_id=cat.id, protein_id=pid) for pid in members)
            added += 1
    return added


PARALOGS = ROOT / "data" / "paralogs.tsv"   # tools/build_paralogs.py が作る


PARALOG_COLOR_OFFSET = 7   # 複合体の色と同じ順にならないようにずらす


def add_paralog_categories(db: Database) -> int:
    """data/paralogs.tsv の採用した組（accepted）を、カテゴリ（出典「パラログ」、複合体ではない）として入れる。
    所属は CategoryMember。説明に根拠（候補の出典・遺伝的相互作用の種類と件数・PMID・GO の重なり）を書く。
    前に入れたパラログのカテゴリは消してから入れ直す。"""
    if not PARALOGS.exists():
        return 0
    table = pd.read_csv(PARALOGS, sep="\t", dtype=str, keep_default_na=False)
    with db.session() as s, s.begin():
        for cat in s.scalars(select(Category).where(Category.source == PARALOG_SOURCE)):
            s.delete(cat)
        s.flush()
        taken = {c.name for c in s.scalars(select(Category))}
        ids = {p.gene_name.upper(): p.id for p in s.scalars(select(Protein))}
        added = 0
        for r in table.itertuples(index=False):
            if r.accepted != "yes" or r.gene_a.upper() not in ids or r.gene_b.upper() not in ids:
                continue
            name = f"{r.gene_a}/{r.gene_b}"
            if name in taken:
                name += "（パラログ）"
            taken.add(name)
            source = {"Alliance・YGOB": "全ゲノム重複でできた組（YGOB・Alliance）", "YGOB": "全ゲノム重複でできた組（YGOB）",
                      "Alliance": "パラログ（Alliance）"}.get(r.sources, r.sources)
            lines = [source]
            if r.genetic:
                lines.append(f"両方を壊すと強く悪くなる（BioGRID の遺伝的相互作用）: {r.genetic}"
                             + (f"。PMID: {r.pmids}" if r.pmids else ""))
            lines.append(f"GO（生物学的過程・分子機能）の重なり: {r.go_overlap}")
            # 組ごとに色を変える（複合体と同じ黄金角の並びを、ずらした位置から使う）
            cat = Category(name=name, is_complex=False, color=complex_color(added + PARALOG_COLOR_OFFSET), source=PARALOG_SOURCE,
                           ref=r.sources, description="\n".join(lines))
            s.add(cat)
            s.flush()
            s.add_all(CategoryMember(category_id=cat.id, protein_id=ids[g.upper()]) for g in (r.gene_a, r.gene_b))
            added += 1
    return added


def write_complex_portal(name_to_gene: dict[str, str]) -> None:
    """Complex Portal の出芽酵母の複合体を、構成要素を遺伝子名に直して data/complex_portal.tsv に書く（アプリに同梱し、
    遺伝子の説明欄に「所属する複合体」、データベースのカテゴリの説明欄に複合体の説明（description 列）として出す）。"""
    if not COMPLEX_PORTAL.exists() or not UNIPROT.exists():
        return
    ac_to_gene = uniprot_genes(next(iter(sorted(UNIPROT.glob("uniprot_yeast_*.tsv"), reverse=True))), name_to_gene)
    table = pd.read_csv(COMPLEX_PORTAL, sep="\t", dtype=str, keep_default_na=False)
    rows = []
    for r in table.itertuples(index=False):
        members = sorted({ac_to_gene[m.split("(")[0].split("-")[0]] for m in str(r[4]).split("|")
                          if m.split("(")[0].split("-")[0] in ac_to_gene})
        if members:
            rows.append([r[0], r[1], ";".join(members), " ".join(str(r[9]).split())])
    out = pd.DataFrame(rows, columns=["complex_ac", "name", "genes", "description"])
    out.to_csv(COMPLEX_PORTAL_OUT, sep="\t", index=False)
    print(f"Complex Portal: 出芽酵母の複合体 {len(rows)} 件 → {COMPLEX_PORTAL_OUT.relative_to(ROOT)}")


def load_yeastract(folder: Path, name_to_gene: dict[str, str]) -> pd.DataFrame:
    """YEASTRACT+ の転写制御の関係（転写制御として登録）。"""
    def genes(pattern: str) -> tuple[set, list[Path]]:
        files = sorted(folder.glob(pattern))
        found, skipped = set(), 0
        for path in files:
            for tf, target in _read_pairs(path):
                a, b = name_to_gene.get(tf.upper()), name_to_gene.get(target.upper())
                if a and b and a != b:
                    found.add((a, b))
                else:
                    skipped += 1
        return found, files, skipped

    pairs, files, skipped = genes("documented_and_*.tsv")
    activators, _, _ = genes("act_*.tsv")
    inhibitors, _, _ = genes("inh_*.tsv")
    dates = sorted({m.group(0) for f in files for m in [re.search(r"\d{4}-\d{2}-\d{2}", f.name)] if m})
    label = f"YEASTRACT+ Documented (DNA binding and expression evidence)（{', '.join(dates) or '取得日不明'} 取得）"
    rows = []
    for a, b in sorted(pairs):
        act, inh = (a, b) in activators, (a, b) in inhibitors
        effect = "activate" if act and not inh else "inhibit" if inh and not act else "none"
        # 促進・抑制の根拠（Expression evidence で TF acting as activator / inhibitor）も根拠欄に残す
        note = ("; activator と inhibitor の両方の報告あり（作用不明）" if act and inh else
                "; TF acting as activator" if act else "; TF acting as inhibitor" if inh else "")
        rows.append([a, b, "transcription", effect, "", "", "", label + note])
    counts = {e: sum(1 for r in rows if r[3] == e) for e in ("activate", "inhibit", "none")}
    print(f"YEASTRACT+: 転写制御 {len(rows)} 組（促進 {counts['activate']} / 抑制 {counts['inhibit']} / 作用不明 {counts['none']}、"
          f"遺伝子を特定できず除外 {skipped} 行）")
    return pd.DataFrame(rows, columns=csv_io.INTERACTION_COLUMNS)


def _effect(itype: str, effect: str) -> str:
    return resolve_effect(itype, effect if effect else None)


def public_records(biogrid: pd.DataFrame, yeastract: pd.DataFrame) -> dict[tuple[str, str], list]:
    """公開データの記録を組ごとに集める: (上流, 下流) → [(出典, 種類, 作用)]（精査済みの根拠欄に残すため）"""
    records: dict[tuple[str, str], list] = {}
    for a, b, t, e, ev in zip(biogrid["source_protein"], biogrid["target_protein"], biogrid["interaction_type"],
                              biogrid["effect"], biogrid["evidence"]):
        # BioGRID の文献番号も残す（根拠欄で PubMed へのリンクになる）
        pmids = re.search(r"PMID: ([\d, ]+(?:ほか \d+ 報)?)", ev or "")
        records.setdefault((a.upper(), b.upper()), []).append(
            (f"BioGRID（PMID {pmids.group(1).strip()}）" if pmids else "BioGRID", t, e))
    for a, b, t, e in zip(yeastract["source_protein"], yeastract["target_protein"], yeastract["interaction_type"],
                          yeastract["effect"]):
        records.setdefault((a.upper(), b.upper()), []).append(("YEASTRACT+", t, e))
    return records


def annotate_curated(curated: pd.DataFrame, public: dict, report: list) -> pd.DataFrame:
    """精査済みの関係（優先して登録する）の根拠欄に、同じ組の公開データの記録を残す。
    公開データの作用（促進・抑制）と食い違えば印を付けて一覧に出す（精査済みの作用はそのまま）。"""
    curated = curated.copy()
    noted = disagree = 0
    for i, row in curated.iterrows():
        recs = public.get((row["source_protein"].upper(), row["target_protein"].upper()))
        if not recs:
            continue
        mine = _effect(row["interaction_type"], row.get("effect", ""))
        text = " / ".join(f"{src} {type_label(t)}・{EFFECT_JA[_effect(t, e)]}" for src, t, e in recs)
        other = sorted({_effect(t, e) for _src, t, e in recs} - {"none"})
        warn = ""
        if other and other != [mine]:
            warn = f"; ⚠ 出典で作用が食い違う（この関係 {EFFECT_JA[mine]} / 公開データ {'・'.join(EFFECT_JA[o] for o in other)}）"
            disagree += 1
            report.append(["精査済みと公開データ", row["source_protein"], row["target_protein"],
                           type_label(row["interaction_type"]), EFFECT_JA[mine], text])
        curated.at[i, "evidence"] = f"{row.get('evidence', '')}; 公開データ: {text}{warn}".lstrip("; ")
        noted += 1
    print(f"精査済みの関係のうち公開データにもある {noted} 組は、根拠欄に公開データの記録を残した（作用が食い違う {disagree} 組）")
    return curated


def apply_idea(frames: list[pd.DataFrame], name_to_gene: dict[str, str], report: list) -> None:
    """転写因子を誘導した時系列（IDEA）で、公開データの転写制御の作用の向きを決める（frames をその場で書き換える）。

    誘導の直後に動いた組について、作用不明なら向きを付け、記録と逆なら IDEA の向きに直して一覧に出す。
    IDEA の向きは文献で確認した転写制御と 100% 一致し、YEASTRACT+ の記録は 71% だったため（tools/idea.py）。
    文献で確認した関係（精査済み）は変えない。"""
    if not idea.DATA.exists():
        print("IDEA: raw_data/IDEA/ にデータがないので使いません")
        return
    effects = idea.load(lambda n: name_to_gene.get(n.upper()))
    filled = agreed = fixed = 0
    for frame in frames:
        for i, row in frame.iterrows():
            if row["interaction_type"] != "transcription":
                continue
            hit = effects.get((row["source_protein"], row["target_protein"]))
            if hit is None:
                continue
            effect, v, t = hit
            current = _effect(row["interaction_type"], row.get("effect", ""))
            text = idea.describe(effect, v, t)
            if current == effect:
                frame.at[i, "evidence"] = f"{row['evidence']}; {text}（記録の向きと一致）"
                agreed += 1
                continue
            frame.at[i, "effect"] = effect
            if current == "none":
                frame.at[i, "evidence"] = f"{row['evidence']}; 作用の向き: {text}"
                filled += 1
            else:
                frame.at[i, "evidence"] = f"{row['evidence']}; 作用の向き: {text}（記録の{EFFECT_JA[current]}を直した）"
                fixed += 1
                report.append(["IDEA と記録", row["source_protein"], row["target_protein"], "転写制御",
                               EFFECT_JA[effect], f"{text}。記録は{EFFECT_JA[current]}"])
    print(f"IDEA: 誘導直後の応答 {len(effects)} 組 → 作用不明に向きを付けた {filled} 本 / 記録と一致 {agreed} 本 / "
          f"記録と逆なので直した {fixed} 本")


def apply_deleteome(frames: list[pd.DataFrame], name_to_gene: dict[str, str], report: list,
                    curated: pd.DataFrame | None = None) -> None:
    """転写因子の破壊株データで、転写制御の作用の向きを補う（frames をその場で書き換える）。

    破壊で標的が大きく変わった組（1.7 倍以上・p < 0.05）について、作用不明なら推定した向きを付け、
    記録の向きと同じなら根拠欄にそう書き、食い違えば ⚠ を付けて一覧に出す（記録の向きは変えない）。
    交差検証（tools/evaluate_sign_inference.py）では、食い違いを作用不明にしたり反転したりしても
    確からしさがはっきり良くならなかったため、文献の記録を優先する。精査済みの関係は書き足すだけ。"""
    if not deleteome.DATA.exists():
        print("Deleteome: raw_data/DELETEOME/ にデータがないので使いません")
        return
    tfs = {a for f in frames + ([curated] if curated is not None else [])
           for a, t in zip(f["source_protein"], f["interaction_type"]) if t == "transcription"}
    gene_of = lambda sysname, name: name_to_gene.get(sysname.upper()) or name_to_gene.get(name.upper())  # noqa: E731
    changes = deleteome.load_changes(gene_of, wanted=tfs)
    filled = agreed = disagreed = 0
    for frame, is_curated in [(f, False) for f in frames] + ([(curated, True)] if curated is not None else []):
        for i, row in frame.iterrows():
            if row["interaction_type"] != "transcription":
                continue
            inf = deleteome.inferred_effect(changes, row["source_protein"], row["target_protein"])
            if inf is None:
                continue
            effect, m, p = inf
            current = _effect(row["interaction_type"], row.get("effect", ""))
            text = deleteome.describe(effect, m, p)
            if current == "none" and not is_curated:
                frame.at[i, "effect"] = effect
                frame.at[i, "evidence"] = f"{row['evidence']}; 作用の向き: {text}"
                filled += 1
            elif current == effect:
                frame.at[i, "evidence"] = f"{row['evidence']}; {text}（記録の向きと一致）"
                agreed += 1
            elif current != "none":
                frame.at[i, "evidence"] = (f"{row['evidence']}; ⚠ 破壊株データでは逆向き: {text}"
                                           f"（記録の{EFFECT_JA[current]}のまま）")
                disagreed += 1
                report.append(["破壊株データと記録", row["source_protein"], row["target_protein"], "転写制御",
                               EFFECT_JA[current], text])
    print(f"Deleteome: 転写因子の破壊株 {len(changes)} 件 → 作用不明に向きを付けた {filled} 本 / "
          f"記録と一致 {agreed} 本 / 記録と逆 {disagreed} 本（記録のまま・⚠ を付けた）")


def apply_condition_expression(frames: list[pd.DataFrame], name_to_gene: dict[str, str], report: list) -> None:
    """条件ごとの発現と転写因子の活性の目印で、作用不明の転写制御に向きを付ける（frames をその場で書き換える。
    tools/condition_expression.py）。記録の向きがある関係は変えず、一致・逆を数えて、逆のものは一覧に出す（⚠ は付けない）。"""
    if not condition_expression.available():
        print("条件ごとの発現: data/expression.db か data/activity_markers.csv がないので使いません")
        return
    from tor_app import conditions
    labels = conditions.labels()
    activity, z = condition_expression.load(lambda n: name_to_gene.get(str(n).upper()))
    # 転写因子ごとの関門: 記録の向きのある関係と 10 本以上比べられて、一致が 65% 未満の転写因子は使わない
    # （その転写因子の活性の目印と標的の動きが合っていない。間接の作用や目印の誤りが多いと考える）
    tally: dict[str, list[int]] = {}
    for frame in frames:
        for row in frame.itertuples(index=False):
            current = _effect(row.interaction_type, row.effect) if row.interaction_type == "transcription" else "none"
            if current == "none":
                continue
            inf = condition_expression.inferred_effect(activity, z, row.source_protein, row.target_protein)
            if inf:
                t = tally.setdefault(row.source_protein, [0, 0])
                t[0 if inf[0] == current else 1] += 1
    blocked = {tf for tf, (ok, ng) in tally.items()
               if ok + ng >= condition_expression.GATE_MIN and ok / (ok + ng) < condition_expression.GATE_AGREE}
    if blocked:
        print("条件ごとの発現: 記録との一致が低いので使わない転写因子: "
              + "、".join(f"{tf}（{tally[tf][0]}/{sum(tally[tf])}）" for tf in sorted(blocked)))
    filled = agreed = disagreed = 0
    for frame in frames:
        for i, row in frame.iterrows():
            if row["interaction_type"] != "transcription":
                continue
            if row["source_protein"] in blocked:
                continue
            inf = condition_expression.inferred_effect(activity, z, row["source_protein"], row["target_protein"])
            if inf is None:
                continue
            effect, score, conds = inf
            text = condition_expression.describe(effect, score, conds, labels)
            current = _effect(row["interaction_type"], row.get("effect", ""))
            if current == "none":
                frame.at[i, "effect"] = effect
                frame.at[i, "evidence"] = f"{row['evidence']}; 作用の向き: {text}"
                filled += 1
            elif current == effect:
                agreed += 1
            else:
                disagreed += 1
                report.append(["条件ごとの発現と記録", row["source_protein"], row["target_protein"], "転写制御",
                               EFFECT_JA[current], text])
    print(f"条件ごとの発現: 活性の分かる転写因子 {len(activity)} 個 → 作用不明に向きを付けた {filled} 本 / "
          f"記録と一致 {agreed} 本 / 記録と逆 {disagreed} 本（記録のまま・一覧に出した）")


YEASTRACT_CONDITIONS = RAW / "YEASTRACT" / "conditions"
# 細胞周期: 同調培養の発現の山の時期（tools/cell_cycle_peaks.py）と、時期の決まった転写因子（data/cell_cycle_tfs.csv）
CELL_CYCLE_PEAKS = RAW / "CELL_CYCLE" / "peak_phase.tsv"
CELL_CYCLE_TFS = ROOT / "data" / "cell_cycle_tfs.csv"
CELL_CYCLE_SCORE = 0.4   # これ以上の相関の遺伝子を「周期で発現が変わる」とする（約 1,060 遺伝子。Spellman 1998 は約 800）
CELL_CYCLE_KEYS = {"M/G1": ["mitotic_exit"], "G1": ["g1"], "S": ["s_phase"], "G2/M": ["g2", "m_phase"]}
# 文献の列 partner のサイクリン → 細胞周期の時期（Cdc28・Pho85 と組んで働く時期）。condition に時期が書かれていない行だけに、
# 「組むサイクリン … からの推定」として付ける（論文に書かれた時期と根拠欄で見分けられるように）
CYCLIN_PHASES = {"CLN1": ["g1"], "CLN2": ["g1"], "CLN3": ["g1"], "CLB5": ["s_phase"], "CLB6": ["s_phase"],
                 "CLB3": ["g2", "m_phase"], "CLB4": ["g2", "m_phase"], "CLB1": ["g2", "m_phase"], "CLB2": ["g2", "m_phase"],
                 "PCL1": ["g1"], "PCL2": ["g1"]}


def collect_conditions(name_to_gene: dict[str, str], report: list) -> dict[tuple[str, str, bool], dict[str, set[str]]]:
    """関係が働く条件を集める。返り値: {(上流, 下流, 転写制御だけか): {条件のキー: {出典}}}。

    - 文献の CSV（sample_data/literature/）の condition 列: その組のすべての関係に付ける
    - YEASTRACT+ の環境条件で絞った表（raw_data/YEASTRACT/conditions/ の *.tsv・*.txt）: その組の転写制御に付ける。
      ファイル名が環境条件の群・小群（例: "Stress - Heat.tsv"、"Carbon source quality_availability.tsv"）
    - 細胞周期の時期: data/cell_cycle_tfs.csv の転写因子 → 標的の転写制御で、標的の発現の山（raw_data/CELL_CYCLE/peak_phase.tsv）が
      転写因子の働く時期と同じものに、その時期を付ける（例: MBP1 → CLB5 は G1 期）
    条件の言葉は data/conditions.csv で当てはめる（tor_app/conditions.py）。
    """
    from tor_app import conditions
    found: dict[tuple[str, str, bool], dict[str, set[str]]] = {}

    def add(a, b, tx_only, keys, origin):
        for k in keys:
            found.setdefault((a, b, tx_only), {}).setdefault(k, set()).add(origin)

    unknown: dict[str, int] = {}
    cycle_keys = {c.key for c in conditions.load() if c.group == "細胞周期"}
    n_cyclin = 0
    for path in sorted(LITERATURE.glob("*.csv")):
        for r in csv_io.read_csv(str(path)).to_dict("records"):
            if str(r.get("status", "")).strip() == "reject":
                continue
            text = str(r.get("condition") or "").strip()
            text = "" if text == "nan" else text
            keys = conditions.classify(text, split_slash=False) if text else []   # "G1/S"・"S/G2" は両方の時期
            if text and not keys:
                unknown[text] = unknown.get(text, 0) + 1
            for a, b, _ in literature_pairs(r, name_to_gene):
                if keys:
                    add(a, b, False, keys, "文献")
                if not cycle_keys & set(keys):
                    for cyclin in str(r.get("partner") or "").replace(";", " ").upper().split():
                        if cyclin in CYCLIN_PHASES:
                            add(a, b, False, CYCLIN_PHASES[cyclin], f"組むサイクリン {cyclin} からの推定")
                            n_cyclin += 1
    if n_cyclin:
        print(f"細胞周期: 文献の組む相手（サイクリン）から時期を推定した行 {n_cyclin}")
    n_files = 0
    if YEASTRACT_CONDITIONS.exists():
        for path in sorted(list(YEASTRACT_CONDITIONS.glob("*.tsv")) + list(YEASTRACT_CONDITIONS.glob("*.txt"))):
            if path.name == "FETCHED.txt":   # 取得日の記録
                continue
            # ファイル名は「群 - 小群」（"/" は "_"）。tools/fetch_yeastract_conditions.py が付ける
            group, _, sub = path.stem.partition(" - ")
            group, sub = group.replace("_", "/"), sub.replace("_", "/")
            label = f"{group} / {sub}" if sub else group
            keys = conditions.classify_yeastract(group, sub)
            if not keys:
                # 当てはめていない小群（Unstressed log-phase growth はほぼ全部の組にあって絞れないので、わざと外している）
                report.append(["YEASTRACT+ の条件", "", "", "転写制御", "",
                               f"条件に当てていない（data/conditions.csv の yeastract 列にない）: {path.name}"])
                continue
            n_files += 1
            for tf, target in _read_pairs(path):
                a, b = name_to_gene.get(tf.upper()), name_to_gene.get(target.upper())
                if a and b:
                    add(a, b, True, keys, f"YEASTRACT+（{label}）")
    for text, n in sorted(unknown.items()):
        report.append(f"文献の条件: 当てはまる条件がない（data/conditions.csv に言葉を足す）: {text}（{n} 行）")
    print(f"条件: 文献と YEASTRACT+ の表 {n_files} 個から {len(found)} 組")
    if CELL_CYCLE_PEAKS.exists():
        peaks = pd.read_csv(CELL_CYCLE_PEAKS, sep="\t")
        peaks = peaks[peaks["score"] >= CELL_CYCLE_SCORE]
        tfs = pd.read_csv(CELL_CYCLE_TFS)
        n = 0
        for tf, phases in zip(tfs["tf"], tfs["phases"]):
            a = name_to_gene.get(tf.upper())
            for orf, phase in zip(peaks["orf"], peaks["phase"]):
                b = name_to_gene.get(orf.upper())
                if a and b and phase in phases.split(";"):
                    add(a, b, True, CELL_CYCLE_KEYS[phase], "細胞周期の発現（SPELL）")
                    n += 1
        print(f"細胞周期: 周期で発現が変わる遺伝子 {len(peaks)} 個 → 時期の合う転写因子との組 {n}（DB にある転写制御だけに付ける）")
    return found


def apply_conditions(db: Database, found: dict) -> None:
    """集めた条件を DB の interactions.conditions に書き、根拠欄に出典を足す。"""
    from tor_app import conditions
    labels = conditions.labels()
    with db.session() as s, s.begin():
        ids = {p.gene_name: p.id for p in s.scalars(select(Protein))}
        by_pair: dict[tuple[int, int], list] = {}
        for it in s.scalars(select(Interaction)):
            by_pair.setdefault((it.source_id, it.target_id), []).append(it)
        n = 0
        for (a, b, tx_only), keys in found.items():
            for it in by_pair.get((ids.get(a), ids.get(b)), []):
                if tx_only and it.interaction_type != "transcription":
                    continue
                have = set(conditions.split(it.conditions))
                new = set(keys) - have
                if not new:
                    continue
                it.conditions = conditions.join(have | new)
                # 出典を根拠欄に足す（文献の条件は、文献の取り込みで根拠欄に「条件: …」が入っているので足さない）
                origin = "; ".join(f"{labels.get(k, k)}（{'・'.join(sorted(keys[k] - {'文献'}))}）"
                                   for k in sorted(new) if keys[k] - {"文献"})
                if origin:
                    it.evidence = f"{it.evidence}; 条件: {origin}" if it.evidence else f"条件: {origin}"
                n += 1
    print(f"条件を付けた関係: {n} 本")


def apply_details(db: Database, name_to_gene: dict[str, str], report: list) -> None:
    """文献の CSV の性質の列（partner・site など。vocab.DETAILS）を、同じ組・同じ種類の関係に入れる（reject の行は使わない）。
    同じ関係に文献の行がいくつもあれば、値を重ねずに ";" でつなぐ。1 つの値であるべき列（necessity・strength）が食い違えば
    両方を残し、根拠欄に ⚠ を付けて一覧に出す。種類の合う関係がなければ、その組の関係が 1 本だけのときはそれに入れる。"""
    from tor_app.db.vocab import DETAILS, SINGLE_DETAILS
    columns = [name for name, _ in DETAILS]
    values: dict[tuple[str, str, str], dict[str, list[str]]] = {}
    for path in sorted(LITERATURE.glob("*.csv")):
        table = pd.read_csv(path, dtype=str, keep_default_na=False)
        if not set(columns) & set(table.columns):
            continue
        for _, r in table.iterrows():
            if r.get("status", "") == "reject":
                continue
            for a, b, _ in literature_pairs(r, name_to_gene):
                got = values.setdefault((a, b, (r.get("interaction_type", "") or "other").strip().lower()), {})
                for col in columns:
                    for v in str(r.get(col, "") or "").split(";"):
                        v = v.strip()
                        if v and v not in got.setdefault(col, []):
                            got[col].append(v)
    n, missing = 0, 0
    with db.session() as s, s.begin():
        genes = {p.id: p.gene_name for p in s.scalars(select(Protein))}
        by_pair: dict[tuple[str, str], list] = {}
        for it in s.scalars(select(Interaction)).unique():
            by_pair.setdefault((genes[it.source_id], genes[it.target_id]), []).append(it)
        for (a, b, kind), cols in values.items():
            if not any(cols.values()):
                continue
            pair = by_pair.get((a, b), [])
            its = [it for it in pair if it.interaction_type == kind] or (pair if len(pair) == 1 else [])
            if not its:
                missing += 1
                report.append(["文献の性質", a, b, kind, "", "種類の合う関係が DB にない（性質を入れていない）"])
                continue
            for it in its:
                for col, vals in cols.items():
                    if vals:
                        setattr(it, col, ";".join(vals))
                    if col in SINGLE_DETAILS and len(vals) > 1:
                        note = f"⚠ 文献で {col} が食い違う（{' / '.join(vals)}）"
                        it.evidence = f"{it.evidence}; {note}" if it.evidence else note
                        report.append(["文献の性質の食い違い", a, b, kind, "", note])
                n += 1
    print(f"文献の性質（組む相手・部位など）を入れた関係: {n} 本" + (f"（DB に合う関係がない組 {missing}）" if missing else ""))


def write_report(report: list) -> None:
    CONFLICT_REPORT.parent.mkdir(exist_ok=True)
    with open(CONFLICT_REPORT, "w", encoding="utf-8", newline="") as f:
        f.write("区分\t上流\t下流\t種類\t採用した作用\t内容\n")
        for row in report:
            f.write("\t".join(str(x) for x in row) + "\n")
    print(f"出典の食い違い・取り込まなかったもの {len(report)} 件: {CONFLICT_REPORT.relative_to(ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sgd", type=Path, default=RAW / "SGD_features.tab")
    parser.add_argument("--biogrid", type=Path, default=None,
                        help="出芽酵母の BioGRID TAB3 ファイル（省略時は raw_data/ から探す）")
    parser.add_argument("--curated-proteins", type=Path, default=ROOT / "sample_data" / "proteins.csv")
    parser.add_argument("--curated-interactions", type=Path, default=None,
                        help="手作業の関係の CSV（既定は取り込まない。sample_data/interactions.csv は構造確認用）")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "tor_pathway.db")
    args = parser.parse_args()

    biogrid = args.biogrid or next(iter(sorted(RAW.glob(
        "**/BIOGRID-ORGANISM-Saccharomyces_cerevisiae_S288c-*.tab3.txt"))), None)
    if biogrid is None:
        sys.exit("出芽酵母の BioGRID ファイルが見つかりません（raw_data/ に置いてください）")

    sgd = load_sgd(args.sgd)
    print(f"SGD: ORF {len(sgd)} 個")
    curated_p = csv_io.read_csv(str(args.curated_proteins))
    curated_i = csv_io.read_csv(str(args.curated_interactions)) if args.curated_interactions else \
        pd.DataFrame(columns=csv_io.INTERACTION_COLUMNS, dtype=object)
    curated_p, curated_i = standardize_curated(sgd, curated_p, curated_i)
    curated_i = curated_i.astype(object)   # 空のとき（既定）も文字列の列として扱う
    proteins = merge_curated(sgd, curated_p)
    systematic_to_gene = {s.upper(): g for s, g in zip(proteins["standard_name"], proteins["gene_name"]) if s}

    name_to_gene = {**{g.upper(): g for g in proteins["gene_name"]}, **systematic_to_gene}
    # 文献で確認した関係は、サンプル（精査済みとして扱ってきたもの）より優先する。同じ組のサンプルの記録は根拠欄に残す
    literature = load_literature(LITERATURE, name_to_gene) if LITERATURE.exists() else \
        pd.DataFrame(columns=csv_io.INTERACTION_COLUMNS, dtype=object)
    if len(literature):
        lit_pairs = {(a.upper(), b.upper()) for a, b in zip(literature["source_protein"], literature["target_protein"])}
        for i, row in literature.iterrows():
            same = curated_i[(curated_i["source_protein"].str.upper() == row["source_protein"].upper())
                             & (curated_i["target_protein"].str.upper() == row["target_protein"].upper())]
            for _, old in same.iterrows():
                literature.at[i, "evidence"] += f"; サンプルの記録: {old['interaction_type']}・{old.get('effect', '') or '既定'}"
        keep = [(a.upper(), b.upper()) not in lit_pairs
                for a, b in zip(curated_i["source_protein"], curated_i["target_protein"])]
        curated_i = pd.concat([literature, curated_i[keep]], ignore_index=True)
        print(f"文献で確認した関係: {len(literature)} 本（sample_data/literature/）")
    enzymatic = load_biogrid_enzymatic(biogrid, name_to_gene)
    biogrid_all = enzymatic.copy()   # 精査済みと重なる組も含めた BioGRID の記録（根拠欄に残すため）
    report: list[list[str]] = []     # docs/source_conflicts.tsv に出す行
    if LITERATURE.exists():
        report += literature_problems(LITERATURE, name_to_gene)
    # 精査済みの関係がある組は、同じ種類（転写制御かそれ以外か）の公開データを登録しない（二重の線を避ける）。
    # 種類の違う公開データ（文献はリン酸化で YEASTRACT+ は転写制御、など）は残す
    curated_pairs = {(r["source_protein"].upper(), r["target_protein"].upper(), kind_class(r["interaction_type"]))
                     for _, r in curated_i.iterrows()}
    # 文献で誤りと分かった組は、公開データからも取り込まない
    rejected = load_rejected(LITERATURE, name_to_gene) if LITERATURE.exists() else {}
    for (a, b, k), why in rejected.items():
        report.append(["文献で誤りと確認", a, b, "転写制御" if k == "tx" else "転写制御以外", "取り込まない", why])
    if rejected:
        print(f"文献で誤りと分かった組: {len({k[:2] for k in rejected})} 組（同じ種類の公開データからも取り込まない）")
    curated_pairs |= set(rejected)
    keep = [(a.upper(), b.upper(), kind_class(t)) not in curated_pairs
            for a, b, t in zip(enzymatic["source_protein"], enzymatic["target_protein"], enzymatic["interaction_type"])]
    print(f"  うち精査済みの関係と重なる {len(keep) - sum(keep)} 組は精査済みを優先")
    enzymatic = enzymatic[keep].reset_index(drop=True)

    # YEASTRACT+ の転写制御
    tx_rows = pd.DataFrame(columns=csv_io.INTERACTION_COLUMNS)
    tx_all = pd.DataFrame(columns=csv_io.INTERACTION_COLUMNS)
    if any((RAW / "YEASTRACT").glob("documented_and_*.tsv")):
        tx_all = load_yeastract(RAW / "YEASTRACT", name_to_gene)
        tx_rows = tx_all.copy()

    # 同じ組が複数の出典にあるとき、片方を捨てずに根拠欄へ両方を残す。作用が食い違えば印を付けて一覧に出す
    public = public_records(biogrid_all, tx_all)
    curated_i = annotate_curated(curated_i, public, report)
    taken = curated_pairs
    keep = [(a.upper(), b.upper(), "tx") not in taken for a, b in zip(tx_rows["source_protein"], tx_rows["target_protein"])]
    if len(tx_rows):
        print(f"  YEASTRACT+ のうち精査済みの関係と重なる {len(keep) - sum(keep)} 組は、精査済みの根拠欄に残す")
    tx_rows = tx_rows[keep].reset_index(drop=True)
    apply_idea([tx_rows], name_to_gene, report)
    if LITERATURE.exists():
        flags = load_flags(LITERATURE, name_to_gene)
        flagged = apply_flags([enzymatic, tx_rows], flags, report, last=False)
    go_rows = pd.DataFrame(columns=csv_io.INTERACTION_COLUMNS)
    if SGD_GO.exists():
        go_rows = apply_sgd_go([enzymatic, tx_rows], load_sgd_go(SGD_GO, name_to_gene), taken, report)
    ptm_rows = pd.DataFrame(columns=csv_io.INTERACTION_COLUMNS)
    if BIOGRID_PTM.exists():
        ptm_rows = merge_ptm(enzymatic, load_biogrid_ptm(BIOGRID_PTM, name_to_gene), taken)
    uniprot_rows = pd.DataFrame(columns=csv_io.INTERACTION_COLUMNS)
    if UNIPROT.exists():
        uniprot_records, uniprot_mods = load_uniprot(UNIPROT, name_to_gene)
        uniprot_mods = merge_ptm(pd.concat([enzymatic, ptm_rows], ignore_index=True), uniprot_mods, taken)
        uniprot_rows = pd.concat([uniprot_mods, apply_sgd_go([enzymatic, ptm_rows, tx_rows, go_rows], uniprot_records, taken,
                                                             report, label="UniProt")], ignore_index=True)
    gocam_rows = pd.DataFrame(columns=csv_io.INTERACTION_COLUMNS)
    if GO_CAM.exists():
        gocam_rows = apply_sgd_go([enzymatic, ptm_rows, uniprot_rows, tx_rows, go_rows], load_gocam(GO_CAM, name_to_gene),
                                  taken, report,
                                  label="GO-CAM")
    if LITERATURE.exists():   # 後からできた表（SGD GO・PTM・UniProt・GO-CAM）にも、まだ当てていない組の注記を当てる
        apply_flags([go_rows, ptm_rows, uniprot_rows, gocam_rows], {k: v for k, v in flags.items() if k not in flagged},
                    report, done=flagged)
    apply_deleteome([tx_rows, go_rows, gocam_rows], name_to_gene, report, curated_i)
    apply_condition_expression([tx_rows, go_rows, gocam_rows], name_to_gene, report)
    write_report(report)

    args.out.parent.mkdir(exist_ok=True)
    args.out.unlink(missing_ok=True)
    db = Database(str(args.out))
    print("遺伝子:", csv_io.import_proteins(db, proteins, False).summary())
    print("精査済みの関係:", csv_io.import_interactions(db, curated_i, False).summary())
    print("BioGRID の関係:", csv_io.import_interactions(db, enzymatic, False).summary())
    if len(tx_rows):
        print("YEASTRACT+ の関係:", csv_io.import_interactions(db, tx_rows, False).summary())
    if len(go_rows):
        print("SGD の GO の関係:", csv_io.import_interactions(db, go_rows, False).summary())
    if len(ptm_rows):
        print("BioGRID PTM の関係:", csv_io.import_interactions(db, ptm_rows, False).summary())
    if len(uniprot_rows):
        print("UniProt の関係:", csv_io.import_interactions(db, uniprot_rows, False).summary())
    if len(gocam_rows):
        print("GO-CAM の関係:", csv_io.import_interactions(db, gocam_rows, False).summary())
    apply_conditions(db, collect_conditions(name_to_gene, report))
    if LITERATURE.exists():
        apply_details(db, name_to_gene, report)
    write_complex_portal(name_to_gene)
    print("Complex Portal の複合体（カテゴリ）:", add_complex_portal_categories(db))
    print("複合体の色分け:", color_complexes(db))
    print("パラログ（カテゴリ）:", add_paralog_categories(db))
    write_report(report)
    db.engine.dispose()
    print(f"作成しました: {args.out}")


if __name__ == "__main__":
    main()
