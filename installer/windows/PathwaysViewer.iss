; Windows 用のインストーラー（Inno Setup 6）。dist\PathwaysViewer\ を setup.exe にまとめる。
;   先に pyinstaller --noconfirm PathwaysViewer.spec でアプリを作っておく。
;   iscc /DAppVersion=1.0.0 installer\windows\PathwaysViewer.iss  → dist\PathwaysViewer-<版>-windows-setup.exe
; 管理者権限なしでユーザーごとに入れる（%LOCALAPPDATA%\Programs\PathwaysViewer）。
; アンインストールしても %APPDATA%\PathwaysViewer のデータ（編集・ノード配置）は消さない。

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Pathways viewer for Saccharomyces cerevisiae"
#define AppExe "PathwaysViewer.exe"

[Setup]
; AppId は変えない（変えると別のアプリとして入り、上書き更新にならない）
AppId={{6F1C2B7A-4E3D-4B8A-9C55-2D7E1A0B9F31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Raico-lab
DefaultDirName={autopf}\PathwaysViewer
DefaultGroupName=Pathways viewer
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SourceDir=..\..
OutputDir=dist
OutputBaseFilename=PathwaysViewer-{#AppVersion}-windows-setup
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\PathwaysViewer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; 前の版の同梱ファイルが残らないように、上書きの前に消す
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,Pathways viewer}"; Flags: nowait postinstall skipifsilent
