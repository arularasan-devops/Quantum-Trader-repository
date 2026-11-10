#!/usr/bin/env bash
# Restore the journal DB from ./backups (default: newest). Usage: scripts/restore_db.sh [file.dump]
set -euo pipefail
cd "$(dirname "$0")/.."
f="${1:-backups/quantum_latest.dump}"
[ -f "$f" ] || { echo "no backup at $f"; exit 1; }
docker compose stop backend
docker compose exec -T postgres pg_restore -U quantum -d quantum --clean --if-exists --no-owner < "$f"
docker compose start backend
echo "restored from $f"
