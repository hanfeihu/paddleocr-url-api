OCR Paddle Service (URL Batch)

What it does
- Exposes a sync HTTP API that takes up to 50 public image URLs.
- Downloads images concurrently, runs PaddleOCR on CPU in parallel via a process pool.
- Returns only the original URL and the recognized text (joined by newlines).

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
