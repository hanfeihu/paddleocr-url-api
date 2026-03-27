WinSW

This folder expects WinSW.exe renamed to ocr-url-api-service.exe.

Download WinSW (x64):
  https://github.com/winsw/winsw/releases

Place:
  windows/winsw/ocr-url-api-service.exe
  windows/winsw/ocr-url-api.xml

Packaging note:
- The GitHub Actions workflow and windows/collect-dist.ps1 copy both files into the
  distribution root next to ocr-url-api.exe.
- The XML is copied and renamed in the final distribution as:
    ocr-url-api-service.xml
- The final zip should contain:
    ocr-url-api.exe
    ocr-url-api-service.exe
    ocr-url-api-service.xml
    install-service.bat
    uninstall-service.bat
