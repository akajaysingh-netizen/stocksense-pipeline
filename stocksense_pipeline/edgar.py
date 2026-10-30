"""Company fundamentals from SEC EDGAR XBRL company facts.

Quarterly logic (restatements win, Q4 derived from the annual figure minus
three quarters) ported from trader-analyst/facts.py,
github.com/jakeschincariol/day-trader-skill (MIT, see third_party/).
"""

import datetime as _dt

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK%010d.json"

# Flow metrics (three-month values per quarter).
FLOWS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "RevenuesNetOfInterestExpense", "RevenueFromContractWithCustomerIncludingAssessedTax",
                "SalesRevenueNet", "SalesRevenueGoodsNet", "SalesRevenueServicesNet"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "eps_diluted": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
    "dividends_per_share": ["CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"],
}

# Point-in-time balances (latest value).
INSTANTS = {
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
}

PER_SHARE = {"eps_diluted", "dividends_per_share"}
FORMS = ("10-Q", "10-K", "20-F", "40-F")


def _date(s):
    return _dt.date.fromisoformat(s)


def _units(companyfacts, taxonomy, concept, unit):
    node = companyfacts.get("facts", {}).get(taxonomy, {}).get(concept)
    if not node:
        return []
    units = node.get("units", {})
    return units.get(unit) or (units.get(next(iter(units))) if units else []) or []


def _periods(facts):
    """{(start, end): fact} from periodic reports; the latest filing wins."""
    latest = {}
    for f in facts:
        if "start" not in f or f.get("form", "").split("/")[0] not in FORMS:
            continue
        key = (f["start"], f["end"])
        stamp = (f.get("filed", ""), f.get("accn", ""))
        if key not in latest or stamp > (latest[key].get("filed", ""), latest[key].get("accn", "")):
            latest[key] = f
    return latest


def quarterly(facts, derive_q4=True):
    """{end: value} three-month values, deriving Q4 = year − Q1−Q3 where needed."""
    q, years = {}, []
    for (start, end), f in _periods(facts).items():
        days = (_date(end) - _date(start)).days
        if 80 <= days <= 100:
            q[end] = {"start": start, "value": f["val"], "derived": False, "filed": f.get("filed")}
        elif 350 <= days <= 380:
            years.append((start, end, f))
    if derive_q4:
        for start, end, f in years:
            if end in q:
                continue
            inside = sorted((v for k, v in q.items() if v["start"] >= start and k < end), key=lambda v: v["start"])
            if len(inside) == 3:
                q[end] = {"start": start, "value": f["val"] - sum(v["value"] for v in inside),
                          "derived": True, "filed": f.get("filed")}
    return q


def flow_series(companyfacts, key, derive_q4=True):
    unit = "USD/shares" if key in PER_SHARE else "USD"
    merged = {}
    for concept in FLOWS[key]:
        for end, v in quarterly(_units(companyfacts, "us-gaap", concept, unit), derive_q4 and key not in PER_SHARE).items():
            merged.setdefault(end, v)
    return dict(sorted(merged.items()))


def latest_instant(companyfacts, key):
    best = None
    for concept in INSTANTS[key]:
        for f in _units(companyfacts, "us-gaap", concept, "USD"):
            if "start" in f or f.get("form", "").split("/")[0] not in FORMS:
                continue
            stamp = (f["end"], f.get("filed", ""))
            if best is None or stamp > best[0]:
                best = (stamp, f["val"])
        if best:
            break
    return best[1] if best else None, best[0][0] if best else None


def shares_outstanding(companyfacts):
    facts = _units(companyfacts, "dei", "EntityCommonStockSharesOutstanding", "shares")
    if not facts:
        return None
    f = max(facts, key=lambda f: (f.get("end", ""), f.get("filed", "")))
    return f["val"]


def ttm(series):
    vals = [v["value"] for v in list(series.values())[-4:]]
    return sum(vals) if len(vals) == 4 else None


def summarise(companyfacts, quarters=8):
    """Everything the app shows about a company's filings, as plain JSON."""
    out = {"cik": companyfacts.get("cik"), "name": companyfacts.get("entityName"), "quarters": {}, "ttm": {}, "balance": {}}
    for key in FLOWS:
        s = flow_series(companyfacts, key)
        items = list(s.items())[-quarters:]
        out["quarters"][key] = [{"end": end, "value": v["value"], "derived": v["derived"]} for end, v in items]
        out["ttm"][key] = ttm(s)
    for key in INSTANTS:
        val, end = latest_instant(companyfacts, key)
        out["balance"][key] = {"value": val, "as_of": end}
    out["shares_outstanding"] = shares_outstanding(companyfacts)
    return out


def ratios(summary, price):
    """Price-based and quality ratios computed in-house from filings + the IEX price."""
    t, b = summary["ttm"], summary["balance"]
    shares = summary.get("shares_outstanding")
    mcap = price * shares if price and shares else None
    eq = b["equity"]["value"]
    debt = b["long_term_debt"]["value"]
    rev = t.get("revenue")
    ni = t.get("net_income")

    def div(a, c):
        return round(a / c, 2) if a is not None and c not in (None, 0) else None

    return {
        "market_cap": round(mcap) if mcap else None,
        "pe": div(mcap, ni) if ni and ni > 0 else None,
        "ps": div(mcap, rev),
        "pb": div(mcap, eq) if eq and eq > 0 else None,
        "roe_pct": round(100 * ni / eq, 1) if ni is not None and eq and eq > 0 else None,
        "debt_to_equity": div(debt, eq) if eq and eq > 0 else None,
        "net_margin_pct": round(100 * ni / rev, 1) if ni is not None and rev else None,
    }
