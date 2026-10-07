"""YEASTRACT+ の Regulation Matrix から、環境条件（群・小群）ごとの転写制御の表を取る。

関係が働く条件（tor_app/conditions.py）を付けるのに使う。群・小群の一覧は Regulation Matrix の画面から読み、
小群ごとに 1 回問い合わせて、raw_data/YEASTRACT/conditions/「群 - 小群.tsv」（"/" は "_"）に
「転写因子<TAB>標的」の 2 列で保存する。記録がなければ空のファイルを置く（取得済みの印）。

問い合わせの条件: Documented ／ DNA binding or expression evidence ／ 転写因子は raw_data/YEASTRACT/tf_conditions.txt ／
標的はすべての遺伝子。すでにあるファイルは取り直さない（--force で取り直す）。

使い方: .venv/bin/python tools/fetch_yeastract_conditions.py [--workers 5] [--timeout 300] [--force] [--chunk 10]
（記録の多い小群は一度に問い合わせると返ってこない。--chunk 10 で転写因子を 10 個ずつに分けて問い合わせ、
途中の結果を conditions/parts/<小群>/「i-j.tsv」（tf_conditions.txt の i〜j-1 番目）に置き、全部そろったらまとめる。
途中で止まっても、もう一度実行すれば取れていない転写因子だけ取り直す。時間切れになった組はさらに半分に分けて取り直す）
"""
import argparse
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://yeastract-plus.org/yeastract/scerevisiae/"
OUT = ROOT / "raw_data" / "YEASTRACT" / "conditions"
TFS = ROOT / "raw_data" / "YEASTRACT" / "tf_conditions.txt"
TIMEOUT = 300   # 1 回の問い合わせを待つ秒数（--timeout で変える）
HEADERS = {"User-Agent": "PathwaysViewer/1.0 (research; contact via repository owner)"}


def get(url: str, data: dict | None = None, tries: int = 3) -> str:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT or None) as res:   # 0 なら待ち時間の上限なし
                return res.read().decode("utf-8", errors="replace")
        except Exception:   # noqa: BLE001  通信の失敗は少し待って取り直す
            if k == tries - 1:
                raise
            time.sleep(5 * (k + 1))
    return ""


def subgroups() -> list[tuple[str, str]]:
    """Regulation Matrix の画面の JavaScript から、(群, 小群) の一覧を読む。"""
    page = get(BASE + "formregmatrix.php")
    body = page[page.index("function subgroups()"):]
    body = body[:body.index("\n}\n")]
    pairs = []
    for block in re.finditer(r"value == '([^']+)'\) \{(.*?)\n\t\}", body, re.S):
        pairs += [(block.group(1), sub) for _, sub in re.findall(r"new Option\('([^']+)','([^']+)'\)", block.group(2))]
    return pairs


def file_for(group: str, sub: str) -> Path:
    return OUT / f"{group.replace('/', '_')} - {sub.replace('/', '_')}.tsv"


def query(group: str, sub: str, tfs: str) -> list[str]:
    """1 回問い合わせて「転写因子<TAB>標的」の行を返す。"""
    page = get(BASE + "regmatrix.php", {
        "type": "doc", "evidence": "plus", "t_pos": "true", "t_neg": "true",
        "regulators": tfs, "regulated": "", "allgenes": "on", "biggroup": group, "subgroup": sub,
        "tfs": "inserted", "ext": "png", "submit": "Generate"})
    link = re.search(r'href="(tmp/RegulationTwoColumnTable_[^"]+\.tsv)"', page)
    if link:
        text = get(BASE + link.group(1))
        return ["\t".join(p.strip() for p in line.split(";")[:2]) for line in text.splitlines() if ";" in line]
    if "No regulatory associations found" not in page:
        raise RuntimeError(f"結果の形が想定と違う: {group} / {sub}")
    return []


def write_rows(path: Path, rows: list[str]) -> None:
    path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")


def fetch(group: str, sub: str, tfs: list[str]) -> tuple[str, str, int]:
    rows = query(group, sub, "\n".join(tfs))
    write_rows(file_for(group, sub), rows)
    return group, sub, len(rows)


# --- 転写因子を分けて問い合わせる（--chunk） ---

def parts_dir(group: str, sub: str) -> Path:
    return OUT / "parts" / file_for(group, sub).stem


def done_indices(group: str, sub: str) -> set[int]:
    """取得済みの部分ファイル（i-j.tsv）が覆う転写因子の番号。"""
    done = set()
    for f in parts_dir(group, sub).glob("*.tsv"):
        m = re.fullmatch(r"(\d+)-(\d+)", f.stem)
        if m:
            done.update(range(int(m.group(1)), int(m.group(2))))
    return done


def chunks_todo(group: str, sub: str, n_tfs: int, size: int) -> list[tuple[int, int]]:
    """まだ取れていない転写因子を、連続する番号ごとに size 個ずつに分ける。"""
    done = done_indices(group, sub)
    out, i = [], 0
    while i < n_tfs:
        if i in done:
            i += 1
            continue
        j = i
        while j < n_tfs and j not in done and j - i < size:
            j += 1
        out.append((i, j))
        i = j
    return out


