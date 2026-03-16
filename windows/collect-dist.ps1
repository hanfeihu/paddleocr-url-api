Param(
  [Parameter(Mandatory=$false)]
  [string]$DistDir = "dist\\ocr-url-api"
)

$ErrorActionPreference = "Stop"

if (!(Test-Path $DistDir)) {
  throw "Dist directory not found: $DistDir"
}

New-Item -ItemType Directory -Force -Path "$DistDir\\winsw" | Out-Null

Copy-Item -Force "windows\\install-service.bat" "$DistDir\\install-service.bat"
Copy-Item -Force "windows\\uninstall-service.bat" "$DistDir\\uninstall-service.bat"

if (!(Test-Path "windows\\winsw\\ocr-url-api-service.exe")) {
  Write-Host "Missing windows\\winsw\\ocr-url-api-service.exe (WinSW wrapper)."
  Write-Host "Download WinSW x64 and place it there."
  exit 1
}

Copy-Item -Force "windows\\winsw\\ocr-url-api-service.exe" "$DistDir\\ocr-url-api-service.exe"
Copy-Item -Force "windows\\winsw\\ocr-url-api.xml" "$DistDir\\ocr-url-api-service.xml"

Write-Host "Copied service scripts + WinSW wrapper into $DistDir"
