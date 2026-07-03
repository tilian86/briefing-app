#!/bin/zsh
set -eu

LABEL="local.florian.briefing-app"
UID_NUM="$(id -u)"
URL="http://127.0.0.1:8501"

echo "Briefing-App Status"
echo "==================="
echo ""
echo "LaunchAgent:"
launchctl print "gui/$UID_NUM/$LABEL" 2>/dev/null | sed -n '1,40p' || echo "Nicht geladen."
echo ""
echo "HTTP-Check:"
curl -I --max-time 2 "$URL" 2>&1 | sed -n '1,10p' || true
echo ""
echo "URL:"
echo "$URL"
echo ""
read -k 1 "?Taste drücken zum Schließen..."
