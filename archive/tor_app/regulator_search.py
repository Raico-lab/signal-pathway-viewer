"""制御遺伝子探索: 観測した遺伝子の発現・活性の変化（増・減・変化なし）を、どの遺伝子の働きの変化で説明できるかを
探し、候補を点数順に並べる。

並べ方（破壊株データ Deleteome での評価で決めた。tools/evaluate_regulator_search.py）:
- 転写因子 T: 変化した遺伝子（増・減）の中に、T の標的がどれだけ多く含まれるか（超幾何分布の p 値）。
  発現の観測は転写制御の標的、活性の観測はそれ以外の関係（リン酸化など）の相手と比べる。点数は −log10(p)。
- シグナル伝達の遺伝子 R: R からシグナル伝達の関係を（さかのぼる段数 − 1）段以内でたどれる転写因子のうち、
  点数がいちばん高いもの × 0.7^段数（向きを使うようにしたとき 0.8 から下げた。下の評価）。
- 一般的なストレス応答の遺伝子（data/common_response_genes.csv。多くの破壊株で一緒に変わる。画面で選んだときだけ
  graph.common に入る）: 転写因子自身の点数では
  発現の観測から外す（どの転写因子の標的にも多く含まれ、見分ける手がかりにならないため）。ただし含めた点数の 0.8 倍のほうが
  高ければそちらを使う（MSN2/4 のように標的がストレス応答そのものの転写因子のため）。シグナル伝達の遺伝子が受け継ぐ点数では
  外さない（PKA・TORC1 などの本来の出力がストレス応答なので）。破壊株 203 株での評価: 10 位以内 18.7% → 20.7%
  （転写因子 43.1% → 46.2%、それ以外 7.2% → 8.7%）。すべての点数で外すと転写因子は 50.8% になるが、それ以外が 5.8% に下がる。
  熱ショック・ラパマイシンの例（sample_data/regulator_search）ではストレス応答そのものが手がかりなので順位が下がる
  （MSN2/4 1 位 → 4 位、GLN3 2 位 → 5 位）。そのため既定では使わない。
- 増減の向き（促進・抑制）: 転写因子の点数では、重なった標的のうち「活性化」か「不活性化」のどちらか一方と一貫するもの
  （向きの記録がない標的は両方に数える）だけを数える。ただし向きの記録には誤りがある（4 本に 1 本くらい破壊株データと逆。
  MSN4 → リボソームタンパク質遺伝子が「促進」など。YEASTRACT+ の記録そのもの）ので、向きを無視した点数の 0.7 倍のほうが
  高ければそちらを使う。シグナル伝達の遺伝子が受け継ぐ点数でも向きを使う。
  破壊株 203 株での評価（10 位以内）: 全体 18.7% → 21.7%、転写因子 43.1% → 52.3%（順位の中央値 10 → 4）、それ以外 7.2% のまま。
  向きだけ・掛け率 0.6 なら全体 22.7% だが、熱ショックの例で MSN2/4 が 1 → 10 位・PKA が 9 → 17 位に下がる（今の設定では 5 位・
  14 位。HSF1 は 21 → 4 位）。YEASTRACT+ の向きの記録は Deleteome 由来ではないことを確かめた（変化の大きさごとの一致率に、
  Kemmeren の基準での段差がない）。
  表示する向きは、重なった遺伝子の増減と関係の作用から「活性化／不活性化」を決める（決められなければ「向きは不明」）。
- 偶然でも起きる確率は、候補の数だけ同時に調べていることを補正した値（Benjamini–Hochberg の q 値）も出す。

「変化なし」の観測は順位には使わず、候補がその遺伝子の変化を予測してしまう（標的に含む）ときに表示で知らせる。

複合体・役割が重なる組（data/complexes.csv）は 1 つの候補にまとめる（TORC1 なら TOR1・KOG1・LST8 の関係を合わせて 1 つ）。
まとまりの候補の番号は負の数で、名前と構成要素は SignedGraph が持つ。経路の表示では、実際に関係を持つ構成要素に戻す。
"""
import math
from dataclasses import dataclass, field

from .db.vocab import NOT_SIGNAL_FLOW, resolve_effect

