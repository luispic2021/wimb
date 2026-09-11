#!/usr/bin/env bash
# Update the WIMB droplet deployment: pull main, reinstall, restart wimb.service,
# and confirm the app comes back healthy. Run over SSH as the service's own user
# (git pull and pip install need write access to /opt/wimb; only the restart
# needs sudo).
set -euo pipefail

REPO_DIR="/opt/wimb"
SERVICE="wimb.service"
HEALTH_URL="http://127.0.0.1:8000/health"
HEALTH_RETRIES=10
HEALTH_CURL_TIMEOUT=2
# Tracks the last commit that was actually installed, restarted, and confirmed
# healthy - not just the last commit pulled. Comparing against this (rather
# than "did git pull change anything") means a retry after a failed install or
# restart still reinstalls/restarts/health-checks instead of silently no-oping
# because the sha was already pulled on a prior, failed run.
STATE_FILE="$REPO_DIR/.wimb/deployed-sha"

cd "$REPO_DIR"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "Refusing to update: $REPO_DIR has uncommitted changes to tracked files." >&2
  git status --short --untracked-files=no >&2
  exit 1
fi

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" != "main" ]]; then
  echo "Refusing to update: expected branch 'main', found '$branch'." >&2
  exit 1
fi

before_version="$("$REPO_DIR/.venv/bin/python" -c 'import wimb; print(wimb.__version__)')"
before_sha="$(git rev-parse --short HEAD)"
echo "Current: v$before_version ($before_sha)"

git pull --ff-only origin main

after_sha="$(git rev-parse --short HEAD)"
deployed_sha="$(cat "$STATE_FILE" 2>/dev/null || true)"

if [[ "$after_sha" == "$deployed_sha" ]]; then
  echo "v$before_version ($after_sha) is already installed, restarted, and healthy. Nothing to do."
  exit 0
fi

"$REPO_DIR/.venv/bin/python" -m pip install -q -e "$REPO_DIR"
after_version="$("$REPO_DIR/.venv/bin/python" -c 'import wimb; print(wimb.__version__)')"
echo "Installed: v$before_version ($before_sha) -> v$after_version ($after_sha)"

echo "Restarting $SERVICE..."
sudo systemctl restart "$SERVICE"

echo "Waiting for health check at $HEALTH_URL..."
for _ in $(seq 1 "$HEALTH_RETRIES"); do
  if curl -sf --max-time "$HEALTH_CURL_TIMEOUT" "$HEALTH_URL" >/dev/null; then
    mkdir -p "$(dirname "$STATE_FILE")"
    echo "$after_sha" > "$STATE_FILE"
    echo "Healthy: v$after_version ($after_sha) is live."
    exit 0
  fi
  sleep 1
done

echo "$SERVICE did not answer $HEALTH_URL within ${HEALTH_RETRIES}s." >&2
echo "Check: sudo systemctl status $SERVICE  /  journalctl -u $SERVICE -n 50" >&2
echo "Not recording $after_sha as deployed; re-run once healthy to confirm and record it." >&2
exit 1
