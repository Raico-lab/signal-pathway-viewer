"""出芽酵母の細胞周期の論理モデル（Li et al. 2004 PNAS, PMID 15037758 の 11 単位の閾値ブールネットワーク）。

決まり: 単位 i の次の状態は、入力の和 Σ a_ij x_j が正なら 1、負なら 0、0 ならそのまま。
促進 a_ij = A_G、抑制 a_ij = A_R（-1）。自己分解する単位（SELF_DEGRADE）は、入力の和が 0 で 1 だったら次に 0 になる
（寿命 t_d = 1）。全単位を同時に更新する。

つながり（EDGES）は Li 2004 の図 1 のもの。原著の本文は手元で読めなかったので（PNAS は 403、PMC は要旨だけ）、
原著の結果を表で書き直した無料公開の論文 2 本の表を、すべて再現することで確かめる（--verify）:
- Tran et al. 2013 Front Genet（PMID 24376454）表 1: 止まる状態ごとの流れ込む状態の数（a_g = 1・2・3）、
  表 2: Swi5 の自己分解をなくしたとき
- Islam et al. 2021（PMID 34535069）表 2〜5: Sic1・Cln3 と Cln1,2・Clb1,2・SBF と MBF を壊したときの状態の移り変わり
（使った本文: Google Drive の TorPathway調査/2026-10-06_細胞周期モデル/）

使い方:
  .venv/bin/python tools/cell_cycle_model.py --verify       # 論文の表と照らし合わせる
  .venv/bin/python tools/cell_cycle_model.py                # 野生型の 1 周期（START から）と、止まる状態
  .venv/bin/python tools/cell_cycle_model.py --ko Sic1      # 単位を壊したときの移り変わり
  .venv/bin/python tools/cell_cycle_model.py --db           # つながりを DB（tor_pathway.db）の関係と照らし合わせる
"""
import argparse
import sqlite3
from collections import Counter
from itertools import product
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "tor_pathway.db"

NODES = ["Cln3", "MBF", "SBF", "Cln1,2", "Cdh1", "Swi5", "Cdc20,14", "Clb5,6", "Sic1", "Clb1,2", "Mcm1,SFF"]
# (上流, 下流, +1 促進 / -1 抑制)
EDGES = [
    ("Cln3", "MBF", 1), ("Cln3", "SBF", 1),
    ("MBF", "Clb5,6", 1),
    ("SBF", "Cln1,2", 1),
    ("Cln1,2", "Sic1", -1), ("Cln1,2", "Cdh1", -1),
    ("Clb5,6", "Sic1", -1), ("Clb5,6", "Cdh1", -1), ("Clb5,6", "Mcm1,SFF", 1), ("Clb5,6", "Clb1,2", 1),
    ("Clb1,2", "MBF", -1), ("Clb1,2", "SBF", -1), ("Clb1,2", "Sic1", -1), ("Clb1,2", "Cdh1", -1),
    ("Clb1,2", "Swi5", -1), ("Clb1,2", "Cdc20,14", 1), ("Clb1,2", "Mcm1,SFF", 1),
    ("Mcm1,SFF", "Clb1,2", 1), ("Mcm1,SFF", "Cdc20,14", 1), ("Mcm1,SFF", "Swi5", 1),
    ("Cdc20,14", "Clb5,6", -1), ("Cdc20,14", "Clb1,2", -1), ("Cdc20,14", "Swi5", 1),
    ("Cdc20,14", "Sic1", 1), ("Cdc20,14", "Cdh1", 1),
    ("Swi5", "Sic1", 1),
    ("Sic1", "Clb5,6", -1), ("Sic1", "Clb1,2", -1),
    ("Cdh1", "Clb1,2", -1),
]
SELF_DEGRADE = {"Cln3", "Cln1,2", "Swi5", "Cdc20,14", "Mcm1,SFF"}
# 単位 → 遺伝子。サイクリンの単位は、DB では CDC28 から出る関係（partner にサイクリン）としても入っている
GENES = {"Cln3": ["CLN3"], "MBF": ["MBP1", "SWI6"], "SBF": ["SWI4", "SWI6"], "Cln1,2": ["CLN1", "CLN2"],
         "Cdh1": ["CDH1"], "Swi5": ["SWI5"], "Cdc20,14": ["CDC20", "CDC14"], "Clb5,6": ["CLB5", "CLB6"],
         "Sic1": ["SIC1"], "Clb1,2": ["CLB1", "CLB2"], "Mcm1,SFF": ["MCM1", "FKH2", "NDD1"]}