STEP_FACTOR = 0.7         # シグナル伝達の遺伝子が、下流の転写因子の点数を受け継ぐときの 1 段あたりの掛け率
COMMON_FULL_FACTOR = 0.8  # 転写因子自身の点数で、ストレス応答の遺伝子を含めた点数をこの率で使ってよい（0 なら使わない）
COMMON_INHERIT_FULL = True
TF_SIGNED = True        # 転写因子の点数で、向きの記録と観測の増減が一貫する重なりだけを数える
SIGNED_FALLBACK = 0.7   # 向きを無視した点数のこの率のほうが高ければ、そちらを使う（向きの記録の誤りへの備え）
SIGNED_INHERIT = True   # シグナル伝達の遺伝子が受け継ぐ点数でも向きを使う
# 試験中: シグナル伝達の遺伝子の点数の付け方。"best" = 届く転写因子のうち点数がいちばん高いもの × 掛け率^段数。
# "causal" = 符号つきの因果推論。動いた転写因子（点数が CAUSAL_THRESHOLD 以上で向きが決まるもの）それぞれについて、
# 候補の変化から経路の符号で予測した向きと合えば加点、合わなければ減点する（点数 × 掛け率^段数）。経路の符号が
# 決まらない転写因子は CAUSAL_UNKNOWN の重みで加点する
SIGNAL_MODE = "best"
CAUSAL_THRESHOLD = 2.0
CAUSAL_UNKNOWN = 0.0
CAUSAL_PENALTY = 1.0    # 予測と合わない転写因子の減点の率（向きの記録の誤りへの備えで 1 より小さくできる）
CAUSAL_FALLBACK = 1.0   # 符号つきの経路で動いた転写因子に届かない候補は、"best" の点数にこの率を掛けて使う  # シグナル伝達の遺伝子が受け継ぐ点数では、ストレス応答の遺伝子を外さない
SIGN = {"activate": 1, "inhibit": -1}


@dataclass
class Observation:
    gene: int
    kind: str      # "expr"（発現） / "act"（活性）
    change: str    # "up" / "down" / "none"


@dataclass
class GeneVerdict:
    gene: int
    kind: str
    change: str
    outcome: str           # "match"（向きも合う）/ "unknown"（標的だが向きは不明）/ "contra"（標的だが向きが逆）/
                           # "unreached"（標的でない）/ "flat_ok" / "flat_bad"（変化なしなのに標的）/ "self"（候補そのもの）
    points: float = 0.0
    unknown: int = 0
    steps: int = 0
    path: list[int] = field(default_factory=list)       # 候補 → … → g の遺伝子
    edges: list[tuple[int, int, int]] = field(default_factory=list)   # (上流, 下流, 符号 +1/-1/0)


@dataclass
class Candidate:
    gene: int
    direction: int         # +1 = 活性化, -1 = 不活性化, 0 = 向きは不明
    score: float           # 並べる順の点数（−log10 p、シグナル伝達の遺伝子は下流の転写因子から受け継いだ値）
    p_value: float         # 標的の重なりが偶然でも起きる確率（シグナル伝達の遺伝子は、受け継いだ転写因子のもの）
    q_value: float         # 候補の数で補正した確率
    via: int | None        # シグナル伝達の遺伝子の点数を受け継いだ転写因子（転写因子そのものなら None）
    steps: int             # via までの段数
    overlap: int           # 変化した遺伝子のうち、標的に含まれる数
    regulon: int           # 標的の数
    verdicts: list[GeneVerdict]
    name: str = ""         # 候補の名前（まとまりなら「TORC1」など）
    members: list[int] = field(default_factory=list)   # 候補に含まれる遺伝子（まとまりなら構成要素、ふつうは自分だけ）
    via_name: str = ""
    # 因果推論で採点したとき: 動いた転写因子ごとの (転写因子, 段数, 予測と合ったか（None は経路の符号が決まらない）, 経路)
    consistency: list = field(default_factory=list)
    sim_check: object = None   # シミュレーションで確かめた結果（tor_app/sim_verify.Check）

    @property
    def excess(self) -> float:   # 以前の名前（表示の互換のため）
        return self.score

    def count(self, *outcomes: str) -> int:
        return sum(v.outcome in outcomes for v in self.verdicts)


