#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="/usr/local/paddleocr-url-api"
IDENTIFIER="com.paddleocr.urlapi"
VERSION="1.0.18"

DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$ROOT_DIR/.pkgbuild"
PKG_PATH="$DIST_DIR/paddleocr-url-api-$VERSION.pkg"

rm -rf "$BUILD_DIR"
mkdir -p "$DIST_DIR" "$BUILD_DIR/root$INSTALL_DIR" "$BUILD_DIR/root/Library/LaunchDaemons" "$BUILD_DIR/scripts"

cp "$ROOT_DIR/app.py" "$ROOT_DIR/offline/payload/app.py"
rsync -a --delete "$ROOT_DIR/offline/payload/" "$BUILD_DIR/root$INSTALL_DIR/"

# LaunchDaemon
cp "$ROOT_DIR/installer/com.paddleocr.urlapi.plist" "$BUILD_DIR/root/Library/LaunchDaemons/com.paddleocr.urlapi.plist"

# Installer scripts
cp "$ROOT_DIR/installer/postinstall" "$BUILD_DIR/scripts/postinstall"
chmod +x "$BUILD_DIR/scripts/postinstall"

chmod +x "$BUILD_DIR/root$INSTALL_DIR/run_server.sh"

pkgbuild \
  --root "$BUILD_DIR/root" \
  --scripts "$BUILD_DIR/scripts" \
  --identifier "$IDENTIFIER" \
  --version "$VERSION" \
  "$PKG_PATH"

echo "Built: $PKG_PATH"