def fetch_part(group: str, sub: str, tfs: list[str], i: int, j: int) -> int:
    """tfs[i:j] を問い合わせて部分ファイルに置く。失敗したら半分に分けて取り直す。返り値は組の数。"""
    try:
        rows = query(group, sub, "\n".join(tfs[i:j]))
    except Exception:   # noqa: BLE001
        if j - i == 1:
            raise
        mid = (i + j) // 2
        print(f"  {group} / {sub}: 転写因子 {i}-{j} が取れないので 2 つに分けます", flush=True)
        return fetch_part(group, sub, tfs, i, mid) + fetch_part(group, sub, tfs, mid, j)
    write_rows(parts_dir(group, sub) / f"{i}-{j}.tsv", rows)
    return len(rows)


def merge_parts(group: str, sub: str, n_tfs: int) -> int | None:
    """すべての転写因子がそろっていれば、部分ファイルを 1 つにまとめて部分ファイルを消す。そろっていなければ None。"""
    if len(done_indices(group, sub) & set(range(n_tfs))) < n_tfs:
        return None
    files = sorted(parts_dir(group, sub).glob("*.tsv"), key=lambda f: int(f.stem.split("-")[0]))
    rows = list(dict.fromkeys(line for f in files for line in f.read_text(encoding="utf-8").splitlines() if line))
    write_rows(file_for(group, sub), rows)
    for f in parts_dir(group, sub).iterdir():
        f.unlink()
    parts_dir(group, sub).rmdir()
    return len(rows)


def run_chunked(todo: list[tuple[str, str]], tfs: list[str], size: int, workers: int) -> list:
    jobs = [(g, s, i, j) for g, s in todo for i, j in chunks_todo(g, s, len(tfs), size)]
    for g, s in todo:
        parts_dir(g, s).mkdir(parents=True, exist_ok=True)
    print(f"小群 {len(todo)} 個・問い合わせ {len(jobs)} 回（転写因子 {size} 個ずつ・同時に {workers} 件）", flush=True)
    failed = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_part, g, s, tfs, i, j): (g, s, i, j) for g, s, i, j in jobs}
        for k, job in enumerate(as_completed(futures), 1):
            g, s, i, j = futures[job]
            try:
                print(f"  [{k}/{len(jobs)}] {g} / {s} 転写因子 {i}-{j}: {job.result()} 組", flush=True)
            except Exception as e:   # noqa: BLE001
                failed.append((g, s, i, j))
                print(f"  [{k}/{len(jobs)}] {g} / {s} 転写因子 {i}-{j}: 失敗（{e}）", flush=True)
    for g, s in todo:
        n = merge_parts(g, s, len(tfs))
        print(f"  {g} / {s}: " + (f"{n} 組（まとめました）" if n is not None else "まだそろっていません"), flush=True)
    return failed


def write_fetched() -> None:
    (OUT / "FETCHED.txt").write_text(f"取得日: {date.today().isoformat()}\n"
                                    "Regulation Matrix / Documented / DNA binding or expression evidence / "
                                    "転写因子 tf_conditions.txt / All ORF/Genes / 環境条件の小群ごと\n", encoding="utf-8")


def main() -> None:
    global TIMEOUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--timeout", type=int, default=300, help="1 回の問い合わせを待つ秒数。0 なら上限なし（記録の多い小群は長くかかる）")
    ap.add_argument("--chunk", type=int, default=0, help="転写因子をこの数ずつに分けて問い合わせる（0 なら分けない）")
    args = ap.parse_args()
    TIMEOUT = args.timeout
    OUT.mkdir(parents=True, exist_ok=True)
    tfs = [t.strip() for t in TFS.read_text(encoding="utf-8").split() if t.strip() and not t.startswith("#")]
    todo = [(g, s) for g, s in subgroups() if args.force or not file_for(g, s).exists()]
    if args.chunk:
        failed = run_chunked(todo, tfs, args.chunk, args.workers)
        write_fetched()
        if failed:
            print(f"失敗 {len(failed)} 件（もう一度実行すると、取れていない転写因子だけ取り直します）")
            sys.exit(1)
        return
    print(f"小群 {len(todo)} 個を取得します（同時に {args.workers} 件）", flush=True)
    failed = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = {pool.submit(fetch, g, s, tfs): (g, s) for g, s in todo}
        for job in as_completed(jobs):
            g, s = jobs[job]
            try:
                _, _, n = job.result()
                print(f"  {g} / {s}: {n} 組", flush=True)
            except Exception as e:   # noqa: BLE001
                failed.append((g, s))
                print(f"  {g} / {s}: 失敗（{e}）", flush=True)
    write_fetched()
    if failed:
        print(f"失敗 {len(failed)} 件（もう一度実行すると、取れていないものだけ取り直します）")
        sys.exit(1)


if __name__ == "__main__":
    main()