class SignedGraph:
    """符号付きの関係。転写制御の標的（発現の変化を説明する）と、それ以外の関係の相手（活性の変化を説明する）に分けて持つ。"""

    def __init__(self, interactions, known_only: bool, hidden: dict[str, list[str]], blocked: set[int],
                 units=None, names: dict[int, str] | None = None):
        types, effects = set(hidden.get("types", [])), set(hidden.get("effects", []))
        self.tx: dict[int, dict[int, int]] = {}    # 転写因子（またはまとまり）→ {標的: 符号}
        self.sig: dict[int, dict[int, int]] = {}   # 上流（またはまとまり）→ {下流: 符号}（転写制御以外）
        # 複合体・役割が重なる組: 構成要素から出る関係を、まとまり（負の番号）から出る関係にまとめる
        self.unit_of: dict[int, int] = {}          # 構成要素 → まとまり
        self.unit_names: dict[int, str] = {}
        self.unit_members: dict[int, list[int]] = {}
        self.origin: dict[tuple[int, int], int] = {}   # (まとまり, 下流) → 実際に関係を持つ構成要素
        self.common: set[int] = set()              # 一般的なストレス応答の遺伝子（転写因子自身の点数では観測から外す）
        self.common_full_factor = COMMON_FULL_FACTOR
        self.common_inherit_full = COMMON_INHERIT_FULL
        self.tf_signed = TF_SIGNED
        self.signed_fallback = SIGNED_FALLBACK
        self.signed_inherit = SIGNED_INHERIT
        self.step_factor = STEP_FACTOR
        self.signal_mode = SIGNAL_MODE
        self.causal_threshold = CAUSAL_THRESHOLD
        self.causal_unknown = CAUSAL_UNKNOWN
        self.causal_fallback = CAUSAL_FALLBACK
        self.causal_penalty = CAUSAL_PENALTY
        names = names or {}
        for k, unit in enumerate(units or []):
            uid = -(k + 1)
            for g in unit.primary:
                self.unit_of[g] = uid
            self.unit_members[uid] = list(unit.members)
            members = "・".join(names.get(g, str(g)) for g in unit.counted)
            self.unit_names[uid] = f"{unit.name}（{members}）" if members else unit.name
        for it in interactions:
            effect = resolve_effect(it.interaction_type, it.effect)
            if it.source_id == it.target_id or it.interaction_type in types or effect in effects:
                continue
            if known_only and effect == "none":
                continue
            if it.source_id in blocked or it.target_id in blocked:
                continue
            if NOT_SIGNAL_FLOW in (getattr(it, "evidence", "") or ""):
                continue   # 信号の流れではない関係（逆向きのリン酸の受け渡しなど）
            table = self.tx if it.interaction_type == "transcription" else self.sig
            sign = SIGN.get(effect, 0)
            source = self.unit_of.get(it.source_id, it.source_id)
            if source != it.source_id:
                if it.target_id in self.unit_members[source]:
                    continue   # まとまりの中どうしの関係は、まとまりから出る関係にしない
                self.origin.setdefault((source, it.target_id), it.source_id)
            old = table.setdefault(source, {}).get(it.target_id)
            # 同じ組に複数の関係があり作用が食い違うときは作用不明
            table[source][it.target_id] = sign if old is None or old == sign else 0
        self.n_genes = len({g for t in (self.tx, self.sig) for a, d in t.items() for g in (a, *d) if g >= 0}) + \
            len(self.unit_of)

    def name(self, node: int, names: dict[int, str]) -> str:
        return self.unit_names.get(node) or names.get(node, str(node))

    def gene_edge(self, a: int, b: int, s: int) -> tuple[int, int, int]:
        """まとまりから出る関係を、実際に関係を持つ構成要素の関係に戻す（地図に表示するため）。"""
        return (self.origin.get((a, b), a), b, s)


def _log_sf(k: int, total: int, success: int, draws: int) -> float:
    """超幾何分布で、重なりが k 以上になる確率の log10（scipy を使わずに計算する）。"""
    if k <= 0:
        return 0.0

    def logpmf(i):
        return (math.lgamma(success + 1) - math.lgamma(i + 1) - math.lgamma(success - i + 1)
                + math.lgamma(total - success + 1) - math.lgamma(draws - i + 1) - math.lgamma(total - success - draws + i + 1)
                - math.lgamma(total + 1) + math.lgamma(draws + 1) + math.lgamma(total - draws + 1))
    top = min(success, draws)
    terms = [logpmf(i) for i in range(k, top + 1) if 0 <= draws - i <= total - success]
    if not terms:
        return 0.0
    mx = max(terms)
    return (mx + math.log(sum(math.exp(t - mx) for t in terms))) / math.log(10)


def _signaling_paths(graph: SignedGraph, start: int, steps: int) -> dict[int, tuple[int, list[tuple[int, int, int]]]]:
    """start からシグナル伝達の関係を steps 段以内でたどれる転写因子 → (段数, 経路の関係)。最短の経路を 1 本。"""
    seen, frontier, found = {start: []}, [start], {}
    for d in range(1, steps + 1):
        nxt = []
        for u in frontier:
            for v, s in graph.sig.get(u, {}).items():
                key = graph.unit_of.get(v, v)   # まとまりの構成要素に届いたら、まとまりとして先へ進む
                if key in seen or key == start:
                    continue
                seen[key] = seen[u] + [(u, v, s)]   # 関係の下流は実際に届いた遺伝子のまま残す
                nxt.append(key)
                if key in graph.tx and key not in found:
                    found[key] = (d, seen[key])
        frontier = nxt
    return found


