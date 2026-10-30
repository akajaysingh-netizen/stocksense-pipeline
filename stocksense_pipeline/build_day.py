"""Nightly build for one session: universe, IEX prices, fundamentals, then app files.

    python -m stocksense_pipeline.build_day --date 2026-10-27 [--bars FILE] [--offline DIR] [--no-sec] [--limit N]

Writes to out/:
    universe.json, incorporation.json   symbols in the app and why others were excluded
    prices/{date}.csv                   IEX daily bars for the session
    fundamentals/{SYMBOL}.json          SEC filings summary
    history/, snapshot/, quality/       via build_outputs
    manifest/{date}.json                counts, sources and attribution
"""

import argparse
import datetime as dt
import json
import os
import sys

from . import backfill, build_outputs, edgar, iex, universe
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
    dt.date.fromisoformat(a.date)
    sec = SecFetcher(offline_dir=a.offline)

    # 1. Universe
    uni_rows, excluded = None, {}
    if not a.no_sec:
        sec_rows = universe.fetch_universe(sec)
        cache_path = os.path.join(a.out, "incorporation.json")
        cache = json.load(open(cache_path, encoding="utf-8")) if os.path.isfile(cache_path) else {}
        incorporation = universe.incorporation_codes(sec, sorted({r["cik"] for r in sec_rows}), cache)
        _write_json(cache_path, cache)
        uni_rows, excluded = universe.build_universe(sec_rows, incorporation)
        _write_json(os.path.join(a.out, "universe.json"), {"as_of": a.date, "symbols": uni_rows, "excluded": excluded})

    # 2. IEX prices for the session
    bars_csv = a.bars
    if not bars_csv:
        entry = iex.tops_file(a.date.replace("-", ""))
        if entry is None:
            print("No IEX TOPS file for %s (market holiday or weekend)." % a.date)
            return 0
        bars_csv = os.path.join(a.out, "raw", "%s_bars.csv" % a.date)
        os.makedirs(os.path.dirname(bars_csv), exist_ok=True)
        print(iex.run_parser(entry["link"], a.date, bars_csv))
    n_bars = backfill.write_prices(bars_csv, os.path.join(a.out, "prices", "%s.csv" % a.date))

    # 3. Fundamentals
    failures = {}
    if uni_rows and not a.no_sec:
        rows = uni_rows[: a.limit] if a.limit else uni_rows
        for r in rows:
            try:
                facts = sec.json(edgar.COMPANYFACTS_URL % r["cik"], "CIK%010d.json" % r["cik"])
                _write_json(os.path.join(a.out, "fundamentals", "%s.json" % r["symbol"]), edgar.summarise(facts))
            except FetchError as exc:
                failures[r["symbol"]] = str(exc)

    # 4. App files
    build_outputs.main(["--out", a.out])

    manifest = {
        "date": a.date,
        "universe": len(uni_rows) if uni_rows else None,
        "excluded": len(excluded),
        "iex_bars": n_bars,
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
