#!/bin/zsh
set -eu

APP_DIR="/Users/florian/Library/Application Support/Projects/Briefing-App"
STREAMLIT_BIN="/Users/florian/Library/Python/3.9/bin/streamlit"
PORT="8501"

export PATH="/Users/florian/Library/Python/3.9/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export BRIEFING_INSTANCE_LABEL="Briefing-App"
export BRIEFING_INSTANCE_PORT="$PORT"
export BRIEFING_INSTANCE_LAUNCHD_LABEL="local.florian.briefing-app"

cd "$APP_DIR"

if [ -f "$APP_DIR/.env" ]; then
  set -a
  . "$APP_DIR/.env"
  set +a
fi

exec "$STREAMLIT_BIN" run "$APP_DIR/briefing_app.py" \
  --server.address 0.0.0.0 \
  --server.port "$PORT" \
  --server.headless true \
  --browser.gatherUsageStats false
