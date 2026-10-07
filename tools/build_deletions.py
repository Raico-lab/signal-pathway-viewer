"""破壊株での実測（遺伝子を 1 つ壊した株での、野生型との比）を data/expression.db に入れる（表 strains・deletion・deletion_measured）。

- mRNA: Deleteome（Kemmeren et al. 2014 Cell、PMID 24766815）の 1,484 株。raw_data/DELETEOME/
  （tools/deleteome.py と同じファイル）。p < P_MAX かつ |log2 比| ≥ MIN_LOG2 の値だけ入れる（全部入れると 900 万件になる）。
  入っていない遺伝子は「測っていて大きくは変わらなかった」（測った遺伝子は表 deletion_measured）。壊した遺伝子自身は入れない。
- リン酸化: Bodenmiller et al. 2010 Sci Signal（PMID 21177495）のキナーゼ・ホスファターゼの破壊株 119 株。
  BioGRID-PTM の注記（"Deletion of protein Kinase ORF/NAME causes Fold Change logFC x"）から読む。論文が変化として
  報告した部位だけなので、入っていない部位は「変わらなかった」とも「測っていない」とも言えない。値は論文の logFC。
  同じ株・遺伝子・部位が複数の記録にあれば、絶対値の大きい方。

data/expression.db を tools/build_expression.py で作り直したときも、最後にこれを呼ぶ。単独でも実行できる（表を作り直す）。
使い方: .venv/bin/python tools/build_deletions.py
"""
import csv
import math
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "expression.db"
DELETEOME = ROOT / "raw_data" / "DELETEOME" / "deleteome_all_mutants_ex_wt_var_controls.txt"
BIOGRID_PTM = ROOT / "raw_data" / "BIOGRID-PTM" / "BIOGRID-PTM-5.0.262.yeast.tsv"
SGD_FEATURES = ROOT / "raw_data" / "SGD_features.tab"
P_MAX = 0.05
MIN_LOG2 = 0.5
DELETEOME_SOURCE = ("Kemmeren 2014", "24766815")
BODENMILLER_SOURCE = ("Bodenmiller 2010", "21177495")
NOTE = re.compile(r"Deletion of protein (Kinase|Phosphatase) ([A-Za-z0-9-]+)/([A-Za-z0-9-]+) causes Fold Change logFC (-?[\d.]+)")

SCHEMA = """
DROP TABLE IF EXISTS strains;
DROP TABLE IF EXISTS deletion;
DROP TABLE IF EXISTS deletion_measured;
CREATE TABLE strains (id INTEGER PRIMARY KEY, gene_id INTEGER, kind TEXT, label TEXT, source TEXT, pmid TEXT, detail TEXT);
CREATE TABLE deletion (strain_id INTEGER, gene_id INTEGER, site TEXT, value REAL, pval REAL);
CREATE TABLE deletion_measured (kind TEXT, gene_id INTEGER);
"""
INDEXES = """
CREATE INDEX deletion_strain ON deletion (strain_id);
CREATE INDEX deletion_gene ON deletion (gene_id);
CREATE INDEX strains_gene ON strains (gene_id);
"""


def lookup(con) -> dict[str, int]:
    """名前・ORF・SGD の別名（大文字）→ genes.id。Deleteome の株名には古い名前がある（cdk8 = SSN3、kem1 = XRN1 など）。
    別名が複数の遺伝子に当たるときは使わない。"""
    out = {}
    for i, orf, name in con.execute("SELECT id, orf, name FROM genes"):
        out[orf.upper()] = i
        if name:
            out.setdefault(name.upper(), i)
    if SGD_FEATURES.exists():
        alias: dict[str, set[int]] = {}
        with open(SGD_FEATURES, encoding="utf-8") as f:
            for row in csv.reader(f, delimiter="\t"):
                if len(row) > 5 and row[3].upper() in out:
                    for a in row[5].split("|"):
                        if a:
                            alias.setdefault(a.upper(), set()).add(out[row[3].upper()])
        for a, ids in alias.items():
            if len(ids) == 1 and a not in out:
                out[a] = next(iter(ids))
    return out


