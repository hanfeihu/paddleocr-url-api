Offline Bundle (No Internet Install)

This bundle targets macOS Apple Silicon (arm64).

What is included
- Embedded Python 3.11 (standalone build)
- All required wheels (pip --no-index)
- PaddleX official OCR models (cached) under models/
- LaunchDaemon that runs the API on port 8000

Build the offline package
  bash build_offline_pkg.sh

Expected output
  dist/paddleocr-url-api-offline-1.0.7.pkg

Install (offline)
  sudo bash install.sh

Verify
  curl -s http://127.0.0.1:8000/health
  curl -s http://127.0.0.1:8000/ocr \
    -H 'content-type: application/json' \
    -d '{"urls":["https://img.kwcdn.com/product/open/bf5ad965f8db4613be68860fe81e6d36-goods.jpeg"]}'

Logs
  /var/log/paddleocr-url-api.offline.out.log
  /var/log/paddleocr-url-api.offline.err.log

Uninstall
  sudo launchctl unload /Library/LaunchDaemons/com.paddleocr.urlapi.offline.plist
  sudo rm -f /Library/LaunchDaemons/com.paddleocr.urlapi.offline.plist
  sudo rm -rf /usr/local/paddleocr-url-api-offline
