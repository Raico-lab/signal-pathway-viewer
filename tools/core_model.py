"""中心経路の論理モデル（TORC1・PKA・Snf1・HOG・細胞壁の経路と、その下の転写因子など）。

モデルの中身は data/core_model/:
- units.csv: 単位・遺伝子・決まり（rule。Python の論理式 and / or / not で、ほかの単位や入力の名前を使う。input は入力）・
  答え合わせに使う目印の表の単位名（marker_unit）
- edges.csv: 決まりに出てくるつながり 1 本ずつの向きと根拠の PMID（PubMed で要旨を確かめた。論文の文の引用は置かない）。
  決まりとつながりの表が食い違えば、計算の前に知らせる
- conditions.csv: 条件のキー → 入力の変え方（基準は富栄養: Glucose・Nitrogen・AminoAcids が 1、ほかの入力は 0。
  baseline 列があれば、その条件だけ基準を変える。glucose_repletion はグルコースのない状態が基準）

計算: 入力を決めて、単位を units.csv の順に 1 つずつ更新し（直前に更新した値を次の単位が使う）、1 巡しても
変わらなくなるまで繰り返す（基準は全単位 0 から、条件は基準の状態から始める）。TORC1 ⊣ Snf1 ⊣ TORC1 のような
2 つの状態をとりうる輪があり、全単位を同時に更新すると、富栄養でも全部 0 から「TORC1 が止まり Snf1 が働く」側に落ちる。
上流から順に決めれば、富栄養では TORC1 が先に働いて Snf1 は止まる（units.csv は上流から並べる）。
条件の状態と基準の状態の差（上がる・下がる・変わらない）を予測とする（3 値）。周期になれば「振動」とする。

早い時点と落ち着いた状態: edges.csv の status に「一過性」とあるつながり（ストレスでの TORC1 の一時的な低下）は、
早い時点の計算だけで効かせる。0/1 のモデルは時間を表せず、一過性の低下も TORC1 ⊣ Snf1 などの輪を通して
戻らない切り替えになってしまうため。

答え合わせ:
- 目印の表（data/activity_markers.csv）の (単位, 条件, 変化)。no_change は変わらないと予測すれば一致。
  transient_up・transient_down は早い時点と、ほかは落ち着いた状態と比べる
- キナーゼの活性の推定（docs/curation/kinase_activity.tsv、tools/kinase_activity.py が作る。Leutert 2023 の 5 分後）の |z| ≥ 2 の組。
  早い時点と比べる
目印の表と決まりは同じ論文を使っていることがある（例: 17560372）ので、目印の表との一致は「決まりが論文と矛盾しないか」の
確かめに近い。独立の答えは、キナーゼの活性の推定（測定のデータ）のほう。

使い方:
  .venv/bin/python tools/core_model.py                 # 答え合わせ
  .venv/bin/python tools/core_model.py --show rapamycin  # ある条件の各単位の予測（早い時点・落ち着いた状態）
  .venv/bin/python tools/core_model.py --mrna            # 環境 → モデル → 転写因子 → 標的の mRNA を SPELL と比べる
  .venv/bin/python tools/core_model.py --drop SNF1:GLN3  # つながりを外して比べる（争いのあるつながりの確かめ）
  .venv/bin/python tools/core_model.py --ko SCH9         # 単位を壊して（常に 0）計算する
  .venv/bin/python tools/core_model.py --rule "TORC1=(EGO or PIB2) and not Rapamycin"   # 決まりを差し替えて比べる
"""
import argparse
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "data" / "core_model"
MARKERS = ROOT / "data" / "activity_markers.csv"
KINASE = ROOT / "docs" / "curation" / "kinase_activity.tsv"
RICH = {"Glucose": 1, "Nitrogen": 1, "AminoAcids": 1}
Z_SIG = 2.0
EXPECT = {"up": 1, "transient_up": 1, "down": -1, "transient_down": -1, "no_change": 0}
NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
KEYWORDS = {"and", "or", "not"}