CYCLINS = {"Cln3", "Cln1,2", "Clb5,6", "Clb1,2"}
START = {"Cln3": 1, "Cdh1": 1, "Sic1": 1}   # G1 で細胞が大きくなり Cln3 が現れた状態


def step(state: tuple, a_g: float = 1, a_r: float = -1, degrade=SELF_DEGRADE, ko: set = frozenset()) -> tuple:
    x = dict(zip(NODES, state))
    total = Counter()
    for a, b, s in EDGES:
        if x[a]:
            total[b] += a_g if s > 0 else a_r
    out = []
    for n in NODES:
        if n in ko:
            out.append(0)
        elif total[n] > 0:
            out.append(1)
        elif total[n] < 0:
            out.append(0)
        else:
            out.append(0 if n in degrade else x[n])
    return tuple(out)


def attractors(**kw) -> Counter:
    """止まる状態（固定点）→ 流れ込む初期状態の数。周期的なものは周期の状態の組で数える。"""
    basins = Counter()
    for state in product([0, 1], repeat=len(NODES)):
        seen = []
        s = state
        while s not in seen:
            seen.append(s)
            s = step(s, **kw)
        cycle = tuple(sorted(seen[seen.index(s):]))
        basins[cycle] += 1
    return basins


def trajectory(start: dict, steps: int = 20, **kw) -> list[tuple]:
    s = tuple(start.get(n, 0) for n in NODES)
    ko = kw.get("ko", set())
    s = tuple(0 if n in ko else v for n, v in zip(NODES, s))
    out = [s]
    for _ in range(steps):
        s = step(s, **kw)
        if s == out[-1]:
            break
        out.append(s)
    return out


def show(states: list[tuple]) -> None:
    print("      " + " ".join(f"{n:>8}" for n in NODES))
    for i, s in enumerate(states, 1):
        print(f"  {i:>3} " + " ".join(f"{v:>8}" for v in s))


# --- 論文の表（--verify） ---
TRAN_ORDER = ["Cln3", "MBF", "Clb5,6", "Mcm1,SFF", "Swi5", "Cdc20,14", "Cdh1", "Cln1,2", "SBF", "Sic1", "Clb1,2"]
TRAN = {   # Tran et al. 2013 表 1（a_g ごと）・表 2（Swi5 の自己分解なし）: (流れ込む数, 状態)
    "表 1A（a_g = 1）": (dict(a_g=1), [(1764, "00000010010"), (151, "00000001100"), (109, "01000010010"), (9, "00000000010"),
                                     (7, "01000000010"), (7, "00000000000"), (1, "00000010000")]),
    "表 1B（a_g = 2）": (dict(a_g=2), [(1978, "00000010010"), (57, "00000001100"), (7, "00000000010"), (5, "00000000000"),
                                     (1, "00000010000")]),
    "表 1C（a_g = 3）": (dict(a_g=3), [(1936, "00011110011"), (59, "00000010010"), (40, "00000001100"), (7, "00000000010"),
                                     (5, "00000000000"), (1, "00000010000")]),
    "表 2（Swi5 の自己分解なし）": (dict(degrade=SELF_DEGRADE - {"Swi5"}), [
        (1383, "00001010010"), (380, "01001001110"), (139, "00001001110"), (108, "01001010010"), (10, "00001000010"),
        (8, "00000001100"), (6, "01001000010"), (5, "00000000000"), (4, "00001001100"), (1, "00000010000"),
        (1, "00000000010"), (1, "01000000010"), (1, "01000010010"), (1, "00000010010")]),
}
ISLAM_ORDER = ["Cln3", "MBF", "SBF", "Cln1,2", "Cdh1", "Swi5", "Cdc20,14", "Clb5,6", "Sic1", "Clb1,2", "Mcm1,SFF"]
ISLAM = {   # Islam et al. 2021 表 2〜5: (壊す単位, 移り変わり)
    "表 2（Sic1 を壊す）": ({"Sic1"}, ["10001000000", "01101000000", "01111001000", "01110001001", "01110111011",
                                    "00010111011", "00000110011"]),
    "表 3（Cln3 と Cln1,2 を壊す）": ({"Cln3", "Cln1,2"}, ["00001000100"]),
    "表 4（Clb1,2 を壊す）": ({"Clb1,2"}, ["10001000100", "01101000100", "01111000100", "01110000000", "01110001000",
                                      "01110001001", "01110111001"]),
    "表 5（SBF と MBF を壊す）": ({"SBF", "MBF"}, ["10001000100", "00001000100"]),
}


def reorder(bits: str, order: list[str]) -> tuple:
    d = dict(zip(order, map(int, bits)))
    return tuple(d[n] for n in NODES)


