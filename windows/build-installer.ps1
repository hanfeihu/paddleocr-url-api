Param(
  [Parameter(Mandatory=$false)]
  [string]$InstallerScript = "windows\installer.iss"
)

$ErrorActionPreference = "Stop"

if (!(Test-Path "dist\ocr-url-api\ocr-url-api.exe")) {
  throw "Missing staged Windows payload at dist\\ocr-url-api\\ocr-url-api.exe"
}

$possiblePaths = @(
  "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)

$iscc = $possiblePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
  throw "Inno Setup compiler (ISCC.exe) not found."
}

& $iscc $InstallerScript

Write-Host "Built Windows installer using $InstallerScript"
