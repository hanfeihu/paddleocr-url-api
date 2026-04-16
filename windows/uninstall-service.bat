@echo off
setlocal

set DIR=%~dp0
set SERVICE_EXE=%DIR%ocr-url-api-service.exe
set SERVICE_ID=ocr-url-api

if not exist "%SERVICE_EXE%" (
  echo Service wrapper not found: %SERVICE_EXE%
  exit /b 1
)

"%SERVICE_EXE%" stop >nul 2>&1
"%SERVICE_EXE%" uninstall >nul 2>&1
sc stop "%SERVICE_ID%" >nul 2>&1
sc delete "%SERVICE_ID%" >nul 2>&1
taskkill /F /IM ocr-url-api-service.exe /T >nul 2>&1
taskkill /F /IM ocr-url-api.exe /T >nul 2>&1

echo Uninstalled service.

endlocal
