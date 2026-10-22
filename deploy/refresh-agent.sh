#!/usr/bin/env bash
#
# Periodic refresh, run by launchd (see com.winnie.dividend-refresh.plist).
#
# Writes dividends.json and index.html in place. The container bind-mounts both
# read-only, so the live site picks the change up with no rebuild and no deploy,
# and the open page notices within its 60-second poll.

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$HOME/.local/state/dividend-tracker"
LOG="$LOG_DIR/refresh.log"
LOCK="$LOG_DIR/refresh.lock"

mkdir -p "$LOG_DIR"

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOG"; }

# A run takes ~25s. If a previous one is still going — a slow source, a stalled
# request — skip this tick rather than stacking two fetches on the same files.
exec 9>"$LOCK"
if ! flock -n 9 2>/dev/null; then
  # macOS has no flock(1); fall back to a pid file.
  if [ -f "$LOCK.pid" ] && kill -0 "$(cat "$LOCK.pid" 2>/dev/null)" 2>/dev/null; then
    log "SKIP      previous run still active (pid $(cat "$LOCK.pid"))"
    exit 0
  fi
fi
echo $$ > "$LOCK.pid"
trap 'rm -f "$LOCK.pid"' EXIT

# No point waking the sources when the machine is offline.
if ! /sbin/ping -c1 -t3 query2.finance.yahoo.com >/dev/null 2>&1; then
  log "SKIP      no network"
  exit 0
fi

cd "$PROJECT_DIR" || { log "FAIL      cannot cd to $PROJECT_DIR"; exit 1; }

BEFORE=$(python3 -c "import json;print(json.load(open('dividends.json'))['lastUpdated'])" 2>/dev/null || echo "?")

# --min-ok 40 leaves the good file in place if a source throttles us.
OUT=$(python3 fetch_dividends.py --min-ok 40 2>&1)
RC=$?

if [ $RC -ne 0 ]; then
  log "ABORTED   guard tripped, data left untouched -- $(echo "$OUT" | grep -i 'ABORT' | head -1)"
  exit 0
fi

AFTER=$(python3 -c "import json;print(json.load(open('dividends.json'))['lastUpdated'])" 2>/dev/null || echo "?")
CHANGED=$(echo "$OUT" | grep -oE '[0-9]+ counter\(s\) changed' | head -1)

if [ "$BEFORE" = "$AFTER" ]; then
  log "OK        no change"
else
  log "UPDATED   $AFTER  ($CHANGED)"
fi

# Keep the log from growing without bound.
if [ "$(wc -c < "$LOG" 2>/dev/null || echo 0)" -gt 1048576 ]; then
  tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
