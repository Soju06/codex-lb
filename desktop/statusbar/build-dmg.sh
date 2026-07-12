#!/usr/bin/env bash
set -euo pipefail

APP_NAME="CodexLBStatusBar"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATUSBAR_DIR="${ROOT_DIR}"
BUILD_DIR="${STATUSBAR_DIR}/build"
DIST_DIR="${STATUSBAR_DIR}/dist"
APP_DIR="${BUILD_DIR}/${APP_NAME}.app"
CONTENTS_DIR="${APP_DIR}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
DMG_ROOT="${BUILD_DIR}/dmg-root"
DMG_PATH="${DIST_DIR}/${APP_NAME}.dmg"

rm -rf "${BUILD_DIR}" "${DIST_DIR}"
mkdir -p "${MACOS_DIR}" "${DIST_DIR}"

xcrun swiftc \
  -O \
  -parse-as-library \
  -o "${MACOS_DIR}/${APP_NAME}" \
  "${STATUSBAR_DIR}/StatusBarLogic.swift" \
  "${STATUSBAR_DIR}/CodexLBStatusBar.swift" \
  -framework Cocoa \
  -framework Foundation \
  -framework ServiceManagement

cat > "${CONTENTS_DIR}/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>CodexLBStatusBar</string>
  <key>CFBundleIdentifier</key>
  <string>local.codex-lb.statusbar</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>Codex LB Status</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.1.0</string>
  <key>CFBundleVersion</key>
  <string>2</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

codesign --force --deep --sign - "${APP_DIR}" >/dev/null
mkdir -p "${DMG_ROOT}"
cp -R "${APP_DIR}" "${DMG_ROOT}/"
ln -s /Applications "${DMG_ROOT}/Applications"

hdiutil create \
  -volname "Codex LB Status" \
  -srcfolder "${DMG_ROOT}" \
  -ov \
  -format UDZO \
  "${DMG_PATH}" >/dev/null

echo "${DMG_PATH}"
