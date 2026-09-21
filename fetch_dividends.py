#!/usr/bin/env python3
"""Refresh dividends.json (and the embedded seed in index.html) from Yahoo Finance.

Data flow: fetch_dividends.py -> dividends.json -> index.html dashboard.

The counter list, market/type classification, manual entries, links and the
`estimate` fallbacks are curated by hand -- this script never invents or drops
counters. It only rewrites each counter's `auto` block.

Sources, in the order the README prefers them:
  1. Moomoo API      -- needs the OpenD gateway on 127.0.0.1:11111 plus the
                        moomoo SDK. Skipped automatically when unavailable.
  2. Yahoo Finance    -- via yfinance; covers KLSE/SGX/HKEX/SWX/US uniformly.
Counters typed `unlisted` are never fetched.

Usage:
  python3 fetch_dividends.py               # refresh dividends.json + index.html
  python3 fetch_dividends.py --dry-run     # report changes, write nothing
  python3 fetch_dividends.py --only AVGO,D05
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")
OUT = os.path.join(HERE, "dividends.json")
OVERRIDES = os.path.join(HERE, "overrides.json")
MARKER = "/*__EMBEDDED_DATA__*/"
PROVIDER = "Yahoo Finance"
KLSE_PROVIDER = "KLSE Screener"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")


# ---------------------------------------------------------------- base data

def _embedded_span(src):
    """Return (start, end) offsets of the embedded JSON object in index.html."""
    i = src.index(MARKER) + len(MARKER)
    start = src.index("{", i)
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(src)):
        ch = src[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return start, j + 1
    raise ValueError("unterminated embedded data block in index.html")


def load_base():
    """Prefer an existing dividends.json; fall back to the embedded seed."""
    if os.path.exists(OUT):
        with open(OUT) as fh:
            return json.load(fh), "dividends.json"
    with open(INDEX) as fh:
        src = fh.read()
    a, b = _embedded_span(src)
    return json.loads(src[a:b]), "embedded seed"


# ------------------------------------------------------------------ helpers

def iso(value):
    """Yahoo hands back epoch seconds or datetimes; normalise to YYYY-MM-DD."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            return dt.datetime.utcfromtimestamp(value).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value)[:10]
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def infer_frequency(dates):
    """Dividends per year, inferred from the trailing 400 days of history."""
    if len(dates) < 2:
        return None
    cutoff = dates[-1] - dt.timedelta(days=400)
    recent = [d for d in dates if d >= cutoff]
    if len(recent) < 2:
        return None
    span = (recent[-1] - recent[0]).days
    if span <= 0:
        return None
    per_year = round((len(recent) - 1) * 365.0 / span)
    return {1: "annual", 2: "semi-annual", 4: "quarterly", 12: "monthly"}.get(per_year)


# Marker for "this counter simply does not pay a dividend" -- an answer, not a
# failure. Kept distinct from transport/parse errors in the run summary.
NO_DIVIDEND = "no dividend history at %s" % PROVIDER
STALE_DAYS = 400


def drop_if_stale(auto, today):
    """A counter that last paid years ago has no pending dividend to track.

    Both sources happily report that ancient cycle; keep it as a note so the row
    falls back to its curated estimate instead of showing as upcoming.
    """
    ex = auto.get("exDate")
    if not ex or auto.get("error"):
        return auto
    if (dt.date.fromisoformat(today) - dt.date.fromisoformat(ex)).days <= STALE_DAYS:
        return auto
    stale = blank_auto("last dividend %s (over %d days ago)" % (ex, STALE_DAYS))
    stale["provider"] = auto.get("provider")
    stale["fetchedAt"] = today
    return stale


def blank_auto(error):
    return {
        "exDate": None, "paymentDate": None, "currency": None,
        "announced": None, "estimated": None, "frequency": None,
        "provider": None, "fetchedAt": None, "error": error,
    }


# ------------------------------------------------------------------ sources

