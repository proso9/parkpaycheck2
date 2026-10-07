; 停车场异常车辆检测 GUI 安装包脚本（Inno Setup 6）
; 由 CI 调用（见 .github/workflows/release.yml）：
;   ISCC /DAppVersion=x.y.z /DOutputName=parkcheck-setup-vX.Y.Z-windows-x64 installer/parkcheck.iss
; 打包内容为 Nuitka standalone 产物目录 build/parkcheck-gui/（先完成 Nuitka 打包再编译本脚本）。
; 注意：本文件必须保存为 UTF-8（带 BOM），否则中文 AppName 与注释会乱码。

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef OutputName
  #define OutputName "parkcheck-setup-unknown-windows-x64"
#endif

#define AppName "停车场异常车辆检测"
#define AppEx "parkcheck-gui.exe"

[Setup]
AppId={{7D1A0DCF-2AB8-40F5-AE32-9733D8F0C278}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
; 用户级安装（不需要管理员权限）：默认装到 %LOCALAPPDATA%\parkcheck。
; GUI 的默认日志/输出目录位于安装目录下（exe 旁的 document/、output/），
; 必须保证当前用户可写，因此不能用 Program Files。
PrivilegesRequired=lowest
DefaultDirName={localappdata}\parkcheck
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
WizardStyle=modern
; 产物输出到仓库根目录的 build/（相对本脚本目录），供 CI 步骤附加到 Release
OutputDir=..\build
OutputBaseFilename={#OutputName}
Compression=lzma2/max
SolidCompression=yes
UninstallDisplayIcon={app}\{#AppEx}
; 升级时若 GUI 正在运行（含系统托盘后台驻留），提示用户关闭后继续
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\build\parkcheck-gui\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppEx}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppEx}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppEx}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
