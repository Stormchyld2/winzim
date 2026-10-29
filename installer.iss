; NOT USED YET - the GitHub build makes only CindrixRecon.exe. Kept for when we package an installer later.
[Setup]
AppName=Cindrix Recon
AppVersion=1.3.0
AppPublisher=Cindrix
DefaultDirName={autopf}\Cindrix Recon
DefaultGroupName=Cindrix Recon
UninstallDisplayIcon={app}\CindrixRecon.exe
OutputDir=installer
OutputBaseFilename=CindrixRecon-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern

[Files]
Source: "dist\CindrixRecon.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Cindrix Recon"; Filename: "{app}\CindrixRecon.exe"
Name: "{autodesktop}\Cindrix Recon"; Filename: "{app}\CindrixRecon.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\CindrixRecon.exe"; Description: "Start Cindrix Recon"; Flags: nowait postinstall skipifsilent
