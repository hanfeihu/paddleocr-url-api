#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

IDENTIFIER="com.paddleocr.urlapi.offline"
VERSION="1.0.10"
INSTALL_DIR="/usr/local/paddleocr-url-api-offline"

DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$ROOT_DIR/.offline_pkgbuild"
PKG_PATH="$DIST_DIR/paddleocr-url-api-offline-$VERSION.pkg"

rm -rf "$BUILD_DIR"
mkdir -p "$DIST_DIR" "$BUILD_DIR/root$INSTALL_DIR" "$BUILD_DIR/root/Library/LaunchDaemons" "$BUILD_DIR/scripts"

# Keep the service code in the offline payload in sync with the main app.
cp "$ROOT_DIR/../app.py" "$ROOT_DIR/payload/app.py"

# Payload (prebuilt embedded python + site-packages + wheels + models)
rsync -a --delete "$ROOT_DIR/payload/" "$BUILD_DIR/root$INSTALL_DIR/"

# LaunchDaemon
cp "$ROOT_DIR/com.paddleocr.urlapi.offline.plist" "$BUILD_DIR/root/Library/LaunchDaemons/com.paddleocr.urlapi.offline.plist"

# Postinstall: just load daemon (no network installs)
cat > "$BUILD_DIR/scripts/postinstall" <<'POST'
#!/bin/bash
set -euo pipefail

PLIST="/Library/LaunchDaemons/com.paddleocr.urlapi.offline.plist"
launchctl unload "$PLIST" >/dev/null 2>&1 || true
launchctl load "$PLIST"
launchctl start com.paddleocr.urlapi.offline || true
exit 0
POST

chmod +x "$BUILD_DIR/scripts/postinstall"
chmod +x "$BUILD_DIR/root$INSTALL_DIR/run_server.sh"

pkgbuild \
  --root "$BUILD_DIR/root" \
  --scripts "$BUILD_DIR/scripts" \
  --identifier "$IDENTIFIER" \
  --version "$VERSION" \
  "$PKG_PATH"

echo "Built: $PKG_PATH"
