import json
import os
import shutil
import tempfile
import unittest

from stocksense_pipeline import build_day, edgar, iex, universe

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return json.load(fh)


class UniverseTest(unittest.TestCase):
    def test_filters_exchanges_indian_issuers_and_duplicates(self):
        rows = universe.parse_sec_tickers(load("company_tickers_exchange.json"))
        uni, excluded = universe.build_universe(rows, incorporation={1000005: "K7"})
        self.assertEqual([r["symbol"] for r in uni], ["ACME", "BRK.B"])
        self.assertEqual(excluded["INFY"], "indian_adr")
        self.assertEqual(excluded["BHRT"], "incorporated_india")
        self.assertEqual(excluded["PINK"], "exchange:OTC")

    def test_symbol_normalisation(self):
        self.assertEqual(universe.normalise_symbol(" brk-b "), "BRK.B")


class EdgarTest(unittest.TestCase):
    def setUp(self):
        self.facts = load("CIK0001000001.json")

    def test_quarters_derive_q4_and_restatement_wins(self):
        s = edgar.flow_series(self.facts, "revenue")
        self.assertEqual([v["value"] for v in s.values()], [100, 110, 120, 130, 145])
        self.assertTrue(s["2025-12-31"]["derived"])

    def test_summary_and_ratios(self):
        summary = edgar.summarise(self.facts)
        self.assertEqual(summary["ttm"]["revenue"], 505)
        self.assertEqual(summary["ttm"]["net_income"], 55)
        self.assertEqual(summary["balance"]["equity"]["value"], 500)
        self.assertEqual(summary["shares_outstanding"], 10)
        r = edgar.ratios(summary, 11.0)
        self.assertEqual(r["market_cap"], 110)
        self.assertEqual(r["pe"], 2.0)
        self.assertEqual(r["ps"], 0.22)
        self.assertEqual(r["roe_pct"], 11.0)
        self.assertEqual(r["debt_to_equity"], 0.5)
        self.assertEqual(r["net_margin_pct"], 10.9)

    def test_ratios_without_price(self):
        r = edgar.ratios(edgar.summarise(self.facts), None)
        self.assertIsNone(r["market_cap"])
        self.assertIsNone(r["pe"])


class IexTest(unittest.TestCase):
    def test_tops_entry_picks_latest_version(self):
        index = [{"feed": "DEEP", "version": "1.0", "link": "d"},
                 {"feed": "TOPS", "version": "1.5", "link": "old"},
                 {"feed": "TOPS", "version": "1.6", "link": "new"}]
        self.assertEqual(iex.tops_file("20261027", index)["link"], "new")
        self.assertIsNone(iex.tops_file("20261025", []))

    def test_read_bars_drops_test_securities_and_filters_universe(self):
        bars = iex.read_bars(os.path.join(FIX, "bars.csv"))
        self.assertNotIn("ZIEXT", bars)
        self.assertTrue(bars["SPY"]["etp"])
        bars = iex.read_bars(os.path.join(FIX, "bars.csv"), {"ACME", "BRK.B"})
        self.assertEqual(sorted(bars), ["ACME", "BRK.B"])


class BuildDayTest(unittest.TestCase):
    def test_offline_end_to_end(self):
        out = tempfile.mkdtemp()
        try:
            rc = build_day.main(["--date", "2026-10-27", "--bars", os.path.join(FIX, "bars.csv"),
                                 "--offline", FIX, "--out", out])
            self.assertEqual(rc, 0)
            snap = json.load(open(os.path.join(out, "snapshot", "2026-10-27.json"), encoding="utf-8"))
            by = {r["symbol"]: r for r in snap}
            self.assertEqual(set(by), {"ACME", "BRK.B"})
            self.assertEqual(by["ACME"]["price"], 11.0)
            self.assertEqual(by["ACME"]["pe"], 2.0)
            manifest = json.load(open(os.path.join(out, "manifest", "2026-10-27.json"), encoding="utf-8"))
            self.assertEqual(manifest["sources"]["prices"]["attribution"], "Data provided for free by IEX")
            self.assertEqual(manifest["sec_failures"], 1)  # BRK.B has no fixture file
            self.assertNotIn("INFY", open(os.path.join(out, "prices", "2026-10-27.csv"), encoding="utf-8").read())
        finally:
            shutil.rmtree(out)


if __name__ == "__main__":
    unittest.main()
