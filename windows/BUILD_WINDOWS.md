Windows Build (EXE + Service)

Goal
- Build a Windows x64 distribution that runs the same API as macOS.
- Provide a Windows Service that starts on boot.

What you get
- dist/ocr-url-api/ocr-url-api.exe
- dist/ocr-url-api/models/ (optional, for fully offline model cache)
- dist/ocr-url-api/install-service.bat
- dist/ocr-url-api/uninstall-service.bat
- dist/ocr-url-api/ocr-url-api-service.exe
- dist/ocr-url-api/ocr-url-api-service.xml
- dist/ocr-url-api.zip (GitHub Actions artifact)

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
  Then collect the service wrapper + scripts into the dist root:
    powershell -ExecutionPolicy Bypass -File windows\collect-dist.ps1

5) Install as a Windows Service (run cmd as Administrator)
  cd dist\ocr-url-api
  install-service.bat

What install-service.bat expects
- ocr-url-api-service.exe
- ocr-url-api-service.xml

The service itself also requires `ocr-url-api.exe` to be present because the WinSW xml points to `%BASE%\ocr-url-api.exe`.

The service wrapper files must be in the same folder as ocr-url-api.exe.

Uninstall
  uninstall-service.bat

Verify
  curl http://127.0.0.1:8000/health
  curl -H "content-type: application/json" -d "{\"urls\":[\"https://...\"]}" http://127.0.0.1:8000/ocr

GitHub Actions
- Workflow: `.github/workflows/windows-build.yml`
- Trigger manually with `workflow_dispatch`, or by pushing packaging/code changes to `main`
- Artifact name: `ocr-url-api-windows-x64`
- Uploaded file: `dist/ocr-url-api.zip`

How to use the GitHub Actions artifact
1. Download `ocr-url-api-windows-x64`
2. Extract `ocr-url-api.zip`
3. Open **Command Prompt as Administrator**
4. `cd` into the extracted folder that contains `ocr-url-api.exe`
5. Run `install-service.bat`
6. Verify the service with `curl http://127.0.0.1:8000/health`

Notes
- The service uses embedded env vars from the WinSW xml.
- Keep uvicorn workers=1; the app uses a process pool for OCR parallelism.
- This distribution is Windows x64 only.
