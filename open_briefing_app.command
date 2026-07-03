#!/bin/zsh
set -eu

URL="http://localhost:8501"

if command -v open >/dev/null 2>&1; then
  open "$URL"
fi

echo "Briefing-App geöffnet:"
echo "$URL"
echo ""
read -k 1 "?Taste drücken zum Schließen..."
