"""IEX HIST: find the day's TOPS file and reduce it to daily bars with iexparse.

Terms (https://www.iex.io/products/market-data-connectivity/hist-terms): free,
"as is", and anyone who shows the data must cite "Data provided for free by
IEX" with a link. Prices are IEX trades only, not the consolidated tape.
"""

import csv
import os
import shutil
import subprocess
import urllib.request

from .http import FetchError, get_json

HIST_INDEX_URL = "https://iextrading.com/api/1.0/hist?date=%s"
ATTRIBUTION = "Data provided for free by IEX"
ATTRIBUTION_URL = "https://iextrading.com/"

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PARSER = os.path.join(HERE, "..", "iexparse", "iexparse.exe" if os.name == "nt" else "iexparse")


def tops_file(date_compact, index=None):
    """The TOPS entry {link, size, version} for a YYYYMMDD date, or None if IEX has none (holiday, weekend)."""
    index = index if index is not None else get_json(HIST_INDEX_URL % date_compact)
    tops = [e for e in index or [] if e.get("feed") == "TOPS"]
    if not tops:
        return None
    tops.sort(key=lambda e: e.get("version", ""))
    return tops[-1]


def run_parser(link, date_iso, out_csv, parser=DEFAULT_PARSER, chunk=8 << 20):
    """Stream the gzipped pcap from IEX straight into iexparse; nothing is stored."""
    parser = os.path.abspath(parser)
    if not os.path.isfile(parser) and not shutil.which(parser):
        raise FetchError("iexparse binary not found at %s (run: go build in pipeline/iexparse)" % parser)
    proc = subprocess.Popen([parser, "-date", date_iso, "-o", out_csv],
                            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    req = urllib.request.Request(link, headers={"User-Agent": "stocksense-pipeline"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            while True:
                block = resp.read(chunk)
                if not block:
                    break
                proc.stdin.write(block)
    finally:
        proc.stdin.close()
    err = proc.stderr.read().decode("utf-8", "replace").strip()
    if proc.wait() != 0:
        raise FetchError("iexparse failed: %s" % err)
    return err


def read_bars(path, universe_symbols=None):
    """Load iexparse CSV → {symbol: bar}. Drops test securities and, if given, symbols outside the universe."""
    bars = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["test"] == "1":
                continue
            sym = row["symbol"]
            if universe_symbols is not None and sym not in universe_symbols:
                continue
            if float(row["close"]) <= 0:
                continue
            bars[sym] = {
                "date": row["date"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
                "trades": int(row["trades"]),
                "odd_lot_only": row["odd_lot_only"] == "1",
                "etp": row["etp"] == "1",
            }
    return bars
