macOS Installer Package

This package installs the bundled macOS service variant. It includes the embedded runtime, bundled dependencies, and cached models.

What the installer does
- Installs the service into:
  /usr/local/paddleocr-url-api
- Copies the fully bundled runtime payload into that directory, including:
  - app.py
  - python/
  - wheels/
  - models/
  - run_server.sh
- Installs a LaunchDaemon:
  /Library/LaunchDaemons/com.paddleocr.urlapi.plist
- Starts the daemon so the API is available on port 8000

Build the .pkg (on a Mac)
  cd ocr_paddle_service
  bash installer/build_pkg.sh

Output
  dist/paddleocr-url-api-1.0.14.pkg

Install
  sudo installer -pkg dist/paddleocr-url-api-1.0.14.pkg -target /

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
- This package does not create a venv or call pip during installation.
- Models are bundled and PaddleX cache is pinned into the install directory.
- If you prefer the alternate offline install path, see offline/README_OFFLINE.md.