class Model:
    def __init__(self, drop: set[tuple[str, str]] = frozenset(), ko: set[str] = frozenset(),
                 rules: dict[str, str] | None = None, early: bool = False):
        with open(MODEL / "units.csv", encoding="utf-8") as f:
            self.units = {r["unit"]: r for r in csv.DictReader(f)}
        for u, rule in (rules or {}).items():
            self.units[u] = {**self.units[u], "rule": rule}
        with open(MODEL / "edges.csv", encoding="utf-8") as f:
            self.edges = {(r["source"], r["target"]): r for r in csv.DictReader(f)}
        self.inputs = [u for u, r in self.units.items() if r["rule"] == "input"]
        self.nodes = [u for u, r in self.units.items() if r["rule"] != "input"]
        self.ko = set(ko)
        self.rules = {}
        # 一過性のつながり（status に「一過性」）は、早い時点（early）だけで効かせる
        transient = {k for k, r in self.edges.items() if "一過性" in r["status"]}
        for u in self.nodes:
            rule = self.units[u]["rule"]
            for src, tgt in set(drop) | (set() if early else transient):
                if tgt == u:
                    rule = self._remove(rule, src)
            self.rules[u] = compile(rule, u, "eval")
        self.problems = self.check()

    @staticmethod
    def _remove(rule: str, name: str) -> str:
        """決まりから単位 name を外す（or なら偽、and なら真として扱う形に置き換える）。
        決まりが and だけなら名前を True に、それ以外は False にする。「A and not (B or C)」「not (B and not C)」「A or not B」
        の形で正しく外れることを確かめてある（今の units.csv の形）。and と or が入り組んだ決まりを足すときは確かめ直す。"""
        # "not X" と "X" をそれぞれ、効かない値に置き換える: or の項は False、and の項は True
        if re.search(rf"\band\b", rule) and not re.search(rf"\bor\b", rule):
            return re.sub(rf"\bnot {name}\b|\b{name}\b", "True", rule)
        return re.sub(rf"\bnot {name}\b|\b{name}\b", "False", rule)

    def rule_edges(self, u: str) -> set[tuple[str, int]]:
        """決まりに出てくる (上流, 向き)。not の付いた名前・not (...) の中の名前は抑制。"""
        rule = self.units[u]["rule"]
        out = set()
        negated_groups = [m.span() for m in re.finditer(r"not \([^)]*\)", rule)]
        for m in NAME.finditer(rule):
            name = m.group(0)
            if name in KEYWORDS or name in ("True", "False"):
                continue
            direct = rule[max(0, m.start() - 4):m.start()] == "not "
            grouped = sum(1 for a, b in negated_groups if a <= m.start() < b) % 2 == 1
            neg = direct != grouped   # not (A and not B) の B は促進
            out.add((name, -1 if neg else 1))
        return out

    def check(self) -> list[str]:
        problems = []
        used = set()
        for u in self.nodes:
            for src, sign in self.rule_edges(u):
                if src not in self.units:
                    problems.append(f"{u} の決まりに知らない名前 {src}")
                    continue
                e = self.edges.get((src, u))
                used.add((src, u))
                if e is None:
                    problems.append(f"{src} → {u} が edges.csv にない")
                elif (e["sign"] == "+") != (sign > 0):
                    problems.append(f"{src} → {u} の向きが決まり（{'+' if sign > 0 else '-'}）と edges.csv（{e['sign']}）で違う")
                elif not e["pmids"]:
                    problems.append(f"{src} → {u} に根拠の PMID がない")
        for k in self.edges:
            if k not in used:
                problems.append(f"{k[0]} → {k[1]} は edges.csv にあるが決まりで使っていない")
        return problems

    def run(self, inputs: dict[str, int], start: dict[str, int] | None = None, max_steps: int = 100):
        """変わらなくなった状態（周期なら周期の状態の平均）と、周期かどうか。"""
        state = {u: 0 for u in self.nodes} if start is None else dict(start)
        seen = []
        for _ in range(max_steps):
            env = {**inputs, **state}
            for u in self.nodes:
                env[u] = 0 if u in self.ko else int(bool(eval(self.rules[u], {}, env)))
            new = {u: env[u] for u in self.nodes}
            key = tuple(new[u] for u in self.nodes)
            if new == state:
                return new, False
            if key in seen:
                cycle = seen[seen.index(key):]
                avg = {u: sum(c[i] for c in cycle) / len(cycle) for i, u in enumerate(self.nodes)}
                return avg, True
            seen.append(key)
            state = new
        return state, True


