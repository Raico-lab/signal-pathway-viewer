// Python (PyQt) 側から window.app の関数を呼び出して描画を更新する。
// クリック等のイベントは QWebChannel 経由で Python の bridge に通知する。
(function () {
  "use strict";

  let bridge = null;
  let colorMode = "role";
  // 線の色: "type" は修飾の種類ごと、"effect" は作用（促進・抑制・作用不明・結合）ごと
  let edgeColorMode = "type";
  // 作用の色はアプリ全体で 促進＝赤・抑制＝青（TFs 一覧も同じ）
  const EFFECT_COLORS = { activate: "#e53935", inhibit: "#1e88e5", none: "#757575", binding: "#bdbdbd" };
  const edgeEffectKey = (e) => (e.data("directed") === false ? "binding" : e.data("effect") || "none");
  const edgeColor = (e) => (edgeColorMode === "effect" ? EFFECT_COLORS[edgeEffectKey(e)] || EFFECT_COLORS.none : e.data("color"));
  let lastLegend = { types: [], categories: [], roles: [] };
  let hidden = { roles: [], types: [], effects: [] };   // チェックを外した（隠す）役割・制御の種類・作用の向き
  let currentLayout = "dagre_tb";
  let showSymbols = false;   // 線上の文字（種類記号）は既定で表示しない
  let showMore = true;   // 遺伝子名の下に、表示されていない下流の数（+数字）を出すか
  let maxDepth = 2;       // 表示している下流の段数
  let lodDepth = null;    // 縮小で見せている下流の段数
  let orthogonal = true;  // 線を直角の折れ線で引き回すか

  // 直角の折れ線を引き直す（配置が変わったとき・表示するノードが変わったとき）
  let rerouteTimer = null;
  let animating = false;   // 注目を移したときのアニメーション中は線を引き直さない（移動後にまとめて引く）
  // 何番目の表示指示か。新しい指示が来たら、前の表示の残りの処理（配置・アニメーション・線の経路計算）は捨てる
  let renderGen = 0;
  // 重なりを避けきれなかった案内を出す線の本数（アプリの確認 WARN_EDGES と同じ。150〜200 本では案内は出さない）
  const NOTICE_EDGES = 200;
  // ---- 計算中の印 ----
  // 並べ直し・線の計算を始めてから BUSY_DELAY_MS たっても終わらなければ、カーソルの右下に回る印を出す
  // （カーソルが地図の外なら最後に地図の上にあった所、一度も来ていなければ地図の中央）。一度出したら BUSY_MIN_MS は出しておく
  const BUSY_DELAY_MS = 600, BUSY_MIN_MS = 300, BUSY_MAX_MS = 60000;
  const busyMark = document.getElementById("busy");
  let busySince = null, busyShownAt = 0, busyTimer = null, busyHideTimer = null, busyGiveUp = null;
  let mouseAt = null;
  function placeBusy() {
    const box = document.getElementById("cy").getBoundingClientRect();
    const p = mouseAt || { x: box.left + box.width / 2, y: box.top + box.height / 2 };
    busyMark.style.left = `${p.x + 14}px`;
    busyMark.style.top = `${p.y + 14}px`;
  }
  document.addEventListener("mousemove", (e) => {
    mouseAt = { x: e.clientX, y: e.clientY };
    if (busyMark.classList.contains("on")) placeBusy();
  });
  function beginBusy() {
    clearTimeout(busyHideTimer);
    if (busySince !== null) return;
    busySince = performance.now();
    busyTimer = setTimeout(() => { placeBusy(); busyMark.classList.add("on"); busyShownAt = performance.now(); }, BUSY_DELAY_MS);
    busyGiveUp = setTimeout(endBusy, BUSY_MAX_MS);   // 終わりの合図を取りこぼしても出し続けない
  }
  function endBusy() {
    if (busySince === null) return;
    busySince = null;
    clearTimeout(busyTimer);
    clearTimeout(busyGiveUp);
    if (!busyMark.classList.contains("on")) return;
    const left = BUSY_MIN_MS - (performance.now() - busyShownAt);
    busyHideTimer = setTimeout(() => busyMark.classList.remove("on"), Math.max(0, left));
  }

  function scheduleReroute() {   // ズーム・ドラッグの直後は、落ち着いてからまとめて引き直す
    if (animating) return;
    clearTimeout(rerouteTimer);
    rerouteTimer = setTimeout(reroute, 150);
  }
  function reroute() {
    clearTimeout(rerouteTimer);
    beginBusy();
    if (animating) return;   // 動き終わったら呼び直される
    if (!orthogonal) {
      window.orthoRouter.cancel();
      window.orthoRouter.clear(cy);
      cy.edges().removeClass("routing");
      endBusy();
      return;
    }
    window.routeDone = null;
    window.orthoRouter.route(cy, (r) => {
      window.routeDone = r;
      endBusy();
      if (r.routed) console.debug(`route: ${r.routed} edges, overlap ${r.fallback}, ${r.ms} ms`);
      // 案内は、実際に見えている線（薄くしたもの・隠したものを除く）が NOTICE_EDGES 本を超え、
      // その中に重なりを避けきれなかった線があるときだけ出す
      const visible = (id) => { const e = cy.getElementById(id); return e.nonempty() && !e.hasClass("faded") && !e.hasClass("filtered") && !e.hasClass("tfpair-off"); };
      const shownTotal = (r.edgeIds || []).filter(visible).length;
      const shownOverlap = (r.overlapped || []).filter(visible).length;
      if (r.dense && shownOverlap && shownTotal > NOTICE_EDGES) {
        showMessage(`線が多いため、${shownTotal} 本のうち ${shownOverlap} 本は重なりを避けきれていません。` +
                    "段数を減らすか、凡例のチェックで種類を絞ると見やすくなります");
        setTimeout(() => showMessage(""), 8000);
      }
    });
  }

  // ---- 条件ごとの発現・リン酸化・タンパク質量（色分けが mrna / phospho / protein のとき） ----
  // 値は遺伝子名 → [対照との log2 の比, 補足]。DATA_MAX（4 倍）以上の変化は同じ濃さにする
  // 条件ごとの実測（mrna など）と、破壊株での実測（del_mrna・del_phospho）。値はどれも log2 比
  const DATA_KINDS = ["mrna", "phospho", "protein", "del_mrna", "del_phospho"];
  const DATA_MAX = 2, DATA_NONE = "#e0e0e0", DATA_KO = "#212121";
  let dataValues = {}, dataTitle = null, dataNote = null, dataKo = null;   // dataKo: 破壊株で壊した遺伝子
  function mix(a, b, t) {
    const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
    const [x, y] = [p(a), p(b)];
    return "#" + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join("");
  }
  function dataColor(v) {
    const t = Math.min(1, Math.abs(v) / DATA_MAX);
    return v >= 0 ? mix("#ffffff", "#d32f2f", t) : mix("#ffffff", "#1565c0", t);
  }

  function nodeTextColor(n) {   // 濃い赤・青の上では白い文字にする
    if (!DATA_KINDS.includes(colorMode)) return "#212121";
    if (dataKo && n.data("gene") === dataKo) return "#ffffff";
    const v = dataValues[n.data("gene")];
    return v !== undefined && Math.abs(v[0]) >= 1.4 ? "#ffffff" : "#212121";
  }

  function nodeFill(n) {
    if (n.data("isLabel") || n.data("isCxLabel")) return "#ffffff";   // 段の見出し・複合体の名札（背景は透明にしている）
    if (colorMode === "role") return n.data("roleColor");
    if (DATA_KINDS.includes(colorMode)) {
      if (dataKo && n.data("gene") === dataKo) return DATA_KO;
      const v = dataValues[n.data("gene")];
      return v === undefined ? DATA_NONE : dataColor(v[0]);
    }
    return n.data("color");
  }

  function nodeLabel(n) {
    const lines = [n.data("label")];
    const extra = [];
    if (showMore && n.data("more")) extra.push(`+${n.data("more")}`);
    if (extra.length) lines.push(extra.join("  "));
    return lines.join("\n");
  }

  function edgeLabel(e) {
    const parts = [];
    if (showSymbols && e.data("symbol")) parts.push(e.data("symbol"));
    return parts.join(" ");
  }

  // ノードの大きさ: 文字数に合わせ、直角の線ではつながりの多いものほど横に広げて出入り口を確保する
  const FOCUS_SCALE = 1.35;   // 注目中（地図の中心）の遺伝子は大きく描く
  function nodeWidth(n) {
    const scale = n.data("focus") ? FOCUS_SCALE : 1;
    const text = (Math.max(...nodeLabel(n).split("\n").map((l) => l.length)) * 8.5 + 18) * scale;
    if (!orthogonal) return text;
    // 隠す・縮小で省くに関係なく、この表示でつながっている線の本数で決める（隠しても箱の大きさが変わらないように）
    // 省いている線（転写因子どうし）も数える: 箱の大きさで、その遺伝子の関係の広さが分かるように（見せる・隠すで大きさは変わらない）
    const degree = n.connectedEdges().length;
    return Math.min(360, Math.max(text, degree * 5));
  }
  function nodeHeight(n) {
    return Math.round((nodeLabel(n).includes("\n") ? 40 : 28) * (n.data("focus") ? FOCUS_SCALE : 1));
  }

  // 終点（制御される側）の印: 形が作用を表し、印のある端で向きが分かる。作用不明でも向きがあれば白抜きの菱形を付ける
  const ARROWS = { activate: "triangle", inhibit: "tee", none: "diamond" };

  const cy = cytoscape({
    container: document.getElementById("cy"),
    minZoom: 0.05,
    autoungrabify: true,   // 遺伝子はドラッグで動かせない（配置は自動で決める）
    maxZoom: 4,
    style: [
      { selector: "node", style: {
        "shape": "round-rectangle",
        "background-color": nodeFill,
        "label": nodeLabel,
        "text-wrap": "wrap",
        "text-valign": "center",
        "text-halign": "center",
        "font-size": 12,
        "font-weight": "bold",
        "color": nodeTextColor,
        "width": nodeWidth,
        "height": nodeHeight,
        "border-width": 1.5,
        "border-color": "#546e7a",
        "min-zoomed-font-size": 4,   // 縮小した大きなマップでも名前を出す（画面上 4px 未満で省略）
      }},
      // 強調表示は、遺伝子の枠線の色で示す
      // 上流・下流を追加した起点（2 回目のクリック）: 紫の枠線。選択を外しても残る
      { selector: "node[?grown]", style: { "border-width": 3.5, "border-color": "#8e24aa" }},
      // 注目中（地図の中心）: 橙の太い枠線、名前を少し大きく
      { selector: "node[?focus]", style: { "border-width": 4.5, "border-color": "#ff8f00", "font-size": 16 }},
      { selector: "node[?isComplex]", style: {
        "shape": "round-rectangle",
        "background-color": "data(color)",
        "background-opacity": 0.10,
        "border-width": 2,
        "border-style": "dashed",
        "border-color": "data(color)",
        "label": "data(label)",
        "text-valign": "top",
        "text-halign": "center",
        "font-size": 11,
        "color": "#37474f",
        "text-opacity": 0,   // 名前は線より手前に出すため、別の名札（isCxLabel）で描く
        "padding": "14px",
      }},
      // 複合体の名札: 線より手前に描く（ふつうのノードは線の上に描かれる）。クリックは素通り
      { selector: "node[?isCxLabel]", style: {
        "label": "data(label)", "background-opacity": 0, "border-width": 0, "width": 1, "height": 1,
        "font-size": 11, "font-weight": "bold", "color": "#37474f", "text-valign": "center", "text-halign": "center",
        "text-background-color": "#fafafa", "text-background-opacity": 0.85, "text-background-padding": "2px",
        "text-background-shape": "round-rectangle", "events": "no",
      }},
      { selector: "node[?isLabel]", style: {
        "label": "data(label)", "background-opacity": 0, "border-width": 0, "width": 80, "height": 24,
        "font-size": 14, "font-weight": "bold", "color": "#78909c", "text-valign": "center", "text-halign": "center",
        "events": "no",
      }},
      { selector: "node.tips-hidden", style: { "border-style": "dashed", "border-width": 3 }},
      { selector: ".lod-hidden", style: { "display": "none" }},
      { selector: ".filtered", style: { "display": "none" }},
      // 転写因子どうしの線: ふだんは隠し、どちらかの端を緑で選んだときだけ見せる（syncTfPairs）
      { selector: "edge.tfpair-off", style: { "display": "none" }},
      { selector: "edge", style: {
        "curve-style": "bezier",
        "width": 1.6,
        "line-color": edgeColor,
        "target-arrow-color": edgeColor,
        "target-arrow-shape": (e) => (e.data("directed") === false ? "none" : ARROWS[e.data("effect")] || "diamond"),
        "target-arrow-fill": (e) => (e.data("effect") === "none" ? "hollow" : "filled"),
        "arrow-scale": 1.4,   // 細い線でも矢印・T 字が見えるように
        // 破線は結合（向きなし）だけ。作用不明でも向きのある関係は実線
        "line-style": (e) => e.data("directed") === false ? "dashed" : "solid",
        "label": edgeLabel,
        "font-size": 9,
        "color": "#424242",
        "text-rotation": "autorotate",
        "text-background-color": "#fafafa",
        "text-background-opacity": 0.85,
        "text-background-padding": "1px",
        "min-zoomed-font-size": 6,
      }},
      { selector: "edge[!directed]", style: { "line-dash-pattern": [3, 4] }},
      { selector: "edge:selected", style: { "overlay-color": "#1e88e5", "overlay-opacity": 0.18, "overlay-padding": 6 }},
      // 選択中: 1 回目のクリックは緑、2 回目は紫の枠線（注目中のものは太さをそのままにして色だけ変える）
      { selector: "node.picked", style: { "border-color": "#43a047" }},
      { selector: "node.picked2", style: { "border-color": "#8e24aa" }},
      { selector: "node.picked[!focus], node.picked2[!focus]", style: { "border-width": 3.5 }},
      { selector: ".faded", style: { "opacity": 0.12 }},
      // 左の「経路」タブ: チェックした遺伝子（と共通の制御因子・標的）に青緑の縁取り。
      // 遺伝子をクリックしてその遺伝子を通る経路を強調している間は、ほかの経路を少し薄くする
      { selector: "node.rel-end", style: { "underlay-color": "#26a69a", "underlay-padding": 7, "underlay-opacity": 0.45,
                                           "underlay-shape": "round-rectangle" }},
      // 「経路」タブの基準の遺伝子は濃い縁取り
      { selector: "node.rel-anchor", style: { "underlay-color": "#00695c", "underlay-padding": 10, "underlay-opacity": 0.6,
                                              "underlay-shape": "round-rectangle" }},
      // 制御遺伝子探索: 観測した遺伝子の印（一致・矛盾・届かない）
      { selector: "node.obs-ok", style: { "underlay-color": "#43a047", "underlay-padding": 7, "underlay-opacity": 0.5,
                                          "underlay-shape": "round-rectangle" }},
      { selector: "node.obs-bad", style: { "underlay-color": "#e53935", "underlay-padding": 7, "underlay-opacity": 0.55,
                                           "underlay-shape": "round-rectangle" }},
      { selector: "node.obs-none", style: { "underlay-color": "#9e9e9e", "underlay-padding": 7, "underlay-opacity": 0.45,
                                            "underlay-shape": "round-rectangle" }},
      { selector: ".rel-dim", style: { "opacity": 0.3 }},
      // 左の「条件」タブで外した条件だけの関係（線は下で消す）と、選んだ条件の関係を持たない遺伝子は薄くする
      { selector: ".cond-dim", style: { "opacity": 0.12 }},
      // 強調していない線は薄くせず消す（クリックも拾わない）
      { selector: "edge.faded, edge.rel-dim, edge.cond-dim", style: { "opacity": 0, "events": "no" }},
      { selector: "edge.rel-focus", style: { "width": 4 }},
      { selector: "edge.routing", style: { "opacity": 0, "events": "no" }},
      // 下流が多すぎる遺伝子のまとめの枠（制御の種類ごと。線はふだん隠す）
      { selector: "node[?isBundle]", style: { "border-style": "solid", "border-width": 1.5, "background-opacity": 0.06 }},
      // パラログの枠: 組ごとの色の二重線と薄い塗り（複合体の点線と線の形で見分ける）
      { selector: "node[?isParalog]", style: { "border-style": "double", "border-width": 4, "background-opacity": 0.08, "padding": "8px" }},
      { selector: "node.found", style: { "border-width": 4, "border-color": "#00acc1" }},
      // 遺伝子の箱（と複合体の名札）は、いつも線より手前に描く。線が遺伝子の上を通るとき（線が多くて
      // 引き回しきれなかったときなど）も、箱の裏を通るようにする。Cytoscape の既定では、枠の中の遺伝子に
      // つながる線は枠の深さで描かれ、枠の外の遺伝子の箱より手前に出てしまう
      { selector: "node[!isComplex]", style: { "z-compound-depth": "top", "z-index": 10 }},
    ],
  });

  window.__cy = cy;   // 開発時の確認用

  // ---- 近傍ハイライト ----
  function highlight(ele) {
    cy.elements().removeClass("faded");
    if (!ele) { applyConditionFilter(); return; }
    let keep = ele.isNode() ? ele.closedNeighborhood() : ele.union(ele.connectedNodes());
    keep = keep.union(keep.parents());
    cy.elements().not(keep).addClass("faded");
    syncComplexLabels();
    applyConditionFilter();
  }

  // ---- 複合体の名札（線より手前に描く） ----
  // 複合体の枠の上端の中央に置き、枠の表示・薄さに合わせる。構成要素が動いたら追いかける
  function addComplexLabels() {
    cy.add(cy.nodes("[?isComplex]").map((c) => ({
      group: "nodes", selectable: false, grabbable: false,
      data: { id: `cxlabel:${c.id()}`, isCxLabel: true, label: c.data("label"), of: c.id() },
    })));
    syncComplexLabels();
  }
  // 複合体・パラログの名札は、枠の中の遺伝子を緑で選んでいるときと、枠そのものを押したときだけ出す。
  // 下流のまとめの名札（「PHO85 の下流 リン酸化 377」など）は、何のまとまりか分かるよういつも出す
  let openFrameId = null;
  const labelOpen = (c) => c.data("isBundle") || c.id() === openFrameId || membersOf(c).some((m) => pickedSet.has(m.id()));
  function syncComplexLabels() {
    cy.batch(() => cy.nodes("[?isCxLabel]").forEach((l) => {
      const c = cy.getElementById(l.data("of"));
      const hide = c.empty() || c.hasClass("filtered") || c.hasClass("lod-hidden") || !c.children().length
        || !labelOpen(c);
      if (l.hasClass("filtered") !== hide) l.toggleClass("filtered", hide);
      if (hide) return;
      if (l.hasClass("faded") !== c.hasClass("faded")) l.toggleClass("faded", c.hasClass("faded"));
      const bb = c.boundingBox({ includeLabels: false, includeOverlays: false });
      const x = (bb.x1 + bb.x2) / 2, y = bb.y1 - parseFloat(l.style("font-size")) * 0.75;
      const now = l.position();
      if (Math.abs(now.x - x) > 0.5 || Math.abs(now.y - y) > 0.5) l.position({ x, y });
    }));
  }
  let cxScheduled = false;
  cy.on("position", "node", (evt) => {
    if (cxScheduled || evt.target.data("isCxLabel")) return;   // 名札自身が動いたときは追いかけない（無限に繰り返すため）
    cxScheduled = true;
    requestAnimationFrame(() => { cxScheduled = false; syncComplexLabels(); });
  });

  // 1 回目のクリックで選択（右側に情報を表示）、選択中の遺伝子をもう一度クリックすると注目
  let selectedNodeId = null;
  let clickStage = 0;   // 選択中の遺伝子を何回クリックしたか
  // 詳細を表示している遺伝子に「選択中」の印を付ける（null で外す）。stage は何回目のクリックか
  // Shift を押しながらのクリックで、緑の選択を複数にできる（pickedSet）。詳細を出すのは最後にクリックしたもの
  let pickedSet = new Set();
  function markPicked(id, stage = 1) {
    pickedSet = new Set(id && stage < 2 ? [id] : []);
    cy.nodes(".picked, .picked2").removeClass("picked picked2");
    if (id) cy.getElementById(id).addClass(stage >= 2 ? "picked2" : "picked");
    renderMarks();
    syncTfPairs();
  }
  function refreshPicked() {
    cy.nodes(".picked, .picked2").removeClass("picked picked2");
    pickedSet.forEach((id) => cy.getElementById(id).addClass("picked"));
    renderMarks();
    syncTfPairs();
  }
  // 転写因子どうしの線は、どちらかの端が緑で選ばれているときと、「経路」の一覧で選んだ経路の線のときだけ見せる。
  // 見せる線が変わったら線を引き直す（遺伝子の配置はそのまま）
  // 注目している線（クリックした線・説明欄から選んだ線）は、省く線でも見せる
  let focusEdgeId = null;
  // まとめの枠を押すと、その枠の遺伝子の線を見せる（もう一度押すと隠す）
  const openBundles = new Set();
  function tfPairOff(e, focusEdges) {
    // まとめの起点からの線（hubEdge）は、起点を選んでも出さない（数百本が一度に出るのを避ける）
    const bySource = pickedSet.has(e.data("source")) && !e.data("hubEdge");
    return !bySource && !pickedSet.has(e.data("target")) && !(focusEdges && focusEdges.has(edgeKey(e)))
      && e.id() !== focusEdgeId && !(e.data("bundle") && openBundles.has(e.data("bundle")))
      && !(e.data("frame") && e.data("frame") === openFrameId);   // 複合体の中の線は、その枠を押している間も見せる
  }
  function syncTfPairs() {
    syncComplexLabels();   // 緑の選択・まとめの枠を開いたかで、枠の名札を出し入れする
    const focusEdges = pathIds && pathFocus ? new Set(pathFocus.edges) : null;
    const shown = [], hidden = [];
    cy.batch(() => {
      cy.edges("[?tfPair]").forEach((e) => {
        const off = tfPairOff(e, focusEdges);
        if (e.hasClass("tfpair-off") === off) return;
        e.toggleClass("tfpair-off", off);
        (off ? hidden : shown).push(e);
      });
    });
    if (shown.length || hidden.length) applyConditionFilter();
    if (!shown.length && !hidden.length) return;
    // 隠すだけなら、残る線はそのまま（引き直さない）。見せる線も、経路を計算済み（一度出した線）ならその形をそのまま使う。
    // まだ経路のない線だけ、計算し終えるまで隠しておき、ほかの線を動かさずに足す（router.js）
    const unrouted = shown.filter((e) => !e.scratch("_poly"));
    cy.collection(shown.filter((e) => e.scratch("_poly"))).removeClass("routing");
    if (!unrouted.length) return;
    if (orthogonal) {
      cy.collection(unrouted).addClass("routing");
      reroute();
    }
  }
  // 緑の選択の 1 つを外す（Shift クリック・凡例の ×）。残りがあれば、最後に選んだものの詳細を出す
  function unpick(id) {
    if (id === menuNodeId) hideActionMenu();
    pickedSet.delete(id);
    if (id === selectedNodeId) {
      selectedNodeId = pickedSet.size ? [...pickedSet].pop() : null;
      clickStage = selectedNodeId ? 1 : 0;
    }
    refreshPicked();
    if (pickedSet.size) {
      if (pathIds) applyPath();
      else highlightMany(cy.collection([...pickedSet].map((p) => cy.getElementById(p))));
      if (bridge) bridge.onNodeClicked(selectedNodeId);
    } else {
      if (pathIds) applyPath(); else highlight(null);
      if (bridge) bridge.onBackgroundClicked();
    }
  }
  // 左の「経路」タブ: 経路上の遺伝子と、経路が通る関係の線だけを目立たせ、ほかは薄くする。
  // 緑の選択とは独立していて、地図をクリックしても消えない（タブで OFF にしたときだけ消える）。
  // pathFocus: 遺伝子をクリックしたとき、その遺伝子を通る経路（遺伝子と線）。それ以外の経路を少し薄くする
  // relAnchor: 「経路」タブで選んだ基準の遺伝子（緑で選んだ遺伝子とのつながりをアプリが求めて強調する）
  // pathMarks: 制御遺伝子探索で観測した遺伝子の印（id → "ok" / "bad" / "none"）
  let condOff = new Set(), condTop = false, condGenes = null;   // condGenes: 条件タブの「遺伝子」で目立たせる遺伝子（null なら絞らない）
  // 最も上流の経路: on の線のうち、起点になるもの（始まりの遺伝子に on の線が入ってこないもの）だけを残す。
  // 起点のない輪（on の線だけで回っている部分）は、どこが起点か決められないので輪の線をすべて残す
  function topmostEdges(on) {
    const into = new Set(on.filter((e) => e.data("source") !== e.data("target")).map((e) => e.data("target")));
    const roots = on.filter((e) => !into.has(e.data("source")));
    const reached = new Set(roots.map((e) => e.data("source")));
    // 遺伝子 → 出ていく線の先（毎回すべての線を見直すと、線の多い図で遺伝子の数 × 線の数の計算になる）
    const next = new Map();
    on.forEach((e) => {
      const s = e.data("source");
      if (!next.has(s)) next.set(s, []);
      next.get(s).push(e.data("target"));
    });
    const queue = [...reached];
    while (queue.length) {
      (next.get(queue.pop()) || []).forEach((t) => {
        if (!reached.has(t)) { reached.add(t); queue.push(t); }
      });
    }
    return roots.union(on.filter((e) => !reached.has(e.data("source"))));
  }
  function applyConditionFilter() {
    cy.batch(() => {
      cy.elements(".cond-dim").removeClass("cond-dim");
      if (condGenes) {
        // 条件タブの「遺伝子」: チェックした条件で大きく変化した遺伝子だけを目立たせ、ほかの遺伝子と、薄くした遺伝子につながる線を薄くする
        const dim = cy.nodes().filter((n) => isGene(n) && !condGenes.has(n.id()));
        dim.addClass("cond-dim");
        dim.connectedEdges().addClass("cond-dim");
        return;
      }
      if (!condOff.size && !condTop) return;
      // 目立たせる遺伝子は、選んだ条件の線のうち実際に見えているものの両端だけ（緑の選択で薄くした線・
      // 転写因子どうしで隠した線・凡例で隠した線は数えない。数えると線のない遺伝子だけが目立ってしまう）
      let on = cy.edges().filter((e) => !e.is(".tfpair-off, .filtered, .lod-hidden, .faded, .rel-dim")
        && (e.data("conds") || []).some((k) => !condOff.has(k)));
      if (condTop) on = topmostEdges(on);
      cy.edges().difference(on).addClass("cond-dim");
      let keep = on.connectedNodes();
      // 見えている線のない遺伝子（パラログとして加わっただけの遺伝子など）は、パラログの相手が目立っていれば目立たせる
      const lone = cy.nodes().filter((n) => isGene(n) && !keep.contains(n) && (n.data("paralogs") || []).length
        && !n.connectedEdges().some((e) => !e.is(".tfpair-off, .filtered, .lod-hidden, .faded, .rel-dim")));
      for (let changed = true; changed;) {   // 線のない相手どうしがつながっているときのために、増えなくなるまで繰り返す
        const add = lone.filter((n) => !keep.contains(n) && n.data("paralogs").some((id) => keep.contains(cy.getElementById(id))));
        changed = add.nonempty();
        keep = keep.union(add);
      }
      cy.nodes().filter((n) => isGene(n) && !keep.contains(n)).addClass("cond-dim");
    });
  }
  let pathIds = null, pathEdges = new Set(), pathEnds = new Set(), pathFocus = null, relAnchor = null, pathMarks = {};
  const edgeKey = (e) => `${e.data("source")}>${e.data("target")}`;
  // 線が経路の関係か。複合体の枠から出るまとめた線（graph_data.py の _group_edges）は、元の遺伝子の組（pairs）で見る
  const onPath = (e, keys, ids) => (e.data("pairs") || [edgeKey(e)]).some((k) => {
    const [s, t] = k.split(">");
    return keys.has(k) && ids.has(s) && ids.has(t);
  });
  function applyPath() {
    if (!pathIds) return;
    const ids = new Set(pathIds);
    const nodes = cy.nodes().filter((n) => ids.has(n.id()));
    const edges = cy.edges().filter((e) => onPath(e, pathEdges, ids));
    cy.elements().removeClass("rel-end rel-dim rel-focus").addClass("faded");
    nodes.union(edges).union(nodes.parents()).removeClass("faded");
    nodes.filter((n) => pathEnds.has(n.id())).addClass("rel-end");
    cy.nodes(".obs-ok, .obs-bad, .obs-none").removeClass("obs-ok obs-bad obs-none");
    Object.entries(pathMarks).forEach(([id, mark]) => {
      const n = cy.getElementById(id);
      if (n.nonempty()) n.removeClass("rel-end").addClass(`obs-${mark}`);
    });
    cy.nodes(".rel-anchor").removeClass("rel-anchor");
    if (relAnchor) cy.getElementById(relAnchor).addClass("rel-anchor");
    if (pathFocus) {
      // 強調する経路には、経路の表示に入っていない遺伝子・線（注目した遺伝子とのつながり）も含まれうる
      const fn = new Set(pathFocus.nodes), fe = new Set(pathFocus.edges);
      const fNodes = cy.nodes().filter((n) => fn.has(n.id()));
      const fEdges = cy.edges().filter((e) => onPath(e, fe, fn));
      fNodes.union(fEdges).union(fNodes.parents()).removeClass("faded");
      nodes.filter((n) => !fn.has(n.id())).addClass("rel-dim");
      edges.filter((e) => !onPath(e, fe, fn)).addClass("rel-dim");
      fEdges.addClass("rel-focus");
    }
    syncComplexLabels();
    syncTfPairs();
    applyConditionFilter();
  }
  function resetPath() {
    pathIds = null; pathEdges = new Set(); pathEnds = new Set(); pathFocus = null; relAnchor = null; pathMarks = {};
    cy.elements().removeClass("rel-end rel-anchor rel-dim rel-focus faded obs-ok obs-bad obs-none");
    syncComplexLabels();
    syncTfPairs();
    applyConditionFilter();
  }

  function highlightMany(nodes) {
    cy.elements().removeClass("faded");
    if (!nodes.length) { applyConditionFilter(); return; }
    let keep = nodes.closedNeighborhood();
    keep = keep.union(keep.parents());
    cy.elements().not(keep).addClass("faded");
    syncComplexLabels();
    applyConditionFilter();
  }

  // 凡例の「強調表示」に、いま印の付いている遺伝子名を並べる（× で個別に解除）
  let lastPickedKey = "[]";
  function renderMarks() {
    const chip = (n, kind, removable = true) =>
      `<span class="mark-chip ${kind}" data-node="${n.id()}" title="地図で場所を見る">${n.data("label")}` +
      (removable ? `<button data-kind="${kind}" data-id="${n.id()}" title="解除">×</button>` : "<span style='width:4px'></span>") +
      "</span>";
    const genes = cy.nodes().filter(isGene).sort((a, b) => a.data("label").localeCompare(b.data("label")));
    const picked = genes.filter((n) => n.hasClass("picked"));
    const grown = genes.filter((n) => n.data("grown"));
    const focus = genes.filter((n) => n.data("focus"));
    // 緑の選択をアプリに伝える（左の「経路」タブの遺伝子一覧に使う）。変わったときだけ
    const pickedKey = JSON.stringify(picked.map((n) => n.id()));
    if (pickedKey !== lastPickedKey && bridge) { lastPickedKey = pickedKey; bridge.onPickedChanged(pickedKey); }
    document.getElementById("marks-picked").innerHTML = picked.map((n) => chip(n, "picked")).join("");
    document.getElementById("marks-grown").innerHTML = grown.map((n) => chip(n, "grown")).join("");
    // 注目は常に 1 つなので × は出さない（別の遺伝子に注目すると切り替わる）
    document.getElementById("marks-focus").innerHTML = focus.map((n) => chip(n, "focus", false)).join("");
  }
  // 強調表示の名前を押すと、その遺伝子へ移る（枠の色・選択・注目は変えない）
  function zoomToGene(id) {
    const n = cy.getElementById(id);
    if (n.empty()) return;
    cy.animate({ center: { eles: n }, zoom: Math.max(cy.zoom(), 1.2), duration: 350,
                 complete: () => { viewportAnimationDone(); applyLod(); } });
  }
  document.getElementById("legend").addEventListener("click", (e) => {
    const button = e.target.closest(".mark-chip button");
    if (!button) {
      const name = e.target.closest(".mark-chip");
      if (name) { e.preventDefault(); zoomToGene(name.dataset.node); }
      return;
    }
    e.preventDefault();
    const id = button.dataset.id;
    if (button.dataset.kind === "picked") { unpick(id); return; }
    // 選択中の遺伝子の印を外すときは、クリックの回数も最初に戻す
    if (id === selectedNodeId) {
      selectedNodeId = null;
      clickStage = 0;
      markPicked(null);
      highlight(null);
    }
    if (button.dataset.kind === "grown") { if (bridge) bridge.onUngrow(id); }
  });
  // ---- 選択中の遺伝子をもう一度クリックしたときの選択肢（拡張・注目）。遺伝子の下に中央揃えで出す ----
  const actionMenu = document.getElementById("node-actions");
  let menuNodeId = null;
  function placeActionMenu() {
    if (!menuNodeId) return;
    const n = cy.getElementById(menuNodeId);
    if (n.empty() || n.style("display") === "none") { hideActionMenu(); return; }
    const bb = n.renderedBoundingBox({ includeLabels: false, includeOverlays: false });
    const w = actionMenu.offsetWidth, area = document.getElementById("cy").clientWidth;
    const x = Math.max(4, Math.min(area - w - 4, (bb.x1 + bb.x2) / 2 - w / 2));
    actionMenu.style.left = `${x}px`;
    actionMenu.style.top = `${bb.y2 + 8}px`;
    // 吹き出しの三角は遺伝子の中央を指す（端で寄せたときもずれないように）
    actionMenu.style.setProperty("--arrow-x", `${(bb.x1 + bb.x2) / 2 - x}px`);
  }
  function showActionMenu(n) {
    menuNodeId = n.id();
    actionMenu.style.display = "flex";
    placeActionMenu();
  }
  function hideActionMenu() {
    menuNodeId = null;
    actionMenu.style.display = "none";
  }
  cy.on("viewport", placeActionMenu);
  cy.on("position", "node", (evt) => { if (evt.target.id() === menuNodeId) placeActionMenu(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideActionMenu(); });
  actionMenu.addEventListener("click", (e) => {
    const button = e.target.closest("button[data-action]");
    if (!button || !menuNodeId) return;
    const id = menuNodeId;
    hideActionMenu();
    if (button.dataset.action === "grow") {   // 拡張: 上流・下流を 1 階層追加（選択はそのまま、紫の印）
      clickStage = 2;
      markPicked(id, 2);
      if (bridge) bridge.onNodeGrow(id);
    } else {                                  // 注目: この遺伝子を中心に表示し直す（オレンジの印）
      // 緑の選択はここでは外さない（アプリが、これまでの注目との入れ替えを決めて描き直す）
      selectedNodeId = null;
      clickStage = 0;
      if (bridge) bridge.onNodeFocus(id);
    }
  });

  cy.on("tap", "node", (evt) => {
    const n = evt.target;
    hideActionMenu();
    focusEdgeId = null;   // 線への注目を外す（省く線なら、また隠れる）
    // 経路の表示中は、近くの遺伝子を目立たせる代わりに経路の表示を保つ。一覧で選んで描いた経路（pathFocus）も消さない
    // （消すのは背景をクリックしたときだけ）
    const show = (ele) => { if (pathIds) applyPath(); else highlight(ele); };
    if (n.data("isBundle")) {
      if (openBundles.has(n.id())) openBundles.delete(n.id()); else openBundles.add(n.id());
      syncTfPairs();
      return;
    }
    if (n.data("isComplex")) {
      selectedNodeId = null;
      openFrameId = n.id();
      markPicked(null);   // 緑の選択を外す（syncTfPairs で、枠の中の線と名札も出し直す）
      show(n);
      if (bridge) bridge.onComplexClicked(n.data("label"));
      return;
    }
    openFrameId = null;
    // Shift を押しながら: 緑の選択に加える・外す（クリックの回数は進めない）
    if (evt.originalEvent && evt.originalEvent.shiftKey) {
      if (pickedSet.has(n.id())) { unpick(n.id()); return; }
      if (clickStage !== 1) pickedSet = new Set();   // 紫（2 回目）の途中なら選び直す
      pickedSet.add(n.id());
      selectedNodeId = n.id();
      clickStage = 1;
      refreshPicked();
      if (pathIds) applyPath();
      else highlightMany(cy.collection([...pickedSet].map((p) => cy.getElementById(p))));
      if (bridge) bridge.onNodeClicked(n.id());
      return;
    }
    // 選択中の遺伝子をもう一度クリック: 拡張（紫）か注目（オレンジ）かを選ぶ
    if (n.id() === selectedNodeId) {
      showActionMenu(n);
      return;
    }
    selectedNodeId = n.id();
    clickStage = 1;
    markPicked(n.id());
    show(n);
    if (bridge) bridge.onNodeClicked(n.id());
  });
  // 実際に動かし始めたら、動かしている遺伝子につながる線を隠す（クリックだけなら隠さない）
  cy.on("drag", "node", (evt) => {
    if (!orthogonal) return;
    const n = evt.target;
    n.union(n.descendants()).connectedEdges().addClass("routing");
  });
  cy.on("dragfree", "node", () => scheduleReroute());
  cy.on("tap", "edge", (evt) => {
    hideActionMenu();
    focusEdgeId = evt.target.id();   // 緑の選択が外れても、この線は見せたままにする
    selectedNodeId = null;
    markPicked(null);
    if (pathIds) applyPath(); else highlight(evt.target);   // 一覧で選んで描いた経路は消さない
    if (bridge) bridge.onEdgeClicked(evt.target.id());
  });
  cy.on("tap", (evt) => {
    if (evt.target === cy) {
      hideActionMenu();
      focusEdgeId = null;
      openFrameId = null;   // markPicked(null) の syncTfPairs で、枠の中の線を隠し直す
      selectedNodeId = null;
      markPicked(null);
      if (pathIds) { pathFocus = null; applyPath(); } else highlight(null);
      if (bridge) bridge.onBackgroundClicked();
    }
  });

  // ---- レイアウト ----
  const LAYOUTS = {
    dagre_tb: { name: "dagre", rankDir: "TB", nodeSep: 25, rankSep: 70, edgeSep: 10 },
    dagre_lr: { name: "dagre", rankDir: "LR", nodeSep: 20, rankSep: 90, edgeSep: 10 },
    // 同心円: つながりの多い遺伝子ほど内側。位置は concentricPositions で決め、動きだけ preset で付ける
    concentric: { name: "preset" },
    // 配置の番号（data.order。階層の上から順に振ってある）の順に並べる
    grid: { name: "grid", avoidOverlap: true, sort: (a, b) => (a.data("order") ?? 0) - (b.data("order") ?? 0) },
  };

  // 階層配置で 1 段に遺伝子が多すぎると極端に横長になるため、複数行に折り返す
  // ---- 階層ごとの段に並べる ----
  // ネットワーク全体での階層（data.level）ごとに 1 段を作り、上流の階層から順に並べる。
  // 表示にない階層は飛ばして詰める。段の中の左右の順序は配置の番号（data.order。ネットワーク全体で固定）の小さい順で、
  // 複合体の構成要素は隣どうしにする。今の位置は使わないので、同じ遺伝子の組なら操作の順序によらず同じ配置になる。
  const WRAP = 10;            // 1 行に並べる最大数（超えたら同じ段の中で折り返す。WRAP 個より多い複合体は枠の中で折り返す）
  const isGene = (n) => !n.data("isComplex") && !n.data("isLabel") && !n.data("isCxLabel");
  // 枠の中の遺伝子（まとめの枠の中に入れ子にしたパラログの枠の遺伝子も含める）
  function membersOf(c) { return c.descendants().filter((n) => !n.data("isComplex")); }

  // 段の中の隣どうしの隙間・同じ段で折り返したときの行の間隔・段と段の間隔。
  // 折り返しの行は詰め、段と段の間はその倍以上空けて、折り返しと階層の違いを見分けられるようにする
  // 階層の配置の間隔。無駄な余白を作らないよう、遺伝子・線が多いほど詰める（線が重なることは許す）
  // - 段の中の遺伝子どうしの隙間: 遺伝子が多いほど狭く
  // - 行と行の間: 箱が重ならない最小の間隔に、その間を横切る線の本数ぶんの通り道を足す。通り道 1 本の幅は
  //   線が多いほど狭く、足す本数にも上限を設ける。段の変わり目は折り返しの行間より少し広くして見分けられるようにする
  function levelSpacing(horizontal, genes, edges) {
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    const gap = Math.round(clamp((horizontal ? 26 : 34) - genes / 8, 12, horizontal ? 26 : 34));
    const lane = clamp(4 - edges / 60, 1.5, 4);
    return { gap, lane, laneCap: 6, bandExtra: horizontal ? 24 : 12 };
  }

  // 表示中の遺伝子の間だけで詰めた段の番号（data.rank）を付ける。
  // 上下の順序は全体の階層（data.level、制御する側が上）に従い、表示中の関係で「全体の階層が上 → 下」になっているものを
  // たどった最も長い経路の段数で数える。互いに作用し合う組など同じ階層のものは同じ段。階層のないものは一番下の段
  function assignRanks(nodes) {
    const lv = (n) => n.data("level");
    const has = (n) => lv(n) !== null && lv(n) !== undefined;
    const ids = new Set(nodes.map((n) => n.id()));
    // 全体の階層ごとに段を決める（同じ階層は同じ段）。上の階層から順に、表示中の関係で上流になっている階層の
    // 段より 1 つ下、かつ直前の階層の段より上には行かないようにする（見出しの階層の順序を保つ）
    const levels = [...new Set(nodes.filter(has).map(lv))].sort((a, b) => a - b);
    const feeders = new Map(levels.map((l) => [l, new Set()]));
    nodes.filter(has).forEach((n) => {
      n.incomers("edge").forEach((e) => {
        const src = e.source();
        // 転写制御は段の並べ方に使わない（別扱い）
        if (e.data("type") !== "transcription" && ids.has(src.id()) && has(src) && lv(src) < lv(n)) feeders.get(lv(n)).add(lv(src));
      });
    });
    const rankOf = new Map();
    let previous = 0;
    levels.forEach((l) => {
      let r = previous;
      feeders.get(l).forEach((f) => { r = Math.max(r, rankOf.get(f) + 1); });
      rankOf.set(l, r);
      previous = r;
    });
    nodes.forEach((n) => n.data("rank", has(n) ? rankOf.get(lv(n)) : Infinity));
    // 左の TFs 一覧で加えた転写因子は、一番上の専用の段に並べる（転写制御は階層に使わないため）
    nodes.forEach((n) => { if (n.data("txReg")) n.data("rank", -1); });
  }

  // 段の見出し（左端または上端）を、今の遺伝子の位置から作り直す。クリックやドラッグはできない飾り
  const SHOW_LEVEL_LABELS = false;   // 段の見出し（第 n 階層）は表示しない（段の並びだけで上下が分かるため）
  function addLevelLabels(horizontal) {
    cy.remove(cy.nodes("[?isLabel]"));
    if (!SHOW_LEVEL_LABELS) return;
    const main = horizontal ? "x" : "y", cross = horizontal ? "y" : "x";
    const size = (n) => (horizontal ? n.height() : n.width());
    const genes = cy.nodes().filter((n) => isGene(n) && !n.hasClass("filtered"));
    if (!genes.length) return;
    const bands = new Map();   // 段 → { 位置の一覧, 含まれる全体の階層 }
    genes.forEach((n) => {
      // 段の番号がなければ（保存した配置・追加したもの）、位置で段を見分ける
      const key = n.data("rank") ?? (n.data("level") === null || n.data("level") === undefined ? Infinity : `y${Math.round(n.position(main))}`);
      if (!bands.has(key)) bands.set(key, { mains: [], levels: new Set() });
      bands.get(key).mains.push(n.position(main));
      if (n.data("level") !== null && n.data("level") !== undefined) bands.get(key).levels.add(n.data("level"));
    });
    const edge = Math.min(...genes.map((n) => n.position(cross) - size(n) / 2));
    bands.forEach(({ mains, levels }, key) => {
      const at = (Math.min(...mains) + Math.max(...mains)) / 2;
      const lo = Math.min(...levels) + 1, hi = Math.max(...levels) + 1;
      const text = !levels.size ? "階層なし" : lo === hi ? `第${lo}階層` : `第${lo}〜${hi}階層`;
      cy.add({
        group: "nodes", selectable: false, grabbable: false,
        data: { id: `level:${key}`, isLabel: true, label: text },
        position: horizontal ? { x: at, y: edge - 46 } : { x: edge - 82, y: at },
      });
    });
  }

  // ---- 同心円 ----
  // つながりの多い順（同じなら配置の番号の順）に、中心から外の輪へ詰めて並べる。輪の周の長さは実際の箱の幅の合計で、
  // 輪と輪の間は実際の箱の高さで決める（いちばん大きい箱に合わせて全体の間隔が広がらないように）。
  // 間隔は直角の線を通す分だけ空ける
  const RING_GAP_X = 26, RING_GAP_Y = 34, RING_MIN_GAP = 14;
  function concentricPositions(genes) {
    const list = genes.toArray().sort((a, b) => (b.degree() - a.degree()) || ((a.data("order") ?? 0) - (b.data("order") ?? 0)));
    const pos = {};
    if (!list.length) return pos;
    const size = (n) => ({ w: n.outerWidth(), h: n.outerHeight() });
    const placed = [];   // 置いた箱 { x, y, w, h }
    const clash = (x, y, sz) => placed.some((b) =>
      Math.abs(x - b.x) < (sz.w + b.w) / 2 + RING_MIN_GAP && Math.abs(y - b.y) < (sz.h + b.h) / 2 + RING_MIN_GAP);
    const first = size(list[0]);
    pos[list[0].id()] = { x: 0, y: 0 };
    placed.push({ x: 0, y: 0, ...first });
    let inner = Math.max(first.w, first.h) / 2;   // 内側の輪のおおよその外周
    let i = 1;
    while (i < list.length) {
      // 周に収まる数だけこの輪に入れる（隣どうしの弦が箱の幅＋隙間になる角度で数える）
      let r = inner + RING_GAP_Y + list[i].outerHeight() / 2;
      const ring = [];
      let used = 0;
      while (i < list.length) {
        const w = list[i].outerWidth() + RING_GAP_X;
        const a = 2 * Math.asin(Math.min(1, w / (2 * r)));
        if (ring.length && used + a > 2 * Math.PI) break;
        ring.push(list[i]); used += a; i++;
      }
      // 置いてみて、内側の箱と重なるなら半径を少しずつ広げる
      const place = (radius) => {
        const widths = ring.map((n) => 2 * Math.asin(Math.min(1, (n.outerWidth() + RING_GAP_X) / (2 * radius))));
        const scale = (2 * Math.PI) / Math.max(widths.reduce((a, b) => a + b, 0), 2 * Math.PI);
        let angle = -Math.PI / 2;
        return ring.map((n, k) => {
          angle += widths[k] * scale / 2;
          const p = { x: radius * Math.cos(angle), y: radius * Math.sin(angle) };
          angle += widths[k] * scale / 2;
          return p;
        });
      };
      let points = place(r);
      for (let tries = 0; tries < 200 && ring.some((n, k) => clash(points[k].x, points[k].y, size(n))); tries++) {
        r += 6;
        points = place(r);
      }
      let outer = 0;
      ring.forEach((n, k) => {
        pos[n.id()] = points[k];
        const sz = size(n);
        placed.push({ x: points[k].x, y: points[k].y, ...sz });
        outer = Math.max(outer, Math.hypot(points[k].x, points[k].y) + Math.max(sz.w, sz.h) / 2);
      });
      inner = r + Math.max(...ring.map((n) => n.outerHeight())) / 2;
    }
    return pos;
  }

  function arrangeByLevel(horizontal) {
    cy.remove(cy.nodes("[?isLabel]"));
    const main = horizontal ? "x" : "y";      // 段が進む方向
    const cross = horizontal ? "y" : "x";     // 段の中で並ぶ方向
    const size = (n) => (horizontal ? n.height() : n.width());
    // まとめの遺伝子は段に並べず、あとで地図の下に枠ごとに並べる
    const genes = cy.nodes().filter((n) => isGene(n) && !n.hasClass("filtered") && !n.data("bundled"));
    if (!genes.length) { placeBundles(horizontal, null, (n, p) => n.position(p)); return; }
    const shownEdges = cy.edges().filter((e) => !e.hasClass("filtered") && !e.data("tfPair") && genes.contains(e.source()) && genes.contains(e.target()));
    const { gap, lane, laneCap, bandExtra } = levelSpacing(horizontal, genes.length, shownEdges.length);

    // 複合体は構成要素の番号の平均の位置にまとめる
    const order = (n) => n.data("order") ?? 0;
    const groupPos = {};
    cy.nodes("[?isComplex]").forEach((c) => {
      const ch = c.children();
      if (ch.length) groupPos[c.id()] = ch.reduce((sum, m) => sum + order(m), 0) / ch.length;
    });
    const orderKey = (n) => (frameOf(n) && groupPos[frameOf(n).id()] !== undefined ? groupPos[frameOf(n).id()] : order(n));

    assignRanks(genes.toArray());
    gatherSpanningComplexes(genes);
    alignParalogs(genes);
    const bands = new Map();
    genes.forEach((n) => {
      const key = n.data("rank");
      if (!bands.has(key)) bands.set(key, []);
      bands.get(key).push(n);
    });
    // 1) 段ごとに行へ詰め、行の中の左右の位置を決める
    const thick = (n) => (horizontal ? n.width() : n.height());
    const parentOf = (n) => (frameOf(n) ? frameOf(n).id() : null);
    const framed = (row) => row.some((n) => frameOf(n));
    const lines = [];   // { row, newBand }
    let width = 0;
    [...bands.keys()].sort((a, b) => a - b).forEach((key) => {
      const nodes = bands.get(key).sort((a, b) => (orderKey(a) - orderKey(b)) || (order(a) - order(b)));
      // 複合体の構成要素はひとまとまりにして、WRAP 個を超えない範囲で行に詰める（WRAP 個以下の枠は 2 行にまたがらないように）
      const units = [];
      nodes.forEach((n) => {
        const last = units[units.length - 1];
        if (last && frameOf(n) && parentOf(last[0]) === parentOf(n)) last.push(n);
        else units.push([n]);
      });
      // WRAP 個より多い複合体は、WRAP 個ずつの行に折り返す（その複合体だけの行にする）
      const rowList = [[]];
      units.forEach((u) => {
        const cur = rowList[rowList.length - 1];
        if (u.length > WRAP) {
          if (!cur.length) rowList.pop();
          for (let k = 0; k < u.length; k += WRAP) rowList.push(u.slice(k, k + WRAP));
          rowList.push([]);
        } else if (cur.length && cur.length + u.length > WRAP) rowList.push([...u]);
        else cur.push(...u);
      });
      if (rowList.length > 1 && !rowList[rowList.length - 1].length) rowList.pop();
      rowList.forEach((row, r) => {
        // 隣が別の複合体（または複合体の内外）なら、枠どうしが重ならないよう間を広げる
        const space = (i) => (i === 0 ? 0 : gap + (parentOf(row[i]) !== parentOf(row[i - 1]) ? 34 : 0));
        const total = row.reduce((sum, n, i) => sum + size(n) + space(i), 0);
        width = Math.max(width, total);
        let cursor = -total / 2;
        row.forEach((n, i) => {
          cursor += space(i);
          n.position(cross, cursor + size(n) / 2);
          cursor += size(n);
        });
        lines.push({ row, newBand: r === 0 });
      });
    });

    // 2) 行と行の間隔: 箱が重ならない間隔＋その間を横切る線の通り道。段の変わり目は折り返しの行間より少し広くする。
    //    図が横幅の MAX_ASPECT 倍より縦に長くなるときは、重ならない範囲で間隔を詰めて正方形に近づける
    const MAX_ASPECT = 1.25;
    const half = (row) => Math.max(...row.map(thick)) / 2;
    const lineOf = new Map();
    lines.forEach((line, k) => line.row.forEach((n) => lineOf.set(n.id(), k)));
    const crossing = new Array(lines.length).fill(0);   // crossing[k]: 行 k と行 k+1 の間を横切る線の本数
    shownEdges.forEach((e) => {
      const a = lineOf.get(e.source().id()), b = lineOf.get(e.target().id());
      if (a === undefined || b === undefined) return;
      for (let k = Math.min(a, b); k < Math.max(a, b); k++) crossing[k]++;
    });
    const pairs = lines.slice(1).map((line, k) => {
      const prev = lines[k];
      // 複合体の枠と名前の分（名前と線の重なりは許す）。同じ複合体を折り返した行どうしの間には要らない
      const one = parentOf(prev.row[0]);
      const same = one !== null && prev.row.concat(line.row).every((n) => parentOf(n) === one);
      const extra = same ? 0 : (framed(prev.row) ? 10 : 0) + (framed(line.row) ? 22 : 0);
      const tight = half(prev.row) + half(line.row) + 14;   // 箱どうしが重ならない間隔
      const nominal = tight + 6 + lane * Math.min(crossing[k], laneCap) + (line.newBand ? bandExtra : 0);
      return { nominal, min: tight + (line.newBand ? bandExtra / 2 : 0), extra };
    });
    const extent = (f) => pairs.reduce((sum, q) => sum + Math.max(q.min, q.nominal * f) + q.extra, 0);
    let f = 1;
    if (extent(1) > width * MAX_ASPECT) {
      let lo = 0, hi = 1;
      for (let k = 0; k < 20; k++) { const mid = (lo + hi) / 2; if (extent(mid) > width * MAX_ASPECT) hi = mid; else lo = mid; }
      f = lo;
    }
    let at = 0;
    lines.forEach((line, k) => {
      if (k > 0) at += Math.max(pairs[k - 1].min, pairs[k - 1].nominal * f) + pairs[k - 1].extra;
      line.row.forEach((n) => n.position(main, at));
    });

    placeBundles(horizontal, genes.boundingBox(), (n, p) => n.position(p));
    addLevelLabels(horizontal);
  }

  // 構成要素が別々の段にある複合体は、枠が間の段のほかの遺伝子まで囲んでしまう。そこで段をまたぐ複合体だけ、
  // 構成要素を専用の段にまとめる。専用の段は構成要素の段の中央値（偶数個なら上側）のすぐ下に置き（rank + 0.5）、
  // 同じ位置に入る複合体どうしは同じ段に横に並べる。段のない構成要素（階層なし）は中央値の計算に使わない
  // パラログは、組のうち上の段（注目している遺伝子があればその段）に並べてそろえる。
  // 複合体（構成要素の段の中央に専用の段を作る）と違い、段は増やさない
  function alignParalogs(genes) {
    cy.nodes("[?isParalog]").forEach((c) => {
      const members = c.children().filter((n) => genes.contains(n));
      if (members.length < 2) return;
      const seed = members.filter((n) => n.data("seed"))[0];
      const finite = members.map((n) => n.data("rank")).filter((r) => Number.isFinite(r));
      const rank = seed ? seed.data("rank") : (finite.length ? Math.min(...finite) : members[0].data("rank"));
      members.forEach((n) => n.data("rank", rank));
    });
  }
  // 並べるときにまとめる枠
  const frameOf = (n) => { const p = n.parent(); return p.nonempty() ? p : null; };

  function gatherSpanningComplexes(genes) {
    cy.nodes("[?isComplex]").filter((c) => !c.data("isBundle") && !c.data("isParalog")).forEach((c) => {
      const members = c.children().filter((n) => genes.contains(n));
      const ranks = [...new Set(members.map((n) => n.data("rank")))];
      if (ranks.length < 2) return;
      const finite = members.map((n) => n.data("rank")).filter((r) => Number.isFinite(r)).sort((x, y) => x - y);
      if (!finite.length) return;
      const median = finite[Math.floor((finite.length - 1) / 2)];
      members.forEach((n) => n.data("rank", median + 0.5));
    });
  }

  // ---- まとめの枠の配置 ----
  // 下流が多すぎる遺伝子のまとめは、地図（bb）の下（横向きの配置なら右）に、起点の遺伝子の左右の順に枠ごと並べる。
  // 枠の中は名前順の格子。並びが地図の幅を大きく超えるときは折り返す
  // 太字の文字列の幅（画面に描く前でも測れるように canvas で測る）
  const measureCtx = document.createElement("canvas").getContext("2d");
  function textWidth(text, size) {
    measureCtx.font = `bold ${size}px sans-serif`;
    return measureCtx.measureText(text).width;
  }
  function placeBundles(horizontal, bb, put) {
    const bundles = cy.nodes("[?isBundle]").filter((b) => membersOf(b).some((n) => !n.hasClass("filtered")));
    if (!bundles.length) return;
    bb = bb || { x1: 0, x2: 0, y1: 0, y2: 0 };
    const cross = horizontal ? "y" : "x";
    const hubAt = (b) => { const h = cy.getElementById(b.data("hub")); return h.nonempty() ? h.position(cross) : 0; };
    const list = bundles.toArray().sort((a, b) => (hubAt(a) - hubAt(b)) || a.id().localeCompare(b.id()));
    const LABEL_MAX = COMPLEX_LABEL_PX / COMPLEX_LABEL_MIN_ZOOM;
    const GAP_X = 12, GAP_Y = 10, BETWEEN = 30 + 2 * LABEL_MAX;   // 枠の余白（名札の大きさまで広がる）の分も空ける
    const PARALOG_PAD = 24;   // 中のパラログの枠（二重線と余白）の分
    const wOf = (n) => (horizontal ? n.height() : n.width()), hOf = (n) => (horizontal ? n.width() : n.height());
    const left = horizontal ? bb.y1 : bb.x1;
    const span = Math.max(1200, horizontal ? bb.y2 - bb.y1 : bb.x2 - bb.x1);
    let along = left, out = (horizontal ? bb.x2 : bb.y2) + 90, deepest = 0;
    list.forEach((b) => {
      const kids = membersOf(b).filter((n) => !n.hasClass("filtered")).toArray()
        .sort((x, y) => String(x.data("label")).localeCompare(String(y.data("label"))));
      // 中のパラログの枠の遺伝子は、続けて並べ、行をまたがないようにする（ひとまとまり unit）
      const unitOf = (n) => (n.parent().id() !== b.id() ? n.parent().id() : n.id());
      const units = [];
      kids.forEach((n) => {
        const u = units.find((v) => unitOf(v[0]) === unitOf(n));
        if (u) u.push(n); else units.push([n]);
      });
      const unitW = (u) => u.reduce((sum, n) => sum + wOf(n) + GAP_X, 0) + (u.length > 1 ? PARALOG_PAD : 0);
      // 枠の幅: 中身の面積がおおよそ正方形になる幅（地図の幅は超えない）。名前の札より狭くはしない
      const area = kids.reduce((sum, n) => sum + (wOf(n) + GAP_X) * (hOf(n) + GAP_Y), 0);
      // 名札（太字）がはみ出さない幅。名札は縮小すると最大 2 倍の大きさになる（fixComplexLabels）ので、その大きさで測る
      const labelW = textWidth(String(b.data("label")), LABEL_MAX) + 20;
      const width = Math.max(labelW, Math.max(...units.map(unitW)), Math.min(span, Math.sqrt(area) * 1.3));
      // 左から詰めて並べ、幅を超えたら次の行へ
      const rows = [[]];
      let x = 0;
      units.forEach((u) => {
        const w = unitW(u);
        if (x + w > width && rows[rows.length - 1].length) { rows.push([]); x = 0; }
        rows[rows.length - 1].push(u);
        x += w;
      });
      const rowH = (row) => Math.max(...row.flat().map(hOf)) + GAP_Y + (row.some((u) => u.length > 1) ? PARALOG_PAD : 0);
      const height = rows.reduce((sum, row) => sum + rowH(row), 0);
      if (along > left && along - left + width > span) {   // 地図の幅を超えるなら折り返す
        along = left;
        out += deepest + BETWEEN + 20;
        deepest = 0;
      }
      let o = out;
      // 行は取った幅の中央に寄せる（枠は中身の大きさまで縮むので、名札が枠より広くても隣にはみ出さないように）
      const rowW = (row) => row.reduce((sum, u) => sum + unitW(u), 0);
      rows.forEach((row) => {
        let a = along + (width - rowW(row)) / 2;
        row.forEach((u) => {
          if (u.length > 1) a += PARALOG_PAD / 2;
          u.forEach((n) => {
            const w = wOf(n) + GAP_X;
            put(n, horizontal ? { x: o + rowH(row) / 2, y: a + w / 2 } : { x: a + w / 2, y: o + rowH(row) / 2 });
            a += w;
          });
          if (u.length > 1) a += PARALOG_PAD / 2;
        });
        o += rowH(row);
      });
      along += width + BETWEEN;
      deepest = Math.max(deepest, height);
    });
  }

  // 配置の対象（チェックボックスで隠したものは除く。縮小で隠れているものは含める）
  function laidOut() {
    return cy.elements().filter((e) => !e.hasClass("filtered") && !e.data("isLabel") && !e.data("isCxLabel"));
  }

  // ---- チェックボックスによる絞り込み ----
  function applyFilters() {
    fitZoomCache = null;   // 配置の対象が変わる
    const roles = new Set(hidden.roles), types = new Set(hidden.types), effects = new Set(hidden.effects);
    const byKind = (e) => types.has(e.data("type")) || effects.has(e.data("effect"));
    cy.batch(() => {
      cy.elements().removeClass("filtered");
      const genes = cy.nodes().filter(isGene);
      // 役割を外した遺伝子と、それにつながる線
      genes.forEach((n) => { if (roles.has(n.data("role"))) n.addClass("filtered"); });
      cy.edges().forEach((e) => {
        if (byKind(e) || e.source().hasClass("filtered") || e.target().hasClass("filtered")) e.addClass("filtered");
      });
      // 制御の種類・作用の向きで線を隠したために、表示中の線が残らなくなった遺伝子は隠す。
      // ただし、自分で表示させたもの（注目・拡張の起点・追加したもの・左の一覧で選んだ TFs）は残す
      genes.forEach((n) => {
        if (n.hasClass("filtered") || n.data("seed") || n.data("txReg") || n.data("pinned") || n.data("grown")) return;
        const es = n.connectedEdges();
        if (es.length && es.every((e) => e.hasClass("filtered")) && es.some(byKind)) n.addClass("filtered");
      });
      cy.nodes("[?isComplex]").forEach((c) => c.toggleClass("filtered", membersOf(c).every((m) => m.hasClass("filtered"))));
    });
    syncComplexLabels();
    applyConditionFilter();
  }

  // ---- 注目を移したときのアニメーション ----
  const MOVE_MS = 650;

  // 要素を枠いっぱいに収めるときの拡大率と位置（アニメーションの行き先を先に計算する）
  function viewportFor(eles, pad = 40) {
    const bb = eles.boundingBox();
    const w = cy.width(), h = cy.height();
    let zoom = Math.min((w - 2 * pad) / Math.max(bb.w, 1), (h - 2 * pad) / Math.max(bb.h, 1));
    zoom = Math.max(cy.minZoom(), Math.min(cy.maxZoom(), zoom));
    return { zoom, pan: { x: (w - zoom * (bb.x1 + bb.x2)) / 2, y: (h - zoom * (bb.y1 + bb.y2)) / 2 } };
  }

  // 配置を決めた後に呼ぶ。前の表示にもあった遺伝子は前の位置から新しい位置へ動かし、
  // 新しく現れたものはその場で浮かび上がらせる。線は移動が終わってから引き直す。
  // 拡大率 zoom のとき、モデル座標 p が画面の中心に来る表示位置
  function panToCenter(p, zoom) {
    return { x: cy.width() / 2 - zoom * p.x, y: cy.height() / 2 - zoom * p.y };
  }

  // クリックした遺伝子（2・3 回目）と、その直接の上流・下流が見える表示範囲。
  // 遺伝子が小さくなりすぎない（大きくなりすぎない）よう拡大率に下限・上限を設け、
  // 全部が入らないときは、クリックした遺伝子を中心にする
  const READABLE_ZOOM = 0.7;   // これより縮小しない（遺伝子の高さが画面上で約 20px、文字が約 8px）
  const CLOSE_ZOOM = 1.3;      // これより拡大しない（既定の表示に近い大きさ）
  function viewportAround(node, pad = 50) {
    const near = node.closedNeighborhood("node").filter((n) => isGene(n) && n.style("display") !== "none");
    const bb = near.boundingBox({ includeLabels: false });
    const fit = Math.min((cy.width() - 2 * pad) / Math.max(bb.w, 1), (cy.height() - 2 * pad) / Math.max(bb.h, 1));
    const zoom = Math.max(READABLE_ZOOM, Math.min(CLOSE_ZOOM, fit));
    const p = node.position();
    // 上流・下流がすべて入るなら、それらのまとまりの中央を画面の中央に（クリックしたものは中央付近に来る）
    const c = fit >= READABLE_ZOOM ? { x: (bb.x1 + bb.x2) / 2, y: (bb.y1 + bb.y2) / 2 } : { x: p.x, y: p.y };
    return { zoom, pan: panToCenter(c, zoom) };
  }

  function transition(prev, keepView = false, centerOn = null) {
    const gen = renderGen;
    const genes = cy.nodes().filter(isGene);
    const labels = cy.nodes("[?isLabel]");
    let target = viewportFor(laidOut().union(labels));
    // 3 回目のクリック: クリックした遺伝子と、その上流・下流が見えるようにする（位置は配置し終えた後のもの）。
    // 2 回目のクリック（keepView）: 追加した分も含めて図全体が枠に収まるようにする
    const center = centerOn ? cy.getElementById(centerOn) : null;
    if (center && center.nonempty() && !keepView) target = viewportAround(center);
    const moving = genes.filter((n) => prev[n.id()]);
    if (!moving.length) {   // 前の表示と共通の遺伝子がなければ、これまでどおり
      reroute();
      if (keepView && !(center && center.nonempty())) { applyLod(true); return; }
      cy.animate({ zoom: target.zoom, pan: target.pan }, { duration: 300, complete: () => { viewportAnimationDone(); applyLod(true); } });
      return;
    }
    const finals = new Map();
    genes.forEach((n) => finals.set(n.id(), { x: n.position("x"), y: n.position("y") }));
    animating = true;
    endBusy();   // 動かしている間は数えない（動き終わって線を引くときに数え直す）
    cy.batch(() => {
      genes.forEach((n) => { if (prev[n.id()]) n.position(prev[n.id()]); else n.style("opacity", 0); });
      if (!keepView) labels.style("opacity", 0);   // 追加のときは既にある段の見出しをちらつかせない
      cy.edges().addClass("routing");
    });
    const opts = { duration: MOVE_MS, easing: "ease-in-out-cubic" };
    moving.forEach((n) => n.animate({ position: finals.get(n.id()) }, opts));
    (keepView ? genes.difference(moving) : genes.difference(moving).union(labels)).animate({ style: { opacity: 1 } }, opts);
    const done = () => {
      if (gen !== renderGen) return;   // 新しい表示に切り替わった
      animating = false;
      cy.batch(() => {
        genes.forEach((n) => n.position(finals.get(n.id())));   // 途中で止まっても行き先にそろえる
        genes.union(labels).removeStyle("opacity");
        if (!orthogonal) cy.edges().removeClass("routing");
      });
      viewportAnimationDone();
      applyLod(true);
      reroute();
    };
    // 2 回目のクリックでは拡大率を変えない。追加した遺伝子が画面からはみ出すときだけ、必要な分だけずらす
    if (keepView && center && center.nonempty()) {
      // 2 回目のクリック: 追加した上流・下流も含めて、図全体が見えるよう表示範囲を合わせる
      cy.animate({ zoom: target.zoom, pan: target.pan }, opts);
      setTimeout(done, MOVE_MS + 20);
    } else if (keepView) {
      const added = genes.difference(moving);
      if (added.nonempty()) {
        const ext = cy.extent(), z = cy.zoom(), pad = 30 / z;
        const bb = { x1: Infinity, y1: Infinity, x2: -Infinity, y2: -Infinity };
        added.forEach((n) => {
          const f = finals.get(n.id()), hw = n.width() / 2, hh = n.height() / 2;
          bb.x1 = Math.min(bb.x1, f.x - hw); bb.x2 = Math.max(bb.x2, f.x + hw);
          bb.y1 = Math.min(bb.y1, f.y - hh); bb.y2 = Math.max(bb.y2, f.y + hh);
        });
        const shift = (lo, hi, elo, ehi) => (lo < elo + pad ? lo - (elo + pad) : hi > ehi - pad ? Math.min(hi - (ehi - pad), lo - (elo + pad)) : 0);
        const dx = shift(bb.x1, bb.x2, ext.x1, ext.x2), dy = shift(bb.y1, bb.y2, ext.y1, ext.y2);
        if (dx || dy) cy.animate({ pan: { x: cy.pan().x - dx * z, y: cy.pan().y - dy * z } }, opts);
      }
      setTimeout(done, MOVE_MS + 20);
    }
    else cy.animate({ zoom: target.zoom, pan: target.pan }, Object.assign({}, opts, { complete: done }));
  }

  let layoutRun = null;   // 動いている最中の配置（同心円・格子）
  let layoutToken = 0;
  function runLayout(name, prev = null, centerOn = null) {
    beginBusy();
    currentLayout = name;
    cy.remove(cy.nodes("[?isLabel]"));
    let base = LAYOUTS[name] || LAYOUTS.dagre_tb;
    // 直角の線では、段と段・遺伝子どうしの間に線を通す隙間が要るので間隔を広げる
    if (orthogonal && base.name === "dagre") {
      const busy = Math.min(1.6, 1 + cy.edges().length / 400);
      base = Object.assign({}, base, { nodeSep: base.nodeSep * 1.6 * busy, rankSep: base.rankSep * 1.5 * busy });
    }
    if (base.name === "dagre") {
      // 階層の配置は、段（全体の階層）と配置の番号だけで決める（dagre の計算は使わない）
      arrangeByLevel(base.rankDir === "LR");
      if (prev) { transition(prev, false, centerOn); return; }
      reroute();
      // 段の見出しも枠に収める
      cy.animate({ fit: { eles: laidOut().union(cy.nodes("[?isLabel]")), padding: 40 }, duration: 300,
                   complete: () => { viewportAnimationDone(); applyLod(true); } });
      return;
    }
    if (orthogonal) cy.edges().addClass("routing");   // 動いている間は線を隠し、止まってから経路を計算して出す
    // 動いている間は線を引き直さない（枠に収めるときの拡大・縮小で線の計算が走り、途中の位置で線が出てしまうため）。
    // 前の配置の計算が後から終わっても、新しい配置の途中で線を引かないよう、最後に始めた配置だけを有効にする
    if (layoutRun) layoutRun.stop();
    const token = ++layoutToken;
    animating = true;
    endBusy();   // 動かしている間は数えない（動き終わって線を引くときに数え直す）
    let eles = laidOut();
    if (name === "concentric") {
      const genes = eles.nodes().filter(isGene);
      const pos = concentricPositions(genes.filter((n) => !n.data("bundled")));
      const xs = Object.values(pos);
      const bb = xs.length ? { x1: Math.min(...xs.map((p) => p.x)), x2: Math.max(...xs.map((p) => p.x)),
                               y1: Math.min(...xs.map((p) => p.y)), y2: Math.max(...xs.map((p) => p.y)) } : null;
      placeBundles(false, bb, (n, p) => { pos[n.id()] = p; });
      eles = genes;
      base = Object.assign({}, base, { positions: (n) => pos[n.id()] });
    }
    const layout = layoutRun = eles.layout(Object.assign({ animate: true, animationDuration: 400, fit: true, padding: 40 }, base));
    layout.one("layoutstop", () => {
      if (token !== layoutToken) return;
      layoutRun = null;
      animating = false;
      applyLod(true);
      reroute();
    });
    layout.run();
  }

  // ---- 縮小時に下流の枝先を隠す ----
  // 図全体を枠に収めたときの拡大率が小さい（大きな図）ときは、その拡大率を基準にする。
  // 全体を表示した状態ではすべて見せ、そこからさらに縮小したときだけ枝先を省く（省いた段が空白で残らないように）
  let fitZoomCache = null;   // 図全体を枠に収める拡大率（配置し直したときに求め直す。ズームのたびに全要素の範囲を測らない）
  function depthForZoom(zoom) {
    if (fitZoomCache === null) fitZoomCache = viewportFor(laidOut()).zoom;
    const fitZoom = fitZoomCache;
    const z = zoom / Math.min(1, fitZoom / 0.6);
    if (z >= 0.6 - 1e-6) return maxDepth;
    if (z >= 0.4) return maxDepth - 1;
    if (z >= 0.25) return maxDepth - 2;
    return 1;
  }

  let lodEnabled = false;   // 凡例の「表示」欄の「縮小で枝先を省略」（既定はオフ: 拡大・縮小で遺伝子を消さない）
  function applyLod(force) {
    if (force) fitZoomCache = null;   // 配置・表示が変わったので測り直す
    const limit = lodEnabled ? Math.max(1, Math.min(maxDepth, depthForZoom(cy.zoom()))) : maxDepth;
    if (!force && limit === lodDepth) return;
    lodDepth = limit;
    cy.batch(() => {
      const leaves = cy.nodes().filter(isGene);
      // 2 回目のクリックで追加したもの（pinned）は縮小しても省略しない
      leaves.forEach((n) => n.toggleClass("lod-hidden", n.data("depth") > limit && !n.data("pinned")));
      leaves.forEach((n) => {
        const hiddenChild = n.outgoers("node").some((m) => m.hasClass("lod-hidden"));
        n.toggleClass("tips-hidden", !n.hasClass("lod-hidden") && hiddenChild);
      });
      cy.nodes().filter((n) => n.data("isComplex")).forEach((c) => {
        c.toggleClass("lod-hidden", membersOf(c).every((m) => m.hasClass("lod-hidden")));
      });
    });
    syncComplexLabels();
    applyConditionFilter();
    if (bridge) bridge.onLodChanged(limit, maxDepth);
    if (!force) scheduleReroute();
  }

  // 複合体（カテゴリ）の名前は、拡大しても画面上で同じ大きさに保つ
  const COMPLEX_LABEL_PX = 11;          // 複合体・まとめの枠の名札の大きさ（画面上の px）
  const COMPLEX_LABEL_MIN_ZOOM = 0.6;   // これより縮小しても名札は大きくしない（地図の上で大きくなりすぎないように）
  function fixComplexLabels() {
    const size = COMPLEX_LABEL_PX / Math.max(cy.zoom(), COMPLEX_LABEL_MIN_ZOOM);
    cy.batch(() => {
      cy.nodes("[?isComplex]").style({ "font-size": size, "padding": `${Math.max(14, size)}px` });
      cy.nodes("[?isCxLabel]").style({ "font-size": size });
    });
    syncComplexLabels();
  }

  let lodScheduled = false;
  cy.on("zoom", () => {
    if (lodScheduled) return;
    lodScheduled = true;
    requestAnimationFrame(() => { lodScheduled = false; fixComplexLabels(); applyLod(false); });
  });

  function showMessage(text) {
    const el = document.getElementById("message");
    el.textContent = text;
    el.style.display = text ? "block" : "none";
  }

  // ---- 凡例 ----
  // 凡例の 1 行。kind を渡すと行の左にチェックボックス（表示するか）を付ける。
  // absent は今の表示に出ていない項目で、文字とチェックボックスを灰色にする（操作はできる）
  function legendRow(kind, key, inner, absent = false) {
    const cls = absent ? " absent" : "";
    if (!kind) return `<div class="legend-row${cls}">${inner}</div>`;
    const on = !key.split("|").some((k) => hidden[kind].includes(k));   // 1 行にまとめた項目は、どれか隠していれば外す
    return `<label class="legend-row${on ? "" : " off"}${cls}">` +
      `<input type="checkbox" class="filter" data-kind="${kind}" data-key="${key}"${on ? " checked" : ""}>${inner}</label>`;
  }

  function renderLegend(legend) {
    lastLegend = legend;
    document.getElementById("legend-types").innerHTML = legend.types.map((t) =>
      legendRow("types", t.key, `<span class="type-line" style="background:${t.color}"></span>` +
                `<span class="symbol-badge">${t.symbol || "—"}</span>${t.label}`,
                t.shown === false)).join("");   // 今の表示に出ていない種類は灰色に
    // 作用の向きの行（HTML に固定で書いてある）のチェックを、今の状態に合わせる
    document.querySelectorAll('input.filter[data-kind="effects"]').forEach((cb) => {
      cb.checked = !hidden.effects.includes(cb.dataset.key);
      cb.closest(".legend-row").classList.toggle("off", !cb.checked);
    });
    renderNodeLegend();
    syncSectionChecks();
  }

  // チェックボックスの操作はアプリに伝え、全表示枠に反映してもらう
  document.getElementById("legend").addEventListener("change", (e) => {
    const cb = e.target;
    if (!cb.classList || !cb.classList.contains("filter")) return;
    cb.closest(".legend-row").classList.toggle("off", !cb.checked);
    syncSectionChecks();
    if (bridge) bridge.onFilterToggled(cb.dataset.kind, cb.dataset.key, cb.checked);
  });
  // 見出しのチェックボックス: 欄の項目がすべてチェックされているときだけチェックが付く（どれか外れていれば外れる）。
  // チェックを付けると欄の項目をすべて表示し、外すとすべて外す（「表示」欄は、表示の項目をすべてオン・オフ）
  const sectionBoxes = (check) => [...check.closest(".lsec").querySelectorAll(
    check.dataset.kind === "display" ? ".lsec-body input.opt" : ".lsec-body input.filter")];
  function syncSectionChecks() {
    document.querySelectorAll("#legend .sec-check").forEach((check) => {
      const boxes = sectionBoxes(check);
      check.checked = boxes.length > 0 && boxes.every((cb) => cb.checked);
    });
  }
  document.querySelectorAll("#legend .sec-check").forEach((check) => {
    check.addEventListener("click", (e) => e.stopPropagation());   // 見出しの開閉はしない
    check.addEventListener("change", () => {
      const on = check.checked;
      const boxes = sectionBoxes(check);
      if (!bridge) return;
      if (check.dataset.kind === "display") {
        boxes.forEach((cb) => { if (cb.checked !== on) bridge.onDisplayOption(cb.dataset.opt, on); });
      } else if (on) {
        bridge.onFilterReset(check.dataset.kind);
      } else {
        const keys = boxes.filter((cb) => cb.checked).map((cb) => cb.dataset.key);
        if (keys.length) bridge.onFilterToggled(check.dataset.kind, keys.join("|"), false);
      }
    });
  });
  document.getElementById("legend-help").addEventListener("click", () => { if (bridge) bridge.onHelp(); });

  // 凡例の「表示」欄（+数・種類記号）: アプリに伝え、全表示枠に反映してもらう
  document.getElementById("legend").addEventListener("change", (e) => {
    const cb = e.target;
    if (!cb.classList || !cb.classList.contains("opt")) return;
    syncSectionChecks();
    if (bridge) bridge.onDisplayOption(cb.dataset.opt, cb.checked);
  });

  // 凡例の欄ごとの開閉（見出しをクリック）。開閉の状態はこの PC に覚えておく（どの表示枠でも同じ）
  const CLOSED_KEY = "legendClosedSections";
  function loadClosed() {
    try { return new Set(JSON.parse(localStorage.getItem(CLOSED_KEY) || "[]")); } catch (err) { return new Set(); }
  }
  loadClosed().forEach((key) => {
    const sec = document.querySelector(`.lsec[data-sec="${key}"]`);
    if (sec) sec.classList.add("closed");
  });
  document.querySelectorAll(".lsec > .legend-section").forEach((head) => {
    head.addEventListener("click", () => {
      const sec = head.parentElement;
      sec.classList.toggle("closed");
      const closed = [...document.querySelectorAll(".lsec.closed")].map((s) => s.dataset.sec);
      try { localStorage.setItem(CLOSED_KEY, JSON.stringify(closed)); } catch (err) { /* 保存できなくても動作は続ける */ }
    });
  });

  // 遺伝子の色の凡例（色分けの種類に合わせて切り替える）
  function renderNodeLegend() {
    const row = (color, name, cls = "", kind = null, key = null) =>
      legendRow(kind, key, `<span class="swatch${cls}" style="background:${color}"></span>${name}`);
    const roleRows = (lastLegend.roles || []).map((r) => row(r.color, r.name, "", "roles", r.key)).join("");
    const titles = { role: "役割" };
    let html = "";
    if (DATA_KINDS.includes(colorMode)) {
      titles[colorMode] = dataTitle || "";
      html = [2, 1, 0.5, 0, -0.5, -1, -2].map((v) =>
        row(dataColor(v), `${v > 0 ? "+" : v < 0 ? "−" : "±"}${Math.abs(v)}${Math.abs(v) === DATA_MAX ? " 以上" : ""}`)).join("")
        + (dataKo ? row(DATA_KO, "壊した遺伝子") : "")
        + row(DATA_NONE, "データなし") + `<div class="legend-row" style="color:#777">${dataNote || "log2 比"}</div>`;
    } else if (colorMode === "role") html = roleRows;
    document.getElementById("legend-node-title").textContent = titles[colorMode] || "";
    document.getElementById("legend-nodes").innerHTML = html;
    // 「遺伝子の色」欄の見出しのチェックボックスは、役割で色分けしている（行にチェックがある）ときだけ出す
    document.getElementById("legend-nodes-check").style.display = colorMode === "role" ? "" : "none";
    // 色分けが「役割」以外のときも、役割で絞り込めるよう別の欄に出す
    document.getElementById("legend-role-filter").style.display = colorMode === "role" ? "none" : "block";
    document.getElementById("legend-roles").innerHTML = colorMode === "role" ? "" : roleRows;
  }
  // 凡例: 表示枠が狭いときは自動で折りたたむ（利用者が開閉したらそれに従う）
  // ---- ページ全体の拡大を止める（凡例の文字が大きくならないように） ----
  // Ctrl（⌘）＋ホイールやピンチはページの拡大ではなく図の拡大・縮小だけにする（図は Cytoscape が処理する）
  window.addEventListener("wheel", (e) => { if (e.ctrlKey || e.metaKey) e.preventDefault(); }, { passive: false });
  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && ["+", "-", "=", "0", ";"].includes(e.key)) e.preventDefault();
    // ブラウザ版: 戻る・進む（⌘[ ⌘] ⌥← ⌥→）は外側の画面に渡す（ブラウザ自身の履歴移動はさせない）
    const back = (e.metaKey && e.key === "[") || (e.altKey && e.key === "ArrowLeft");
    const fwd = (e.metaKey && e.key === "]") || (e.altKey && e.key === "ArrowRight");
    if ((back || fwd) && window.frameElement) {
      e.preventDefault();
      e.stopPropagation();
      window.frameElement.ownerDocument.dispatchEvent(new KeyboardEvent("keydown", {
        key: e.key, code: e.code, metaKey: e.metaKey, altKey: e.altKey, ctrlKey: e.ctrlKey, shiftKey: e.shiftKey,
        bubbles: true, cancelable: true,
      }));
    }
  }, true);
  ["gesturestart", "gesturechange"].forEach((t) => document.addEventListener(t, (e) => e.preventDefault()));
  // それでもページが拡大された場合は、凡例を拡大率の逆数で縮めて見た目の大きさと位置を保つ
  function keepLegendSize() {
    const vv = window.visualViewport;
    const legend = document.getElementById("legend");
    if (!vv || Math.abs(vv.scale - 1) < 0.001) { legend.style.transform = ""; return; }
    const right = document.documentElement.clientWidth - (vv.offsetLeft + vv.width);
    legend.style.transformOrigin = "top right";
    legend.style.transform = `translate(${-right}px, ${vv.offsetTop}px) scale(${1 / vv.scale})`;
  }
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", keepLegendSize);
    window.visualViewport.addEventListener("scroll", keepLegendSize);
  }

  let legendUserSet = false;
  const NARROW = 720;
  function setLegendCollapsed(collapsed) {
    document.body.classList.toggle("legend-collapsed", collapsed);
    document.getElementById("legend-caret").textContent = collapsed ? "◂" : "▾";
    cy.resize();
  }
  document.getElementById("legend-toggle").addEventListener("click", () => {
    legendUserSet = true;
    setLegendCollapsed(!document.body.classList.contains("legend-collapsed"));
  });

  // 枠の大きさが変わったら図を合わせ直す（利用者が拡大・移動した後は合わせ直さない）
  let autoFit = true;
  cy.on("scrollzoom dragpan pinchzoom", () => { autoFit = false; });
  let resizeTimer = null;
  // 表示範囲のアニメーション中に枠の大きさが変わると、アニメーションが古い大きさの行き先へ戻してしまうので、
  // 終わってから合わせ直す
  let fitAfterAnimation = false;
  function fitToFrame() {
    if (!autoFit || !cy.elements().length) return;
    if (animating || cy.animated()) { fitAfterAnimation = true; return; }
    fitAfterAnimation = false;
    cy.fit(laidOut().union(cy.nodes("[?isLabel]")), 40);
  }
  function viewportAnimationDone() {
    if (fitAfterAnimation) setTimeout(fitToFrame, 0);   // 完了の直後はまだアニメーション中と判定されるので一拍おく
  }
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (!legendUserSet) setLegendCollapsed(window.innerWidth < NARROW);
      cy.resize();
      fitZoomCache = null;   // 枠の大きさが変わったので、全体を収める拡大率を測り直す
      fitToFrame();
    }, 150);
  });

  function refreshStyle() {
    renderNodeLegend();
    cy.style().update();
  }

  // 凡例: 作用の見本の線を今の色分けに合わせ、種類の欄の色見本は「作用」で色分けしている間は隠す
  function refreshEdgeLegend() {
    const byEffect = edgeColorMode === "effect";
    document.querySelectorAll("#legend svg[data-effect]").forEach((svg) => {
      const color = byEffect ? EFFECT_COLORS[svg.dataset.effect] : "#555";
      svg.querySelectorAll("line, polygon").forEach((el) => {
        el.setAttribute("stroke", color);
        if (el.getAttribute("fill") !== "#fff") el.setAttribute("fill", color);
      });
    });
    document.getElementById("legend-types").classList.toggle("no-color", byEffect);
  }

  // ---- Python から呼ばれる API ----
  window.app = {
    setElements(elements, legend, positions, layoutName, depth, opts = {}) {
      beginBusy();
      currentLayout = layoutName;
      const centerOn = opts.centerOn || null;   // 2・3 回目のクリック: この遺伝子を画面の中心にする
      const keepView = !!opts.keepView;   // 2 回目のクリック: 表示範囲と選択をそのままにする
      maxDepth = depth;
      autoFit = true;
      hideActionMenu();
      const keepSelected = keepView ? selectedNodeId : null;
      if (!keepView) { selectedNodeId = null; clickStage = 0; }
      if (!legendUserSet) setLegendCollapsed(window.innerWidth < NARROW);
      lodDepth = null;
      // 前の表示の位置を覚えておく（新しい配置へ動かすアニメーションに使う）
      const prev = {};
      cy.nodes().filter(isGene).forEach((n) => { prev[n.id()] = { x: n.position("x"), y: n.position("y") }; });
      const gen = ++renderGen;
      window.orthoRouter.cancel();   // 前の表示の線の経路計算を止める
      layoutToken++;                  // 前の表示の配置（同心円など）が後から終わっても線を引かない
      if (layoutRun) { layoutRun.stop(); layoutRun = null; }
      cy.stop(true, false);
      cy.elements().stop(true, false);
      cy.elements().stop(true, true);
      animating = false;
      cy.elements().remove();
      if (!elements.length) {
        endBusy();
        showMessage("上の欄に注目する遺伝子名を入力して「表示」を押してください。例: TOR1");
        renderLegend({ types: [], categories: [], roles: [] });
        return;
      }
      showMessage("");
      cy.add(elements);
      // 転写因子どうしの線は、緑で選んでいる遺伝子の線だけ見せる（描き直しても選択は残るので、今の選択で決める）
      cy.edges("[?tfPair]").forEach((e) => e.toggleClass("tfpair-off", tfPairOff(e, null)));
      addComplexLabels();
      applyFilters();
      applyConditionFilter();
      if (orthogonal) cy.edges().addClass("routing");   // 直角の経路を計算し終えるまで線を隠す
      fixComplexLabels();
      cy.edges().forEach((e) => e.toggleClass("modified", !!e.data("modified")));
      renderLegend(legend);
      // 配置以降は後回しにし、その間に新しい表示の指示が来ていたら捨てる（段数を続けて変えたとき、重い計算を止める）
      setTimeout(() => { if (gen === renderGen) layoutAndShow(); }, 0);
      const layoutAndShow = () => {
      const leaves = cy.nodes().filter(isGene);
      const saved = positions || {};
      const missing = leaves.filter((n) => !saved[n.data("gene")]);
      if (positions && missing.length < leaves.length) {
        leaves.forEach((n) => { const p = saved[n.data("gene")]; if (p) n.position({ x: p[0], y: p[1] }); });
        const byLevel = layoutName === "dagre_tb" || layoutName === "dagre_lr";
        if (byLevel) {
          // 遺伝子が増えても減っても、段と配置の番号のルールどおりに並べ直す（操作の順序によらず同じ配置にする）
          arrangeByLevel(layoutName === "dagre_lr");
        } else if (missing.length) {
          // 位置が決まっていない遺伝子は、つながっている遺伝子の周りに並べる（なければ右側）
          const placed = leaves.difference(missing);
          const bb = placed.boundingBox();
          const around = {};
          let spare = 0;
          // まとめの遺伝子は、つながる遺伝子の周りではなく枠ごとに地図の下へ
          if (missing.some((n) => n.data("bundled"))) placeBundles(false, placed.filter((n) => !n.data("bundled")).boundingBox(), (n, p) => n.position(p));
          missing.filter((n) => !n.data("bundled")).forEach((n) => {
            const anchor = n.neighborhood("node").intersection(placed)[0];
            if (anchor) {
              const k = around[anchor.id()] = (around[anchor.id()] || 0) + 1;
              const angle = (k * 2.4) % (2 * Math.PI);
              const r = 90 + 28 * Math.floor(k / 6);
              const p = anchor.position();
              n.position({ x: p.x + r * Math.cos(angle), y: p.y + r * Math.sin(angle) });
            } else {
              n.position({ x: bb.x2 + 120 + (spare % 3) * 90, y: bb.y1 + Math.floor(spare / 3) * 50 });
              spare++;
            }
          });
        }
        if (byLevel) addLevelLabels(layoutName === "dagre_lr");
        transition(prev, keepView, centerOn);
      } else {
        runLayout(layoutName, prev, centerOn);
      }
      refreshStyle();
      if (keepSelected && cy.getElementById(keepSelected).nonempty()) {
        highlight(cy.getElementById(keepSelected));
        if (clickStage < 2 && pickedSet.size > 1) {
          pickedSet = new Set([...pickedSet].filter((id) => cy.getElementById(id).nonempty()));
          refreshPicked();
        } else {
          markPicked(keepSelected, clickStage);
        }
      } else {
        renderMarks();
      }
      applyPath();   // 「経路」の表示中なら、描き直した後も経路を目立たせる
      };
    },
    // 説明欄の一覧から線を選んだとき: その線に注目する（省く線でも見せて、目立たせる）
    focusEdge(id) {
      const e = cy.getElementById(id);
      if (e.empty()) return;
      focusEdgeId = id;
      syncTfPairs();
      if (!pathIds) highlight(e);
    },
    // 左の「条件」タブ: off は外した条件のキー。外したものがなければ普段どおり。外したものがあれば、
    // 選んでいる条件を 1 つでも持つ線と、その両端の遺伝子だけを目立たせる（「条件の記録なし」も条件の 1 つとして扱う）
    setConditionFilter(off, top = false, genes = null) {
      condOff = new Set(off || []);
      condTop = !!top;
      condGenes = genes ? new Set(genes) : null;
      applyConditionFilter();
    },
    setColorMode(mode) { colorMode = mode; refreshStyle(); },
    setDataValues(values, title, note = null, ko = null) {
      dataValues = values || {};
      dataTitle = title;
      dataNote = note;
      dataKo = ko;
      if (DATA_KINDS.includes(colorMode)) refreshStyle();
    },
    setEdgeColorMode(mode) { edgeColorMode = mode; refreshEdgeLegend(); cy.style().update(); },
    setLod(on) {
      lodEnabled = on;
      document.querySelectorAll('#legend input.opt[data-opt="lod"]').forEach((cb) => { cb.checked = on; });
      syncSectionChecks();
      applyLod(true);
      reroute();
    },
    setEdgeLabels(symbols, more = true) {
      showSymbols = symbols;
      const sizeChanged = showMore !== more;
      showMore = more;
      // 凡例の「表示」欄のチェックを今の設定に合わせる
      const opts = { symbols, more };
      document.querySelectorAll("#legend input.opt").forEach((cb) => { if (cb.dataset.opt in opts) cb.checked = !!opts[cb.dataset.opt]; });
      syncSectionChecks();
      cy.style().update();
      if (sizeChanged) reroute();   // 「+数字」の有無で遺伝子の箱の幅が変わるので線を引き直す
    },
    // 再配置・配置の方式の変更: 今の位置から新しい配置へ動かす。線は経路を計算し終えるまで隠す（古い線を出さない）
    runLayout(name) {
      window.orthoRouter.cancel();
      clearTimeout(rerouteTimer);
      const prev = {};
      cy.nodes().filter(isGene).forEach((n) => { prev[n.id()] = { x: n.position("x"), y: n.position("y") }; });
      if (orthogonal) cy.edges().addClass("routing");
      runLayout(name, prev);
    },
    setFilters(h) {
      hidden = { roles: h.roles || [], types: h.types || [], effects: h.effects || [] };
      renderLegend(lastLegend);
      if (!cy.elements().length) return;
      const before = cy.nodes().filter((n) => isGene(n) && !n.hasClass("filtered"));
      applyFilters();
      // 表示に戻した遺伝子が、詰めた後の配置と重なるときだけ配置し直す（隠したときは詰めない）
      const shown = cy.nodes().filter((n) => isGene(n) && !n.hasClass("filtered"));
      const back = shown.difference(before);
      const overlapsOthers = back.some((n) => {
        const a = n.boundingBox({ includeLabels: false });
        return shown.difference(n).some((m) => {
          const b = m.boundingBox({ includeLabels: false });
          return a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2;
        });
      });
      if (overlapsOthers) { runLayout(currentLayout); return; }
      // 隠したときは引き直さない（残った線は隠す前の形のまま）。表示に戻した線に使える形がないときだけ引き直す
      // 縮小で省略している線（経路を引いていない）は数えない。実際に表示される線に経路がないときだけ引き直す
      const shownNow = (x) => x.style("display") !== "none";
      if (orthogonal && cy.edges().some((e) => e.hasClass("routing") && shownNow(e) && shownNow(e.source()) && shownNow(e.target()))) reroute();
    },
    fit() { autoFit = true; cy.animate({ fit: { eles: cy.elements(), padding: 40 }, duration: 300 }); },
    getPositions() {
      const out = {};
      cy.nodes().filter(isGene).forEach((n) => {
        const p = n.position(); out[n.data("gene")] = [Math.round(p.x), Math.round(p.y)];
      });
      return JSON.stringify(out);
    },
    focusGene(gene) {
      const target = cy.nodes().filter((n) => (n.data("gene") || "").toUpperCase() === gene.toUpperCase());
      cy.nodes().removeClass("found");
      if (target.empty()) return false;
      target.addClass("found");
      cy.elements().unselect();
      target.select();
      highlight(target[0]);
      cy.animate({ center: { eles: target }, zoom: Math.max(cy.zoom(), 1.2), duration: 350 });
      if (bridge) bridge.onNodeClicked(target[0].id());
      return true;
    },
    // 線が多いため表示をキャンセルしたとき: 2・3 回目のクリックを取り消し、選択（1 回目）の状態に戻す
    resetPick() {
      const n = selectedNodeId ? cy.getElementById(selectedNodeId) : null;
      if (n && n.nonempty()) { clickStage = 1; markPicked(selectedNodeId, 1); }
    },
    // 経路: 経路上の遺伝子・経路が通る関係（"p上流>p下流"）・チェックした遺伝子など（縁取り）
    showPath(nodeIds, edgeKeys, endIds, anchorId, marks = {}) {
      pathIds = nodeIds; pathEdges = new Set(edgeKeys); pathEnds = new Set(endIds); pathFocus = null;
      relAnchor = anchorId; pathMarks = marks || {};
      applyPath();
    },
    // 経路の表示中にクリックした遺伝子を通る経路（null で強調をやめる）
    focusPath(nodeIds, edgeKeys) { pathFocus = nodeIds ? { nodes: nodeIds, edges: edgeKeys } : null; applyPath(); },
    clearPath() { resetPath(); highlight(null); },
    // 左の「経路」タブの一覧で選んだ遺伝子を、緑の選択にする（地図の選択と連動）
    setPicked(ids) {
      hideActionMenu();
      const valid = ids.filter((id) => cy.getElementById(id).nonempty());
      pickedSet = new Set(valid);
      selectedNodeId = valid.length ? valid[valid.length - 1] : null;
      clickStage = valid.length ? 1 : 0;
      refreshPicked();
      if (pathIds) applyPath();
      else if (valid.length) highlightMany(cy.collection(valid.map((id) => cy.getElementById(id))));
      else highlight(null);
    },
    // 左の「遺伝子」タブで、緑で選んでいる遺伝子をもう一度押したとき: その遺伝子へ移る（凡例の名前を押したときと同じ）
    zoomToGene(id) { zoomToGene(id); },
    // 緑（1 回目のクリック）で選択中の遺伝子の ID（なければ null）
    pickedNode() { return clickStage === 1 && selectedNodeId ? selectedNodeId : null; },
    // 緑で選択中のすべて（Shift で複数選んだもの）
    pickedNodes() { return JSON.stringify(clickStage === 1 ? [...pickedSet] : []); },
    clearHighlight() { highlight(null); cy.nodes().removeClass("found"); cy.elements().unselect(); },
    setOrthogonal(on) { orthogonal = on; cy.style().update(); reroute(); },
    setMaxDepth(depth) { maxDepth = depth; applyLod(true); },
    zoomTo(level) { cy.zoom({ level, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } }); },
    exportPng() { return cy.png({ output: "base64uri", full: true, scale: 3, bg: "#ffffff" }); },
    exportSvg() { return cy.svg({ full: true, scale: 1, bg: "#ffffff" }); },
  };

  new QWebChannel(qt.webChannelTransport, (channel) => {
    bridge = channel.objects.bridge;
    bridge.onReady();
  });
})();
