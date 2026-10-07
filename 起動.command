#!/bin/zsh
# シグナル経路ビュアーを起動する（Finder でダブルクリック）。
# ソースコードから起動するため、コードを変更するとすぐ反映される。
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "初回セットアップ中です（数分かかります）…"
  PY=$(command -v python3.11 || command -v python3.12 || command -v python3.13)
  if [ -z "$PY" ]; then
    echo "Python 3.10 以上が見つかりません。インストールしてから再度実行してください。"
    read -k1 "?何かキーを押すと閉じます"
    exit 1
  fi
  "$PY" -m venv .venv && .venv/bin/pip install -q -r requirements.txt || {
    echo "セットアップに失敗しました。"
    read -k1 "?何かキーを押すと閉じます"
    exit 1
  }
fi

echo "起動しています…（アプリを閉じるとこのウィンドウも終了します）"
.venv/bin/python main.py 2>> "$HOME/Library/Logs/PathwaysViewer.log"
