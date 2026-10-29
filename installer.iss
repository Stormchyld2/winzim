; Inno Setup script: builds installer\WinZim-Setup.exe from dist\WinZim.exe
[Setup]
AppName=WinZim
AppVersion=1.1.0
AppPublisher=WinZim
DefaultDirName={autopf}\WinZim
DefaultGroupName=WinZim
UninstallDisplayIcon={app}\WinZim.exe
OutputDir=installer
OutputBaseFilename=WinZim-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern

[Files]
Source: "dist\WinZim.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\WinZim"; Filename: "{app}\WinZim.exe"
Name: "{autodesktop}\WinZim"; Filename: "{app}\WinZim.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\WinZim.exe"; Description: "Start WinZim"; Flags: nowait postinstall skipifsilent
