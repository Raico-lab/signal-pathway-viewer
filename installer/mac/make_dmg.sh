#!/bin/zsh
# dist/PathwaysViewer.app から Mac 用のインストーラー（.dmg）を作る。
#   先に pyinstaller --noconfirm PathwaysViewer.spec でアプリを作っておく。
#   ./installer/mac/make_dmg.sh [版]   → dist/PathwaysViewer-<版>-mac.dmg
# 開くとアプリと「アプリケーション」フォルダが並ぶので、アプリをドラッグして入れる。
set -e
cd "$(dirname "$0")/../.."

VERSION="${1:-${PATHWAYS_VERSION:-0.0.0}}"
APP="dist/PathwaysViewer.app"
DMG="dist/PathwaysViewer-${VERSION}-mac.dmg"
[ -d "$APP" ] || { echo "$APP がありません。先に PyInstaller でビルドしてください。"; exit 1; }

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/PathwaysViewer.app"
ln -s /Applications "$STAGE/Applications"

rm -f "$DMG"
hdiutil create -volname "Pathways viewer" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG"
echo "作成しました: $DMG"
