"""閾値付きシグナル伝達のシミュレーション（予測用の簡易モデル）。

- 各遺伝子の活性は 0〜1。
- 制御関係 1 本が運ぶシグナル = 強度 × σ(k × (上流活性 − 閾値))。上流活性が閾値を超えると伝わる。
- 活性化方向の入力は noisy-OR（どれか 1 本でも十分に来れば活性化）。入力がなければ基底活性。
- 抑制方向の入力は掛け算で効く: 活性 × Π(1 − 抑制シグナル)。
- 複合体・役割が重なる組（data/complexes.csv）: 同じ枠の遺伝子はどれか 1 つが働けばよく（最大値）、必須の枠は
  すべてそろって働く（最小値）。構成要素から出る関係は、まとまり全体の働きを運ぶ（TOR1 を壊しても TOR2 があれば
  TORC1 の働きは残り、TOR1 → SCH9 などの関係はそれを伝える）。構成要素自身の活性は、自分の値とまとまりの働きの小さい方。
- 固定 (clamp) した遺伝子は上流に関係なくその値をとる。
- separate=True（発現と活性を分ける計算）: 転写制御の関係は標的の「発現」を、それ以外の関係は標的の「シグナル伝達の状態」を
  動かし、活性 = 発現 × 状態 とする。発現は、活性化する転写因子が働かなくても基底の量（EXPR_FLOOR）が残り、
  転写の抑制でも最大 TX_REPRESS_MAX の割合までしか下がらない（転写の調節だけでは活性が消えないようにする）。
  関係が運ぶシグナルは、どちらも上流の活性から計算する。地図のシミュレーションで使う。
"""
import math
from dataclasses import dataclass, field

import numpy as np

STEEPNESS = 15.0
TX_REPRESS_MAX = 0.5   # separate=True のとき、転写の抑制で発現が下がる最大の割合（転写の調節は何倍かの上げ下げで、完全には消さない）
EXPR_FLOOR = 0.6   # separate=True のとき、活性化する転写因子が働かなくても残る発現の量（閾値 0.5 より上にして、転写因子の活性が経路を伝わるようにする）


@dataclass
class SimEdge:
    id: int
    source: int
    target: int
    effect: str  # activate / inhibit / none
    threshold: float
    weight: float
    kind: str = "sig"   # "tx" = 転写制御（発現を動かす）/ "sig" = それ以外（活性を動かす）。separate=True のときだけ使う


@dataclass
class SimResult:
    activity: dict[int, float]
    edge_signal: dict[int, float]
    converged: bool
    iterations: int
    unit_activity: dict[str, float] = field(default_factory=dict)   # 複合体・役割が重なる組ごとの働き
    expression: dict[int, float] = field(default_factory=dict)      # separate=True のときの発現


@dataclass
class Network:
    node_ids: list[int]
    basal: dict[int, float | None]
    edges: list[SimEdge]
    # 複合体・役割が重なる組: (名前, 必須の枠ごとの遺伝子のリスト, まとまりの働きを関係で運ぶ遺伝子)
    units: list[tuple[str, list[list[int]], list[int]]] = field(default_factory=list)

    def has_activators(self, node_id: int) -> bool:
        if not hasattr(self, "_activated"):
            self._activated = {e.target for e in self.edges if e.effect == "activate"}
        return node_id in self._activated

    def input_nodes(self) -> list[int]:
        """上流からの入力（活性化・抑制）を持たない遺伝子。"""
        targets = {e.target for e in self.edges if e.effect != "none"}
        return [n for n in self.node_ids if n not in targets]

    def default_basal(self, node_id: int) -> float:
        value = self.basal.get(node_id)
        if value is not None:
            return value
        return 0.0 if self.has_activators(node_id) else 1.0


def transfer(upstream: float, threshold: float) -> float:
    return 1.0 / (1.0 + math.exp(-STEEPNESS * (upstream - threshold)))


