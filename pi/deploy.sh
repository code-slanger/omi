#!/usr/bin/env bash
# Deploy the Pi service to Raspberry Pi via Docker context.
# Run from: pi/
# Usage:
#   ./deploy.sh
#   PI_HOST=mitch@192.168.0.27 ./deploy.sh

set -euo pipefail

CONTEXT_NAME="lucho"
PI_HOST="${PI_HOST:-lucho@192.168.0.27}"
ENV_FILE="service/.env"

# ── 1. Create Docker context if it doesn't exist ──────────────────────────

if ! docker context inspect "$CONTEXT_NAME" &>/dev/null; then
  echo "==> Creating Docker context '$CONTEXT_NAME'..."
  docker context create "$CONTEXT_NAME" --docker "host=ssh://$PI_HOST"
fi

# ── 2. Ensure Pi directories exist ───────────────────────────────────────

echo "==> Creating data directories on Pi..."
ssh "$PI_HOST" "mkdir -p ~/omi/data ~/vault ~/pi"

# ── 3. Copy config files to Pi ───────────────────────────────────────────

if [[ -f "$ENV_FILE" ]]; then
  echo "==> Copying .env to Pi..."
  ssh "$PI_HOST" "mkdir -p ~/omi/service"
  scp "$ENV_FILE" "$PI_HOST:~/omi/service/.env"
else
  echo "WARNING: $ENV_FILE not found. Create it from service/.env.example before deploying."
  exit 1
fi


# ── 4. Build and start ────────────────────────────────────────────────────

echo "==> Building and starting service on $PI_HOST..."
docker --context "$CONTEXT_NAME" compose up -d --build

echo ""
echo "==> Done."
echo "    nano-claw:  http://192.168.0.27:8000  (health: curl http://192.168.0.27:8000/health)"
echo "    corpus-ui:  http://192.168.0.27:8001"
echo ""
echo "    Logs:    docker --context $CONTEXT_NAME compose logs -f nano-claw"
echo "             docker --context $CONTEXT_NAME compose logs -f corpus-ui"
