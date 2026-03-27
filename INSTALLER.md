macOS Installer Package

This package installs the online macOS service variant. It is intended for Macs that can install Python dependencies during postinstall.

What the installer does
- Installs the service into:
  /usr/local/paddleocr-url-api
- Copies:
  - app.py
  - requirements.txt
  - run_server.sh
- Creates a Python venv under that directory
- Installs Python dependencies from requirements.txt into the venv
- Installs a LaunchDaemon:
  /Library/LaunchDaemons/com.paddleocr.urlapi.plist
- Starts the daemon so the API is available on port 8000

Build the .pkg (on a Mac)
  cd ocr_paddle_service
  bash installer/build_pkg.sh

Output
  dist/paddleocr-url-api-1.0.9.pkg

Install
  sudo installer -pkg dist/paddleocr-url-api-1.0.9.pkg -target /

Verify
  curl -s http://127.0.0.1:8000/health
  curl -s http://127.0.0.1:8000/ocr \
    -H 'content-type: application/json' \
    -d '{"urls":["https://img.kwcdn.com/product/open/dad15c72918141d0aedcc65ea45832fa-goods.jpeg"]}'

Logs
  /var/log/paddleocr-url-api.out.log
  /var/log/paddleocr-url-api.err.log

Uninstall
  See UNINSTALL.md for the full removal steps.

Notes
- The postinstall script prefers Homebrew Python 3.11 at /opt/homebrew/bin/python3.11.
  If it is not available, it falls back to /usr/bin/python3.
- This package installs dependencies during postinstall, so internet access may still be required.
- If you need a no-network install, use the offline bundle under offline/.
