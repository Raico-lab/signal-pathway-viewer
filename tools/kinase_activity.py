"""リン酸化の測定（Leutert et al. 2023、57 条件）から、キナーゼ・ホスファターゼの条件ごとの活性を推定し（KSEA）、
条件ごとの活性の目印の表（data/activity_markers.csv）と比べる。

推定: 条件（測定）ごとに、単位 U の基質の部位の log2 比の平均を、全部位の平均・標準偏差と比べた z
  z = (基質の平均 − 全部位の平均) × √n / 全部位の標準偏差（Casado et al. 2013 の KSEA）
ホスファターゼの基質は符号を反転する（活性が上がれば基質のリン酸化は下がる）。基質の部位が MIN_SITES 未満なら出さない。
同じ条件のキーに測定が複数あれば（osmotic の NaCl・KCl・ソルビトールなど）、z を合わせる（Stouffer: Σz / √k）。

基質の部位の出典（単位ごとに合わせて使い、出典ごとの数字も出す）:
- BioGRID-PTM: 酵素と部位の記録（Relationship が kinase・phosphatase、Identity が catalytic）
- Leutert: 論文が使った一覧（raw_data/LEUTERT2023/zenodo/kinase_phos_substrate_yeast_2.csv。キナーゼを止めた実験で
  動いた部位 regulated_datasets が中心。TORC1 の部位はラパマイシンの実験から来ているので、ラパマイシンでの答え合わせは甘くなる）。
  PhosphoGRID 由来の phosphogrid_Regulated・phosphogrid（キナーゼの破壊で動いた部位。向きも直接か間接かも区別しない）は使わない:
  それだけで推定すると目印との一致 64%（PKA が炭素飢餓・非発酵性炭素源などで 4 回逆）、外すと全体の一致が 72% → 86%（2026-10-06）
- DB: tor_pathway.db のリン酸化・脱リン酸化の関係で部位（site）のあるもの

単位: 目印の表の単位（TORC1・PKA・MSN2/4 など）と、その genes 列の遺伝子。Leutert の一覧の単位名（TORC1・PKA・CLB2 など）は
ALIASES で合わせる。

答え合わせ: 目印の表の (単位, 条件, 変化) のうち、推定が出たもの。|z| ≥ Z_SIG で向きが合えば一致、逆なら逆、
それ以外は有意でない。no_change は |z| < Z_SIG なら一致とする。Leutert の測定は 5 分後（定常期は 24 時間）なので、
遅く動く目印は有意でないになりやすい。

使い方: .venv/bin/python tools/kinase_activity.py [--out docs/curation/kinase_activity.tsv]
出力: 単位 × 条件の z の表（5. の中心経路のモデルの入力と答え合わせに使う）
"""
import argparse
import csv
import math
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPRESSION = ROOT / "data" / "expression.db"
DB = ROOT / "data" / "tor_pathway.db"
MARKERS = ROOT / "data" / "activity_markers.csv"
LEUTERT_KS = ROOT / "raw_data" / "LEUTERT2023" / "zenodo" / "kinase_phos_substrate_yeast_2.csv"
BIOGRID_PTM = ROOT / "raw_data" / "BIOGRID-PTM" / "BIOGRID-PTM-5.0.262.yeast.tsv"
BIOGRID_REL = ROOT / "raw_data" / "BIOGRID-PTM" / "BIOGRID-PTM-RELATIONSHIPS-5.0.262.yeast.tsv"
MIN_SITES = 3
Z_SIG = 2.0
EXPECT = {"up": 1, "transient_up": 1, "down": -1, "transient_down": -1, "no_change": 0}
# Leutert の一覧の単位名 → 目印の表の単位名
ALIASES = {"CLB2": "CDC28", "CLN2": "CDC28", "TPK1": "PKA", "TPK2": "PKA", "TPK3": "PKA"}
LEUTERT_SKIP = {"phosphogrid_Regulated", "phosphogrid"}
SITE = re.compile(r"[STY]\d+")


def gene_names() -> tuple[dict[str, str], dict[str, str]]:
    """(ORF → 遺伝子名, 名前・ORF → 遺伝子名)。名前は大文字。"""
    con = sqlite3.connect(f"file:{EXPRESSION}?mode=ro", uri=True)
    orf_to, any_to = {}, {}
    for orf, name in con.execute("select orf, name from genes"):
        n = (name or orf).upper()
        orf_to[orf.upper()] = n
        any_to[orf.upper()] = n
        any_to[n] = n
    return orf_to, any_to


