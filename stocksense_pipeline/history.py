"""Per-symbol price history from the daily price files, with split adjustment.

IEX HIST carries no corporate actions, so splits are inferred: an overnight
change that matches a common split ratio within 3% is treated as a split and
earlier prices are divided by it. Any other overnight move over 40% is left
alone and flagged for review. Both are recorded in the symbol's events.
"""

import csv
import math
import os

SPLIT_RATIOS = [2, 3, 4, 5, 8, 10, 15, 20, 1.5, 1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 8, 1 / 10, 1 / 15, 1 / 20, 2 / 3]
SPLIT_TOLERANCE = 0.03
JUMP_FLAG = 0.40


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
    """{symbol: {"dates", "close", "volume", "no_trade"}} on the common calendar.

    A session where the symbol had no IEX trade carries the previous close,
    volume 0 and no_trade=True. Days before the first trade are omitted.
    """
    dates = sorted(days)
    symbols = sorted({s for d in days.values() for s in d})
    out = {}
    for sym in symbols:
        s = {"dates": [], "close": [], "volume": [], "no_trade": []}
        last = None
        for d in dates:
            row = days[d].get(sym)
            if row is not None and float(row["close"]) > 0:
                last = float(row["close"])
                s["dates"].append(d)
                s["close"].append(last)
                s["volume"].append(int(row["volume"]))
                s["no_trade"].append(False)
            elif last is not None:
                s["dates"].append(d)
                s["close"].append(last)
                s["volume"].append(0)
                s["no_trade"].append(True)
        out[sym] = s
    return out


def match_split(ratio):
    """The split factor an overnight price ratio (prev/close) matches, or None."""
    for r in SPLIT_RATIOS:
        if abs(ratio / r - 1) <= SPLIT_TOLERANCE:
            return r
    return None


def adjust(series):
    """Return (adjusted closes, events). Volumes are scaled the opposite way."""
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
        if factor:
            for j in range(i):
                close[j] = close[j] / factor
                volume[j] = int(round(volume[j] * factor))
            events.append({"date": series["dates"][i], "type": "split_inferred", "factor": round(factor, 4)})
        else:
            events.append({"date": series["dates"][i], "type": "large_move", "change_pct": round(100 * (cur / prev - 1), 1)})
    events.reverse()
    return [round(c, 4) for c in close], volume, events
