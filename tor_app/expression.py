"""条件ごとの発現・リン酸化・タンパク質量（data/expression.db。tools/build_expression.py が作る）を読む。

地図の DB とは別の読み取り専用のファイル。遺伝子は systematic 名（ORF）で引く。値はどれも対照（処理前・未処理）との
log2 の比。種類（kind）:
- mrna: mRNA の量（SGD の発現データ。条件ごとに測定の値の中央値）
- phospho: リン酸化（Leutert et al. 2023。条件ごとに、変化の最も大きい部位の値）
- protein: タンパク質の量（Leutert et al. 2023 の 30 条件）
"""
import sqlite3
from dataclasses import dataclass

from . import conditions
from .paths import resource_path

KINDS = {"mrna": "発現（mRNA）", "phospho": "リン酸化", "protein": "タンパク質量"}
SHORT = {"mrna": "mRNA", "phospho": "リン酸化", "protein": "タンパク質"}   # 説明欄の表の見出し（折り返さない短い名前）

_con: sqlite3.Connection | None = None
_missing = False


def _db() -> sqlite3.Connection | None:
    global _con, _missing
    if _con is None and not _missing:
        path = resource_path("data", "expression.db")
        if not path.exists():
            _missing = True
            return None
        _con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    return _con


def available() -> bool:
    return _db() is not None


def condition_keys() -> list[str]:
    """データのある条件のキー（data/conditions.csv の並び）。"""
    con = _db()
    if con is None:
        return []
    have = {c for (c,) in con.execute("SELECT DISTINCT condition FROM summary")}
    return [c.key for c in conditions.load() if c.key in have]


def kinds_for(condition: str) -> list[str]:
    con = _db()
    if con is None:
        return []
    have = {k for (k,) in con.execute("SELECT DISTINCT kind FROM summary WHERE condition = ?", (condition,))}
    return [k for k in KINDS if k in have]


def values(kind: str, condition: str) -> dict[str, tuple[float, str]]:
    """ORF → (値, 補足)。補足はリン酸化の部位（補正後も有意なら末尾に "*"）。"""
    con = _db()
    if con is None:
        return {}
    return {orf: (v, d) for orf, v, d in con.execute(
        "SELECT g.orf, s.value, s.detail FROM summary s JOIN genes g ON g.id = s.gene_id "
        "WHERE s.kind = ? AND s.condition = ?", (kind, condition))}


def measures(kind: str, condition: str) -> list[dict]:
    """その条件の測定の一覧（説明欄・Help 用）。"""
    con = _db()
    if con is None:
        return []
    return [dict(id=i, label=l, source=s, pmid=p, detail=d, flag=f or "")
            for i, l, s, p, d, f in con.execute(
                "SELECT id, label, source, pmid, detail, flag FROM measures WHERE kind = ? "
                "AND (';' || conditions || ';') LIKE ? ORDER BY id", (kind, f"%;{condition};%"))]


@dataclass
class Point:
    kind: str
    conditions: str
    label: str
    source: str
    pmid: str
    value: float
    site: str = ""
    pval: float | None = None
    padj: float | None = None


def gene_points(orf: str) -> list[Point]:
    """1 つの遺伝子の、すべての測定の値（mRNA・リン酸化の部位・タンパク質量）。"""
    con = _db()
    if con is None:
        return []
    row = con.execute("SELECT id FROM genes WHERE orf = ?", (orf.upper(),)).fetchone()
    if row is None:
        return []
    gid = row[0]
    out = [Point("mrna", c, l, s, p, v) for c, l, s, p, v in con.execute(
        "SELECT m.conditions, m.label, m.source, m.pmid, x.value FROM mrna x JOIN measures m ON m.id = x.measure_id "
        "WHERE x.gene_id = ? ORDER BY m.id", (gid,))]
    out += [Point("phospho", c, l, s, p, v, site, pv, pa) for c, l, s, p, site, v, pv, pa in con.execute(
        "SELECT m.conditions, m.label, m.source, m.pmid, x.site, x.value, x.pval, x.padj FROM phospho x "
        "JOIN measures m ON m.id = x.measure_id WHERE x.gene_id = ? ORDER BY m.id, x.site", (gid,))]
    out += [Point("protein", c, l, s, p, v, "", pv, pa) for c, l, s, p, v, pv, pa in con.execute(
        "SELECT m.conditions, m.label, m.source, m.pmid, x.value, x.pval, x.padj FROM protein x "
        "JOIN measures m ON m.id = x.measure_id WHERE x.gene_id = ? ORDER BY m.id", (gid,))]
    return out


def gene_summary(orf: str) -> dict[str, dict[str, tuple[float, str]]]:
    """1 つの遺伝子の、条件ごとのまとめ: 条件 → {種類: (値, 補足)}。"""
    con = _db()
    if con is None:
        return {}
    out: dict[str, dict[str, tuple[float, str]]] = {}
    for cond, kind, v, d in con.execute(
            "SELECT s.condition, s.kind, s.value, s.detail FROM summary s JOIN genes g ON g.id = s.gene_id "
            "WHERE g.orf = ?", (orf.upper(),)):
        out.setdefault(cond, {})[kind] = (v, d)
    return out


# ---- 破壊株での実測（tools/build_deletions.py。表 strains・deletion・deletion_measured） ----
DELETION_KINDS = {"del_mrna": "破壊株の mRNA", "del_phospho": "破壊株のリン酸化"}
_DEL_BASE = {"del_mrna": "mrna", "del_phospho": "phospho"}


def has_deletions() -> bool:
    con = _db()
    if con is None:
        return False
    return con.execute("SELECT 1 FROM sqlite_master WHERE name = 'strains'").fetchone() is not None


