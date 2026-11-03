; Inno Setup script for XRD Analyzer (per-user install by default, no admin rights needed).
; Build: ISCC.exe /DAppVersion=0.1.1 packaging\windows\xrd-analyzer.iss
; Expects the PyInstaller output in dist\XRD-Analyzer\ (relative to the repository root).
#ifndef AppVersion
  #define AppVersion "0.1.1"
#endif
#define AppName "XRD Analyzer"
#define SrcDir "..\..\dist\XRD-Analyzer"

[Setup]
AppId={{6B7C2E7E-1E1B-4E6A-9F0D-3B1F0C8A2D41}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=xrd-analyzer contributors
AppPublisherURL=https://github.com/jongsu-king/xrd-analyzer
AppSupportURL=https://github.com/jongsu-king/xrd-analyzer/issues
DefaultDirName={autopf}\XRD Analyzer
DefaultGroupName=XRD Analyzer
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\..\LICENSE
SetupIconFile=..\icons\xrd-analyzer.ico
UninstallDisplayIcon={app}\XRD-Analyzer.exe
OutputDir=..\..\dist
OutputBaseFilename=XRD-Analyzer-{#AppVersion}-windows-x64-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesEnvironment=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
#if FileExists(AddBackslash(CompilerPath) + "Languages\Korean.isl")
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "addtopath"; Description: "Add the command-line tool (xrd-analyzer-cli) to PATH"; Flags: unchecked

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\XRD Analyzer"; Filename: "{app}\XRD-Analyzer.exe"
Name: "{group}\Uninstall XRD Analyzer"; Filename: "{uninstallexe}"
Name: "{autodesktop}\XRD Analyzer"; Filename: "{app}\XRD-Analyzer.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
Filename: "{app}\XRD-Analyzer.exe"; Description: "{cm:LaunchProgram,XRD Analyzer}"; Flags: nowait postinstall skipifsilent

[Code]
function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Param) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;
