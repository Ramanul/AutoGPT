#!/usr/bin/env bash
set -eo pipefail

LOG_FILE="/tmp/autogpt-dashboard-launcher.log"
exec >>"$LOG_FILE" 2>&1

echo "==== launcher start $(date -Iseconds) ===="

cd /workspaces/AutoGPT/classic/agent_ui

# Start dashboard backend if not already running on 8765
if ! curl -fsS http://127.0.0.1:8765/health >/dev/null 2>&1; then
  nohup python3 server.py >/tmp/autogpt-dashboard.log 2>&1 &

  # Wait until dashboard is ready
  for _ in {1..30}; do
    if curl -fsS http://127.0.0.1:8765/health >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

# Start official AutoGPT UI through dashboard API (best effort)
curl -fsS -X POST http://127.0.0.1:8765/api/autogpt/start \
  -H 'Content-Type: application/json' \
  -d '{}' >/dev/null 2>&1 || true

# Build target URL.
if [[ -n "${CODESPACE_NAME:-}" && -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]]; then
  TARGET_URL="https://${CODESPACE_NAME}-8765.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
else
  TARGET_URL="http://localhost:8765"
fi

echo "Target URL: $TARGET_URL"

# Open browser with best available mechanism.
open_with_cmd() {
  local cmd="$1"
  shift
  if command -v "$cmd" >/dev/null 2>&1; then
    "$cmd" "$@" "$TARGET_URL" && return 0
  fi
  return 1
}

# Prefer environment-provided opener first (Codespaces), then Chrome family, then generic openers.
if [[ -n "${BROWSER:-}" ]] && open_with_cmd "$BROWSER"; then
  exit 0
fi

if open_with_cmd google-chrome-stable || \
  open_with_cmd google-chrome || \
  open_with_cmd chromium-browser || \
  open_with_cmd chromium || \
  open_with_cmd xdg-open || \
  open_with_cmd gio open; then
  exit 0
fi

echo "No browser opener found. Open manually: $TARGET_URL"
exit 0
