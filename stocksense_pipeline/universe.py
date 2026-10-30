"""The stock universe: US-listed common stocks and ADRs from SEC's ticker list.

Excluded (docs/07 §4, docs/08 BRL-09): exchanges other than NYSE / Nasdaq /
NYSE American / Cboe, and companies incorporated in India (EDGAR state code
K7) or on the manual list of US-listed ADRs of Indian companies.
"""

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"

ALLOWED_EXCHANGES = {"NYSE", "Nasdaq", "NYSE American", "NYSE MKT", "CBOE", "Cboe"}

# EDGAR uses its own country codes for state of incorporation; India is K7.
INDIA_INCORPORATION_CODES = {"K7"}

# Indian issuers listed in the US as ADRs. Kept by hand because some are
# incorporated outside India (e.g. via Mauritius or Jersey). Review with counsel.
INDIAN_ADRS = {"INFY", "WIT", "HDB", "IBN", "RDY", "SIFY", "WNS", "YTRA", "MMYT"}


def normalise_symbol(sym):
    """SEC writes share classes with '-' (BRK-B), IEX with '.' (BRK.B)."""
    return sym.strip().upper().replace("-", ".")


def parse_sec_tickers(payload):
    """company_tickers_exchange.json → list of dicts {cik, name, symbol, exchange}."""
    fields = payload.get("fields", [])
    idx = {name: i for i, name in enumerate(fields)}
    out = []
    for row in payload.get("data", []):
        out.append({
            "cik": int(row[idx["cik"]]),
            "name": row[idx["name"]],
            "symbol": normalise_symbol(row[idx["ticker"]]),
            "exchange": row[idx["exchange"]] or "",
        })
    return out


def build_universe(sec_rows, incorporation=None):
    """Filter SEC rows to the app universe.

    incorporation: optional {cik: EDGAR stateOfIncorporation code}.
    Returns (universe, excluded) where excluded maps symbol → reason.
    """
    incorporation = incorporation or {}
    universe, excluded, seen = [], {}, set()
    for r in sec_rows:
        sym = r["symbol"]
        if sym in seen:
            continue
        seen.add(sym)
        if r["exchange"] not in ALLOWED_EXCHANGES:
            excluded[sym] = "exchange:%s" % (r["exchange"] or "none")
        elif sym in INDIAN_ADRS:
            excluded[sym] = "indian_adr"
        elif incorporation.get(r["cik"]) in INDIA_INCORPORATION_CODES:
            excluded[sym] = "incorporated_india"
        else:
            universe.append(r)
    universe.sort(key=lambda r: r["symbol"])
    return universe, excluded


SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK%010d.json"


def fetch_universe(fetcher):
    return parse_sec_tickers(fetcher.json(SEC_TICKERS_URL, "company_tickers_exchange.json"))


def incorporation_codes(fetcher, ciks, cache):
    """{cik: stateOfIncorporation} using a cache dict (str(cik) → code or "").

    Unknown or unreachable companies get "" and stay in the universe; the
    manual INDIAN_ADRS list still applies to them.
    """
    from .http import FetchError

    out = {}
    for cik in ciks:
        key = str(cik)
        if key not in cache:
            try:
                sub = fetcher.json(SUBMISSIONS_URL % cik, "submissions_CIK%010d.json" % cik)
                cache[key] = (sub.get("stateOfIncorporation") or "").upper()
            except FetchError:
                cache[key] = ""
        out[cik] = cache[key]
    return out
