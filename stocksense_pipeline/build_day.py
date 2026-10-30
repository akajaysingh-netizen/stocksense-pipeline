"""Build one session's data: universe, IEX bars, fundamentals and ratios.

    python -m stocksense_pipeline.build_day --date 2026-10-27 [--bars FILE] [--offline DIR] [--limit N]

Writes to pipeline/out/:
    universe.json                  symbols in the app and why others were excluded
    prices/{date}.csv              IEX daily bars for universe symbols
    fundamentals/{SYMBOL}.json     filings summary + ratios at that day's price
    snapshot/{date}.json           one row per symbol for the screener
    manifest/{date}.json           counts, sources and attribution
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys

from . import edgar, iex, universe
from .http import FetchError, SecFetcher

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True, help="session date YYYY-MM-DD")
    ap.add_argument("--bars", help="existing iexparse CSV (skip the IEX download)")
    ap.add_argument("--offline", help="directory of saved SEC JSON files (tests, no network)")
    ap.add_argument("--no-sec", action="store_true", help="skip SEC calls (no SEC_USER_AGENT yet)")
    ap.add_argument("--limit", type=int, default=0, help="only fetch fundamentals for the first N symbols")
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    a = ap.parse_args(argv)

    date = dt.date.fromisoformat(a.date)
    sec = SecFetcher(offline_dir=a.offline)

    # 1. Universe
    if a.no_sec:
        uni_rows, excluded = None, {}
    else:
        sec_rows = universe.fetch_universe(sec)
        cache_path = os.path.join(a.out, "incorporation.json")
        cache = json.load(open(cache_path, encoding="utf-8")) if os.path.isfile(cache_path) else {}
        incorporation = universe.incorporation_codes(sec, sorted({r["cik"] for r in sec_rows}), cache)
        _write_json(cache_path, cache)
        uni_rows, excluded = universe.build_universe(sec_rows, incorporation)
        _write_json(os.path.join(a.out, "universe.json"), {"as_of": a.date, "symbols": uni_rows, "excluded": excluded})
    symbols = {r["symbol"] for r in uni_rows} if uni_rows else None

    # 2. IEX bars
    bars_csv = a.bars
    if not bars_csv:
        entry = iex.tops_file(date.strftime("%Y%m%d"))
        if entry is None:
            print("No IEX TOPS file for %s (market holiday or weekend)." % a.date)
            return 0
        bars_csv = os.path.join(a.out, "raw", "%s_bars.csv" % a.date)
        os.makedirs(os.path.dirname(bars_csv), exist_ok=True)
        print(iex.run_parser(entry["link"], a.date, bars_csv))
    bars = iex.read_bars(bars_csv, symbols)
    prices_path = os.path.join(a.out, "prices", "%s.csv" % a.date)
    os.makedirs(os.path.dirname(prices_path), exist_ok=True)
    with open(prices_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "open", "high", "low", "close", "volume", "trades", "odd_lot_only"])
        for sym in sorted(bars):
            b = bars[sym]
            w.writerow([sym, b["open"], b["high"], b["low"], b["close"], b["volume"], b["trades"], int(b["odd_lot_only"])])

    # 3. Fundamentals + snapshot rows
    snapshot, failures = [], {}
    rows = uni_rows or [{"symbol": s, "cik": None, "name": None, "exchange": None} for s in sorted(bars)]
    if a.limit:
        rows = rows[: a.limit]
    for r in rows:
        bar = bars.get(r["symbol"])
        row = {"symbol": r["symbol"], "name": r["name"], "exchange": r["exchange"],
               "price": bar["close"] if bar else None, "volume": bar["volume"] if bar else None,
               "no_iex_trade": bar is None}
        if r.get("cik") and not a.no_sec:
            try:
                facts = sec.json(edgar.COMPANYFACTS_URL % r["cik"], "CIK%010d.json" % r["cik"])
                summary = edgar.summarise(facts)
                summary["ratios"] = edgar.ratios(summary, row["price"])
                _write_json(os.path.join(a.out, "fundamentals", "%s.json" % r["symbol"]), summary)
                row.update(summary["ratios"])
            except FetchError as exc:
                failures[r["symbol"]] = str(exc)
        snapshot.append(row)
    _write_json(os.path.join(a.out, "snapshot", "%s.json" % a.date), snapshot)

    manifest = {
        "date": a.date,
        "universe": len(uni_rows) if uni_rows else None,
        "excluded": len(excluded),
        "iex_bars": len(bars),
        "snapshot_rows": len(snapshot),
        "no_iex_trade": sum(1 for s in snapshot if s["no_iex_trade"]),
        "sec_failures": len(failures),
        "sources": {
            "prices": {"name": "IEX HIST (TOPS)", "attribution": iex.ATTRIBUTION, "url": iex.ATTRIBUTION_URL,
                       "note": "Last IEX trade, previous session; IEX trades only, not the consolidated tape."},
            "fundamentals": {"name": "SEC EDGAR XBRL company facts", "attribution": "Source: SEC EDGAR"},
        },
    }
    _write_json(os.path.join(a.out, "manifest", "%s.json" % a.date), manifest)
    print(json.dumps(manifest, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
