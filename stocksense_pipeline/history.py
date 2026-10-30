"""Per-symbol price history from the daily price files, with split adjustment.

IEX HIST carries no corporate actions, so splits are inferred. An overnight
change counts as a split only when all of these hold:
  - it matches a common split ratio within 2% (3:2 and 2:3 are left out:
    small caps move 50% far more often than they split 3:2);
  - both sessions had round-lot trades (no carried-forward or odd-lot price);
  - the price level held for up to 3 traded sessions on each side.
Earlier prices are then divided by the factor. Any other overnight move over
40% is left alone and flagged for review. Both are recorded in the events.
"""

import csv
import math
import os

SPLIT_RATIOS = [2, 3, 4, 5, 8, 10, 15, 20, 25, 30, 40, 50, 100,
                1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 8, 1 / 10, 1 / 15, 1 / 20, 1 / 25, 1 / 30, 1 / 40, 1 / 50, 1 / 100]
SPLIT_TOLERANCE = 0.02
JUMP_FLAG = 0.40
HOLD_SESSIONS = 3
HOLD_BAND = 0.25


def load_days(prices_dir):
    """{date: {symbol: row}} for every prices/{date}.csv, oldest first."""
    days = {}
    for name in sorted(os.listdir(prices_dir)):
        if not name.endswith(".csv"):
            continue
        date = name[:-4]
        with open(os.path.join(prices_dir, name), newline="", encoding="utf-8") as fh:
            days[date] = {r["symbol"]: r for r in csv.DictReader(fh)}
    return days


def series_by_symbol(days):
    """{symbol: {"dates", "close", "volume", "no_trade", "odd_lot"}} on the common calendar.

    A session where the symbol had no IEX trade carries the previous close,
    volume 0 and no_trade=True. odd_lot marks a close set by odd-lot trades
    only. Days before the first trade are omitted.
    """
    dates = sorted(days)
    symbols = sorted({s for d in days.values() for s in d})
    out = {}
    for sym in symbols:
        s = {"dates": [], "close": [], "volume": [], "no_trade": [], "odd_lot": []}
        last = None
        for d in dates:
            row = days[d].get(sym)
            if row is not None and float(row["close"]) > 0:
                last = float(row["close"])
                s["dates"].append(d)
                s["close"].append(last)
                s["volume"].append(int(row["volume"]))
                s["no_trade"].append(False)
                s["odd_lot"].append(row.get("odd_lot_only") == "1")
            elif last is not None:
                s["dates"].append(d)
                s["close"].append(last)
                s["volume"].append(0)
                s["no_trade"].append(True)
                s["odd_lot"].append(False)
        out[sym] = s
    return out


def match_split(ratio):
    """The split factor an overnight price ratio (prev/close) matches, or None."""
    for r in SPLIT_RATIOS:
        if abs(ratio / r - 1) <= SPLIT_TOLERANCE:
            return r
    return None


def _firm(series, i):
    """A round-lot IEX trade set this session's close."""
    return not series["no_trade"][i] and not series.get("odd_lot", [False] * (i + 1))[i]


def _level_held(series, close, start, step, level):
    """Up to HOLD_SESSIONS traded closes from start (walking by step) stay within HOLD_BAND of level."""
    seen, j = 0, start
    while 0 <= j < len(close) and seen < HOLD_SESSIONS:
        if not series["no_trade"][j]:
            if abs(close[j] / level - 1) > HOLD_BAND:
                return False
            seen += 1
        j += step
    return True


def adjust(series):
    """Return (adjusted closes, volumes, events). Volumes are scaled the opposite way."""
    close = list(series["close"])
    volume = list(series["volume"])
    events = []
    for i in range(len(close) - 1, 0, -1):
        prev, cur = close[i - 1], close[i]
        if prev <= 0 or cur <= 0 or series["no_trade"][i]:
            continue
        move = abs(math.log(cur / prev))
        if move < math.log(1 + JUMP_FLAG):
            continue
        factor = match_split(prev / cur)
        if factor and not (_firm(series, i) and _firm(series, i - 1)
                           and _level_held(series, close, i + 1, 1, cur)
                           and _level_held(series, close, i - 2, -1, prev)):
            factor = None
        if factor:
            for j in range(i):
                close[j] = close[j] / factor
                volume[j] = int(round(volume[j] * factor))
            events.append({"date": series["dates"][i], "type": "split_inferred", "factor": round(factor, 4)})
        else:
            events.append({"date": series["dates"][i], "type": "large_move", "change_pct": round(100 * (cur / prev - 1), 1)})
    events.reverse()
    return [round(c, 4) for c in close], volume, events
