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
| macOS (bundled) | `.pkg` | `bash installer/build_pkg.sh` | `sudo installer -pkg dist/paddleocr-url-api-1.0.14.pkg -target /` | Bundles Python, wheels, models, and runs without install-time pip |
| macOS (offline alt) | `.pkg` + offline bundle | `bash offline/build_offline_pkg.sh` | `sudo bash offline/install.sh` | Apple Silicon only; same full payload under the offline install path |
| Windows x64 | `.exe` installer | GitHub Actions or local Windows build | Run the installer as Administrator | Installs files and registers the WinSW-backed service |

GitHub Actions packaging
- macOS workflow: `.github/workflows/macos-build.yml`
- Windows workflow: `.github/workflows/windows-build.yml`
- Both support manual trigger (`workflow_dispatch`)
- Release target assets:
  - `paddleocr-url-api-1.0.14.pkg`
  - `ocr-url-api-setup-1.0.14.exe`

Windows package usage
1. Download `ocr-url-api-setup-1.0.14.exe` from GitHub Releases or the Windows installer artifact.
2. Run the installer as Administrator.
3. Let the installer copy files and register the Windows service.
4. Verify with `curl http://127.0.0.1:8000/health`.
5. Uninstall from Windows Apps & Features or the generated uninstaller.

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
