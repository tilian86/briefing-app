#!/bin/zsh
set -eu

LABEL="local.florian.briefing-app"
UID_NUM="$(id -u)"

launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true

echo "Briefing-App auf Port 8501 wurde gestoppt."
echo ""
read -k 1 "?Taste drücken zum Schließen..."