def build_deleteome(con, gid: dict[str, int], problems: list[str]) -> None:
    with open(DELETEOME, encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f, delimiter="\t")
        header, kinds = next(reader), next(reader)
        columns: dict[str, dict] = {}
        for i, (name, kind) in enumerate(zip(header, kinds)):
            if i >= 3 and "-del" in name:
                columns.setdefault(name, {})[kind] = i
        strains = []
        for name, c in sorted(columns.items()):
            gene = gid.get(name.split("-del")[0].strip().upper())
            if gene is None or "M" not in c or "p_value" not in c:
                problems.append(f"Deleteome: 遺伝子が分からない株 {name}")
                continue
            cur = con.execute("INSERT INTO strains (gene_id, kind, label, source, pmid, detail) VALUES (?, 'mrna', ?, ?, ?, ?)",
                              (gene, name, *DELETEOME_SOURCE,
                               f"Deleteome の {name}。p < {P_MAX} かつ |log2 比| ≥ {MIN_LOG2} の値だけ"))
            strains.append((cur.lastrowid, gene, c["M"], c["p_value"]))
        measured, batch = set(), []
        for row in reader:
            target = gid.get(row[1].strip().upper()) or gid.get(row[2].strip().upper())
            if target is None:
                continue
            measured.add(target)
            for sid, gene, mi, pi in strains:
                if target == gene:
                    continue
                try:
                    m, p = float(row[mi]), float(row[pi])
                except (ValueError, IndexError):
                    continue
                if p < P_MAX and abs(m) >= MIN_LOG2 and not math.isnan(m):
                    batch.append((sid, target, "", round(m, 3), p))
        con.executemany("INSERT INTO deletion VALUES (?, ?, ?, ?, ?)", batch)
        con.executemany("INSERT INTO deletion_measured VALUES ('mrna', ?)", [(g,) for g in sorted(measured)])
    print(f"Deleteome: {len(strains)} 株、値 {len(batch)} 件、測った遺伝子 {len(measured)}")


def build_bodenmiller(con, gid: dict[str, int], problems: list[str]) -> None:
    best: dict[tuple[int, int, str], float] = {}
    kinds: dict[int, str] = {}
    with open(BIOGRID_PTM, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if "Deletion of protein" not in r["Notes"]:
                continue
            target = gid.get(r["Systematic Name"].upper())
            if target is None:
                continue
            site = f"{r['Residue']}{r['Position']}"
            for kind, orf, name, fc in NOTE.findall(r["Notes"]):
                gene = gid.get(orf.upper()) or gid.get(name.upper())
                if gene is None:
                    problems.append(f"Bodenmiller: 遺伝子が分からない株 {orf}/{name}")
                    continue
                kinds[gene] = kind
                key = (gene, target, site)
                if key not in best or abs(float(fc)) > abs(best[key]):
                    best[key] = float(fc)
    sid = {}
    for gene, kind in sorted(kinds.items()):
        cur = con.execute("INSERT INTO strains (gene_id, kind, label, source, pmid, detail) VALUES (?, 'phospho', ?, ?, ?, ?)",
                          (gene, f"{'キナーゼ' if kind == 'Kinase' else 'ホスファターゼ'}の破壊株", *BODENMILLER_SOURCE,
                           "BioGRID-PTM の注記から。論文が変化として報告した部位だけ（値は論文の logFC）"))
        sid[gene] = cur.lastrowid
    con.executemany("INSERT INTO deletion VALUES (?, ?, ?, ?, NULL)",
                    [(sid[g], t, s, round(v, 3)) for (g, t, s), v in best.items() if g != t])
    print(f"Bodenmiller: {len(sid)} 株、部位の値 {len(best)} 件")


def build(con) -> list[str]:
    problems: list[str] = []
    con.executescript(SCHEMA)
    gid = lookup(con)
    if DELETEOME.exists():
        build_deleteome(con, gid, problems)
    else:
        problems.append("raw_data/DELETEOME/ がないので Deleteome は入れていない")
    if BIOGRID_PTM.exists():
        build_bodenmiller(con, gid, problems)
    else:
        problems.append("raw_data/BIOGRID-PTM/ がないので Bodenmiller は入れていない")
    con.executescript(INDEXES)
    return problems


def main() -> None:
    con = sqlite3.connect(OUT)
    problems = build(con)
    con.commit()
    con.execute("VACUUM")
    con.close()
    print(f"→ {OUT}（{OUT.stat().st_size / 1e6:.1f} MB）")
    for p in problems[:20]:
        print("  ", p)


if __name__ == "__main__":
    main()
