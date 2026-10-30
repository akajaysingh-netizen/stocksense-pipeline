"""Daily per-stock numbers for the screener, stock page and setups.

Setup rules are the same as the app's (web/src/lib/indicators.ts), ported
from trader-scanner, github.com/jakeschincariol/day-trader-skill (MIT).
"""

SETUP_KEYS = ("breakout_20d", "bull_flag", "reclaim_ma20", "oversold", "oversold_bounce")


def sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def rsi(closes, n=14):
    """Wilder's RSI."""
    if len(closes) < n + 1:
        return None
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def pct_change(closes, sessions):
    if len(closes) <= sessions or closes[-1 - sessions] <= 0:
        return None
    return 100 * (closes[-1] / closes[-1 - sessions] - 1)


def find_setups(c):
    if len(c) < 30:
        return []
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20, ma20_prev = sma(c, 20), sma(c[:-1], 20)
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r, r_prev = rsi(c), rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags


def quantile(sorted_values, q):
    if not sorted_values:
        return None
    pos = (len(sorted_values) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def history_30d(closes, horizon=30):
    """Distribution of past 30-session returns (history, not a forecast)."""
    rets = sorted(100 * (closes[i] / closes[i - horizon] - 1) for i in range(horizon, len(closes)) if closes[i - horizon] > 0)
    if len(rets) < 20:
        return None
    return {
        "windows": len(rets),
        "pct_up": round(100 * sum(1 for r in rets if r > 0) / len(rets), 1),
        "p10": round(quantile(rets, 0.10), 2),
        "p50": round(quantile(rets, 0.50), 2),
        "p90": round(quantile(rets, 0.90), 2),
    }


def compute(closes, volumes):
    """All derived numbers for the latest session of one stock."""
    if not closes:
        return {}
    last = closes[-1]
    year = closes[-252:]
    s50, s200 = sma(closes, 50), sma(closes, 200)
    v20 = sma(volumes[:-1], 20) if len(volumes) > 20 else None
    r = rsi(closes)
    return {
        "chg_1d": _r(pct_change(closes, 1)),
        "chg_1m": _r(pct_change(closes, 21)),
        "chg_3m": _r(pct_change(closes, 63)),
        "chg_6m": _r(pct_change(closes, 126)),
        "chg_1y": _r(pct_change(closes, 251)),
        "rsi14": _r(r),
        "above_sma50": None if s50 is None else last > s50,
        "above_sma200": None if s200 is None else last > s200,
        "from_high_52w": _r(100 * (last / max(year) - 1)),
        "from_low_52w": _r(100 * (last / min(year) - 1)),
        "vol_vs_20d": _r(volumes[-1] / v20) if v20 else None,
        "setups": find_setups(closes),
        "history_30d": history_30d(closes),
        "sessions": len(closes),
    }


def _r(v, digits=2):
    return None if v is None else round(v, digits)