def verify() -> bool:
    ok_all = True
    for name, (kw, rows) in TRAN.items():
        got = attractors(**kw)
        fixed = {c[0]: n for c, n in got.items() if len(c) == 1}
        cycles = [c for c in got if len(c) > 1]
        expect = {reorder(b, TRAN_ORDER): n for n, b in rows}
        ok = fixed == expect and not cycles
        ok_all &= ok
        print(f"  {'一致' if ok else '不一致'}  Tran 2013 {name}: 止まる状態 {len(fixed)} 個（論文 {len(expect)} 個）")
        if not ok:
            for s in set(fixed) | set(expect):
                if fixed.get(s) != expect.get(s):
                    print(f"      {''.join(map(str, s))}  計算 {fixed.get(s)}  論文 {expect.get(s)}")
            if cycles:
                print(f"      周期的な状態の組 {len(cycles)} 個")
    for name, (ko, rows) in ISLAM.items():
        expect = [reorder(b, ISLAM_ORDER) for b in rows]
        got = trajectory(dict(zip(NODES, expect[0])), ko=ko)
        ok = got == expect
        ok_all &= ok
        print(f"  {'一致' if ok else '不一致'}  Islam 2021 {name}: {len(got)} 段（論文 {len(expect)} 段）")
        if not ok:
            show(got)
    return ok_all


def check_db() -> None:
    """Li のつながりごとに、DB に同じ単位どうしの関係があるか・向きが合うかを出す。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        "select a.gene_name, b.gene_name, i.interaction_type, i.effect, i.partner from interactions i "
        "join proteins a on a.id = i.source_id join proteins b on b.id = i.target_id").fetchall()
    tally = Counter()
    for a, b, s in EDGES:
        src, dst = set(GENES[a]), set(GENES[b])
        hits = []
        for x, y, kind, effect, partner in rows:
            x, y, partner = x.upper(), y.upper(), (partner or "").upper()
            # 下流がサイクリンの単位なら、CDC28 への関係（Sic1 ⊣ Cdc28 など）も数える
            if not (y in dst or (b in CYCLINS and y == "CDC28" and kind != "transcription"
                                 and (not partner or any(g in partner for g in dst)))):
                continue
            # 上流がサイクリンの単位なら、CDC28 からの関係も数える（partner が空なら「相手不明」と印を付ける）
            if x in src:
                hits.append((x, y, kind, effect, ""))
            elif a in CYCLINS and x == "CDC28":
                if any(g in partner for g in src):
                    hits.append((x, y, kind, effect, ""))
                elif not partner:
                    hits.append((x, y, kind, effect, "相手不明"))
        if hits and all(h[4] for h in hits):
            verdict_note = "（CDC28 の相手不明だけ）"
        else:
            verdict_note = ""
        signed = [h for h in hits if h[3] in ("activate", "inhibit")]
        agree = [h for h in signed if (h[3] == "activate") == (s > 0)]
        if not hits:
            verdict = "DB にない"
        elif not signed:
            verdict = "向き不明だけ"
        elif agree and len(agree) == len(signed):
            verdict = "向きも一致"
        elif agree:
            verdict = "一致と逆が混在"
        else:
            verdict = "逆"
        verdict += verdict_note
        tally[verdict] += 1
        shown = "; ".join(f"{x}→{y} {k} {e}" + (f"（{n}）" if n else "") for x, y, k, e, n in hits[:4]) + (" …" if len(hits) > 4 else "")
        print(f"  {a:>9} {'→' if s > 0 else '⊣'} {b:<9} {verdict:<10} {shown}")
    print("\n  " + "・".join(f"{k} {v}" for k, v in tally.most_common()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--ko", default="", help="壊す単位（, 区切り。例: Sic1 / Cln3,Cln1,2 は Cln3+Cln1,2 のように + で）")
    ap.add_argument("--db", action="store_true")
    args = ap.parse_args()
    if args.db:
        check_db()
        return
    if args.verify:
        print("論文の表との照合:")
        print("すべて一致" if verify() else "不一致あり")
        return
    ko = {k for k in args.ko.split("+") if k}
    print(f"START からの移り変わり{'（' + '・'.join(sorted(ko)) + ' を壊す）' if ko else ''}:")
    show(trajectory(START, ko=ko))
    print("\n止まる状態（流れ込む初期状態の数）:")
    for c, n in attractors(ko=ko).most_common():
        on = [NODES[i] for i, v in enumerate(c[0]) if v]
        print(f"  {n:>5}  {'・'.join(on) or '（すべて 0）'}{'  （周期 ' + str(len(c)) + '）' if len(c) > 1 else ''}")


if __name__ == "__main__":
    main()