def load_conditions() -> dict[str, dict]:
    def parse(text):
        return {k: int(v) for k, v in (p.split("=") for p in text.split(";") if p)}
    with open(MODEL / "conditions.csv", encoding="utf-8") as f:
        return {r["condition"]: {"baseline": parse(r["baseline"]), "inputs": parse(r["inputs"])} for r in csv.DictReader(f)}


def predict(model: Model, conditions: dict) -> dict[str, dict[str, tuple[int, bool]]]:
    """条件 → 単位 → (予測の向き +1/0/-1, 振動したか)。"""
    out = {}
    for cond, c in conditions.items():
        base_inputs = {i: 0 for i in model.inputs} | RICH | c["baseline"]
        base, osc1 = model.run(base_inputs)
        after, osc2 = model.run(base_inputs | c["inputs"], start={u: round(v) for u, v in base.items()})
        out[cond] = {u: ((after[u] > base[u]) - (after[u] < base[u]), osc1 or osc2) for u in model.nodes}
    return out


def answers(model: Model) -> list[tuple[str, str, int, str]]:
    """(単位, 条件, 期待の向き, 答えの出どころ, 早い時点か)。目印の一過性の行とリン酸化（5 分後）は早い時点と比べる。"""
    by_marker = {r["marker_unit"]: u for u, r in model.units.items() if r["marker_unit"]}
    out = []
    seen = {}
    with open(MARKERS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            u = by_marker.get(r["unit"])
            if u and r["change"] in EXPECT:
                k = (u, r["condition"], r["change"].startswith("transient"))
                if k in seen and seen[k] != EXPECT[r["change"]]:
                    seen[k] = None
                elif k not in seen:
                    seen[k] = EXPECT[r["change"]]
    out += [(u, c, s, "目印", early) for (u, c, early), s in seen.items() if s is not None]
    if KINASE.exists():
        with open(KINASE, encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                u = by_marker.get(r["unit"])
                if u and abs(float(r["z"])) >= Z_SIG:
                    out.append((u, r["condition"], 1 if float(r["z"]) > 0 else -1, "リン酸化（KSEA）", True))
    return out


def evaluate(model: Model, early_model: Model, conditions: dict, verbose: bool = True) -> dict[str, Counter]:
    """model は落ち着いた状態（一過性のつながりなし）、early_model は早い時点（一過性のつながりあり）。"""
    pred = predict(model, conditions)
    pred_early = predict(early_model, conditions)
    tally: dict[str, Counter] = defaultdict(Counter)
    rows = []
    for u, cond, exp, src, early in answers(model):
        if cond not in pred:
            continue
        p, osc = (pred_early if early else pred)[cond][u]
        if osc:
            v = "振動"
        elif p == exp:
            v = "一致"
        elif exp == 0:
            v = "変わらないはずが変わる"
        elif p == 0:
            v = "変わると言えない"
        else:
            v = "逆"
        tally[src][v] += 1
        rows.append((src, u, cond, exp, p, v))
    if verbose:
        for src in ["目印", "リン酸化（KSEA）"]:
            t = tally[src]
            n = sum(t.values())
            sig = t["一致"] + t["逆"]
            print(f"■ {src}: 比べた {n}  一致 {t['一致']}（{t['一致'] / n:.0%}）  逆 {t['逆']}  "
                  f"変わると言えない {t['変わると言えない']}  変わらないはずが変わる {t['変わらないはずが変わる']}  振動 {t['振動']}"
                  f"  ／ 向きを予測した組での一致 {t['一致'] - sum(1 for r in rows if r[0] == src and r[3] == 0 and r[5] == '一致')}"
                  f"/{sig - sum(1 for r in rows if r[0] == src and r[3] == 0 and r[5] == '一致') if sig else 0}"
                  if n else f"■ {src}: なし")
        print("\n■ 合わない組（逆・変わると言えない・変わらないはずが変わる）")
        arrow = {1: "↑", -1: "↓", 0: "→"}
        for src, u, cond, exp, p, v in sorted(rows, key=lambda r: (r[5], r[1], r[2])):
            if v != "一致":
                print(f"  {v:<12} {u:<12} {cond:<22} 答え {arrow[exp]}（{src}）  予測 {arrow[p]}")
    return tally


def mrna(model: Model, conditions: dict) -> None:
    """モデルの転写因子の予測から、標的の mRNA の上下を予測して SPELL と比べる（tools/predict_tf_targets.py の計算を使う）。
    目印の表の転写因子の活性で予測した場合と、同じ条件・同じ転写因子どうしで比べる。"""
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    import predict_tf_targets as ptt
    edges, z = ptt.load_edges(), ptt.load_z()
    major = ptt.majority(z)
    pred = predict(model, {c: v for c, v in conditions.items() if c in z})
    activity = {}
    for cond, units in pred.items():
        for u, (p, osc) in units.items():
            if p and not osc:
                for g in model.units[u]["genes"].split(";"):
                    activity[(g.upper(), cond)] = (p, "モデル", "")
    markers = ptt.load_activity()
    # 同じ (転写因子, 条件) に目印もある組だけで比べる
    both = {k for k in activity if k in markers}
    use = lambda prov: prov != ptt.CIRCULAR   # noqa: E731
    print(f"モデルが上下を予測した (転写因子, 条件) {len(activity)} 組（目印もある組 {len(both)}）\n")
    print("  モデルの予測から  " + ptt.fmt(ptt.score(ptt.predict(edges, activity, z, use), z, major)))
    print("  （目印もある組だけ）")
    print("  モデルの予測から  " + ptt.fmt(ptt.score(ptt.predict(edges, {k: activity[k] for k in both}, z, use), z, major)))
    print("  目印から          " + ptt.fmt(ptt.score(ptt.predict(edges, {k: markers[k] for k in both}, z, use), z, major)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show")
    ap.add_argument("--mrna", action="store_true", help="モデルの転写因子の予測から標的の mRNA を予測して SPELL と比べる")
    ap.add_argument("--drop", default="", help="外すつながり（上流:下流 を , で区切る）")
    ap.add_argument("--ko", default="", help="壊す単位（, 区切り）")
    ap.add_argument("--rule", action="append", default=[], help="決まりを差し替える（単位=式。争いのある決まりの比べ方）")
    args = ap.parse_args()
    drop = {tuple(x.split(":")) for x in args.drop.split(",") if x}
    rules = dict(r.split("=", 1) for r in args.rule)
    ko = {x for x in args.ko.split(",") if x}
    model = Model(drop, ko, rules)
    early_model = Model(drop, ko, rules, early=True)
    if model.problems:
        print("モデルの表の問題:")
        for p in model.problems:
            print("  " + p)
        print()
    conditions = load_conditions()
    if args.mrna:
        mrna(model, conditions)
        return
    if args.show:
        pred = predict(model, {args.show: conditions[args.show]})[args.show]
        pred_early = predict(early_model, {args.show: conditions[args.show]})[args.show]
        arrow = lambda p: "↑" if p > 0 else "↓" if p < 0 else "→"   # noqa: E731
        print(f"  {'単位':<26} 早い時点  落ち着いた状態")
        for u in model.nodes:
            (p, osc), (q, osc2) = pred[u], pred_early[u]
            print(f"  {model.units[u]['label']:<28} {arrow(q)}{'（振動）' if osc2 else ''}        {arrow(p)}{'（振動）' if osc else ''}")
        return
    print(f"単位 {len(model.nodes)} 個・入力 {len(model.inputs)} 個・つながり {len(model.edges)} 本・条件 {len(conditions)} 個"
          + (f"（外したつながり: {', '.join(':'.join(d) for d in drop)}）" if drop else "")
          + (f"（壊した単位: {', '.join(sorted(model.ko))}）" if model.ko else "") + "\n")
    evaluate(model, early_model, conditions)


if __name__ == "__main__":
    main()