def simulate(net: Network, clamps: dict[int, float], max_iter: int = 400, tol: float = 1e-4,
             separate: bool = False) -> SimResult:
    """全遺伝子規模でも速く計算できるよう、numpy でまとめて計算する。"""
    ids = list(net.node_ids)
    index = {n: i for i, n in enumerate(ids)}
    size = len(ids)
    if size == 0:
        return SimResult({}, {}, True, 0)

    def edge_arrays(effect, kind=None):
        es = [e for e in net.edges if e.effect == effect and e.source in index and e.target in index
              and (kind is None or e.kind == kind)]
        return (es, np.array([index[e.source] for e in es], dtype=int),
                np.array([index[e.target] for e in es], dtype=int),
                np.array([e.threshold for e in es], dtype=float),
                np.array([e.weight for e in es], dtype=float))

    act_edges, a_src, a_tgt, a_th, a_w = edge_arrays("activate")
    inh_edges, i_src, i_tgt, i_th, i_w = edge_arrays("inhibit")
    has_act = np.bincount(a_tgt, minlength=size) > 0
    if separate:
        _e, ta_src, ta_tgt, ta_th, ta_w = edge_arrays("activate", "tx")
        _e, ti_src, ti_tgt, ti_th, ti_w = edge_arrays("inhibit", "tx")
        _e, sa_src, sa_tgt, sa_th, sa_w = edge_arrays("activate", "sig")
        _e, si_src, si_tgt, si_th, si_w = edge_arrays("inhibit", "sig")
        has_tx_act = np.bincount(ta_tgt, minlength=size) > 0
        has_sig_act = np.bincount(sa_tgt, minlength=size) > 0
    expr = np.ones(size)
    basal_set = np.array([net.basal.get(n) is not None for n in ids])
    basal = np.array([net.basal.get(n) if net.basal.get(n) is not None else 0.0 for n in ids])
    clamp_mask = np.array([n in clamps for n in ids])
    clamp_val = np.array([clamps.get(n, 0.0) for n in ids])
    # 必須の枠の遺伝子が計算範囲に 1 つもないまとまりは、まとまりとしては計算しない
    units = []
    for name, slots, primary in net.units:
        idx_slots = [np.array([index[g] for g in slot if g in index], dtype=int) for slot in slots]
        carriers = np.array([index[g] for g in primary if g in index], dtype=int)
        if idx_slots and all(len(s) for s in idx_slots) and len(carriers):
            units.append((name, idx_slots, carriers))

    def unit_values(level):
        return [min(float(level[s].max()) for s in slots) for _name, slots, _c in units]

    def effective(level):
        """関係の上流として使う活性: まとまりに入る遺伝子は、まとまり全体の働き。"""
        if not units:
            return level
        eff = level.copy()
        for (_name, _slots, carriers), value in zip(units, unit_values(level)):
            eff[carriers] = value
        return eff

    def signal(act, src, th, w):
        with np.errstate(over="ignore"):
            return w / (1.0 + np.exp(-STEEPNESS * (act[src] - th)))

    def step(act):
        nonlocal expr
        eff = effective(act)
        if separate:
            # 発現: 転写制御だけで決まる（基底の量は残る）
            not_tx = np.ones(size)
            np.multiply.at(not_tx, ta_tgt, 1.0 - signal(eff, ta_src, ta_th, ta_w))
            e = np.where(has_tx_act, np.maximum(1.0 - not_tx, EXPR_FLOOR), 1.0)
            np.multiply.at(e, ti_tgt, 1.0 - TX_REPRESS_MAX * signal(eff, ti_src, ti_th, ti_w))
            e = np.where(clamp_mask, np.minimum(e, clamp_val), e)   # 壊した遺伝子は発現もない
            expr = np.clip(e, 0.0, 1.0)
            # シグナル伝達の状態: これまでの計算を転写制御以外の関係で行う
            not_sig = np.ones(size)
            np.multiply.at(not_sig, sa_tgt, 1.0 - signal(eff, sa_src, sa_th, sa_w))
            state = np.where(has_sig_act, 1.0 - not_sig, np.where(basal_set, basal, 1.0))
            state = np.where(has_sig_act & basal_set, np.maximum(state, basal), state)
            np.multiply.at(state, si_tgt, 1.0 - signal(eff, si_src, si_th, si_w))
            level = np.clip(expr * np.clip(state, 0.0, 1.0), 0.0, 1.0)
        else:
            not_active = np.ones(size)
            np.multiply.at(not_active, a_tgt, 1.0 - signal(eff, a_src, a_th, a_w))
            level = np.where(has_act, 1.0 - not_active, np.where(basal_set, basal, 1.0))
            level = np.where(has_act & basal_set, np.maximum(level, basal), level)
            np.multiply.at(level, i_tgt, 1.0 - signal(eff, i_src, i_th, i_w))
            level = np.clip(level, 0.0, 1.0)
        level = np.where(clamp_mask, clamp_val, level)
        # 構成要素自身の活性は、自分の値とまとまりの働きの小さい方（複合体なら全員がいちばん弱い枠に揃う）
        for (_name, _slots, carriers), value in zip(units, unit_values(level)):
            free = carriers[~clamp_mask[carriers]]
            level[free] = np.minimum(level[free], value)
        return level

    act = np.where(clamp_mask, clamp_val, np.where(basal_set, basal, np.where(has_act, 0.0, 1.0)))
    converged = False
    iterations = 0
    # まず減衰なしで反復し、フィードバックで振動する場合は減衰をかけて収束させる
    for damping in (1.0, 0.5, 0.2):
        for _ in range(max_iter // 3):
            iterations += 1
            new = step(act)
            delta = float(np.max(np.abs(new - act)))
            act = act + damping * (new - act)
            if delta < tol:
                converged = True
                break
        if converged:
            break

    activity = {n: float(act[i]) for i, n in enumerate(ids)}
    eff = effective(act)
    edge_signal = {}
    for es, src, th, w in ((act_edges, a_src, a_th, a_w), (inh_edges, i_src, i_th, i_w)):
        for e, v in zip(es, signal(eff, src, th, w)):
            edge_signal[e.id] = float(v)
    for e in net.edges:
        if e.effect == "none":
            edge_signal[e.id] = 0.0
    unit_activity = {name: value for (name, _s, _c), value in zip(units, unit_values(act))}
    expression = {n: float(expr[i]) for i, n in enumerate(ids)} if separate else {}
    return SimResult(activity, edge_signal, converged, iterations, unit_activity, expression)
