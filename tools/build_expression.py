"""条件ごとの発現・リン酸化・タンパク質量の DB（data/expression.db）を作る。地図の DB（tor_pathway.db）とは別のファイル。

情報源と対応表:
- mRNA: SGD の発現データ（SPELL、raw_data/SPELL/datasets/）。対応表 data/expression_sources.csv の 1 行が 1 つの測定
  （study・file: 研究のフォルダと PCL、conditions: 条件のキー（";" 区切り）、treat・control: 処理・対照の列名に当てる
  正規表現（大文字小文字は区別しない）、sign: 比の向きが逆（未処理/処理）なら -1、label: 画面に出す説明）。
  値は 処理の列の平均 − 対照の列の平均（control が空なら処理の列の平均。2 色のアレイで、すでに対照との log 比のもの）。
  1 色のアレイで値が対数でなければ（中央値が LOG_MIN 以上）log2 に直してから引く。
- リン酸化・タンパク質量: Leutert et al. 2023 Nat Struct Mol Biol（PMID 37845410、raw_data/LEUTERT2023/）。
  補足表 6（部位ごとの変化、LIMMA）と補足表 4 の protein_diff_reg（30 条件）。対応表 data/phospho_sources.csv
  （treatment: 論文の条件 ID、conditions、label）。「NA」（アミノ酸なし）は表 6 で欠損として書き出されていて使えない。

向きの点検: リボソームタンパク質（RPL・RPS）の遺伝子の平均の変化（列 rp_mean）。ストレス・飢餓・ラパマイシンでは
下がるのがふつう（RP_DOWN の条件）、グルコースの添加では上がる。逆なら flag に書き、作り終わりに一覧を出す。
ケモスタットの定常状態どうしの比較（弱酸・低温など）は増殖速度がそろえてあるので点検しない。

条件ごとのまとめ（表 summary）: 遺伝子ごとに、その条件の測定の値の中央値（mRNA・タンパク質量）。リン酸化は、
その条件のどれかの測定で補正前の p < PVAL になった部位のうち、変化の平均の絶対値が最も大きい部位の値（detail に部位。
補正後の p < PADJ でもあれば部位の後に "*"）。測られていても当てはまる部位がなければ 0。
Leutert の測定は 5 分後で反復が少なく、ラパマイシンでの SCH9・NPR1 の脱リン酸化も補正後の p は 0.1〜0.4 になるため、
補正前の p で拾い、補正後でも有意かは印で分ける。

遺伝子は表 genes の番号で持つ（orf は systematic 名、name は遺伝子名）。値の表は遺伝子の順に並べて持つ（WITHOUT ROWID）。

使い方: .venv/bin/python tools/build_expression.py
"""
import csv
import math
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xlsx_rows import rows as xlsx_rows  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw_data"
SPELL = RAW / "SPELL" / "datasets"
LEUTERT = RAW / "LEUTERT2023"
FEATURES = RAW / "SGD_features.tab"
OUT = ROOT / "data" / "expression.db"
MRNA_SOURCES = ROOT / "data" / "expression_sources.csv"
PHOSPHO_SOURCES = ROOT / "data" / "phospho_sources.csv"
LEUTERT_PMID = "37845410"

LOG_MIN = 30.0   # 値の中央値がこれ以上なら対数でないとみなす
PVAL = 0.05
PADJ = 0.05
RP_DOWN = {"heat", "osmotic", "oxidative", "er", "reductive", "nitrogen_starvation", "amino_acid_starvation",
           "carbon_starvation", "glucose_limitation", "rapamycin", "stationary", "diauxic_shift", "metal_stress",
           "alkaline", "genotoxic"}
RP_UP = {"glucose_repletion"}
RP_LIMIT = 0.3

SCHEMA = """
CREATE TABLE info (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE genes (id INTEGER PRIMARY KEY, orf TEXT UNIQUE, name TEXT);
CREATE TABLE measures (id INTEGER PRIMARY KEY, kind TEXT, conditions TEXT, label TEXT, source TEXT, pmid TEXT,
                       detail TEXT, rp_mean REAL, flag TEXT DEFAULT '');
CREATE TABLE mrna (gene_id INTEGER, measure_id INTEGER, value REAL, PRIMARY KEY (gene_id, measure_id)) WITHOUT ROWID;
CREATE TABLE phospho (gene_id INTEGER, measure_id INTEGER, site TEXT, value REAL, pval REAL, padj REAL,
                      PRIMARY KEY (gene_id, measure_id, site)) WITHOUT ROWID;
CREATE TABLE protein (gene_id INTEGER, measure_id INTEGER, value REAL, pval REAL, padj REAL,
                      PRIMARY KEY (gene_id, measure_id)) WITHOUT ROWID;
CREATE TABLE summary (kind TEXT, condition TEXT, gene_id INTEGER, value REAL, n INTEGER, detail TEXT,
                      PRIMARY KEY (kind, condition, gene_id)) WITHOUT ROWID;
"""