def moomoo_available():
    """Primary source per the README -- only usable with OpenD running."""
    try:
        import moomoo  # noqa: F401
    except ImportError:
        return False
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 11111), timeout=2):
            return True
    except OSError:
        return False


def _klse_date(text):
    """'15 Sep 2026' -> '2026-09-15'."""
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return dt.datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def fetch_klsescreener(code, today):
    """Declared entitlements for a Bursa counter.

    Yahoo carries no payment date for KLSE listings and falls back to the
    trailing historical amount, so for Bursa counters this source is better on
    both counts: it lists the payment date and the amount actually declared for
    the cycle now pending.
    """
    import requests
    from bs4 import BeautifulSoup

    url = "https://www.klsescreener.com/v2/stocks/view/%s" % code
    res = requests.get(url, headers={"User-Agent": UA}, timeout=25)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    table = None
    for candidate in soup.find_all("table"):
        heads = [th.get_text(strip=True) for th in candidate.find_all("th")]
        if any("Payment" in h for h in heads) and any("EX" in h.upper() for h in heads):
            table = candidate
            break
    if table is None:
        return blank_auto("no entitlement table at KLSE Screener")

    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 6:
            continue                      # financial-year group heading
        _, _, subject, ex, pay, amount = cells[:6]
        indicator = cells[6] if len(cells) > 6 else ""
        if "dividend" not in subject.lower():
            continue                      # bonus issue, share split, rights
        if indicator and indicator.lower() != "currency":
            continue                      # amount is not a per-share figure
        ex_iso = _klse_date(ex)
        if not ex_iso:
            continue
        try:
            value = float(amount.replace(",", ""))
        except ValueError:
            value = None
        rows.append((ex_iso, _klse_date(pay), value))

    if not rows:
        return blank_auto("no dividend rows at KLSE Screener")

    rows.sort()
    ex_iso, pay_iso, value = rows[-1]     # the cycle furthest forward
    recent = [dt.date.fromisoformat(r[0]) for r in rows]

    return {
        "exDate": ex_iso,
        "paymentDate": pay_iso,
        "currency": "MYR",
        "announced": value,               # declared, not inferred
        "estimated": None,
        "frequency": infer_frequency(recent),
        "provider": KLSE_PROVIDER,
        "fetchedAt": today,
        "error": None,
    }


def fetch_yahoo(symbol, today):
    """Latest dividend facts for one Yahoo symbol."""
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    try:
        info = ticker.info or {}
    except Exception:
        info = {}

    history = []
    try:
        series = ticker.dividends
        history = [(d.date(), float(v)) for d, v in series.items()]
    except Exception:
        pass

    ex = iso(info.get("exDividendDate"))
    pay = iso(info.get("dividendDate"))
    currency = info.get("currency")

    if not ex and history:
        ex = history[-1][0].isoformat()
    if not ex and not history:
        return blank_auto(NO_DIVIDEND)

    # Yahoo's dividendDate goes stale independently of exDividendDate; a payment
    # that lands before its own ex-date is a leftover from a prior cycle.
    if pay and ex and pay < ex:
        pay = None

    # `announced` only when the amount for this exact ex-date is on the tape.
    # A future ex-date that history hasn't reached yet is an estimate.
    announced = None
    estimated = None
    by_date = dict(history)
    if ex and ex in {d.isoformat() for d, _ in history}:
        announced = by_date[dt.date.fromisoformat(ex)]
    elif history:
        estimated = history[-1][1]

    return {
        "exDate": ex,
        "paymentDate": pay,
        "currency": currency,
        "announced": announced,
        "estimated": estimated,
        "frequency": infer_frequency([d for d, _ in history]),
        "provider": PROVIDER,
        "fetchedAt": today,
        "error": None,
    }


