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
