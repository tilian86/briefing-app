#!/bin/zsh
set -eu

PLIST="/Users/florian/Library/LaunchAgents/local.florian.briefing-app.plist"
LABEL="local.florian.briefing-app"
UID_NUM="$(id -u)"

launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST"
launchctl kickstart -k "gui/$UID_NUM/$LABEL"

echo "Briefing-App gestartet bzw. neu gestartet."
echo "Lokal: http://localhost:8501"
echo ""
read -k 1 "?Taste drücken zum Schließen..."
