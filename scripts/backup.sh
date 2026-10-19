#!/usr/bin/env bash
# Consistent copy of the SQLite database (online backup, safe while the API runs) into
# ./backups/. The file holds the encrypted token and personal data: keep it private, and keep
# .env with it, or the token inside cannot be decrypted.
#
#   ./scripts/backup.sh            ./backups/insta-watcher-<date>.db
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
chmod 700 backups
name="insta-watcher-$(date -u +%Y%m%dT%H%M%SZ).db"
docker compose exec -T api python -c "
import sqlite3
src = sqlite3.connect('/data/app.db')
dst = sqlite3.connect('/tmp/backup.db')
src.backup(dst)
dst.close(); src.close()
"
docker compose cp "api:/tmp/backup.db" "backups/$name"
docker compose exec -T api rm -f /tmp/backup.db
chmod 600 "backups/$name"
echo "backups/$name"
