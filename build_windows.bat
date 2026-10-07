@echo off
chcp 65001 >nul
rem Windows PC 上で実行ファイルとインストーラーを作る（Python 3.10 以上が必要）
rem   build_windows.bat [版]   例: build_windows.bat 1.0.0
set VERSION=%1
if "%VERSION%"=="" set VERSION=0.0.0
set PATHWAYS_VERSION=%VERSION%
py -3 -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
pyinstaller --noconfirm PathwaysViewer.spec || goto :error

set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist %ISCC% (
  echo.
  echo Inno Setup 6 が見つからないため、インストーラーは作りませんでした。
  echo https://jrsoftware.org/isdl.php から入れると dist\PathwaysViewer-%VERSION%-windows-setup.exe を作れます。
  echo それまでは dist\PathwaysViewer フォルダを丸ごと配布し、PathwaysViewer.exe をダブルクリックで起動します。
  pause
  exit /b 0
)
%ISCC% /DAppVersion=%VERSION% installer\windows\PathwaysViewer.iss || goto :error
echo.
echo 作成しました: dist\PathwaysViewer-%VERSION%-windows-setup.exe
pause
exit /b 0

:error
echo ビルドに失敗しました。
pause
exit /b 1
