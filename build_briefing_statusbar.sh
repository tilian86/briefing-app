#!/bin/zsh
set -eu

APP_NAME="Briefing Status"
APP_DIR="/Users/florian/Applications/Audio-Briefing/${APP_NAME}.app"
SRC="/Users/florian/Library/Application Support/Projects/Briefing-App/StatusBar/BriefingStatusBar.m"
BIN_DIR="${APP_DIR}/Contents/MacOS"
RES_DIR="${APP_DIR}/Contents/Resources"
BIN_PATH="${BIN_DIR}/BriefingStatusBar"
PLIST_PATH="${APP_DIR}/Contents/Info.plist"

mkdir -p "$BIN_DIR" "$RES_DIR"

/usr/bin/clang -fobjc-arc -framework Cocoa "$SRC" -o "$BIN_PATH"
chmod +x "$BIN_PATH"

cat > "$PLIST_PATH" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>de</string>
    <key>CFBundleExecutable</key>
    <string>BriefingStatusBar</string>
    <key>CFBundleIdentifier</key>
    <string>local.florian.briefing-statusbar</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>Briefing Status</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST
