; Inno Setup script for the UAV SILM Ground Station (built by packaging/build_app.py)
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "UAV SILM Ground Station"
#define ExeName "UAV-SILM-Ground-Station.exe"

[Setup]
AppId={{6E1F3C52-8B0D-4C4A-9C77-5A3E2F1B7D10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=UAV SILM team
AppPublisherURL=https://www.uavsilm.eu
; per-user install: no administrator rights needed
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\UAV SILM Ground Station
DefaultGroupName=UAV SILM
DisableProgramGroupPage=yes
LicenseFile={#SourceDir}\_internal\LICENSE
OutputDir={#OutDir}
OutputBaseFilename=UAV-SILM-Ground-Station-Setup-{#AppVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#ExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#ExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#ExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#ExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