def deletion_strains() -> list[tuple[str, str, set[str]]]:
    """破壊株のある遺伝子: (ORF, 遺伝子名, 種類 {mrna, phospho})。遺伝子名の順。"""
    if not has_deletions():
        return []
    out: dict[str, list] = {}
    for orf, name, kind in _db().execute(
            "SELECT g.orf, g.name, s.kind FROM strains s JOIN genes g ON g.id = s.gene_id"):
        out.setdefault(orf, [orf, name or orf, set()])[2].add(kind)
    return sorted((tuple(v) for v in out.values()), key=lambda v: v[1].upper())


def deletion_values(kind: str, strain_orf: str) -> dict[str, tuple[float, str]]:
    """地図の色分け: 遺伝子 strain_orf を壊した株での、ORF → (値, 補足)。kind は del_mrna / del_phospho。
    mRNA は測った遺伝子のうち表にないものを 0（大きくは変わらなかった）にする。リン酸化は遺伝子ごとに絶対値の
    最も大きい部位（補足は部位）。壊した遺伝子自身は "壊した遺伝子" として値なしで返さない。"""
    base = _DEL_BASE.get(kind)
    if base is None or not has_deletions():
        return {}
    con = _db()
    out: dict[str, tuple[float, str]] = {}
    for orf, site, v in con.execute(
            "SELECT g.orf, d.site, d.value FROM deletion d JOIN strains s ON s.id = d.strain_id "
            "JOIN genes g ON g.id = d.gene_id JOIN genes k ON k.id = s.gene_id WHERE s.kind = ? AND k.orf = ?",
            (base, strain_orf.upper())):
        if orf not in out or abs(v) > abs(out[orf][0]):
            out[orf] = (v, site)
    if base == "mrna" and out is not None:
        has = con.execute("SELECT 1 FROM strains s JOIN genes k ON k.id = s.gene_id WHERE s.kind = 'mrna' AND k.orf = ?",
                          (strain_orf.upper(),)).fetchone()
        if has:
            for (orf,) in con.execute("SELECT g.orf FROM deletion_measured m JOIN genes g ON g.id = m.gene_id "
                                      "WHERE m.kind = 'mrna'"):
                out.setdefault(orf, (0.0, ""))
    out.pop(strain_orf.upper(), None)
    return out


@dataclass
class DeletionPoint:
    kind: str          # mrna / phospho
    strain: str        # 壊した遺伝子の ORF
    strain_name: str
    gene: str          # 変わった遺伝子の ORF
    gene_name: str
    site: str
    value: float
    source: str
    pmid: str


def _deletion_points(where: str, orf: str) -> list[DeletionPoint]:
    if not has_deletions():
        return []
    return [DeletionPoint(*r) for r in _db().execute(
        "SELECT s.kind, k.orf, COALESCE(k.name, k.orf), g.orf, COALESCE(g.name, g.orf), d.site, d.value, s.source, s.pmid "
        "FROM deletion d JOIN strains s ON s.id = d.strain_id JOIN genes g ON g.id = d.gene_id "
        f"JOIN genes k ON k.id = s.gene_id WHERE {where} = ? ORDER BY ABS(d.value) DESC", (orf.upper(),))]


def strain_changes(orf: str) -> list[DeletionPoint]:
    """遺伝子 orf を壊した株で変わった遺伝子（変化の大きい順）。"""
    return _deletion_points("k.orf", orf)


def changed_in(orf: str) -> list[DeletionPoint]:
    """遺伝子 orf が変わった破壊株（変化の大きい順）。"""
    return _deletion_points("g.orf", orf)


def strain_info(orf: str) -> list[dict]:
    """遺伝子 orf の破壊株の測定（種類・名前・出典）。"""
    if not has_deletions():
        return []
    return [dict(kind=k, label=l, source=s, pmid=p, detail=d) for k, l, s, p, d in _db().execute(
        "SELECT s.kind, s.label, s.source, s.pmid, s.detail FROM strains s JOIN genes k ON k.id = s.gene_id "
        "WHERE k.orf = ?", (orf.upper(),))]


_targets_cache: dict[str, dict[str, dict[str, float]]] = {}


def deletion_targets(kind: str) -> dict[str, dict[str, float]]:
    """壊した遺伝子の ORF → {変わった遺伝子の ORF: 値}（kind は mrna / phospho。リン酸化は遺伝子ごとに絶対値の最大）。
    破壊株タブで、今の地図の遺伝子が変わった株を見分けるのに使う（一度読んだら覚えておく）。"""
    if kind in _targets_cache:
        return _targets_cache[kind]
    out: dict[str, dict[str, float]] = {}
    if has_deletions():
        for strain, gene, v in _db().execute(
                "SELECT k.orf, g.orf, d.value FROM deletion d JOIN strains s ON s.id = d.strain_id "
                "JOIN genes g ON g.id = d.gene_id JOIN genes k ON k.id = s.gene_id WHERE s.kind = ?", (kind,)):
            cur = out.setdefault(strain, {})
            if gene not in cur or abs(v) > abs(cur[gene]):
                cur[gene] = v
    _targets_cache[kind] = out
    return out


def find_gene(text: str) -> tuple[str, str] | None:
    """遺伝子名か ORF（大文字・小文字は問わない）→ (ORF, 遺伝子名)。当たらなければ None。"""
    con = _db()
    t = text.strip().upper()
    if con is None or not t:
        return None
    row = con.execute("SELECT orf, COALESCE(name, orf) FROM genes WHERE UPPER(name) = ? OR orf = ? LIMIT 1", (t, t)).fetchone()
    return (row[0], row[1]) if row else None


def changed_genes(kind: str) -> set[str]:
    """どれかの破壊株で変化した遺伝子の ORF（kind は mrna / phospho）。"""
    return {g for genes in deletion_targets(kind).values() for g in genes}
