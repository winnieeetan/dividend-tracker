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

A scheduled cloud agent ("Dividend tracker daily refresh") runs every weekday at
01:00 UTC / 09:00 MYT. It runs the fetcher, pushes the result to `main`, and
writes the new snapshot into the shared dashboard's database so every open copy
picks it up. Manage or pause it at https://claude.ai/code/routines

It passes `--min-ok 40`: if a source throttles the runner and fewer than 40
counters come back clean, the run stops without writing, so a bad fetch cannot
replace good data.

## Standalone file (no server, no hosting)

Every run of `fetch_dividends.py` also writes **`dividend-tracker.html`** — one
self-contained file with the data baked in. Open it straight from the Finder,
AirDrop it to a phone, or keep it in iCloud Drive; it needs no server and no
network. Ticks and Counters-tab edits are saved in whichever browser opens it.

Its data is frozen at the moment it was built, so rebuild it (`python3
fetch_dividends.py`) whenever you want current figures.
