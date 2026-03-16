Windows Build (EXE + Service)

Goal
- Build a Windows x64 distribution that runs the same API as macOS.
- Provide a Windows Service that starts on boot.

What you get
- dist/ocr-url-api/ocr-url-api.exe
- models/ (optional, for fully offline model cache)
- install-service.bat / uninstall-service.bat
- ocr-url-api-service.exe + ocr-url-api-service.xml (WinSW wrapper)

Prereqs (on a Windows x64 machine)
- Python 3.11 x64
- Visual C++ Redistributable (usually already installed)

1) Create venv and install deps
  python -m venv .venv
  .venv\Scripts\activate
  pip install -U pip
  pip install -r requirements.txt
  pip install pyinstaller

2) Offline models (required)
  This build expects offline models to be bundled.
  Put PaddleX official models into:
    windows\models\official_models\...
  (This repo already includes the official_models folder copied from a working macOS cache.)

3) Build
  pyinstaller --noconfirm --clean windows\pyinstaller.spec

4) Add WinSW
  Download WinSW x64 exe and place it as:
    windows\winsw\ocr-url-api-service.exe
  Copy these into dist folder:
    copy windows\winsw\ocr-url-api-service.exe dist\ocr-url-api\
    copy windows\winsw\ocr-url-api.xml dist\ocr-url-api\ocr-url-api-service.xml

5) Install as a Windows Service (run cmd as Administrator)
  cd dist\ocr-url-api
  install-service.bat

Uninstall
  uninstall-service.bat

Verify
  curl http://127.0.0.1:8000/health
  curl -H "content-type: application/json" -d "{\"urls\":[\"https://...\"]}" http://127.0.0.1:8000/ocr

Notes
- The service uses embedded env vars from the WinSW xml.
- Keep uvicorn workers=1; the app uses a process pool for OCR parallelism.
- This distribution is Windows x64 only.
