"""Session 1 probe: discover FMP's ACTUAL current surface before building
on it. Resolves every logical endpoint, extracts identity anchors, checks
statement timestamp fields on the KHC restatement sentinel, and runs price
semantics spot-checks on the AAPL 2020 split and COST 2023 special
dividend. Output is compact on purpose - paste it back."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.fmp_client import FMPClient, ART


def banner(t):
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8)


def main():
    c = FMPClient()
    report = {}

    banner("1. ENDPOINT RESOLUTION (candidate -> status / rows)")
    probe_kw = {
        "profile": {"symbol": "AAPL"},
        "symbol_change": {},
        "delisted": {"page": 0},
        "income_q": {"symbol": "AAPL", "limit": 4},
        "balance_q": {"symbol": "AAPL", "limit": 4},
        "cashflow_q": {"symbol": "AAPL", "limit": 4},
        "income_as_reported": {"symbol": "AAPL", "limit": 1},
        "prices_full": {"symbol": "AAPL", "date_from": "2024-01-02", "date_to": "2024-01-10"},
        "prices_unadjusted": {"symbol": "AAPL", "date_from": "2024-01-02", "date_to": "2024-01-10"},
        "prices_div_adjusted": {"symbol": "AAPL", "date_from": "2024-01-02", "date_to": "2024-01-10"},
        "dividends": {"symbol": "AAPL", "limit": 8},
        "splits": {"symbol": "AAPL"},
        "mktcap_hist": {"symbol": "AAPL", "limit": 5},
        "mna_search": {"name": "Activision"},
        "surprises": {"symbol": "AAPL"},
        "estimates": {"symbol": "AAPL", "limit": 4},
        "insider": {"symbol": "AAPL"},
        "inst_ownership": {"symbol": "AAPL"},
        "treasury": {},
    }
    surface = {}
    for logical, kw in probe_kw.items():
        rows = c.probe(logical, **kw)
        surface[logical] = rows
        for r in rows:
            mark = "OK " if r["ok"] and r["n"] else ("ok0" if r["ok"] else "FAIL")
            print("  %-20s %s %4s n=%-6s %s" % (logical, mark, r["status"], r["n"], r["path"]))
    report["surface"] = surface

    banner("2. IDENTITY ANCHORS (fields the security master keys on)")
    anchors = {}
    for sym in ["AAPL", "META", "BRK-B", "KHC", "BBBY", "GEV"]:
        try:
            p = c.get("profile", symbol=sym)[0]
            row = {k: p.get(k) for k in
                   ["symbol", "cik", "isin", "cusip", "exchangeShortName", "exchange",
                    "ipoDate", "isActivelyTrading", "currency", "sector"]}
            anchors[sym] = row
            print("  %-6s cik=%s isin=%s cusip=%s active=%s ipo=%s" % (
                sym, row.get("cik"), row.get("isin"), row.get("cusip"),
                row.get("isActivelyTrading"), row.get("ipoDate")))
        except Exception as e:
            anchors[sym] = {"err": str(e)[:100]}
            print("  %-6s ERR %s" % (sym, e))
    report["anchors"] = anchors

    banner("3. TICKER-CHANGE FEED (looking for FB->META and SQ->XYZ)")
    changes = {"hits": []}
    try:
        rows = c.get("symbol_change")
        print("  feed rows: %d; first-row keys: %s" % (len(rows), sorted(rows[0].keys())))
        for r in rows:
            blob = json.dumps(r).upper()
            if '"FB"' in blob or "META" in blob or '"SQ"' in blob or '"XYZ"' in blob:
                changes["hits"].append(r)
        for h in changes["hits"][:6]:
            print("  hit: %s" % h)
        print("  total hits: %d" % len(changes["hits"]))
        changes["n"] = len(rows)
    except Exception as e:
        changes["err"] = str(e)[:120]
        print("  ERR %s" % e)
    report["symbol_change"] = changes

    banner("4. DELISTED FEED (scanning pages for BBBY)")
    delist = {"found": None, "pages_scanned": 0}
    try:
        for page in range(10):
            rows = c.get("delisted", page=page, allow_empty=True)
            if not rows:
                break
            delist["pages_scanned"] = page + 1
            if page == 0:
                print("  page0 rows: %d; keys: %s" % (len(rows), sorted(rows[0].keys())))
            hit = [r for r in rows if r.get("symbol") == "BBBY"]
            if hit:
                delist["found"] = hit[0]
                print("  BBBY: %s" % hit[0])
                break
        if not delist["found"]:
            print("  BBBY not in %d pages scanned (fine - note the page depth)" % delist["pages_scanned"])
    except Exception as e:
        delist["err"] = str(e)[:120]
        print("  ERR %s" % e)
    report["delisted"] = delist

    banner("5. STATEMENT TIMESTAMPS (KHC - restatement sentinel)")
    stmts = {}
    try:
        rows = c.get("income_q", symbol="KHC", limit=12)
        keys = sorted(rows[0].keys())
        ts = [k for k in keys if any(w in k.lower() for w in ("date", "filing", "filling", "accepted"))]
        print("  standardized: %d keys; timestampish -> %s" % (len(keys), ts))
        for r in rows[:8]:
            print("    %s  period=%s  filed=%s  accepted=%s  cik=%s" % (
                r.get("date"), r.get("period"),
                r.get("fillingDate") or r.get("filingDate"),
                r.get("acceptedDate"), r.get("cik")))
        stmts["ts_fields"] = ts
        ar = c.get("income_as_reported", symbol="KHC", limit=2)
        ark = sorted(ar[0].keys())
        print("  as-reported row0: %d keys; sample: %s" % (len(ark), ark[:12]))
        print("  as-reported timestampish: %s" %
              [k for k in ark if any(w in k.lower() for w in ("date", "period", "accepted"))])
        stmts["as_reported_nkeys"] = len(ark)
    except Exception as e:
        stmts["err"] = str(e)[:150]
        print("  ERR %s" % e)
    report["statements"] = stmts

    def show(logical, sym, f, t, label):
        try:
            rows = c.get(logical, symbol=sym, date_from=f, date_to=t)
            rows = sorted(rows, key=lambda r: r.get("date", ""))
            print("  [%s] keys: %s" % (label, sorted(rows[0].keys())[:10]))
            for r in rows:
                print("    %s  close=%s  adjClose=%s" % (r.get("date"), r.get("close"), r.get("adjClose")))
            return [{k: r.get(k) for k in ("date", "open", "close", "adjClose", "volume")} for r in rows]
        except Exception as e:
            print("  [%s] ERR %s" % (label, e))
            return {"err": str(e)[:120]}

    px = {}
    banner("6. PRICE SEMANTICS - AAPL 4:1 split window (2020-08-24 .. 09-04)")
    px["split_full"] = show("prices_full", "AAPL", "2020-08-24", "2020-09-04", "prices_full")
    px["split_unadj"] = show("prices_unadjusted", "AAPL", "2020-08-24", "2020-09-04", "prices_unadjusted")
    px["split_divadj"] = show("prices_div_adjusted", "AAPL", "2020-08-24", "2020-09-04", "prices_div_adjusted")
    print("  READ: pre-split close ~499 => series is UNadjusted; ~124 => split-adjusted.")

    banner("7. PRICE SEMANTICS - COST special dividend window (2023-12-20 .. 2024-01-08)")
    px["special_full"] = show("prices_full", "COST", "2023-12-20", "2024-01-08", "prices_full")
    px["special_divadj"] = show("prices_div_adjusted", "COST", "2023-12-20", "2024-01-08", "prices_div_adjusted")
    try:
        divs = c.get("dividends", symbol="COST", limit=60)
        big = [d for d in divs if float(d.get("dividend") or d.get("adjDividend") or 0) > 5][:3]
        print("  COST large divs on feed: %s" % [
            {k: d.get(k) for k in ("date", "recordDate", "paymentDate", "dividend", "adjDividend")} for d in big])
        px["cost_specials"] = big
    except Exception as e:
        print("  dividends ERR %s" % e)
    print("  READ: close gaps ~-$15 across the ex-date while adjClose stays smooth => adjClose is dividend-adjusted.")

    report["price_semantics"] = px
    out = ART / "endpoint_surface.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    banner("DONE -> %s" % out)


if __name__ == "__main__":
    main()
