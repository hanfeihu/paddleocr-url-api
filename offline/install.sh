#!/bin/bash
set -euo pipefail

# Offline installer for macOS Apple Silicon (arm64)
# Installs service to /usr/local/paddleocr-url-api-offline and registers a LaunchDaemon.

INSTALL_DIR="/usr/local/paddleocr-url-api-offline"
DAEMON_PLIST="/Library/LaunchDaemons/com.paddleocr.urlapi.offline.plist"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(uname -m)" != "arm64" ]; then
  echo "This offline bundle is for macOS arm64 only." 1>&2
  exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo bash $ROOT_DIR/install.sh" 1>&2
  exit 1
fi

mkdir -p "$INSTALL_DIR"

echo "Copying payload..."
rsync -a --delete "$ROOT_DIR/payload/" "$INSTALL_DIR/"

echo "Installing LaunchDaemon..."
cp "$ROOT_DIR/com.paddleocr.urlapi.offline.plist" "$DAEMON_PLIST"
chmod 644 "$DAEMON_PLIST"

echo "Unloading previous daemon (if any)..."
launchctl unload "$DAEMON_PLIST" >/dev/null 2>&1 || true

echo "Loading daemon..."
launchctl load "$DAEMON_PLIST"
launchctl start com.paddleocr.urlapi.offline || true

echo "Done. Verify with: curl -s http://127.0.0.1:8000/health"
