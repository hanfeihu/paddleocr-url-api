@echo off
setlocal

set DIR=%~dp0

if not exist "%DIR%ocr-url-api-service.exe" (
  echo Service wrapper not found: %DIR%ocr-url-api-service.exe
  exit /b 1
)

"%DIR%ocr-url-api-service.exe" stop
"%DIR%ocr-url-api-service.exe" uninstall

echo Uninstalled service.

endlocal
