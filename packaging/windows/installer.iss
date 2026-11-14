#ifndef AppVersion
  #error AppVersion is required. Use build.ps1 to compile this installer.
#endif
#ifndef BundleDir
  #error BundleDir is required. Use build.ps1 to compile this installer.
#endif

[Setup]
AppId={{5A0A167E-83C4-4F85-94A3-9AF081A83007}
AppName=QEcho
AppVersion={#AppVersion}
AppPublisher=QEcho
AppPublisherURL=https://github.com/xuhuilun/QEcho
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\QEcho
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\QEcho.exe
OutputBaseFilename=QEcho-Setup-{#AppVersion}-x64
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
#ifdef SignBuild
SignTool=QEchoReleaseSigner
SignedUninstaller=yes
#endif

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\QEcho"; Filename: "{app}\QEcho.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\QEcho"; Filename: "{app}\QEcho.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\QEcho.exe"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,QEcho}"; Flags: nowait postinstall skipifsilent; Check: IsManualInstall
Filename: "{app}\QEcho.exe"; WorkingDir: "{app}"; Parameters: "--after-update"; Flags: nowait; Check: RelaunchAfterUpdate

[CustomMessages]
en.WaitForQEcho=QEcho is still closing. Close QEcho and run the update again.

[Code]
const
  Synchronize = $00100000;
  WaitObject0 = 0;
  ErrorInvalidParameter = 87;

{ Inno Setup's default compiler generates a 32-bit Setup even for an x64 payload. }
function OpenProcess(Access: LongWord; InheritHandle: LongBool; ProcessId: LongWord): LongWord;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(Handle: LongWord; Milliseconds: LongWord): LongWord;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(Handle: LongWord): LongBool;
  external 'CloseHandle@kernel32.dll stdcall';

function HasSwitch(Switch: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if Uppercase(ParamStr(I)) = Switch then
    begin
      Result := True;
      Exit;
    end;
end;

function IsClientUpdate: Boolean;
begin
  Result := HasSwitch('/QECHOUPDATE');
end;

function IsManualInstall: Boolean;
begin
  Result := not IsClientUpdate;
end;

function RelaunchAfterUpdate: Boolean;
begin
  Result := IsClientUpdate and not HasSwitch('/NORESTARTAPPLICATIONS');
end;

function LauncherProcessId: Integer;
var
  I: Integer;
  Arg: String;
begin
  Result := 0;
  for I := 1 to ParamCount do
  begin
    Arg := Uppercase(ParamStr(I));
    if Copy(Arg, 1, 10) = '/QECHOPID=' then
      Result := StrToIntDef(Copy(Arg, 11, Length(Arg)), 0);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ProcessId: Integer;
  ProcessHandle: LongWord;
begin
  Result := '';
  if not IsClientUpdate then
    Exit;

  Result := CustomMessage('WaitForQEcho');
  ProcessId := LauncherProcessId;
  if ProcessId <= 0 then
    Exit;

  { The desktop client releases its database and instance lock before leaving. }
  ProcessHandle := OpenProcess(Synchronize, False, ProcessId);
  if ProcessHandle = 0 then
  begin
    if DLLGetLastError = ErrorInvalidParameter then
      Result := '';
    Exit;
  end;

  try
    Log('Waiting for the previous QEcho launcher to exit.');
    if WaitForSingleObject(ProcessHandle, 30000) = WaitObject0 then
      Result := '';
  finally
    CloseHandle(ProcessHandle);
  end;
end;
