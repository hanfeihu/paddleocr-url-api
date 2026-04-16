#define AppName "OCR URL API"
#define AppVersion "1.0.22"
#define AppPublisher "hanfeihu"
#define AppURL "https://github.com/hanfeihu/paddleocr-url-api"
#define AppExeName "ocr-url-api.exe"
#define ServiceInstallScript "install-service.bat"
#define ServiceUninstallScript "uninstall-service.bat"
#define OutputBaseName "ocr-url-api-setup-1.0.22"

[Setup]
AppId={{3EDE56D8-8EB4-4127-A97E-A420DD8BE95B}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\OCR URL API
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
OutputDir=..\dist
OutputBaseFilename={#OutputBaseName}
CloseApplications=yes
RestartApplications=no
CloseApplicationsFilter=ocr-url-api.exe,ocr-url-api-service.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\ocr-url-api\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\OCR URL API"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall OCR URL API"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#ServiceInstallScript}"; Flags: runhidden waituntilterminated; StatusMsg: "Installing and starting OCR URL API service..."

[UninstallRun]
Filename: "{app}\{#ServiceUninstallScript}"; Flags: runhidden waituntilterminated skipifdoesntexist

[Code]
procedure ExecIfExists(const FileName: string; const Params: string);
var
	ResultCode: Integer;
begin
	if FileExists(FileName) then
		Exec(FileName, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure ExecSystemIfExists(const FileName: string; const Params: string);
var
	ResultCode: Integer;
begin
	if FileExists(FileName) then
		Exec(FileName, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure StopExistingService();
var
	AppDir: string;
	ServiceExe: string;
begin
	AppDir := ExpandConstant('{app}');
	ServiceExe := AppDir + '\ocr-url-api-service.exe';

	ExecIfExists(ServiceExe, 'stop');
	ExecIfExists(ServiceExe, 'uninstall');
	ExecSystemIfExists(ExpandConstant('{sys}\sc.exe'), 'stop ocr-url-api');
	ExecSystemIfExists(ExpandConstant('{sys}\sc.exe'), 'delete ocr-url-api');
	ExecSystemIfExists(ExpandConstant('{sys}\taskkill.exe'), '/F /IM ocr-url-api-service.exe /T');
	ExecSystemIfExists(ExpandConstant('{sys}\taskkill.exe'), '/F /IM ocr-url-api.exe /T');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
	if CurStep = ssInstall then
		StopExistingService();
end;
