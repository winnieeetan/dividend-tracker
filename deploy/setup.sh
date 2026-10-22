#!/usr/bin/env bash
#
# One-time setup: run the dividend dashboard in Docker, served to the tailnet.
#
#   bash deploy/setup.sh
#
# Safe to re-run — every step checks whether it is already done.
# You will be asked for your password once, by Homebrew's installer.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=8080
TAILSCALE=/usr/local/bin/tailscale

say()  { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '    \033[33m!\033[0m %s\n' "$1"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

# --- 1. Homebrew -----------------------------------------------------------
say "Homebrew"
if command -v brew >/dev/null 2>&1; then
  ok "already installed: $(command -v brew)"
else
  warn "installing — this is the step that asks for your password"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
# Apple Silicon puts brew in /opt/homebrew, which is not on PATH until shellenv runs.
if [ -x /opt/homebrew/bin/brew ]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [ -x /usr/local/bin/brew ]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi
command -v brew >/dev/null 2>&1 || die "brew still not on PATH; open a new terminal and re-run"

# --- 2. Colima + Docker CLI ------------------------------------------------
say "Colima and the Docker CLI"
for pkg in colima docker docker-compose; do
  if brew list "$pkg" >/dev/null 2>&1; then ok "$pkg already installed"
  else warn "installing $pkg"; brew install "$pkg"; fi
done

# --- 3. Start the Linux VM that runs containers ----------------------------
say "Colima VM"
if colima status >/dev/null 2>&1; then
  ok "already running"
else
  warn "starting (first run downloads a VM image, a few minutes)"
  # 2 CPUs and 2GB is ample for one nginx container.
  colima start --cpu 2 --memory 2 --disk 20
fi
docker info >/dev/null 2>&1 || die "docker cannot reach the daemon; try: colima restart"
ok "docker is talking to the daemon"

# --- 4. Build and run the dashboard ---------------------------------------
say "Dashboard container"
cd "$PROJECT_DIR"
docker compose -f deploy/docker-compose.yml up -d --build
sleep 3
if curl -fsS -o /dev/null "http://127.0.0.1:${PORT}/index.html"; then
  ok "serving on 127.0.0.1:${PORT}"
else
  docker compose -f deploy/docker-compose.yml logs --tail 30 || true
  die "container is up but not answering on ${PORT}"
fi

# --- 5. Publish to the tailnet --------------------------------------------
say "Tailscale Serve"
if [ ! -x "$TAILSCALE" ]; then
  warn "tailscale CLI not found at $TAILSCALE — skipping"
else
  if "$TAILSCALE" serve --bg "$PORT" 2>&1 | tee /dev/stderr | grep -q "not enabled"; then
    die "Serve is not enabled on your tailnet yet. Open the link printed above, enable it, then re-run this script."
  fi
  ok "published"
  "$TAILSCALE" serve status || true
fi

DNSNAME="$("$TAILSCALE" status --json 2>/dev/null \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || echo "")"

say "Done"
[ -n "$DNSNAME" ] && printf '    Teammates on your tailnet: \033[1mhttps://%s\033[0m\n' "$DNSNAME"
cat <<'NOTES'

    The container restarts by itself after a reboot, but Colima does not.
    After restarting the Mac, run:  colima start

    Useful:
      docker compose -f deploy/docker-compose.yml logs -f     # watch requests
      docker compose -f deploy/docker-compose.yml restart     # reload
      docker compose -f deploy/docker-compose.yml down        # stop
      tailscale serve reset                                   # unpublish
NOTES
