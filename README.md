OCR Paddle Service (URL Batch)

What it does
- Exposes a sync HTTP API that takes up to 50 public image URLs.
- Downloads images concurrently, runs PaddleOCR on CPU in parallel via a process pool.
- Returns only the original URL and the recognized text (joined by newlines).

Packaging and distribution
- macOS online installer package: `INSTALLER.md`
- macOS offline bundle/package: `offline/README_OFFLINE.md`
- Windows x64 EXE + service packaging: `windows/BUILD_WINDOWS.md`

Platform matrix

| Platform | Format | Build path | Install path | Notes |
|---|---|---|---|---|
| macOS (online) | `.pkg` | `bash installer/build_pkg.sh` | `sudo installer -pkg dist/paddleocr-url-api-1.0.8.pkg -target /` | Creates venv and installs deps during postinstall |
| macOS (offline) | `.pkg` + offline bundle | `bash offline/build_offline_pkg.sh` | `sudo bash offline/install.sh` | Apple Silicon only; includes Python, wheels, and models |
| Windows x64 | `ocr-url-api.zip` | local PyInstaller or GitHub Actions | Run `install-service.bat` as Administrator | Final zip contains EXE + WinSW service files |

GitHub Actions packaging
- Windows workflow: `.github/workflows/windows-build.yml`
- Supports manual trigger (`workflow_dispatch`)
- Uploads artifact: `ocr-url-api-windows-x64`
- The artifact contains `dist/ocr-url-api.zip`

Windows package usage
1. Download the `ocr-url-api-windows-x64` artifact from GitHub Actions.
2. Extract `ocr-url-api.zip`.
3. Open **Command Prompt as Administrator**.
4. `cd` into the extracted folder that contains `ocr-url-api.exe`.
5. Run `install-service.bat`.
6. Verify with `curl http://127.0.0.1:8000/health`.
7. Remove with `uninstall-service.bat`.

API
- GET /health
- POST /ocr
  - Request JSON: {"urls": ["https://...", ...]}
  - Response JSON: {"results": [{"url": "...", "text": "..."} | {"url": "...", "error": "..."}]}
  - If no text is found, "text" is an empty string.
  - Max URLs per request: 50 (OCR_MAX_URLS)

Setup (macOS Apple Silicon)
1) Install Python 3.11:
   brew install python@3.11

2) Create venv and install dependencies:
   /opt/homebrew/bin/python3.11 -m venv .venv
   source .venv/bin/activate
   pip install -U pip
   pip install -r requirements.txt

Run
  source .venv/bin/activate
  export OCR_WORKERS=6
  export OMP_NUM_THREADS=1
  export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
  uvicorn app:app --host 0.0.0.0 --port 8000

Run at login (LaunchAgent)
  The repo includes a ready plist:
  /Users/a1/Library/LaunchAgents/com.a1.paddleocr-url-api.plist

  Load / start:
    launchctl load /Users/a1/Library/LaunchAgents/com.a1.paddleocr-url-api.plist
    launchctl start com.a1.paddleocr-url-api

  Stop / unload:
    launchctl stop com.a1.paddleocr-url-api
    launchctl unload /Users/a1/Library/LaunchAgents/com.a1.paddleocr-url-api.plist

  Logs:
    /Users/a1/Library/Logs/paddleocr-url-api.out.log
    /Users/a1/Library/Logs/paddleocr-url-api.err.log

Tune (env vars)
- OCR_MAX_URLS (default 50)
- OCR_MAX_BYTES (default 15MB)
- OCR_DOWNLOAD_CONCURRENCY (default 16)
- OCR_WORKERS (default 6)
- OCR_SIZE_GATE (default 1200)
- OCR_CONNECT_TIMEOUT (default 5)
- OCR_READ_TIMEOUT (default 15)

Test
  curl -s http://127.0.0.1:8000/health
  curl -s http://127.0.0.1:8000/ocr \
    -H 'content-type: application/json' \
    -d '{"urls":["https://img.kwcdn.com/product/open/dad15c72918141d0aedcc65ea45832fa-goods.jpeg"]}'

Uninstall
- English: `UNINSTALL.md`
- 中文: `UNINSTALL.zh-CN.md`