def _sign_of(edges) -> int:
    product = 1
    for _a, _b, s in edges:
        product *= s
    return product


def search(graph: SignedGraph, observations: list[Observation], depth: int, seed: int = 0,
           progress=None, permutations: int = 0, names: dict[int, str] | None = None) -> list[Candidate]:
    """観測をもっともよく説明する遺伝子を、点数の高い順に返す（標的の重なりがあるものだけ）。
    depth は観測した遺伝子からさかのぼる段数（最後の 1 段が転写因子 → 標的。シグナル伝達の遺伝子は depth − 1 段で転写因子に届くもの）。"""
    names = names or {}
    total = max(graph.n_genes, 1)
    changed_expr = {o.gene: o.change for o in observations if o.kind == "expr" and o.change != "none"}
    changed_act = {o.gene: o.change for o in observations if o.kind == "act" and o.change != "none"}

    changed_own = {g: c for g, c in changed_expr.items() if g not in graph.common}

    def tf_stats(t: int, expr: dict[int, str], signed: bool) -> tuple[float, int, int]:
        """転写因子（または活性の観測では関係の上流）t の −log10 p・重なり・標的の数。"""
        log_p, overlap, size = 0.0, 0, 0
        for targets, changed in ((graph.tx.get(t, {}), expr), (graph.sig.get(t, {}), changed_act)):
            if not changed or not targets:
                continue
            hit = targets.keys() & changed.keys()
            k = len(hit)
            lp = _log_sf(k, total, len(targets), len(changed))
            if signed and changed is expr:
                # 活性化（+1）・不活性化（−1）のどちらか一方と一貫する重なり（向きの記録がない標的は両方に数える）
                votes = [targets[g] * (1 if changed[g] == "up" else -1) for g in hit]
                ks = max(sum(v >= 0 for v in votes), sum(v <= 0 for v in votes)) if votes else 0
                lp = min(_log_sf(ks, total, len(targets), len(changed)), lp * graph.signed_fallback)
            log_p += lp
            overlap += k
            size += len(targets)
        return -log_p, overlap, size

    regulators = set(graph.tx) | ({a for a, d in graph.sig.items() if d.keys() & changed_act.keys()} if changed_act else set())
    # シグナル伝達の遺伝子が受け継ぐ点数（ストレス応答の遺伝子を外さない。向きは signed_inherit のときだけ使う）
    direct = {t: tf_stats(t, changed_expr, graph.tf_signed and graph.signed_inherit) for t in regulators}
    use_common = graph.common and len(changed_own) < len(changed_expr)
    if use_common or (graph.tf_signed and not graph.signed_inherit):
        # 転写因子自身の点数（向きを使い、選んだときはストレス応答の遺伝子を除く）
        own = {t: tf_stats(t, changed_own if use_common else changed_expr, graph.tf_signed) for t in regulators}
        if use_common:
            for t, full in direct.items():
                if full[0] * graph.common_full_factor > own[t][0]:
                    own[t] = (full[0] * graph.common_full_factor, full[1], full[2])
    else:
        own = direct
    if progress:
        progress(1, 2)

    def direction_of(t: int) -> int:
        """重なった遺伝子の増減と関係の作用から、t が活性化されたか（+1）不活性化されたか（−1）。決められなければ 0。"""
        votes = 0
        for targets, changed in ((graph.tx.get(t, {}), changed_expr), (graph.sig.get(t, {}), changed_act)):
            for g, change in changed.items():
                s = targets.get(g)
                if s:
                    votes += s * (1 if change == "up" else -1)
        return (votes > 0) - (votes < 0)

    _dir_cache: dict[int, int] = {}

    def tf_dir(t: int) -> int:
        if t not in _dir_cache:
            _dir_cache[t] = direction_of(t)
        return _dir_cache[t]

    results = []
    candidates = set(direct) | set(graph.sig)
    for r in candidates:
        best = (own[r][0], None, 0, []) if r in own and own[r][0] > 0 else (0.0, None, 0, [])
        reach = _signaling_paths(graph, r, max(depth - 1, 0)) if depth > 1 else {}
        own_best = best
        for t, (d, path) in reach.items():
            score = (direct if graph.common_inherit_full else own).get(t, (0.0,))[0] * graph.step_factor ** d
            if score > best[0]:
                best = (score, t, d, path)
        causal_dir, causal_record = None, []
        if graph.signal_mode == "causal" and reach:
            inherit = direct if graph.common_inherit_full else own
            moved = [(t, d, path) for t, (d, path) in reach.items()
                     if own.get(t, (0.0,))[0] >= graph.causal_threshold and tf_dir(t)]
            signed = [m for m in moved if all(e[2] for e in m[2])]
            fallback = best if best[1] is not None else None
            best = own_best
            if fallback is not None and not signed:
                # 符号つきの経路で動いた転写因子に届かないので、因果推論では決められない。今の方式の点数を使う
                fb = fallback[0] * graph.causal_fallback
                if fb > best[0]:
                    best = (fb, fallback[1], fallback[2], fallback[3])
            if signed:
                options = []
                for D in (1, -1):   # 候補が活性化（+1）・不活性化（−1）したとき
                    total, top, record = 0.0, None, []
                    for t, d, path in moved:
                        w = inherit[t][0] * graph.step_factor ** d
                        sp = _sign_of(path) if all(e[2] for e in path) else 0
                        if sp == 0:
                            total += graph.causal_unknown * w
                            record.append((t, d, None, path))
                        elif D * sp == tf_dir(t):
                            total += w
                            record.append((t, d, True, path))
                            if top is None or w > top[0]:
                                top = (w, t, d, path)
                        else:
                            total -= w * graph.causal_penalty
                            record.append((t, d, False, path))
                    options.append((total, D, top, record))
                total, D, top, record = max(options, key=lambda x: x[0])
                if top is not None and total > best[0]:
                    best = (total, top[1], top[2], top[3])
                    causal_dir = D
                    causal_record = record
        score, via, steps, path = best
        if score <= 0:
            continue
        source = via if via is not None else r
        log_p, overlap, size = direct[source] if via is not None and graph.common_inherit_full else own[source]
        sign_to_tf = _sign_of(path) if via is not None else 1
        direction = causal_dir if causal_dir is not None else sign_to_tf * direction_of(source)
        members = graph.unit_members.get(r, [r])
        verdicts = []
        for o in observations:
            if o.gene in members:
                ok = o.kind == "act" and o.change != "none" and direction != 0
                verdicts.append(GeneVerdict(o.gene, o.kind, o.change, "self",
                                            1.0 if ok and (o.change == "up") == (direction > 0) else 0.0))
                continue
            targets = graph.tx.get(source, {}) if o.kind == "expr" else graph.sig.get(source, {})
            if o.gene not in targets:
                verdicts.append(GeneVerdict(o.gene, o.kind, o.change, "flat_ok" if o.change == "none" else "unreached"))
                continue
            edges = [graph.gene_edge(*e) for e in path + [(source, o.gene, targets[o.gene])]]
            steps_g = len(edges)
            if o.change == "none":
                verdicts.append(GeneVerdict(o.gene, o.kind, o.change, "flat_bad", 0.0, 0, steps_g,
                                            [edges[0][0]] + [e[1] for e in edges], edges))
                continue
            # 向きの予測: 候補を direction の向きに変えたとき g が増えるか減るか（作用不明が途中にあれば不明）
            predicted = direction * _sign_of(edges) if direction and all(e[2] for e in edges) else 0
            observed = 1 if o.change == "up" else -1
            outcome = "unknown" if predicted == 0 else ("match" if predicted == observed else "contra")
            verdicts.append(GeneVerdict(o.gene, o.kind, o.change, outcome, 0.0, 0, steps_g,
                                        [edges[0][0]] + [e[1] for e in edges], edges))
        results.append(Candidate(r, direction, round(score, 3), 10 ** (-log_p), 1.0, via, steps, overlap, size,
                                 verdicts, graph.name(r, names), members,
                                 graph.name(via, names) if via is not None else "",
                                 [(t, d, ok, [graph.gene_edge(*e) for e in pth]) for t, d, ok, pth in causal_record]))
    # 候補の数で補正した確率（Benjamini–Hochberg）
    ordered = sorted(results, key=lambda c: c.p_value)
    m = len(ordered)
    q_prev = 1.0
    for i in range(m - 1, -1, -1):
        q_prev = min(q_prev, ordered[i].p_value * m / (i + 1))
        ordered[i].q_value = q_prev
    results.sort(key=lambda c: (-c.score, c.steps, c.gene))
    if progress:
        progress(2, 2)
    return results