def orf_names() -> dict[str, str]:
    """systematic 名 → 遺伝子名（なければ systematic 名）。"""
    out = {}
    with open(FEATURES, encoding="utf-8") as f:
        for c in csv.reader(f, delimiter="\t"):
            if len(c) > 4 and c[1] == "ORF" and c[3]:
                out[c[3].upper()] = c[4] or c[3]
    return out


def read_pcl(path: Path) -> tuple[list[str], dict[str, list[float | None]]]:
    with open(path, encoding="utf-8", errors="replace") as f:
        cols = [c.strip() for c in f.readline().rstrip("\r\n").split("\t")[3:]]
        data = {}
        for line in f:
            p = line.rstrip("\r\n").split("\t")
            if len(p) < 4 or p[0] in ("EWEIGHT", ""):
                continue
            vals = []
            for x in p[3:3 + len(cols)]:
                try:
                    v = float(x)
                    vals.append(v if math.isfinite(v) else None)
                except ValueError:
                    vals.append(None)
            vals += [None] * (len(cols) - len(vals))
            data[p[0].strip().upper()] = vals
    return cols, data


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def find_pcl(study: str, name: str) -> Path | None:
    hits = sorted((SPELL / study).rglob(name)) if (SPELL / study).is_dir() else []
    return hits[0] if hits else None


def rp_check(conds: list[str], rp_mean: float | None) -> str:
    if rp_mean is None:
        return ""
    if any(c in RP_DOWN for c in conds) and rp_mean > RP_LIMIT:
        return f"リボソームタンパク質が上がっている（{rp_mean:+.2f}）"
    if any(c in RP_UP for c in conds) and rp_mean < -RP_LIMIT:
        return f"リボソームタンパク質が下がっている（{rp_mean:+.2f}）"
    return ""


def build_mrna(con, gid, names, problems) -> None:
    rp = {o for o, n in names.items() if re.match(r"RP[LS]\d", n)}
    with open(MRNA_SOURCES, encoding="utf-8") as f:
        specs = list(csv.DictReader(f))
    for s in specs:
        path = find_pcl(s["study"], s["file"])
        if path is None:
            problems.append(f"見つからない: {s['study']}/{s['file']}")
            continue
        cols, data = read_pcl(path)
        tr = [i for i, c in enumerate(cols) if re.search(s["treat"], c, re.I)]
        ct = [i for i, c in enumerate(cols) if s["control"] and re.search(s["control"], c, re.I) and i not in tr]
        if not tr or (s["control"] and not ct):
            problems.append(f"列が当たらない: {s['file']} treat={len(tr)} control={len(ct)}")
            continue
        used = [v for vals in data.values() for i in tr + ct if (v := vals[i]) is not None]
        to_log = bool(used) and statistics.median(used) >= LOG_MIN
        sign = float(s.get("sign") or 1)
        values = {}
        for orf, vals in data.items():
            if orf not in gid:
                continue
            row = [math.log2(max(v, 1.0)) if (v is not None and to_log) else v for v in vals]
            t = mean(row[i] for i in tr)
            if t is None:
                continue
            if ct:
                c = mean(row[i] for i in ct)
                if c is None:
                    continue
                t -= c
            values[orf] = sign * t
        conds = [k for k in s["conditions"].split(";") if k]
        rp_mean = mean(values.get(o) for o in rp)
        flag = rp_check(conds, rp_mean)
        pmid = (re.search(r"PMID_(\d+)", s["study"]) or [None, ""])[1]
        detail = (f"SGD SPELL: {s['study']} / {s['file']}。処理 {len(tr)} 列"
                  + (f"、対照 {len(ct)} 列の平均との差" if ct else "（対照との比）")
                  + ("、log2 に変換" if to_log else "") + ("、比の向きを反転" if sign < 0 else ""))
        cur = con.execute("INSERT INTO measures (kind, conditions, label, source, pmid, detail, rp_mean, flag) "
                          "VALUES ('mrna', ?, ?, ?, ?, ?, ?, ?)",
                          (";".join(conds), s["label"], s["study"].split("_PMID")[0].replace("_", " "), pmid, detail,
                           rp_mean, flag))
        con.executemany("INSERT INTO mrna VALUES (?, ?, ?)",
                        [(gid[o], cur.lastrowid, round(v, 4)) for o, v in values.items()])
        if flag:
            problems.append(f"向き？ {s['study']} {s['file']} [{s['label']}]: {flag}")