# --------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--only", default="", help="comma-separated tickers to refresh")
    args = ap.parse_args()

    data, origin = load_base()
    counters = data["counters"]
    only = {t.strip().upper() for t in args.only.split(",") if t.strip()}
    today = dt.date.today().isoformat()
    print("base: %s (%d counters)" % (origin, len(counters)))

    if moomoo_available():
        print("moomoo: OpenD reachable (unused: Yahoo covers every market here)")
    else:
        print("moomoo: unavailable (SDK or OpenD gateway missing) -> Yahoo Finance")

    cache = {}
    stats = {"ok": 0, "none": 0, "failed": 0, "skipped": 0}
    changes = []

    for c in counters:
        label = "%s %s" % (c["ticker"], c["name"])
        if c.get("type") == "unlisted" or not c.get("yahoo"):
            c["auto"] = blank_auto("not fetched (unlisted)")
            stats["skipped"] += 1
            continue
        if only and c["ticker"].upper() not in only:
            stats["skipped"] += 1
            continue

        symbol = c["yahoo"]
        if symbol not in cache:
            try:
                if c.get("marketKind") == "KLSE":
                    got = fetch_klsescreener(c["ticker"], today)
                    # Bursa counters fall back to Yahoo only if the screener
                    # gave us nothing usable.
                    if got.get("error"):
                        got = fetch_yahoo(symbol, today)
                else:
                    got = fetch_yahoo(symbol, today)
                cache[symbol] = drop_if_stale(got, today)
            except Exception as exc:
                cache[symbol] = blank_auto("%s: %s" % (type(exc).__name__, exc))
            err = cache[symbol]["error"]
            tag = "ok  " if not err else ("none" if err == NO_DIVIDEND else "ERR ")
            print("  %s %-10s %s" % (tag, symbol, cache[symbol]["error"] or
                                     "ex=%s pay=%s amt=%s" % (
                                         cache[symbol]["exDate"],
                                         cache[symbol]["paymentDate"],
                                         cache[symbol]["announced"] or
                                         cache[symbol]["estimated"])))

        fresh = dict(cache[symbol])
        before = c.get("auto") or {}
        if (before.get("exDate"), before.get("paymentDate"),
                before.get("announced")) != (fresh["exDate"], fresh["paymentDate"],
                                             fresh["announced"]):
            changes.append((label, before.get("exDate"), fresh["exDate"]))
        c["auto"] = fresh
        if not fresh["error"]:
            stats["ok"] += 1
        elif fresh["error"] == NO_DIVIDEND:
            stats["none"] += 1
        else:
            stats["failed"] += 1

    # Operator overrides exported from the dashboard merge into `manual`.
    if os.path.exists(OVERRIDES):
        with open(OVERRIDES) as fh:
            ov = json.load(fh)
        by_id = {c["id"]: c for c in counters}
        merged = 0
        for cid, fields in (ov or {}).items():
            if cid in by_id:
                by_id[cid].setdefault("manual", {}).update(
                    {k: v for k, v in fields.items() if v not in (None, "")})
                merged += 1
        print("overrides: merged %d" % merged)

    data["lastUpdated"] = dt.datetime.now(dt.timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "+00:00")
    data["counterCount"] = len(counters)

    print("\nfetched ok=%d no-dividend=%d failed=%d skipped=%d; %d counter(s) changed"
          % (stats["ok"], stats["none"], stats["failed"], stats["skipped"],
             len(changes)))
    for label, old, new in changes[:200]:
        print("  %-42s %s -> %s" % (label[:42], old, new))

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    payload = json.dumps(data, indent=2, ensure_ascii=False)
    with open(OUT, "w") as fh:
        fh.write(payload + "\n")

    # Keep the offline seed in index.html in step with dividends.json.
    with open(INDEX) as fh:
        src = fh.read()
    a, b = _embedded_span(src)
    with open(INDEX, "w") as fh:
        fh.write(src[:a] + json.dumps(data, indent=2, ensure_ascii=False) + src[b:])

    print("wrote %s and refreshed the embedded seed in %s"
          % (os.path.basename(OUT), os.path.basename(INDEX)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
