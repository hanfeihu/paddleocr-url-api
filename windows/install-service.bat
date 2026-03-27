@echo off
setlocal

set DIR=%~dp0

if not exist "%DIR%ocr-url-api-service.exe" (
  echo Missing WinSW service wrapper: %DIR%ocr-url-api-service.exe
  exit /b 1
)

if not exist "%DIR%ocr-url-api-service.xml" (
  echo Missing WinSW service config: %DIR%ocr-url-api-service.xml
  exit /b 1
)

"%DIR%ocr-url-api-service.exe" install
"%DIR%ocr-url-api-service.exe" start

echo Installed and started service.
echo Health: curl http://127.0.0.1:8000/health

endlocal
