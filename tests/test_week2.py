import datetime as dt
import json
import os
import shutil
import tempfile
import unittest

import io
import urllib.error
from unittest import mock

from stocksense_pipeline import backfill, build_outputs, features, history, iex
from stocksense_pipeline.http import FetchError

def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def write_day(prices_dir, date, rows):
    os.makedirs(prices_dir, exist_ok=True)
    with open(os.path.join(prices_dir, "%s.csv" % date), "w", encoding="utf-8") as fh:
        fh.write("symbol,open,high,low,close,volume,trades,odd_lot_only,etp\n")
        for sym, close, vol in rows:
            fh.write("%s,%s,%s,%s,%s,%d,1,0,0\n" % (sym, close, close, close, close, vol))


class BackfillTest(unittest.TestCase):
    def test_weekdays_and_shards(self):
        days = backfill.weekdays_back(dt.date(2026, 10, 27), 7)  # Tue
        self.assertEqual(days[0], dt.date(2026, 10, 19))
        self.assertEqual(days[-1], dt.date(2026, 10, 27))
        self.assertTrue(all(d.weekday() < 5 for d in days))
        parts = [backfill.shard(days, i, 3) for i in range(3)]
        self.assertEqual(sorted(sum(parts, [])), days)

    def test_resumable_and_holidays(self):
        out = tempfile.mkdtemp()
        calls = []

        def fetch_index(compact):
            return None if compact == "20261026" else {"link": compact}

        def parse(link, iso, bars_csv):
            calls.append(iso)
            shutil.copy(os.path.join(FIX, "bars.csv"), bars_csv)
            return "ok"

        try:
            write_day(os.path.join(out, "prices"), "2026-10-23", [("ACME", 10, 100)])
            backfill.main(["--end", "2026-10-27", "--sessions", "3", "--out", out], fetch_index, parse)
            self.assertEqual(calls, ["2026-10-27"])  # 23rd existed, 26th had no file
            self.assertTrue(os.path.isfile(os.path.join(out, "prices", "2026-10-27.csv")))
            self.assertFalse(os.path.exists(os.path.join(out, "raw", "2026-10-27_bars.csv")))
        finally:
            shutil.rmtree(out)

    def test_one_bad_day_does_not_stop_the_shard(self):
        out = tempfile.mkdtemp()
        calls = []

        def fetch_index(compact):
            if compact == "20261026":
                raise FetchError("timeout")
            return {"link": compact}

        def parse(link, iso, bars_csv):
            calls.append(iso)
            shutil.copy(os.path.join(FIX, "bars.csv"), bars_csv)
            return "ok"

        try:
            rc = backfill.main(["--end", "2026-10-27", "--sessions", "3", "--out", out], fetch_index, parse)
            self.assertEqual(rc, 1)  # reported, but the other days were still built
            self.assertEqual(calls, ["2026-10-23", "2026-10-27"])
        finally:
            shutil.rmtree(out)

    def test_holiday_404_means_no_file(self):
        err = urllib.error.HTTPError(iex.HIST_INDEX_URL % "20260703", 404, "Not Found", {}, io.BytesIO(b""))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertIsNone(iex.tops_file("20260703"))
        err500 = urllib.error.HTTPError("u", 500, "Server Error", {}, io.BytesIO(b""))
        with mock.patch("urllib.request.urlopen", side_effect=err500):
            with self.assertRaises(FetchError):
                iex.tops_file("20260703")


