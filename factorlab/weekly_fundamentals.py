"""Atomic weekly vintage inserts. No client construction or I/O at import.

Preserves the existing m1 hash and R8 classification. A live-caught vintage is
not proof of historical availability at its vendor filing time; observed-at
consumer enforcement remains a separate release blocker.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date


INSERT_SQL = """
INSERT INTO fundamentals_q (
    security_id, fiscal_period_end, period, vintage_id, accepted_date,
    filing_date, backfill, value_pit, timing_pit, lag_class, source_hash,
    mapping_version, currency, revenue, gross_profit, ebit, net_income,
    cfo, capex, total_assets, total_debt, cash, equity, shares_dil, raw
) VALUES (
    %(security_id)s, %(fiscal_period_end)s, %(period)s, %(vintage_id)s,
    %(accepted_date)s, %(filing_date)s, false, true, %(timing_pit)s,
    %(lag_class)s, %(source_hash)s, 'm1', %(currency)s, %(revenue)s,
    %(gross_profit)s, %(ebit)s, %(net_income)s, %(cfo)s, %(capex)s,
    %(total_assets)s, %(total_debt)s, %(cash)s, %(equity)s, %(shares_dil)s,
    %(raw)s
) RETURNING vintage_id
"""


def _first(row: dict, *keys: str):
    return next((row[k] for k in keys if row.get(k) is not None), None)


def curate(ir: dict, br: dict, cr: dict) -> dict:
    return {
        "revenue": _first(ir, "revenue"),
        "gross_profit": _first(ir, "grossProfit"),
        "ebit": _first(ir, "operatingIncome"),
        "net_income": _first(ir, "netIncome"),
        "cfo": _first(cr, "operatingCashFlow", "netCashProvidedByOperatingActivities"),
        "capex": _first(cr, "capitalExpenditure"),
        "total_assets": _first(br, "totalAssets"),
        "total_debt": _first(br, "totalDebt"),
        "cash": _first(br, "cashAndShortTermInvestments", "cashAndCashEquivalents"),
        "equity": _first(br, "totalStockholdersEquity", "totalEquity"),
        "shares_dil": _first(ir, "weightedAverageShsOutDil", "weightedAverageShsOut"),
    }


def curated_hash(values: dict) -> str:
    # Keep the frozen m1 algorithm, including original JSON separators.
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str)
                          .encode()).hexdigest()[:16]


def persist_period(db, security_id: int, period_end: str,
                   ir: dict, br: dict, cr: dict) -> str:
    values = curate(ir, br, cr)
    fingerprint = curated_hash(values)
    accepted = ir.get("acceptedDate")
    lag = ((date.fromisoformat(accepted[:10]) - date.fromisoformat(period_end)).days
           if accepted else None)
    timing_pit = lag is not None and lag > 10
    lag_class = ("missing" if not timing_pit else "release" if lag <= 25 else
                 "filing" if lag <= 200 else "delinquent")

    def unit(cur):
        # Serializes writers using this path, not arbitrary legacy writers.
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (1179403349, security_id))
        cur.execute("""SELECT vintage_id, source_hash FROM fundamentals_q
                       WHERE security_id=%s AND fiscal_period_end=%s
                       ORDER BY vintage_id DESC LIMIT 1""", (security_id, period_end))
        previous = cur.fetchone()
        if previous and previous[1] == fingerprint:
            return "unchanged"
        params = dict(values, security_id=security_id, fiscal_period_end=period_end,
                      period=ir.get("period") or "Q?",
                      vintage_id=previous[0] + 1 if previous else 1,
                      accepted_date=accepted, filing_date=ir.get("filingDate"),
                      timing_pit=timing_pit, lag_class=lag_class,
                      source_hash=fingerprint, currency=ir.get("reportedCurrency"),
                      raw=json.dumps({"income": ir, "balance": br, "cashflow": cr},
                                     default=str))
        cur.execute(INSERT_SQL, params)
        if cur.fetchone() != (params["vintage_id"],):
            raise RuntimeError("vintage_insert_not_confirmed")
        return "live_restatements" if previous else "new_vintages"

    return db.atomic(unit)  # Return counters only after a successful commit.


def ingest_security(client, db, security_id: int, symbol: str, cik) -> dict:
    expected_cik = str(cik or "").lstrip("0")
    statements = {}
    for logical in ("income_q", "balance_q", "cashflow_q"):
        rows = client.get(logical, symbol=symbol, limit=2, allow_empty=True)
        if not isinstance(rows, list):
            raise ValueError("statement_response_not_list")
        for row in rows:
            if not isinstance(row, dict) or not row.get("date"):
                raise ValueError("statement_date_missing")
            period_end = row["date"]
            date.fromisoformat(period_end)
            actual_cik = str(row.get("cik") or "").lstrip("0")
            if expected_cik and actual_cik and actual_cik != expected_cik:
                raise ValueError("statement_identity_conflict")
            by = statements.setdefault(period_end, {})
            if logical in by and by[logical] != row:
                raise ValueError("duplicate_statement_conflict")
            by[logical] = row
    outcomes = {"new_vintages": 0, "live_restatements": 0, "unchanged": 0,
                "incomplete": 0, "nodata": 0}
    if not statements:
        outcomes["nodata"] = 1
    for period_end, by in sorted(statements.items()):
        if set(by) != {"income_q", "balance_q", "cashflow_q"}:
            outcomes["incomplete"] += 1
            continue
        tag = persist_period(db, security_id, period_end, by["income_q"],
                             by["balance_q"], by["cashflow_q"])
        outcomes[tag] += 1
    return outcomes
