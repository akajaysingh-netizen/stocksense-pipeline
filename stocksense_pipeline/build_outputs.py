"""Turn the daily price files (+ fundamentals) into the files the app reads.

    python -m stocksense_pipeline.build_outputs [--out DIR]

Reads   out/prices/*.csv, out/universe.json (optional), out/fundamentals/*.json (optional)
Writes  out/history/{SYMBOL}.json   adjusted daily closes, volumes, events
        out/snapshot/{date}.json    columnar screener table for the latest session
        out/quality/{date}.json     data-quality report
"""

import argparse
import json
import os
import sys

from . import edgar, features, history, iex

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

COLUMNS = ["symbol", "name", "exchange", "price", "chg_1d", "chg_1m", "chg_3m", "chg_6m", "chg_1y",
           "rsi14", "above_sma50", "above_sma200", "from_high_52w", "from_low_52w", "vol_vs_20d",
           "iex_volume", "no_iex_trade", "setups", "hist_pct_up", "hist_p10", "hist_p90",
           "market_cap", "pe", "ps", "pb", "roe_pct", "debt_to_equity", "net_margin_pct", "revenue_growth_pct"]

STALE_SESSIONS = 5


def _load_json(path, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path, obj, compact=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        if compact:
            json.dump(obj, fh, separators=(",", ":"))
        else:
            json.dump(obj, fh, indent=1)


def revenue_growth(summary):
    q = [x["value"] for x in summary.get("quarters", {}).get("revenue", [])]
    if len(q) < 8:
        return None
    prev, cur = sum(q[-8:-4]), sum(q[-4:])
    return round(100 * (cur / prev - 1), 1) if prev > 0 else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(ROOT, "out"))
    a = ap.parse_args(argv)

    days = history.load_days(os.path.join(a.out, "prices"))
    if not days:
        print("No price files in %s" % os.path.join(a.out, "prices"))
        return 1
    dates = sorted(days)
    latest = dates[-1]
    uni = _load_json(os.path.join(a.out, "universe.json"))
    meta = {r["symbol"]: r for r in uni["symbols"]} if uni else {}

    rows, quality = [], {"date": latest, "sessions": len(dates), "first_session": dates[0],
                          "stale": [], "large_moves_today": [], "splits_inferred_today": [], "missing_universe": []}
    for sym, s in history.series_by_symbol(days).items():
        if meta and sym not in meta:
            continue
        close, volume, events = history.adjust(s)
        _write_json(os.path.join(a.out, "history", "%s.json" % sym),
                    {"symbol": sym, "dates": s["dates"], "close": close, "volume": volume,
                     "no_trade": s["no_trade"], "events": events,
                     "source": iex.ATTRIBUTION, "source_url": iex.ATTRIBUTION_URL}, compact=True)
        f = features.compute(close, volume)
        last_trade = len(s["no_trade"]) - 1 - s["no_trade"][::-1].index(False) if False in s["no_trade"] else None
        stale_for = len(s["no_trade"]) - 1 - last_trade if last_trade is not None else None
        if stale_for and stale_for >= STALE_SESSIONS:
            quality["stale"].append({"symbol": sym, "sessions_without_trade": stale_for})
        for e in events:
            if e["date"] == latest:
                key = "splits_inferred_today" if e["type"] == "split_inferred" else "large_moves_today"
                quality[key].append(dict(e, symbol=sym))

        fund = _load_json(os.path.join(a.out, "fundamentals", "%s.json" % sym))
        ratios = edgar.ratios(fund, close[-1]) if fund else {}
        m = meta.get(sym, {})
        h = f.get("history_30d") or {}
        row = {
            "symbol": sym, "name": m.get("name"), "exchange": m.get("exchange"),
            "price": close[-1], "iex_volume": volume[-1], "no_iex_trade": s["no_trade"][-1],
            "hist_pct_up": h.get("pct_up"), "hist_p10": h.get("p10"), "hist_p90": h.get("p90"),
            "revenue_growth_pct": revenue_growth(fund) if fund else None,
        }
        row.update({k: f.get(k) for k in COLUMNS if k in f})
        row.update(ratios)
        rows.append([row.get(c) for c in COLUMNS])

    if meta:
        have = {r[0] for r in rows}
        quality["missing_universe"] = sorted(s for s in meta if s not in have)
    snapshot = {"date": latest, "columns": COLUMNS, "rows": sorted(rows, key=lambda r: r[0]),
                "source": {"prices": iex.ATTRIBUTION, "prices_url": iex.ATTRIBUTION_URL,
                           "prices_note": "Last IEX trade, previous session; IEX trades only.",
                           "fundamentals": "Source: SEC EDGAR"}}
    _write_json(os.path.join(a.out, "snapshot", "%s.json" % latest), snapshot, compact=True)
    quality["symbols"] = len(rows)
    quality["stale_count"] = len(quality["stale"])
    _write_json(os.path.join(a.out, "quality", "%s.json" % latest), quality)
    print("build_outputs: %s, %d symbols over %d sessions, %d stale, %d splits inferred today, %d large moves today"
          % (latest, len(rows), len(dates), quality["stale_count"], len(quality["splits_inferred_today"]),
             len(quality["large_moves_today"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