class HistoryTest(unittest.TestCase):
    def test_carry_forward_when_no_trade(self):
        days = {"2026-10-01": {"A": {"close": "10", "volume": "5"}},
                "2026-10-02": {},
                "2026-10-03": {"A": {"close": "11", "volume": "6"}, "B": {"close": "3", "volume": "1"}}}
        s = history.series_by_symbol(days)
        self.assertEqual(s["A"]["close"], [10.0, 10.0, 11.0])
        self.assertEqual(s["A"]["no_trade"], [False, True, False])
        self.assertEqual(s["B"]["dates"], ["2026-10-03"])

    def test_split_inferred_and_large_move_flagged(self):
        s = {"dates": ["d1", "d2", "d3", "d4"], "close": [200.0, 202.0, 101.5, 160.0],
             "volume": [100, 100, 200, 200], "no_trade": [False] * 4}
        close, volume, events = history.adjust(s)
        self.assertEqual(close[:3], [100.0, 101.0, 101.5])
        self.assertEqual(volume[:2], [200, 200])
        self.assertEqual(events[0]["type"], "split_inferred")
        self.assertEqual(events[0]["factor"], 2)
        self.assertEqual(events[1]["type"], "large_move")

    def test_match_split(self):
        self.assertEqual(history.match_split(10.1), 10)
        self.assertAlmostEqual(history.match_split(0.333), 1 / 3)
        self.assertIsNone(history.match_split(1.7))


class FeaturesTest(unittest.TestCase):
    def test_rsi_extremes(self):
        self.assertEqual(features.rsi([float(i) for i in range(1, 30)]), 100.0)
        self.assertLess(features.rsi([float(i) for i in range(60, 30, -1)]), 1)

    def test_setups(self):
        rising = [100.0] * 25 + [101, 102, 103, 104, 110]
        self.assertIn("breakout_20d", features.find_setups(rising))
        falling = [float(x) for x in range(100, 60, -1)]
        self.assertIn("oversold", features.find_setups(falling))
        self.assertIn("oversold_bounce", features.find_setups(falling + [falling[-1] + 0.5]))

    def test_history_30d(self):
        closes = [100 * (1.001 ** i) for i in range(120)]
        h = features.history_30d(closes)
        self.assertEqual(h["pct_up"], 100.0)
        self.assertEqual(h["windows"], 90)
        self.assertIsNone(features.history_30d(closes[:40]))

    def test_compute_fields(self):
        closes = [50 + (i % 7) for i in range(260)]
        f = features.compute(closes, [1000] * 260)
        self.assertEqual(f["sessions"], 260)
        self.assertEqual(f["vol_vs_20d"], 1.0)
        self.assertIsNotNone(f["above_sma200"])


class BuildOutputsTest(unittest.TestCase):
    def test_snapshot_history_and_quality(self):
        out = tempfile.mkdtemp()
        try:
            prices = os.path.join(out, "prices")
            start = dt.date(2026, 6, 1)
            d, n = start, 0
            while n < 60:
                if d.weekday() < 5:
                    rows = [("UP", 10 + n * 0.1, 1000)]
                    if n < 50:
                        rows.append(("GONE", 5, 10))
                    rows.append(("SPLT", 100 if n < 59 else 50.5, 100))
                    write_day(prices, d.isoformat(), rows)
                    n += 1
                d += dt.timedelta(days=1)
            self.assertEqual(build_outputs.main(["--out", out]), 0)
            latest = sorted(os.listdir(prices))[-1][:-4]
            snap = read_json(os.path.join(out, "snapshot", "%s.json" % latest))
            by = {r[0]: dict(zip(snap["columns"], r)) for r in snap["rows"]}
            self.assertTrue(by["GONE"]["no_iex_trade"])
            self.assertGreater(by["UP"]["chg_1m"], 0)
            hist = read_json(os.path.join(out, "history", "SPLT.json"))
            self.assertEqual(hist["close"][0], 50.0)  # adjusted for the inferred 2:1 split
            q = read_json(os.path.join(out, "quality", "%s.json" % latest))
            self.assertEqual(q["stale"][0]["symbol"], "GONE")
            self.assertEqual(q["splits_inferred_today"][0]["symbol"], "SPLT")
        finally:
            shutil.rmtree(out)


if __name__ == "__main__":
    unittest.main()
