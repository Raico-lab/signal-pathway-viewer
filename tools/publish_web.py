"""ブラウザ版を GitHub Pages に公開し、デスクトップ版のインストーラーを Releases に置く。

公開用のリポジトリ（webapp/site.json の repo。中身は書き出したものだけで、ソースは置かない）に:
- gh-pages ブランチ: tools/build_web.py で作った web_dist/ の中身（毎回 1 つのコミットで置き換える。履歴に DB を溜めない）
- gh-pages ブランチの一番上に、デスクトップ版が起動時に照らすもの（data/server.json の base_url がここを指す）:
  manifest.json（DB の版・ハッシュ・取り先 data/tor_pathway.db.gz）と、情報源タブの papers.json・sources.json
- Releases の「desktop」: Windows 版・Mac 版のインストーラー（--desktop のとき。ソースのリポジトリの Actions「build」の
  最新の成功した成果物を、決まった名前 PathwaysViewer-windows-setup.exe・PathwaysViewer-mac.dmg で置き換える）

使い方:
  python tools/publish_web.py             ブラウザ版だけを公開する
  python tools/publish_web.py --desktop   インストーラーも新しくする（Actions の build が終わってから）
  python tools/publish_web.py --installers <フォルダ>   取ってあるインストーラーを使う
  python tools/publish_web.py --dry-run   作るだけで、公開しない
初めて使うとき、公開用のリポジトリがなければ作り、GitHub Pages を有効にする。公開先は https://<owner>.github.io/<repo>/。
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build_web  # noqa: E402

sys.path.insert(0, str(ROOT))
from tor_app.server import SCHEMA_VERSION  # noqa: E402

SITE_FILE = ROOT / "webapp" / "site.json"
SOURCE_REPO = "Raico-lab/signal-pathway-viewer"
RELEASE_TAG = "desktop"
ASSETS = {"windows-setup.exe": "PathwaysViewer-windows-setup.exe", "mac.dmg": "PathwaysViewer-mac.dmg"}


def run(*args, capture=False, check=True, cwd=None) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=capture, check=check)
    return result.stdout.strip() if capture else ""


def gh_json(*args):
    return json.loads(run("gh", *args, capture=True) or "null")


def ensure_repo(repo: str) -> None:
    if subprocess.run(["gh", "repo", "view", repo], capture_output=True).returncode == 0:
        return
    print(f"公開用のリポジトリ {repo} を作ります")
    run("gh", "repo", "create", repo, "--public", "--description",
        "Pathways viewer for Saccharomyces cerevisiae（ブラウザ版と、デスクトップ版のインストーラー）")


def ensure_pages(repo: str) -> None:
    if subprocess.run(["gh", "api", f"repos/{repo}/pages"], capture_output=True).returncode == 0:
        return
    print("GitHub Pages を有効にします（gh-pages ブランチ）")
    run("gh", "api", "-X", "POST", f"repos/{repo}/pages", "-f", "source[branch]=gh-pages", "-f", "source[path]=/")


def update_desktop(repo: str, local: Path | None = None) -> None:
    """ソースのリポジトリの Actions「build」の最新の成功した成果物（local を渡せば、そこに取ってあるもの）を、
    Releases の desktop に決まった名前で置く。"""
    with tempfile.TemporaryDirectory() as tmp:
        if local is not None:
            source, sha = local, ""
        else:
            runs = gh_json("run", "list", "-R", SOURCE_REPO, "--workflow", "build", "--status", "success", "-L", "1",
                           "--json", "databaseId,headSha")
            if not runs:
                raise SystemExit("成功した build が見つかりません")
            run_id, sha = runs[0]["databaseId"], runs[0]["headSha"][:7]
            source = Path(tmp) / "artifacts"
            print(f"build {run_id}（{sha}）の成果物を取ってきます")
            for attempt in range(3):   # 大きいので途中で切れることがある
                shutil.rmtree(source, ignore_errors=True)
                if subprocess.run(["gh", "run", "download", str(run_id), "-R", SOURCE_REPO, "-D", str(source)]).returncode == 0:
                    break
                print("取り直します")
            else:
                raise SystemExit("成果物を取れませんでした。少し待ってからやり直すか、--installers で取ってあるものを使ってください")
        files, version = [], ""
        for path in Path(source).rglob("PathwaysViewer-*"):
            for suffix, name in ASSETS.items():
                if path.name.endswith(suffix):
                    version = path.name[len("PathwaysViewer-"):-len("-" + suffix)]
                    sha = sha or version.rsplit("-", 1)[-1]
                    dest = Path(tmp) / name
                    shutil.copy2(path, dest)
                    files.append(dest)
        if len(files) != len(ASSETS):
            raise SystemExit(f"インストーラーが足りません: {[f.name for f in files]}")
        notes = f"デスクトップ版 {version}（{SOURCE_REPO} の {sha}）"
        if subprocess.run(["gh", "release", "view", RELEASE_TAG, "-R", repo], capture_output=True).returncode != 0:
            run("gh", "release", "create", RELEASE_TAG, "-R", repo, "--target", "gh-pages", "--title", "デスクトップアプリ",
                "--notes", notes,
                *map(str, files))
        else:
            run("gh", "release", "upload", RELEASE_TAG, "-R", repo, "--clobber", *map(str, files))
            run("gh", "release", "edit", RELEASE_TAG, "-R", repo, "--notes", notes)
    print(f"インストーラーを置きました: {version}")


def desktop_info(repo: str) -> dict:
    """Releases の desktop の版と大きさ（デスクトップアプリのタブに出す）。"""
    try:
        release = gh_json("release", "view", RELEASE_TAG, "-R", repo, "--json", "body,assets")
    except subprocess.CalledProcessError:
        return {}
    body = (release or {}).get("body", "")
    version = body.split("デスクトップ版 ", 1)[1].split("（", 1)[0] if "デスクトップ版 " in body else ""
    sizes = {a["name"]: a["size"] for a in (release or {}).get("assets", [])}
    return {"desktop_version": version, "desktop_sizes": sizes}


def write_server_files(out: Path, version: dict, notes: str) -> None:
    """デスクトップ版がサーバーとして読むもの（tor_app/server.py）: DB の版の manifest.json と、情報源の情報。"""
    db = ROOT / "data" / "tor_pathway.db"
    manifest = {"data_version": f"{version['built_at']}（{version.get('commit', '')}）",
                "schema_version": SCHEMA_VERSION, "sha256": hashlib.sha256(db.read_bytes()).hexdigest(),
                "url": "data/tor_pathway.db.gz", "notes": notes}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    for name in ("papers.json", "sources.json"):
        shutil.copy2(ROOT / "data" / name, out / name)


def push_pages(repo: str, out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "site"
        shutil.copytree(out, work)
        run("git", "init", "-q", "-b", "gh-pages", cwd=work)
        run("git", "add", "-A", cwd=work)
        version = json.loads((out / "version.json").read_text(encoding="utf-8"))
        run("git", "-c", "user.name=Pathways publisher", "-c", "user.email=noreply@github.com",
            "commit", "-q", "-m", f"ブラウザ版 {version['version']}（{version.get('commit', '')}）", cwd=work)
        run("git", "push", "-q", "--force", f"https://github.com/{repo}.git", "gh-pages", cwd=work)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--desktop", action="store_true", help="インストーラーも新しくする")
    parser.add_argument("--installers", type=Path, help="インストーラーを取ってあるフォルダ（gh run download で取ったもの）")
    parser.add_argument("--notes", default="", help="データの変更点の一言（デスクトップ版の更新の知らせに出す）")
    parser.add_argument("--dry-run", action="store_true", help="作るだけで、公開しない")
    args = parser.parse_args()
    site = json.loads(SITE_FILE.read_text(encoding="utf-8"))
    repo = site["repo"]
    version = build_web.build()
    write_server_files(build_web.OUT, version, args.notes)

    def write_version() -> None:
        version.update(site)
        version.update(desktop_info(repo) if not args.dry_run else {})
        (build_web.OUT / "version.json").write_text(json.dumps(version, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.dry_run:
        write_version()
        print(f"web_dist/ を作りました（公開はしていません）: 版 {version['version']}")
        return 0
    ensure_repo(repo)
    write_version()
    push_pages(repo, build_web.OUT)   # Releases はコミットのあるリポジトリにしか作れないので、先に置く
    ensure_pages(repo)
    if args.desktop or args.installers:
        update_desktop(repo, args.installers)
        write_version()                # ダウンロードのタブに新しい版と大きさを出す
        push_pages(repo, build_web.OUT)
    owner, name = repo.split("/")
    print(f"公開しました: https://{owner.lower()}.github.io/{name}/（反映まで 1〜2 分かかります）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
