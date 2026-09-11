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
if [[ "$after_sha" == "$before_sha" ]]; then
  echo "Already up to date at v$before_version ($before_sha). Nothing to restart."
  exit 0
fi

"$REPO_DIR/.venv/bin/python" -m pip install -q -e "$REPO_DIR"
after_version="$("$REPO_DIR/.venv/bin/python" -c 'import wimb; print(wimb.__version__)')"
echo "Updated: v$before_version ($before_sha) -> v$after_version ($after_sha)"

echo "Restarting $SERVICE..."
sudo systemctl restart "$SERVICE"

echo "Waiting for health check at $HEALTH_URL..."
for _ in $(seq 1 "$HEALTH_RETRIES"); do
  if curl -sf "$HEALTH_URL" >/dev/null; then
    echo "Healthy: v$after_version ($after_sha) is live."
    exit 0
  fi
  sleep 1
done

echo "$SERVICE did not answer $HEALTH_URL within ${HEALTH_RETRIES}s." >&2
echo "Check: sudo systemctl status $SERVICE  /  journalctl -u $SERVICE -n 50" >&2
exit 1
