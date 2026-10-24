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

# --- fold dashboard edits into the project file -----------------------------
# Counters added or removed in the Counters tab live in the shared store as an
# overlay. Pull them down so the fetcher can make them part of dividends.json;
# the overlay is only cleared after the fetch succeeds, so a throttled run never
# loses someone's edit.
API="http://127.0.0.1:8080/api/roster"
ROSTER_PENDING=0
if ROSTER_JSON=$(curl -fsS --max-time 10 "$API" 2>/dev/null); then
  COUNTS=$(printf '%s' "$ROSTER_JSON" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    a, r = len(d.get('added') or []), len(d.get('removed') or [])
except Exception:
    a = r = 0
print('%d %d' % (a, r))
" 2>/dev/null || echo "0 0")
  ADDED=${COUNTS% *}; REMOVED=${COUNTS#* }
  if [ "$ADDED" != "0" ] || [ "$REMOVED" != "0" ]; then
    printf '%s' "$ROSTER_JSON" > "$PROJECT_DIR/roster.json"
    ROSTER_PENDING=1
    log "ROSTER    folding in $ADDED added, $REMOVED removed"
  fi
fi

BEFORE=$(python3 -c "import json;print(json.load(open('dividends.json'))['lastUpdated'])" 2>/dev/null || echo "?")

# --min-ok 40 leaves the good file in place if a source throttles us.
OUT=$(python3 fetch_dividends.py --min-ok 40 2>&1)
RC=$?

if [ $RC -ne 0 ]; then
  # Leave roster.json and the shared overlay alone so the edit survives to the
  # next run rather than being silently dropped.
  rm -f "$PROJECT_DIR/roster.json"
  log "ABORTED   guard tripped, data and roster left untouched -- $(echo "$OUT" | grep -i 'ABORT' | head -1)"
  exit 0
fi

# The edits are in dividends.json now, so retire the overlay. Doing it in this
# order means a crash leaves a duplicate-but-harmless overlay, never a lost edit.
if [ "$ROSTER_PENDING" = "1" ]; then
  rm -f "$PROJECT_DIR/roster.json"
  if curl -fsS --max-time 10 -X PUT -H 'Content-Type: application/json' \
       -d '{"added":[],"removed":[]}' "$API" >/dev/null 2>&1; then
    log "ROSTER    folded in and cleared"
  else
    log "ROSTER    folded in, but could not clear the overlay (harmless, will re-apply)"
  fi
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
