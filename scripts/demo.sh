#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${BASH_VERSION:-}" ]]; then
  exec bash "$0" "$@"
fi

USER_PORT="${USER_SERVICE_HOST_PORT:-15000}"
NOTIFICATION_PORT="${NOTIFICATION_SERVICE_HOST_PORT:-15001}"
ORDER_PORT="${ORDER_SERVICE_HOST_PORT:-15002}"

USER_BASE="http://localhost:${USER_PORT}"
NOTIFICATION_BASE="http://localhost:${NOTIFICATION_PORT}"
ORDER_BASE="http://localhost:${ORDER_PORT}"

echo "1) Health checks"
curl -sS "${USER_BASE}/health" | jq .
curl -sS "${NOTIFICATION_BASE}/health" | jq .
curl -sS "${ORDER_BASE}/health" | jq .

echo
echo "2) Create user"
EMAIL="alice+$(date +%s)-${RANDOM}@example.com"
USER_ID="$(curl -sS -X POST "${USER_BASE}/users" \
  -H 'Content-Type: application/json' \
  -d "{\"name\":\"Alice\",\"email\":\"${EMAIL}\"}" | jq -er '.id')"
echo "Created user id=$USER_ID"

echo
echo "3) Create order (triggers user-service + notification-service)"
curl -sS -X POST "${ORDER_BASE}/orders" \
  -H 'Content-Type: application/json' \
  -d "{\"user_id\":$USER_ID,\"item\":\"Book\",\"amount\":2}" | jq .

echo
echo "4) List notifications"
curl -sS "${NOTIFICATION_BASE}/notifications" | jq .

echo
echo "Done."
