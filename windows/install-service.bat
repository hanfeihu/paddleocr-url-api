@echo off
setlocal

set DIR=%~dp0
set SERVICE_EXE=%DIR%ocr-url-api-service.exe
set SERVICE_ID=ocr-url-api

if not exist "%SERVICE_EXE%" (
  echo Missing WinSW service wrapper: %SERVICE_EXE%
  exit /b 1
)

if not exist "%DIR%ocr-url-api-service.xml" (
  echo Missing WinSW service config: %DIR%ocr-url-api-service.xml
  exit /b 1
)

rem Make reinstall/upgrade idempotent.
"%SERVICE_EXE%" stop >nul 2>&1
"%SERVICE_EXE%" uninstall >nul 2>&1
sc stop "%SERVICE_ID%" >nul 2>&1
sc delete "%SERVICE_ID%" >nul 2>&1
taskkill /F /IM ocr-url-api-service.exe /T >nul 2>&1
taskkill /F /IM ocr-url-api.exe /T >nul 2>&1

"%SERVICE_EXE%" install
if errorlevel 1 exit /b %errorlevel%

"%SERVICE_EXE%" start
if errorlevel 1 exit /b %errorlevel%

echo Installed and started service.
echo Health: curl http://127.0.0.1:8000/health

endlocal
