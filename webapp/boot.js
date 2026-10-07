// ブラウザ版の起動: Pyodide（ブラウザの中の Python）を読み込み、アプリのコードと DB を入れて、webmain.py を動かす。
// 画面は Python 側（webapp/shim/PyQt6 の互換層）が作る。ここは読み込みの進み具合の表示と、ファイルの出し入れだけ。
"use strict";

const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.29.3/full/";
const PERSIST_DIR = "/persist";       // この PC のブラウザに残すもの（ローカルの DB・表示の設定・登録）。IndexedDB に保存する
const DOWNLOAD_DIR = "/downloads";    // ここに書いたファイルは、ブラウザのダウンロードとして保存する（CSV・PNG・SVG）

const bootEl = document.getElementById("boot");
const stepEl = document.getElementById("boot-step");
const barEl = document.getElementById("boot-bar");

function step(text, fraction) {
  if (stepEl) stepEl.textContent = text;
  if (barEl && fraction != null) barEl.style.width = `${Math.round(fraction * 100)}%`;
}

function fail(error) {
  console.error(error);
  if (!bootEl) return;
  bootEl.classList.add("failed");
  step("起動できませんでした。ページを再読み込みしてください。続くときは管理者に知らせてください。");
  const detail = document.getElementById("boot-detail");
  if (detail) detail.textContent = String(error && error.stack || error);
}

// 読み込んだバイト数を数えながら取ってくる（進み具合の表示に使う）
const progress = { loaded: 0, total: 0 };
async function fetchBytes(url, expected) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  progress.total += expected || Number(res.headers.get("content-length")) || 0;
  const reader = res.body.getReader();
  const chunks = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    size += value.length;
    progress.loaded += value.length;
    if (progress.total) step("データを読み込んでいます…", 0.1 + 0.5 * Math.min(1, progress.loaded / progress.total));
  }
  const out = new Uint8Array(size);
  let offset = 0;
  for (const c of chunks) { out.set(c, offset); offset += c.length; }
  return out;
}

async function gunzip(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

function syncfs(populate, FS) {
  return new Promise((resolve) => FS.syncfs(populate, (err) => { if (err) console.warn("syncfs", err); resolve(); }));
}

// ブラウザに残すもの（/persist）を IndexedDB に書き出す。書き込みが続いても、まとめて少し後に 1 回だけ
let syncTimer = null;
let syncing = false;
function schedulePersist(FS) {
  if (syncTimer) return;
  syncTimer = setTimeout(async () => {
    syncTimer = null;
    if (syncing) { schedulePersist(FS); return; }
    syncing = true;
    await syncfs(false, FS);
    syncing = false;
  }, 1500);
}

// /downloads に書かれたファイルを、ブラウザのダウンロードとして保存する
function watchDownloads(FS) {
  setInterval(() => {
    let names;
    try { names = FS.readdir(DOWNLOAD_DIR).filter((n) => n !== "." && n !== ".."); } catch (e) { return; }
    for (const name of names) {
      const path = `${DOWNLOAD_DIR}/${name}`;
      const stat = FS.stat(path);
      // 書き終わってから（少し待っても大きさが変わらない）保存する
      const key = `${stat.size}:${stat.mtime}`;
      if (watchDownloads.seen[path] !== key) { watchDownloads.seen[path] = key; continue; }
      delete watchDownloads.seen[path];
      const data = FS.readFile(path);
      FS.unlink(path);
      const type = name.endsWith(".png") ? "image/png" : name.endsWith(".svg") ? "image/svg+xml" : "text/csv";
      const url = URL.createObjectURL(new Blob([data], { type }));
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    }
  }, 400);
}
watchDownloads.seen = {};

// 画面の操作の中で起きた Python の例外を、右下に小さく知らせる（アプリは止めない）
window.pvReportError = (text) => {
  let box = document.getElementById("pv-error");
  if (!box) {
    box = document.createElement("div");
    box.id = "pv-error";
    box.innerHTML = "<button title='閉じる'>✕</button><b>エラーが起きました。</b> 操作は続けられます。続くときはページを再読み込みしてください。<pre></pre>";
    box.querySelector("button").onclick = () => { box.style.display = "none"; };
    document.body.appendChild(box);
  }
  box.querySelector("pre").textContent = String(text).split("\n").slice(-12).join("\n");
  box.style.display = "block";
};

const t0 = performance.now();
const mark = (label) => console.debug(`起動 ${label}: ${((performance.now() - t0) / 1000).toFixed(1)} 秒`);

async function boot() {
  step("準備しています…", 0.02);
  const version = await (await fetch("version.json", { cache: "no-cache" })).json();
  const v = encodeURIComponent(version.version);
  window.PV_VERSION = version;
  const pyodideReady = (async () => {
    const py = await loadPyodide({ indexURL: PYODIDE_URL });
    await py.loadPackage(["sqlite3", "sqlalchemy"], { messageCallback: () => {} });
    mark("Python");
    return py;
  })();
  const files = Promise.all([
    fetchBytes(`app.zip?v=${v}`),
    fetchBytes(`data/tor_pathway.db.gz?v=${v}`),
    fetchBytes(`data/expression.db.gz?v=${v}`),
  ]);
  const [pyodide, [appZip, dbGz, exprGz]] = await Promise.all([pyodideReady, files]);
  mark("ファイル");
  step("展開しています…", 0.65);
  const FS = pyodide.FS;
  window.pvPyodide = pyodide;   // 調べるとき用（開発者ツールから Python を動かせる）
  pyodide.unpackArchive(appZip, "zip", { extractDir: "/app" });
  FS.writeFile("/app/data/tor_pathway.db", await gunzip(dbGz));
  FS.writeFile("/app/data/expression.db", await gunzip(exprGz));
  FS.mkdirTree(PERSIST_DIR);
  FS.mount(FS.filesystems.IDBFS, {}, PERSIST_DIR);
  await syncfs(true, FS);
  FS.mkdirTree(DOWNLOAD_DIR);
  watchDownloads(FS);
  window.pvPersist = () => schedulePersist(FS);
  setInterval(() => schedulePersist(FS), 4000);   // 書き込みの通知がなくても、時々書き出す（変わったファイルだけ）
  window.addEventListener("pagehide", () => FS.syncfs(false, () => {}));
  document.addEventListener("visibilitychange", () => { if (document.hidden) FS.syncfs(false, () => {}); });

  step("アプリを起動しています…", 0.75);
  // 画面の更新を挟むため、一度ブラウザに戻ってから Python を動かす
  await new Promise((r) => setTimeout(r, 30));
  pyodide.runPython(`
import os, sys
sys.path[:0] = ["/app/shim", "/app", "/app/site"]
os.environ["XDG_DATA_HOME"] = "${PERSIST_DIR}"
sys.pycache_prefix = "${PERSIST_DIR}/pycache"   # コンパイルした結果をブラウザに残す（webmain._cache_bytecode）
sys.dont_write_bytecode = True
import webmain
if ${location.search.includes("profile") ? "True" : "False"}:
    import cProfile, pstats, io
    _prof = cProfile.Profile()
    _prof.runcall(webmain.main)
    _out = io.StringIO()
    pstats.Stats(_prof, stream=_out).sort_stats("cumulative").print_stats(45)
    print(_out.getvalue())
    _out = io.StringIO()
    pstats.Stats(_prof, stream=_out).sort_stats("tottime").print_stats(30)
    print(_out.getvalue())
else:
    webmain.main()
`);
  mark("画面");
  if (bootEl) bootEl.remove();
}

boot().catch(fail);
