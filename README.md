# Dividend tracker

## References
1. Primary: Moomoo API
2. Claude estimation
3. Secondary references

## Refreshing the data

```bash
python3 fetch_dividends.py            # refresh dividends.json + the embedded seed
python3 fetch_dividends.py --dry-run  # report what would change, write nothing
python3 fetch_dividends.py --only AVGO,D05
```

Needs `yfinance` (`pip3 install yfinance`). The Moomoo path is used automatically
when the OpenD gateway is running on `127.0.0.1:11111`; otherwise the script
falls back to Yahoo Finance, which covers KLSE / SGX / HKEX / SWX / US.

The script only rewrites each counter's `auto` block — the counter list, market
and type classification, manual entries, links and `estimate` fallbacks are
curated by hand and preserved. Serve the folder (`python3 -m http.server`) so the
dashboard reads `dividends.json`; opening `index.html` directly falls back to the
embedded seed.

## Managing counters

The dashboard's **Counters** tab adds and removes counters without editing JSON
by hand. Pick the portfolio (or name a new one), the market, and the type; the
Yahoo symbol is derived from the ticker per market (`5285` on KLSE becomes
`5285.KL`, `00700` on HKEX becomes `0700.HK`) and can be overridden.

Those edits live in the browser's localStorage, so they show on the board at
once but the counter has no dividend data yet. To make them part of the project:

1. **Save roster.json** from the Counters tab into this folder.
2. `python3 fetch_dividends.py`

The fetcher folds the roster into `dividends.json`, fetches the new counters,
and the additions become permanent for everyone. Delete `roster.json` afterwards
so the same edits are not replayed on the next run.

## Automatic refresh

Two scheduled jobs, split because they need different things:

1. **GitHub Actions** (`.github/workflows/refresh.yml`) — weekdays 01:03 UTC.
   Runs the fetcher and commits `dividends.json` and `index.html` to `main`.
   This is where the fetching happens: GitHub's runners have unrestricted
   outbound network.
2. **Cloud routine** "Publish dividend data to dashboard" — weekdays 01:33 UTC,
   half an hour later. Reads what step 1 committed and writes it into the shared
   dashboard's database, so every open copy of the link picks it up. Its sandbox
   can reach GitHub but *not* the finance sources, so it never fetches.
   Manage or pause it at https://claude.ai/code/routines

Both refuse to write when the data looks wrong: the fetcher's `--min-ok 40`
aborts if fewer than 40 counters come back clean, and the routine re-checks the
same thing before publishing.

## Standalone file (no server, no hosting)

Every run of `fetch_dividends.py` also writes **`dividend-tracker.html`** — one
self-contained file with the data baked in. Open it straight from the Finder,
AirDrop it to a phone, or keep it in iCloud Drive; it needs no server and no
network. Ticks and Counters-tab edits are saved in whichever browser opens it.

Its data is frozen at the moment it was built, so rebuild it (`python3
fetch_dividends.py`) whenever you want current figures.

## Serving it to the team over Tailscale

The dashboard runs in its own container and is published to the tailnet, so
teammates reach it and nothing else can — the container binds to loopback only,
and `tailscale serve` is the sole route in.

One-time setup:

```bash
bash deploy/setup.sh
```

It installs Homebrew (asks for your password once), Colima and the Docker CLI,
starts the VM, builds the container, and publishes it. Re-running it is safe.

Tailscale Serve must be enabled on the tailnet first — a one-time click in the
admin console. The script says so and stops if it isn't.

`deploy/` holds the whole setup: `Dockerfile` (nginx:alpine), `nginx.conf`
(no-store on the data files so the dashboard's 60-second poll sees new data),
and `docker-compose.yml`. `index.html` and `dividends.json` are bind-mounted
read-only, so a scheduled fetch updates the live site with no rebuild.

After a reboot the container restarts itself but the Colima VM does not —
run `colima start`.
