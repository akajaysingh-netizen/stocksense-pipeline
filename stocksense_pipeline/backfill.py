"""Download and reduce past IEX sessions, one daily price file per session.

    python -m stocksense_pipeline.backfill --end 2026-10-27 --sessions 252 [--shard 0 --shards 20]

Resumable: a session whose prices/{date}.csv already exists is skipped.
Sharding splits the date list so CI can run many jobs in parallel.
Weekends are skipped here; holidays are skipped because IEX lists no file.
"""

import argparse
import datetime as dt
import os
import sys

from . import iex
from .http import FetchError

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def weekdays_back(end, count):
    """`count` weekdays ending on `end` (inclusive), oldest first."""
    out, d = [], end
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(out))


def shard(items, index, total):
    return [x for i, x in enumerate(items) if i % total == index]


def write_prices(bars_csv, prices_csv):
    bars = iex.read_bars(bars_csv)
    os.makedirs(os.path.dirname(prices_csv), exist_ok=True)
    tmp = prices_csv + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write("symbol,open,high,low,close,volume,trades,odd_lot_only,etp\n")
        for sym in sorted(bars):
            b = bars[sym]
            fh.write("%s,%s,%s,%s,%s,%d,%d,%d,%d\n" % (sym, b["open"], b["high"], b["low"], b["close"],
                                                       b["volume"], b["trades"], b["odd_lot_only"], b["etp"]))
    os.replace(tmp, prices_csv)
    return len(bars)


def main(argv=None, fetch_index=None, parse=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--end", required=True, help="last session YYYY-MM-DD")
    ap.add_argument("--sessions", type=int, default=252)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    a = ap.parse_args(argv)

    fetch_index = fetch_index or (lambda compact: iex.tops_file(compact))
    parse = parse or iex.run_parser
    dates = shard(weekdays_back(dt.date.fromisoformat(a.end), a.sessions), a.shard, a.shards)
    done = skipped = holidays = 0
    failed = []
    for d in dates:
        iso = d.isoformat()
        prices_csv = os.path.join(a.out, "prices", "%s.csv" % iso)
        if os.path.isfile(prices_csv):
            skipped += 1
            continue
        try:
            entry = fetch_index(d.strftime("%Y%m%d"))
        except FetchError as exc:
            print("%s: index failed: %s" % (iso, exc), file=sys.stderr)
            failed.append(iso)
            continue
        if entry is None:
            holidays += 1
            continue
        bars_csv = os.path.join(a.out, "raw", "%s_bars.csv" % iso)
        os.makedirs(os.path.dirname(bars_csv), exist_ok=True)
        try:
            log = parse(entry["link"], iso, bars_csv)
        except FetchError as exc:
            print("%s: failed: %s" % (iso, exc), file=sys.stderr)
            failed.append(iso)
            continue
        n = write_prices(bars_csv, prices_csv)
        os.remove(bars_csv)
        done += 1
        print("%s: %d symbols. %s" % (iso, n, log))
    print("backfill: %d built, %d already present, %d without an IEX file, %d failed %s"
          % (done, skipped, holidays, len(failed), " ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
