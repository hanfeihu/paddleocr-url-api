@echo off
setlocal

set DIR=%~dp0

if not exist "%DIR%winsw\ocr-url-api-service.exe" (
  echo Missing WinSW service wrapper: %DIR%winsw\ocr-url-api-service.exe
  echo See windows\winsw\README.txt
  exit /b 1
)

copy /Y "%DIR%winsw\ocr-url-api-service.exe" "%DIR%ocr-url-api-service.exe" >nul
copy /Y "%DIR%winsw\ocr-url-api.xml" "%DIR%ocr-url-api-service.xml" >nul

"%DIR%ocr-url-api-service.exe" install
"%DIR%ocr-url-api-service.exe" start

echo Installed and started service.
echo Health: curl http://127.0.0.1:8000/health

endlocal
