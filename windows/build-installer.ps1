Param(
  [Parameter(Mandatory=$false)]
  [string]$InstallerScript = "windows\installer.iss"
)

$ErrorActionPreference = "Stop"

if (!(Test-Path "dist\ocr-url-api\ocr-url-api.exe")) {
  throw "Missing staged Windows payload at dist\\ocr-url-api\\ocr-url-api.exe"
}

$command = Get-Command ISCC.exe -ErrorAction SilentlyContinue

$possiblePaths = @(
  if ($command) { $command.Source },
  "$env:ChocolateyInstall\lib\innosetup\tools\ISCC.exe",
  "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ }

$iscc = $possiblePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
  throw "Inno Setup compiler (ISCC.exe) not found."
}

& $iscc $InstallerScript

if ($LASTEXITCODE -ne 0) {
  throw "Inno Setup compilation failed with exit code $LASTEXITCODE"
}

Write-Host "Built Windows installer using $InstallerScript"
