# stocksense-pipeline

Nightly data pipeline for StockSense, a stock research web app for US-listed stocks.
It turns free public data into daily files for the app: prices, fundamentals, a
screener snapshot and a manifest of sources.

| Part | What it does |
|---|---|
| `iexparse/` (Go) | Streams an IEX HIST TOPS file (≈10 GB compressed per day) and reduces it to one daily bar per symbol. Nothing is stored on disk. |
| `stocksense_pipeline/` (Python, standard library only) | Universe from SEC's ticker list, fundamentals from SEC EDGAR XBRL company facts, ratios, and the day build. |
| `tests/` | Offline tests with small synthetic fixtures. |

## Run

```bash
cd iexparse && go build -o iexparse . && cd ..          # iexparse.exe on Windows
python -m stocksense_pipeline.build_day --date 2026-10-27 --no-sec
python -m unittest discover -s tests -t .
```

SEC requests need `SEC_USER_AGENT="Company Name contact@example.com"` (SEC's fair-access rule); without it use `--no-sec`.

## Data sources and terms

- **IEX HIST** — free historical market data from IEX. Prices are IEX trades only (not the consolidated tape). Anyone showing this data must cite **"Data provided for free by IEX"** with a link to [iextrading.com](https://iextrading.com/). Terms: <https://www.iex.io/products/market-data-connectivity/hist-terms>.
- **SEC EDGAR** — public filings; at most 10 requests a second with a declared User-Agent.

## Licences

Parts of `stocksense_pipeline/edgar.py` and the setup rules are ported from
[jakeschincariol/day-trader-skill](https://github.com/jakeschincariol/day-trader-skill) (MIT) — see `third_party/LICENSE.day-trader-skill`.
All other code © 2026 Sunny Sharma. All rights reserved.
