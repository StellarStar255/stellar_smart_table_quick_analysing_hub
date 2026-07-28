; Windows 安装包（Inno Setup 6）。
; 构建: ISCC.exe /DAppVersion=1.0.0 packaging\installer.iss
; 一键升级依赖: 固定 AppId + CloseApplications + 静默安装后自动重启应用。

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Smart Table Hub"
#define ExeName "SmartTableHub.exe"

[Setup]
AppId={{8E2F4A1C-9B7D-4E63-A5D8-2C1F0B3E7A94}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=StellarStar255
AppPublisherURL=https://github.com/StellarStar255/stellar_smart_table_quick_analysing_hub
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=SmartTableHub-{#AppVersion}-windows-x64-setup
SetupIconFile=..\assets\app_icon.ico
UninstallDisplayIcon={app}\{#ExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; 升级时自动关闭正在运行的旧版本
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Files]
Source: "..\dist\SmartTableHub\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#ExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#ExeName}"; Tasks: desktopicon

[Run]
; 安装/升级完成后启动应用（静默升级时也会执行，实现无感重启）
Filename: "{app}\{#ExeName}"; Description: "启动 {#AppName}"; \
    Flags: nowait postinstall runascurrentuser