def load_units(any_to: dict) -> dict[str, set[str]]:
    """目印の表の単位 → 遺伝子。"""
    units: dict[str, set[str]] = defaultdict(set)
    with open(MARKERS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for g in (r["genes"] or r["unit"]).split(";"):
                if any_to.get(g.strip().upper()):
                    units[r["unit"]].add(any_to[g.strip().upper()])
    return units


def load_substrates(orf_to: dict, any_to: dict) -> dict[str, dict[tuple[str, str], tuple[int, set[str]]]]:
    """酵素の名前（遺伝子名、または Leutert の単位名）→ {(基質, 部位): (符号, 出典)}。符号はホスファターゼなら -1。"""
    out: dict = defaultdict(dict)

    def add(enzyme, gene, site, sign, source):
        cur = out[enzyme].get((gene, site))
        out[enzyme][(gene, site)] = (sign, (cur[1] if cur else set()) | {source})

    sites = {}
    with open(BIOGRID_PTM, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["Post Translational Modification"] == "Phosphorylation" and r["Residue"] in "STY":
                g = orf_to.get(r["Systematic Name"].upper())
                if g:
                    sites[r["#PTM ID"]] = (g, f"{r['Residue']}{r['Position']}")
    with open(BIOGRID_REL, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["Identity"] != "catalytic" or r["#PTM ID"] not in sites:
                continue
            enzyme = orf_to.get(r["Systematic Name"].upper())
            if enzyme and r["Relationship"] in ("kinase", "phosphatase"):
                add(enzyme, *sites[r["#PTM ID"]], 1 if r["Relationship"] == "kinase" else -1, "BioGRID-PTM")
    with open(LEUTERT_KS, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["marker"] != "kinase activity" or r["source_type"] in LEUTERT_SKIP:
                continue
            orf, res, pos = r["ref"].rsplit("_", 2)
            g = orf_to.get(orf.upper())
            k = r["kinase_activity"].upper()
            if g:
                add(ALIASES.get(k, any_to.get(k, k)), g, f"{res}{pos}", 1, f"Leutert:{r['source_type']}")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    for a, b, kind, site in con.execute(
            "select a.gene_name, b.gene_name, i.interaction_type, i.site from interactions i "
            "join proteins a on a.id = i.source_id join proteins b on b.id = i.target_id "
            "where i.interaction_type in ('phosphorylation', 'dephosphorylation') and i.site != ''"):
        for s in SITE.findall(site or ""):
            add(any_to.get(a.upper(), a.upper()), any_to.get(b.upper(), b.upper()), s,
                1 if kind == "phosphorylation" else -1, "DB")
    return out


def unit_substrates(units, substrates) -> dict[str, dict]:
    """単位 → {(基質, 部位): (符号, 出典)}。単位の遺伝子の基質と、同じ名前の Leutert の単位の基質を合わせる。
    基質が単位自身の遺伝子なら外す（自己リン酸化は活性の読みに使わない）。"""
    out = {}
    names = set(units) | {k for k in substrates if k not in units}
    for u in names:
        members = units.get(u, {u})
        merged = {}
        for enzyme in members | {u}:
            for key, (sign, src) in substrates.get(enzyme, {}).items():
                if key[0] in members:
                    continue
                old = merged.get(key)
                merged[key] = (sign, (old[1] if old else set()) | src)
        if merged:
            out[u] = merged
    return out


def load_phospho(any_to) -> tuple[dict[int, dict], dict[int, tuple[str, str]]]:
    """測定 → {(遺伝子, 部位): log2 比}、測定 → (条件のキー, 名前)。"""
    con = sqlite3.connect(f"file:{EXPRESSION}?mode=ro", uri=True)
    meta = {mid: (conds, label) for mid, conds, label in
            con.execute("select id, conditions, label from measures where kind = 'phospho'")}
    data: dict[int, dict] = defaultdict(dict)
    for mid, name, orf, site, value in con.execute(
            "select p.measure_id, g.name, g.orf, p.site, p.value from phospho p join genes g on g.id = p.gene_id"):
        if value is not None:
            data[mid][((name or orf).upper(), site)] = value
    return data, meta


def ksea(values: dict, subs: dict, use=lambda src: True) -> tuple[float, int] | None:
    """(z, 使った部位の数)。"""
    vs = list(values.values())
    mean = sum(vs) / len(vs)
    sd = (sum((v - mean) ** 2 for v in vs) / (len(vs) - 1)) ** 0.5
    hits = [sign * values[key] for key, (sign, src) in subs.items() if key in values and use(src)]
    if len(hits) < MIN_SITES or sd == 0:
        return None
    # ホスファターゼの基質は符号を反転したので、全体の平均も同じ向きで引く
    signs = [sign for key, (sign, src) in subs.items() if key in values and use(src)]
    expected = sum(s * mean for s in signs) / len(signs)
    return (sum(hits) / len(hits) - expected) * math.sqrt(len(hits)) / sd, len(hits)


def by_condition(data, meta, subs, use=lambda src: True) -> dict[str, dict[str, tuple[float, int, int]]]:
    """単位 → 条件のキー → (合わせた z, 部位の数の最大, 測定の数)。"""
    out: dict = defaultdict(dict)
    for u, s in subs.items():
        per_cond: dict[str, list] = defaultdict(list)
        for mid, values in data.items():
            r = ksea(values, s, use)
            if r:
                for c in meta[mid][0].split(";"):
                    per_cond[c].append(r)
        for c, rs in per_cond.items():
            out[u][c] = (sum(z for z, _ in rs) / math.sqrt(len(rs)), max(n for _, n in rs), len(rs))
    return out


def evaluate(table, markers) -> tuple[Counter, list]:
    tally, rows = Counter(), []
    for m in markers:
        exp = EXPECT.get(m["change"])
        hit = table.get(m["unit"], {}).get(m["condition"])
        if exp is None or hit is None:
            continue
        z = hit[0]
        if exp == 0:
            verdict = "一致" if abs(z) < Z_SIG else "逆"
        elif abs(z) < Z_SIG:
            verdict = "有意でない"
        else:
            verdict = "一致" if (z > 0) == (exp > 0) else "逆"
        tally[verdict] += 1
        rows.append((m, z, hit, verdict))
    return tally, rows


def fmt(t: Counter) -> str:
    sig = t["一致"] + t["逆"]
    rate = f"{t['一致'] / sig:.0%}" if sig else "-"
    return f"比べた {sum(t.values()):>3}  一致 {t['一致']:>3}  逆 {t['逆']:>3}  有意でない {t['有意でない']:>3}  （有意なもののうち一致 {rate}）"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "curation" / "kinase_activity.tsv")
    args = ap.parse_args()
    orf_to, any_to = gene_names()
    units = load_units(any_to)
    subs = unit_substrates(units, load_substrates(orf_to, any_to))
    data, meta = load_phospho(any_to)
    measured = set().union(*[set(v) for v in data.values()])
    with open(MARKERS, encoding="utf-8") as f:
        markers = list(csv.DictReader(f))
    # 同じ (単位, 条件) の目印は 1 つにまとめる（向きが食い違えば外す）
    uniq: dict = {}
    for m in markers:
        if m["change"] in EXPECT:
            k = (m["unit"], m["condition"])
            if k in uniq and EXPECT[uniq[k]["change"]] != EXPECT[m["change"]]:
                uniq[k] = None
            elif k not in uniq:
                uniq[k] = m
    markers = [m for m in uniq.values() if m]

    table = by_condition(data, meta, subs)
    covered = {u for u, s in subs.items() if sum(1 for k in s if k in measured) >= MIN_SITES}
    print(f"基質の部位のある単位 {len(subs)} 個、うち測定された部位が {MIN_SITES} 以上 {len(covered)} 個"
          f"（目印の表の単位では {len(covered & set(units))} / {len(units)} 個）")
    print(f"判定: |z| ≥ {Z_SIG}\n")
    tally, rows = evaluate(table, markers)
    print("■ 全体（出典を合わせて使う）")
    print("  " + fmt(tally))
    print("\n■ 基質の出典ごと（その出典の部位だけで推定）")
    for src in ["BioGRID-PTM", "Leutert", "DB"]:
        t, _ = evaluate(by_condition(data, meta, subs, lambda s, src=src: any(x.startswith(src) for x in s)), markers)
        print(f"  {src:<12}" + fmt(t))
    print("\n■ ラパマイシンの TORC1 を除く（Leutert の TORC1 の部位はラパマイシンの実験から）")
    print("  " + fmt(Counter(v for m, z, h, v in rows if not (m["unit"] == "TORC1" and m["condition"] == "rapamycin"))))

    print("\n■ 単位ごと")
    per_unit = defaultdict(Counter)
    for m, z, h, v in rows:
        per_unit[m["unit"]][v] += 1
    for u, t in sorted(per_unit.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  {u:<14}" + fmt(t))

    print("\n■ 逆になった目印")
    for m, z, h, v in rows:
        if v == "逆":
            print(f"  {m['unit']:<12} {m['condition']:<20} 目印 {m['change']:<14} z = {z:+.1f}（部位 {h[1]}、測定 {h[2]}）"
                  f"  {m['marker'][:40]}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["unit", "condition", "z", "sites", "measures", "marker_change", "verdict"])
        expect = {(m["unit"], m["condition"]): (m["change"], v) for m, z, h, v in rows}
        for u in sorted(table):
            for c, (z, n, k) in sorted(table[u].items()):
                mc, v = expect.get((u, c), ("", ""))
                w.writerow([u, c, f"{z:.2f}", n, k, mc, v])
    print(f"\n単位 × 条件の表: {args.out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
