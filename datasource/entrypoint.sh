#!/usr/bin/env bash
# Datasource entrypoint: start postgres in the background (official image
# entrypoint handles init + /docker-entrypoint-initdb.d/*.sql on first boot),
# wait until it accepts connections, then run the service command (uvicorn).
set -euo pipefail

docker-entrypoint.sh postgres -c shared_buffers=1GB -c work_mem=64MB &
PG_PID=$!

ready=0
for i in $(seq 1 120); do
  if pg_isready -U dcs -d dcs >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" != 1 ]; then
  echo "[anaviz-datasource] postgres did not become ready" >&2
  exit 1
fi
echo "[anaviz-datasource] postgres ready"

"$@" &
APP_PID=$!

shutdown() {
  kill -TERM "$APP_PID" 2>/dev/null || true
  kill -TERM "$PG_PID" 2>/dev/null || true
  wait "$APP_PID" 2>/dev/null || true
  wait "$PG_PID" 2>/dev/null || true
}
trap shutdown TERM INT EXIT

wait -n "$APP_PID" "$PG_PID"
CODE=$?
shutdown
exit "$CODE"
