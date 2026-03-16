macOS Installer Package

This project can be installed as a .pkg so other Macs can get the same always-on OCR service.

What the installer does
- Installs the service into:
  /usr/local/paddleocr-url-api
- Creates a Python venv under that directory.
- Installs Python deps from requirements.txt into the venv.
- Installs a LaunchDaemon:
  /Library/LaunchDaemons/com.paddleocr.urlapi.plist
- Starts the daemon so the API is available on port 8000.

Build the .pkg (on a Mac)
  cd ocr_paddle_service
  bash installer/build_pkg.sh

Output
  dist/paddleocr-url-api-1.0.0.pkg

Install
  sudo installer -pkg dist/paddleocr-url-api-1.0.0.pkg -target /

Verify
  curl -s http://127.0.0.1:8000/health

Logs
  /var/log/paddleocr-url-api.out.log
  /var/log/paddleocr-url-api.err.log

Uninstall (manual)
- Stop/unload daemon:
  sudo launchctl unload /Library/LaunchDaemons/com.paddleocr.urlapi.plist
- Remove files:
  sudo rm -f /Library/LaunchDaemons/com.paddleocr.urlapi.plist
  sudo rm -rf /usr/local/paddleocr-url-api

Notes
- The postinstall script prefers Homebrew Python 3.11 at /opt/homebrew/bin/python3.11.
  If it is not available, it falls back to /usr/bin/python3.
- PaddleOCR models are downloaded and cached under the installing user's home (PaddleX cache).
  First startup can be slow while models download.
