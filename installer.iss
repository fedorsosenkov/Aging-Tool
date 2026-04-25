; Inno Setup script — MOS Aging Analyzer
; Скачать Inno Setup: https://jrsoftware.org/isinfo.php
; Компиляция: запустить этот файл в Inno Setup Compiler

#define AppName      "MOS Aging Analyzer"
#define AppVersion   "2.0"
#define AppPublisher "AgingTool"
#define AppExeName   "MOS_Aging_Analyzer.exe"
#define AppIcon      "aging_tool\icon.ico"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=installer_output
OutputBaseFilename=MOS_Aging_Analyzer_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#AppIcon}
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
; Ярлык на рабочем столе — пользователь выбирает при установке
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; \
    GroupDescription: "Дополнительные значки:"; Flags: unchecked

[Files]
; Главный exe (собранный PyInstaller --onefile)
Source: "dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

; Иконка (отдельно — для ярлыка)
Source: "{#AppIcon}";          DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Ярлык в меню "Пуск"
Name: "{group}\{#AppName}";    Filename: "{app}\{#AppExeName}"; \
    IconFilename: "{app}\icon.ico"

; Ярлык "Удалить программу" в меню "Пуск"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"

; Ярлык на рабочем столе (если пользователь выбрал)
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Dirs]
; Папка exports/ создаётся рядом с exe при первом запуске программой,
; но можно создать её сразу при установке:
Name: "{app}\exports"

[Run]
; Предложить запустить после установки
Filename: "{app}\{#AppExeName}"; \
    Description: "Запустить {#AppName}"; \
    Flags: nowait postinstall skipifsilent
