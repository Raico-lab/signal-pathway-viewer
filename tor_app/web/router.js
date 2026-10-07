// 直角の折れ線で線を引き回す（遺伝子の上を通らず、他の線と重ならない。交差は許す）。
//
// 図を格子に分け、遺伝子の箱（少し広げたもの）を通れないマスとして、
// 「距離 + 曲がる回数」が小さい経路を A* で探す。経路を 1 本決めるたびに、通ったマスを
// 向き（横・縦）ごとに使用済みにする。後の線は同じマスを同じ向きには通れないので重ならないが、
// 直角に横切ることはできる（交差）。
(function () {
  "use strict";

  const CELL = 8;           // 格子の 1 マス（px）
  const MARGIN = 5;         // 箱の周りに空ける余白（px）
  const BEND_COST = 6;      // 曲がる 1 回をマス何個分とみなすか
  const OVERLAP_COST = 60;  // 他の線と重なる 1 マスの罰点（ほかに道がないときだけ重なる）
  // 出入り口が辺の中点から 1 マス離れるごとの罰点。曲がる回数より重くして中点を優先する
  // （中点が使われていれば中央寄りから）。線が多い図では重なりの罰点より軽くする
  const PORT_COST = 7;
  const DENSE_PORT_COST = 2;
  let portCost = PORT_COST;
  const DENSE = 150;        // 線がこれより多い図では速さを優先する（重なりを少し許す）
  let overlapCost = OVERLAP_COST;
  const MAX_CELLS = 220000; // 格子がこれより大きくなるときはマスを粗くする
  const DIRS = [[1, 0], [-1, 0], [0, 1], [0, -1]];   // 右・左・下・上
  const HORIZONTAL = (d) => d < 2;

  class Heap {
    constructor() { this.items = []; }
    get size() { return this.items.length; }
    push(item) {
      const a = this.items;
      a.push(item);
      let i = a.length - 1;
      while (i > 0) {
        const p = (i - 1) >> 1;
        if (a[p][0] <= a[i][0]) break;
        [a[p], a[i]] = [a[i], a[p]];
        i = p;
      }
    }
    pop() {
      const a = this.items;
      const top = a[0];
      const last = a.pop();
      if (a.length) {
        a[0] = last;
        let i = 0;
        for (;;) {
          const l = 2 * i + 1, r = l + 1;
          let m = i;
          if (l < a.length && a[l][0] < a[m][0]) m = l;
          if (r < a.length && a[r][0] < a[m][0]) m = r;
          if (m === i) break;
          [a[m], a[i]] = [a[i], a[m]];
          i = m;
        }
      }
      return top;
    }
  }

  function buildGrid(boxes) {
    let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
    boxes.forEach((b) => { x1 = Math.min(x1, b.x1); y1 = Math.min(y1, b.y1); x2 = Math.max(x2, b.x2); y2 = Math.max(y2, b.y2); });
    const pad = 6 * CELL;
    let cell = CELL;
    while (((x2 - x1 + 2 * pad) / cell) * ((y2 - y1 + 2 * pad) / cell) > MAX_CELLS) cell += 2;
    const ox = x1 - pad, oy = y1 - pad;
    const w = Math.ceil((x2 - x1 + 2 * pad) / cell) + 1;
    const h = Math.ceil((y2 - y1 + 2 * pad) / cell) + 1;
    return { cell, ox, oy, w, h,
             blocked: new Uint8Array(w * h),
             usedH: new Uint8Array(w * h), usedV: new Uint8Array(w * h),
             // 探索の作業領域（線ごとに確保し直さず、stamp で使い回す）
             cost: new Float32Array(w * h * 4), prev: new Int32Array(w * h * 4),
             stamp: new Int32Array(w * h * 4), gen: 0 };
  }

  const cx = (g, i) => g.ox + i * g.cell;
  const cy_ = (g, j) => g.oy + j * g.cell;
  const colOf = (g, x) => Math.round((x - g.ox) / g.cell);
  const rowOf = (g, y) => Math.round((y - g.oy) / g.cell);

  function blockBox(g, b) {
    const i1 = Math.max(0, Math.floor((b.x1 - MARGIN - g.ox) / g.cell));
    const i2 = Math.min(g.w - 1, Math.ceil((b.x2 + MARGIN - g.ox) / g.cell));
    const j1 = Math.max(0, Math.floor((b.y1 - MARGIN - g.oy) / g.cell));
    const j2 = Math.min(g.h - 1, Math.ceil((b.y2 + MARGIN - g.oy) / g.cell));
    for (let j = j1; j <= j2; j++) for (let i = i1; i <= i2; i++) g.blocked[j * g.w + i] = 1;
    return { i1, i2, j1, j2 };
  }

  // 箱の外側 1 マスの輪（出入り口の候補）。角は除き、出入りの向きを添える。
  // 箱の外側 1 マスの輪（出入り口の候補）。off は辺の中点からの距離（マス）で、罰点に使う
  function ports(g, r, box) {
    const out = [];
    const ci = ((box.x1 + box.x2) / 2 - g.ox) / g.cell;
    const cj = ((box.y1 + box.y2) / 2 - g.oy) / g.cell;
    for (let i = r.i1 + 1; i < r.i2; i++) {
      const off = Math.abs(i - ci);
      if (r.j1 - 1 >= 0) out.push({ i, j: r.j1 - 1, dir: 3, off });          // 上へ出る
      if (r.j2 + 1 < g.h) out.push({ i, j: r.j2 + 1, dir: 2, off });         // 下へ出る
    }
    for (let j = r.j1 + 1; j < r.j2; j++) {
      const off = Math.abs(j - cj);
      if (r.i1 - 1 >= 0) out.push({ i: r.i1 - 1, j, dir: 1, off });          // 左へ出る
      if (r.i2 + 1 < g.w) out.push({ i: r.i2 + 1, j, dir: 0, off });         // 右へ出る
    }
    return out;
  }

  // 通れるマスを、つながっているまとまりごとに番号付けする（箱に囲まれて外へ出られないマスは別の番号になる）。
  // 出口と入口が同じまとまりにないときは、探索しても必ず見つからないので、探索せずにあきらめる
  // （まとめの枠に詰めて並べた遺伝子は箱どうしの隙間がなく、探索が上限まで空回りして 1 本に数十ミリ秒かかっていた）
  function components(g) {
    const comp = new Int32Array(g.w * g.h).fill(-1);
    const stack = [];
    let label = 0;
    for (let start = 0; start < comp.length; start++) {
      if (g.blocked[start] || comp[start] !== -1) continue;
      comp[start] = label;
      stack.push(start);
      while (stack.length) {
        const k = stack.pop(), i = k % g.w, j = (k / g.w) | 0;
        if (i > 0 && !g.blocked[k - 1] && comp[k - 1] === -1) { comp[k - 1] = label; stack.push(k - 1); }
        if (i < g.w - 1 && !g.blocked[k + 1] && comp[k + 1] === -1) { comp[k + 1] = label; stack.push(k + 1); }
        if (j > 0 && !g.blocked[k - g.w] && comp[k - g.w] === -1) { comp[k - g.w] = label; stack.push(k - g.w); }
        if (j < g.h - 1 && !g.blocked[k + g.w] && comp[k + g.w] === -1) { comp[k + g.w] = label; stack.push(k + g.w); }
      }
      label++;
    }
    return comp;
  }
  // 出口のどれかと入口のどれかが、通れるマスでつながっているか
  function reachable(g, comp, starts, goals) {
    const from = new Set();
    starts.forEach((p) => { const k = p.j * g.w + p.i; if (!g.blocked[k]) from.add(comp[k]); });
    return goals.some((p) => { const k = p.j * g.w + p.i; return !g.blocked[k] && from.has(comp[k]); });
  }

  function overlaps(g, k, d) {
    return HORIZONTAL(d) ? g.usedH[k] : g.usedV[k];
  }

  // 出口（source の輪）から入口（target の輪）までの最短経路。返り値はマスと向きの列。
  function search(g, starts, goals, win, limit) {
    const goalMap = new Map();
    let gi = 0, gj = 0;
    goals.forEach((p) => { goalMap.set(p.j * g.w + p.i, p); gi += p.i; gj += p.j; });
    gi /= goals.length; gj /= goals.length;
    const gen = ++g.gen;
    const { cost, prev, stamp } = g;
    const costOf = (s) => (stamp[s] === gen ? cost[s] : Infinity);
    const visit = (s, c, p) => { stamp[s] = gen; cost[s] = c; prev[s] = p; };
    const heap = new Heap();
    // 目的地までの見積もりを少し重く見て（重み付き A*）、探索を速くする
    const hfun = (i, j) => 1.3 * (Math.abs(i - gi) + Math.abs(j - gj));
    starts.forEach((p) => {
      const k = p.j * g.w + p.i;
      if (g.blocked[k]) return;
      const s = k * 4 + p.dir;
      visit(s, (overlaps(g, k, p.dir) ? overlapCost : 0) + portCost * p.off, -1);
      heap.push([hfun(p.i, p.j), s]);
    });
    let expanded = 0;
    while (heap.size) {
      const [, s] = heap.pop();
      const k = s >> 2, d = s & 3;
      const i = k % g.w, j = (k / g.w) | 0;
      const goal = goalMap.get(k);
      // 入口のマスには、箱に向かう向き（出口の向きの逆）で着く
      if (goal && (goal.dir ^ 1) === d) {
        const path = [];
        for (let t = s; t !== -1; t = prev[t]) path.push([t >> 2, t & 3]);
        return path.reverse();
      }
      if (++expanded > limit) break;
      for (let nd = 0; nd < 4; nd++) {
        if (nd === (d ^ 1)) continue;   // 逆戻りしない
        const ni = i + DIRS[nd][0], nj = j + DIRS[nd][1];
        if (ni < win.i1 || nj < win.j1 || ni > win.i2 || nj > win.j2) continue;
        const nk = nj * g.w + ni;
        if (g.blocked[nk]) continue;
        let c = cost[s] + 1 + (nd !== d ? BEND_COST : 0);
        if (overlaps(g, nk, nd)) c += overlapCost;
        // 曲がるマスは横・縦の両方を使うので、曲がる先の向きでも重ならないか
        if (nd !== d && overlaps(g, k, nd)) c += overlapCost;
        // 入口は辺の中点に近いほど良い（箱に向かう向きで入るときだけ）
        const goal2 = goalMap.get(nk);
        if (goal2 && (goal2.dir ^ 1) === nd) c += portCost * goal2.off;
        const ns = nk * 4 + nd;
        if (c < costOf(ns)) {
          visit(ns, c, s);
          heap.push([c + hfun(ni, nj), ns]);
        }
      }
    }
    return null;
  }

  function markUsed(g, path) {
    for (let t = 0; t < path.length; t++) {
      const [k, d] = path[t];
      if (HORIZONTAL(d)) g.usedH[k] = 1; else g.usedV[k] = 1;
      const next = path[t + 1];
      if (next && next[1] !== d) {   // 曲がるマスは両方の向きを使う
        if (HORIZONTAL(next[1])) g.usedH[k] = 1; else g.usedV[k] = 1;
      }
    }
  }

  // 経路のマス列を、箱の縁から縁までの折れ線（曲がり角だけ）にする。
  // 辺に垂直な最初（atEnd なら最後）の区間を、辺の中点の位置へずらす。
  // 中点に最も近いマスから出入りしているときだけ（他の線が使っている中央寄りのマスはそのまま）
  function snapToMidpoint(g, all, dir, box, atEnd) {
    const vertical = dir === 2 || dir === 3;           // 上下の辺から出入りする
    const key = vertical ? "x" : "y";
    const center = vertical ? (box.x1 + box.x2) / 2 : (box.y1 + box.y2) / 2;
    const origin = vertical ? g.ox : g.oy;
    const nearest = origin + Math.round((center - origin) / g.cell) * g.cell;
    const seq = atEnd ? [...all].reverse() : all;
    if (seq[1][key] !== nearest) return;
    const old = seq[0][key];
    for (let t = 0; t < seq.length && seq[t][key] === old; t++) seq[t][key] = center;
  }

  // 仕上げ: 端点が 1 本だけの辺で、中点から少しずれている線を、ほかの線と重ならなければ中点まで寄せる
  function polishEndpoints(results, boxes, maxShift) {
    const ends = new Map();   // "ノード|辺" → [{ poly, atEnd }]
    const sideOf = (b, p) => (Math.abs(p.y - b.y1) < 0.5 ? "top" : Math.abs(p.y - b.y2) < 0.5 ? "bottom"
      : Math.abs(p.x - b.x1) < 0.5 ? "left" : "right");
    results.forEach(({ edge, poly }) => {
      if (!poly) return;
      [[edge.source().id(), poly[0], false], [edge.target().id(), poly[poly.length - 1], true]].forEach(([id, p, atEnd]) => {
        const key = `${id}|${sideOf(boxes.get(id), p)}`;
        if (!ends.has(key)) ends.set(key, []);
        ends.get(key).push({ poly, atEnd, box: boxes.get(id), side: key.split("|")[1] });
      });
    });
    // 線分の一覧は 1 度だけ作る（点は下でその場で書き換えるので、一覧は常に今の位置を指す）。
    // 端ごとに作り直すと、線が千本を超える図で数秒止まる
    const allSegments = results.flatMap(({ poly }) => (poly ? poly.slice(1).map((p, t) => [poly[t], p, poly]) : []));
    ends.forEach((list) => {
      if (list.length !== 1) return;
      const { poly, atEnd, box, side } = list[0];
      const vertical = side === "top" || side === "bottom";   // 上下の辺から出入りする区間は縦の線
      const key = vertical ? "x" : "y";
      const center = vertical ? (box.x1 + box.x2) / 2 : (box.y1 + box.y2) / 2;
      const seq = atEnd ? [...poly].reverse() : poly;
      const old = seq[0][key];
      const shift = center - old;
      if (Math.abs(shift) < 0.01 || Math.abs(shift) > maxShift) return;
      let n = 0;
      while (n < seq.length && seq[n][key] === old) n++;
      // ずらす区間が、ほかの線の同じ向きの区間と重ならないか確かめる
      const other = vertical ? "y" : "x";
      const lo = Math.min(seq[0][other], seq[n - 1][other]), hi = Math.max(seq[0][other], seq[n - 1][other]);
      const clash = allSegments.some(([a, b, owner]) => owner !== poly && Math.abs(a[key] - b[key]) < 0.01 &&
        Math.abs(a[key] - center) < 3 && Math.min(Math.max(a[other], b[other]), hi) - Math.max(Math.min(a[other], b[other]), lo) > 1);
      if (clash) return;
      for (let t = 0; t < n; t++) seq[t][key] = center;
    });
  }

  function toPolyline(g, path, srcBox, tgtBox) {
    const pts = path.map(([k]) => ({ x: cx(g, k % g.w), y: cy_(g, (k / g.w) | 0) }));
    const first = pts[0], last = pts[pts.length - 1];
    const d0 = path[0][1], d1 = path[path.length - 1][1];
    const start = d0 === 3 ? { x: first.x, y: srcBox.y1 } : d0 === 2 ? { x: first.x, y: srcBox.y2 }
      : d0 === 1 ? { x: srcBox.x1, y: first.y } : { x: srcBox.x2, y: first.y };
    const end = d1 === 2 ? { x: last.x, y: tgtBox.y1 } : d1 === 3 ? { x: last.x, y: tgtBox.y2 }
      : d1 === 0 ? { x: tgtBox.x1, y: last.y } : { x: tgtBox.x2, y: last.y };
    const all = [start, ...pts, end];
    snapToMidpoint(g, all, d0, srcBox, false);
    snapToMidpoint(g, all, d1, tgtBox, true);
    const out = [all[0]];
    for (let t = 1; t < all.length - 1; t++) {
      const a = out[out.length - 1], b = all[t], c = all[t + 1];
      const collinear = (a.x === b.x && b.x === c.x) || (a.y === b.y && b.y === c.y);
      if (!collinear) out.push(b);
    }
    out.push(all[all.length - 1]);
    return out;
  }

  // 折れ線を Cytoscape の segments 指定（始点→終点の線を基準にした重み・距離）に変換する。
  // 縮小で非表示（display: none）になっていないか。Cytoscape の visible() は不透明度 0 の線
  // （経路の計算待ちで隠している線）も「見えない」とみなすので使わない
  const shown = (ele) => ele.style("display") !== "none";
  const shownEdge = (e) => shown(e) && shown(e.source()) && shown(e.target());

  function applyPolyline(edge, poly) {
    const s = poly[0], e = poly[poly.length - 1];
    const src = edge.source().position(), tgt = edge.target().position();
    const vx = e.x - s.x, vy = e.y - s.y;
    const len2 = vx * vx + vy * vy || 1;
    const len = Math.sqrt(len2);
    const weights = [], distances = [];
    poly.slice(1, -1).forEach((p) => {
      const px = p.x - s.x, py = p.y - s.y;
      weights.push((px * vx + py * vy) / len2);
      // Cytoscape の segment-distances は、始点→終点の向きを (-vy, vx) 方向へずらす距離
      distances.push((py * vx - px * vy) / len);
    });
    const style = {
      "source-endpoint": `${s.x - src.x}px ${s.y - src.y}px`,
      "target-endpoint": `${e.x - tgt.x}px ${e.y - tgt.y}px`,
      "edge-distances": "endpoints",
    };
    if (weights.length) {
      style["curve-style"] = "segments";
      style["segment-weights"] = weights.map((w) => w.toFixed(4)).join(" ");
      style["segment-distances"] = distances.map((d) => d.toFixed(2)).join(" ");
    } else {
      style["curve-style"] = "straight";
    }
    edge.style(style);
    edge.scratch("_poly", poly);
  }

  function clear(cy) {
    cy.edges().forEach((e) => e.removeScratch("_poly"));
    cy.edges().removeStyle("curve-style segment-weights segment-distances source-endpoint target-endpoint edge-distances taxi-direction");
    cy.removeScratch("_route");
  }

  const WINDOW_PAD = 14;     // まず探す範囲：2 つの箱を囲む範囲 + この数のマス
  const SLICE_MS = 25;       // 1 回にまとめて計算する時間（その後は画面の操作に譲る）
  // 1 回の引き直しにかける時間の上限。超えたら、残りの線は探索せずに Cytoscape 標準の直角線で描く
  // （線が千本を超える図で、引き終わるまで数十秒、線が 1 本も出ないままになるのを防ぐ）
  const BUDGET_MS = 4000;
  let runId = 0;

  // 遺伝子の箱（枠線を含む）。boundingBox() は強調表示の外側の輪（outline）まで含めてしまうので、
  // 中心と幅・高さから求める
  function nodeBox(n) {
    const p = n.position(), hw = n.outerWidth() / 2, hh = n.outerHeight() / 2;
    return { x1: p.x - hw, x2: p.x + hw, y1: p.y - hh, y2: p.y + hh, w: hw * 2, h: hh * 2 };
  }

  // 線の通り道は地図の中で変えない:
  // - 「固定の線」（ふだん出している線。凡例・条件・縮小で今は隠しているものも含む）は、遺伝子の位置・大きさが
  //   変わったときだけ、まとめて引き直す。見えている線の組によらず、いつも同じ順で同じ形になる。
  // - 「ふだん隠す線」（data.tfPair: 転写因子どうし・まとめの線）は、固定の線を動かさずに、使っていない通り道に
  //   後から足す。一度引いたものは隠しても形を残し、また見せるときはそのまま使う。
  // 障害物は、今隠れているものも含めたすべての遺伝子の箱（隠れた遺伝子の場所も空けておく）。
  const isLeaf = (n) => !n.data("isComplex") && !n.data("isCxLabel");
  const isFixed = (e) => !e.data("tfPair") && e.source().id() !== e.target().id();
  function signature(leaves) {
    return leaves.map((n) => { const b = nodeBox(n); return `${n.id()}:${b.x1.toFixed(1)}:${b.y1.toFixed(1)}:${b.w.toFixed(1)}:${b.h.toFixed(1)}`; }).join("|");
  }
  const byLength = (a, b) => {
    const da = a.source().position(), ta = a.target().position();
    const db = b.source().position(), tb = b.target().position();
    return (Math.abs(da.x - ta.x) + Math.abs(da.y - ta.y)) - (Math.abs(db.x - tb.x) + Math.abs(db.y - tb.y))
      || (a.id() < b.id() ? -1 : 1);
  };

  function route(cy, onDone) {
    const id = ++runId;
    const t0 = performance.now();
    const leaves = cy.nodes().filter(isLeaf);
    const sig = signature(leaves);
    let state = cy.scratch("_route");
    const fixed = cy.edges().filter(isFixed);
    // 位置が変わった・新しく描き直した（固定の線に形がない）ときだけ、固定の線から全部引き直す
    const full = !state || state.sig !== sig || fixed.some((e) => !e.scratch("_poly"));
    let order;
    if (full) {
      if (!leaves.length) { clear(cy); if (onDone) onDone({ routed: 0, fallback: 0, ms: 0 }); return; }
      const boxes = new Map();
      leaves.forEach((n) => boxes.set(n.id(), nodeBox(n)));
      const g = buildGrid([...boxes.values()]);
      const regions = new Map();
      boxes.forEach((b, nid) => regions.set(nid, blockBox(g, b)));
      state = { sig, g, boxes, regions, comp: components(g), dense: fixed.length > DENSE, pending: true };
      // 短い線から先に引く（長い線が遠回りを引き受ける）。ふだん隠す線は、今見えているものだけ後から
      const extra = cy.edges().filter((e) => !isFixed(e) && shownEdge(e) && e.source().id() !== e.target().id());
      order = fixed.toArray().sort(byLength).concat(extra.toArray().sort(byLength));
    } else {
      // 位置はそのまま: まだ形のない、見えている線（ふだん隠す線を見せたとき）だけ足す
      order = cy.edges().filter((e) => shownEdge(e) && !e.scratch("_poly") && e.source().id() !== e.target().id())
        .toArray().sort(byLength);
      cy.edges().filter((e) => e.hasClass("routing") && e.scratch("_poly")).removeClass("routing");
      if (!order.length) { if (onDone) onDone({ routed: 0, fallback: 0, ms: 0, incremental: true }); return; }
    }
    const { g, boxes, regions, comp } = state;
    const fixedCount = full ? fixed.length : 0;
    const full_ = { i1: 0, j1: 0, i2: g.w - 1, j2: g.h - 1 };
    let routed = 0, fallback = 0, index = 0;
    const results = [];
    const overlapped = [];   // 重なりを避けきれなかった線の id
    const dense = state.dense;
    overlapCost = dense ? 15 : OVERLAP_COST;
    portCost = dense ? DENSE_PORT_COST : PORT_COST;

    let overBudget = false;
    function routeOne(edge) {
      const sr = regions.get(edge.source().id()), tr = regions.get(edge.target().id());
      if (!sr || !tr) return;
      if (overBudget || performance.now() - t0 > BUDGET_MS) {   // 時間切れ: 箱を避けない標準の直角線にする
        overBudget = true;
        results.push({ edge, poly: null });
        fallback++;
        overlapped.push(edge.id());
        return;
      }
      const starts = ports(g, sr, boxes.get(edge.source().id())), goals = ports(g, tr, boxes.get(edge.target().id()));
      if (!starts.length || !goals.length) return;
      if (!reachable(g, comp, starts, goals)) {   // 箱に囲まれて道がない: 探索せずに標準の直角線にする
        results.push({ edge, poly: null });
        fallback++;
        overlapped.push(edge.id());
        return;
      }
      const win = {
        i1: Math.max(0, Math.min(sr.i1, tr.i1) - WINDOW_PAD), j1: Math.max(0, Math.min(sr.j1, tr.j1) - WINDOW_PAD),
        i2: Math.min(g.w - 1, Math.max(sr.i2, tr.i2) + WINDOW_PAD), j2: Math.min(g.h - 1, Math.max(sr.j2, tr.j2) + WINDOW_PAD),
      };
      let path = search(g, starts, goals, win, dense ? 40000 : 120000);
      if (!dense && (!path || path.some(([k, d]) => overlaps(g, k, d)))) {
        const wide = search(g, starts, goals, full_, 400000);   // 近くで重ならずに引けなければ図全体で探す
        if (wide && (!path || !wide.some(([k, d]) => overlaps(g, k, d)))) path = wide;
      }
      if (!path) {   // 見つからなければ Cytoscape 標準の直角線で描く（箱を避けない）
        results.push({ edge, poly: null });
        fallback++;
        overlapped.push(edge.id());
        return;
      }
      if (path.some(([k, d]) => overlaps(g, k, d))) { fallback++; overlapped.push(edge.id()); }   // 重なりを避けられなかった線
      markUsed(g, path);
      results.push({ edge, poly: toPolyline(g, path, boxes.get(edge.source().id()), boxes.get(edge.target().id())) });
      routed++;
    }

    function slice() {
      if (id !== runId) return;   // 新しい計算が始まった
      const until = performance.now() + SLICE_MS;
      while (index < order.length && performance.now() < until) routeOne(order[index++]);
      if (index < order.length) { setTimeout(slice, 0); return; }
      // 端の寄せは固定の線どうしだけで決める（ふだん隠す線を見せても、固定の線の形が変わらないように）
      if (full) polishEndpoints(results.filter((r) => isFixed(r.edge)), boxes, g.cell * 1.5);
      cy.batch(() => {
        if (full) clear(cy);
        results.forEach(({ edge, poly }) => {
          if (poly) applyPolyline(edge, poly);
          else { edge.style({ "curve-style": "taxi", "taxi-direction": "auto" }); edge.scratch("_poly", "taxi"); }
        });
        cy.collection(results.map((r) => r.edge)).removeClass("routing");
      });
      state.pending = false;
      cy.scratch("_route", state);
      if (onDone) onDone({ routed, fallback, dense, overBudget, total: order.length, overlapped, incremental: !full, fixed: fixedCount,
                           edgeIds: order.map((e) => e.id()), ms: Math.round(performance.now() - t0) });
    }
    slice();
  }

  function cancel() { runId++; }

  window.orthoRouter = { route, clear, cancel };
})();