def build_leutert(con, gid, problems) -> None:
    with open(PHOSPHO_SOURCES, encoding="utf-8") as f:
        specs = {r["treatment"]: r for r in csv.DictReader(f)}
    measure = {}
    for kind in ("phospho", "protein"):
        for t, s in specs.items():
            cur = con.execute("INSERT INTO measures (kind, conditions, label, source, pmid, detail) VALUES (?, ?, ?, ?, ?, ?)",
                              (kind, s["conditions"], s["label"], "Leutert 2023", LEUTERT_PMID,
                               f"Leutert 2023 の条件 {t}。5 分（定常期は 24 時間）、未処理との比（LIMMA）"))
            measure[kind, t] = cur.lastrowid
    for kind, table, sheet in (("phospho", "Supplementary_Table_6.xlsx", "p_site_diff_reg"),
                               ("protein", "Supplementary_Table_4.xlsx", "protein_diff_reg")):
        it = xlsx_rows(LEUTERT / table, sheet)
        ix = {h: i for i, h in enumerate(next(it))}
        batch = []
        for r in it:
            t, orf, fc = r[ix["treatment_id"]], str(r[ix["systematic_name"]] or "").upper(), r[ix["fc_log2"]]
            if t not in specs or orf not in gid or fc is None:
                continue
            pval = r[ix["p_value"]] if r[ix["p_value"]] is not None else 1.0
            padj = r[ix["adj_p_value"]] if r[ix["adj_p_value"]] is not None else 1.0
            key = (gid[orf], measure[kind, t])
            batch.append(key + ((r[ix["p_site"]],) if kind == "phospho" else ()) + (round(fc, 4), pval, padj))
            if len(batch) >= 50000:
                con.executemany(f"INSERT OR IGNORE INTO {kind} VALUES ({','.join('?' * len(batch[0]))})", batch)
                batch = []
        if batch:
            con.executemany(f"INSERT OR IGNORE INTO {kind} VALUES ({','.join('?' * len(batch[0]))})", batch)
        missing = [t for t in specs if not con.execute(f"SELECT 1 FROM {kind} WHERE measure_id = ? LIMIT 1",
                                                       (measure[kind, t],)).fetchone()]
        con.executemany("DELETE FROM measures WHERE id = ?", [(measure[kind, t],) for t in missing])
        if kind == "phospho" and missing:
            problems.append(f"Leutert の表 6 にない条件: {', '.join(missing)}")


def build_summary(con) -> None:
    by_cond = defaultdict(list)
    for mid, kind, conds in con.execute("SELECT id, kind, conditions FROM measures"):
        for c in conds.split(";"):
            if c:
                by_cond[kind, c].append(mid)
    out = []
    for (kind, cond), mids in by_cond.items():
        q = ",".join("?" * len(mids))
        if kind in ("mrna", "protein"):
            vals = defaultdict(list)
            for g, v in con.execute(f"SELECT gene_id, value FROM {kind} WHERE measure_id IN ({q})", mids):
                vals[g].append(v)
            out += [(kind, cond, g, round(statistics.median(v), 4), len(v), "") for g, v in vals.items()]
        else:
            sites, sig, strict = defaultdict(list), set(), set()
            for g, site, v, pval, padj in con.execute(
                    f"SELECT gene_id, site, value, pval, padj FROM phospho WHERE measure_id IN ({q})", mids):
                sites[g, site].append(v)
                if pval < PVAL:
                    sig.add((g, site))
                if padj < PADJ:
                    strict.add((g, site))
            best = {}
            for (g, site), v in sites.items():
                m = sum(v) / len(v)
                if (g, site) in sig and (g not in best or not best[g][1] or abs(m) > abs(best[g][0])):
                    best[g] = (m, site + ("*" if (g, site) in strict else ""))
                best.setdefault(g, (0.0, ""))
            out += [(kind, cond, g, round(m, 4), len(mids), site) for g, (m, site) in best.items()]
    con.executemany("INSERT INTO summary VALUES (?, ?, ?, ?, ?, ?)", out)


def main() -> None:
    names = orf_names()
    tmp = OUT.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO genes (orf, name) VALUES (?, ?)", sorted(names.items()))
    gid = {orf: i for i, orf in con.execute("SELECT id, orf FROM genes")}
    problems: list[str] = []
    build_mrna(con, gid, names, problems)
    build_leutert(con, gid, problems)
    build_summary(con)
    import build_deletions   # 破壊株での実測（Deleteome・Bodenmiller）も同じファイルに入れる
    problems += build_deletions.build(con)
    con.executemany("INSERT INTO info VALUES (?, ?)", [("built", date.today().isoformat()),
                                                       ("sources", "SGD SPELL (2024-04); Leutert et al. 2023 NSMB (PMID 37845410)")])
    con.commit()
    for kind, n in con.execute("SELECT kind, COUNT(*) FROM measures GROUP BY kind"):
        print(f"{kind}: 測定 {n}")
    for kind, n in con.execute("SELECT kind, COUNT(DISTINCT condition) FROM summary GROUP BY kind"):
        print(f"{kind}: 条件 {n}")
    con.execute("VACUUM")
    con.close()
    tmp.replace(OUT)
    print(f"→ {OUT}（{OUT.stat().st_size / 1e6:.1f} MB）")
    for p in problems:
        print("  ", p)


if __name__ == "__main__":
    main()
