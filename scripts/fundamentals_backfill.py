"""Session 8: bitemporal fundamentals backfill (spec s4/s5; rules R7/R8/R18/R19).

Scope: securities ever in_universe. Per security: income+balance+cashflow
quarterlies (~66q) fetched windowed with open-symbol priority (R18), rows
CIK-gated against the issuer (R19), merged per period, labeled at ingestion:
  accepted_date + R8 lag_class (missing/release/filing/delinquent)
  backfill=true, value_pit=false, timing_pit per R8 (verdict #2)
  source_hash over curated fields; raw JSONB keeps all three statements.
Resume: fund_ingest. Env: FUND_LIMIT=N trial. 6 workers.
"""
import hashlib
import json
import os
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.fmp_client import FMPClient
from factorlab.ingest import RDB

TODAY = date.today().isoformat()
QLIMIT = 66
MAPV = "m1"


def banner(t):
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8)


def dur_days(a, b):
    from datetime import date as D
    ya, ma, da = map(int, a.split("-"))
    yb, mb, db = map(int, b.split("-"))
    return (D(yb, mb, db) - D(ya, ma, da)).days


def lag_class(period_end, accepted):
    if not accepted:
        return "missing", False
    lag = dur_days(period_end, accepted[:10])
    if lag <= 10:
        return "missing", False        # R8: period-date-stuffed timestamp
    if lag <= 25:
        return "release", True
    if lag <= 200:
        return "filing", True
    return "delinquent", True


def g(row, *keys):
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return None


def fetch_stmts(c, wins, cik):
    """Windowed open-symbol-priority fetch of the three statements; CIK gate."""
    inc, bal, cf = {}, {}, {}
    n_reject = 0
    for k, (sym, vf, vt) in enumerate(wins):
        for logical, store in (("income_q", inc), ("balance_q", bal), ("cashflow_q", cf)):
            try:
                rows = c.get(logical, symbol=sym, limit=QLIMIT, allow_empty=True)
            except Exception:
                rows = []
            for r in rows:
                d = r.get("date")
                if not d:
                    continue
                if k > 0 and not (vf <= d <= vt):
                    continue                       # R18 window for closed symbols
                rcik = str(r.get("cik") or "").lstrip("0")
                if cik and rcik and rcik != cik:
                    n_reject += 1                  # R19 CIK gate
                    continue
                store.setdefault(d, r)             # open symbol wins
    return inc, bal, cf, n_reject


def build_rows(sec, inc, bal, cf):
    out = []
    for d, ir in inc.items():
        if d < "2009-06-01":
            continue
        br, cr = bal.get(d, {}), cf.get(d, {})
        accepted = ir.get("acceptedDate")
        lc, tpit = lag_class(d, accepted)
        curated = {
            "revenue": g(ir, "revenue"),
            "gross_profit": g(ir, "grossProfit"),
            "ebit": g(ir, "operatingIncome"),
            "net_income": g(ir, "netIncome"),
            "cfo": g(cr, "operatingCashFlow", "netCashProvidedByOperatingActivities"),
            "capex": g(cr, "capitalExpenditure"),
            "total_assets": g(br, "totalAssets"),
            "total_debt": g(br, "totalDebt"),
            "cash": g(br, "cashAndShortTermInvestments", "cashAndCashEquivalents"),
            "equity": g(br, "totalStockholdersEquity", "totalEquity"),
            "shares_dil": g(ir, "weightedAverageShsOutDil", "weightedAverageShsOut"),
        }
        h = hashlib.sha256(json.dumps(curated, sort_keys=True, default=str).encode()).hexdigest()[:16]
        out.append((sec, d, ir.get("period") or "Q?", 1,
                    accepted, (ir.get("filingDate") or None), True, False, tpit, lc,
                    None, h, MAPV, ir.get("reportedCurrency"),
                    curated["revenue"], curated["gross_profit"], curated["ebit"],
                    curated["net_income"], curated["cfo"], curated["capex"],
                    curated["total_assets"], curated["total_debt"], curated["cash"],
                    curated["equity"], curated["shares_dil"],
                    json.dumps({"income": ir, "balance": br, "cashflow": cr}, default=str)))
    return out


def main():
    c0 = FMPClient(min_interval=0.15)
    db = RDB()
    banner("0. TARGETS (ever in-universe)")
    rows = db.safe(lambda cur: (cur.execute("""
        SELECT sm.security_id, sm.symbol, sm.valid_from, sm.valid_to, i.cik
        FROM symbol_map sm
        JOIN securities s USING (security_id) JOIN issuers i USING (issuer_id)
        WHERE sm.security_id IN (SELECT DISTINCT security_id FROM universe_snapshots
                                 WHERE in_universe)"""), cur.fetchall())[1])
    by_sec = {}
    for sec, sym, vf, vt, cik in rows:
        e = by_sec.setdefault(sec, {"wins": [], "cik": str(cik or "").lstrip("0")})
        e["wins"].append((sym, str(vf), str(vt) if vt else TODAY))
    for e in by_sec.values():
        e["wins"].sort(key=lambda w: w[2], reverse=True)
    done = db.safe(lambda cur: (cur.execute("SELECT security_id FROM fund_ingest"),
                                {r[0] for r in cur.fetchall()})[1])
    todo = [(sec, e["wins"], e["cik"]) for sec, e in by_sec.items() if sec not in done]
    limit = int(os.environ.get("FUND_LIMIT", "0"))
    if limit:
        todo = todo[:limit]
    print("  ever-in-universe=%d done=%d processing now=%d" % (len(by_sec), len(done), len(todo)))

    banner("1. SWEEP")
    counts = defaultdict(int)
    lock = threading.Lock()
    prog = [0]

    def one(c, wdb, sec, wins, cik):
        inc, bal, cf, n_reject = fetch_stmts(c, wins, cik)
        frows = build_rows(sec, inc, bal, cf)

        def unit(cur):
            if frows:
                execute_values(cur, """INSERT INTO fundamentals_q
                    (security_id, fiscal_period_end, period, vintage_id, accepted_date,
                     filing_date, backfill, value_pit, timing_pit, lag_class, accession_no,
                     source_hash, mapping_version, currency, revenue, gross_profit, ebit,
                     net_income, cfo, capex, total_assets, total_debt, cash, equity,
                     shares_dil, raw) VALUES %s
                    ON CONFLICT (security_id, fiscal_period_end, vintage_id) DO NOTHING""",
                    frows, page_size=200)
            cur.execute("""INSERT INTO fund_ingest (security_id, n_periods, n_cik_reject)
                           VALUES (%s,%s,%s) ON CONFLICT (security_id) DO UPDATE
                           SET n_periods=EXCLUDED.n_periods,
                               n_cik_reject=EXCLUDED.n_cik_reject, ran_at=now()""",
                        (sec, len(frows), n_reject))
        wdb.safe(unit)
        if n_reject and not frows:
            return "cik-blocked"
        return "ok" if frows else "empty"

    def work(batch):
        wc = FMPClient(min_interval=0.15)
        wdb = RDB()
        for sec, wins, cik in batch:
            tag = "error"
            try:
                tag = one(wc, wdb, sec, wins, cik)
            except Exception as e:
                with lock:
                    print("  sec=%s %s ERROR %s" % (sec, [w[0] for w in wins][:2], str(e)[:80]))
            with lock:
                counts[tag] += 1
                prog[0] += 1
                if prog[0] % 200 == 0:
                    print("  ...%d/%d %s" % (prog[0], len(todo), dict(counts)))
        wdb.close()

    NW = 6
    batches = [todo[i::NW] for i in range(NW)]
    with ThreadPoolExecutor(max_workers=NW) as ex:
        list(ex.map(work, batches))
    print("  sweep done: %s" % dict(counts))

    banner("2. REPORT")
    def q(sql):
        return db.safe(lambda cur: (cur.execute(sql), cur.fetchall())[1])
    print("  fundamentals_q rows: %s across %s securities" % (
        q("SELECT count(*) FROM fundamentals_q")[0][0],
        q("SELECT count(DISTINCT security_id) FROM fundamentals_q")[0][0]))
    print("  lag_class: %s" % q("SELECT lag_class, count(*) FROM fundamentals_q GROUP BY 1 ORDER BY 2 DESC"))
    print("  timing_pit: %s" % q("SELECT timing_pit, count(*) FROM fundamentals_q GROUP BY 1"))
    print("  cik rejects: %s securities, %s rows total" % (
        q("SELECT count(*) FROM fund_ingest WHERE n_cik_reject > 0")[0][0],
        q("SELECT COALESCE(sum(n_cik_reject),0) FROM fund_ingest")[0][0]))
    print("  null rates: %s" % q("""SELECT
        round(100.0*count(*) FILTER (WHERE revenue IS NULL)/count(*),1) rev,
        round(100.0*count(*) FILTER (WHERE ebit IS NULL)/count(*),1) ebit,
        round(100.0*count(*) FILTER (WHERE cfo IS NULL)/count(*),1) cfo,
        round(100.0*count(*) FILTER (WHERE total_assets IS NULL)/count(*),1) assets,
        round(100.0*count(*) FILTER (WHERE equity IS NULL)/count(*),1) equity,
        round(100.0*count(*) FILTER (WHERE shares_dil IS NULL)/count(*),1) shares
        FROM fundamentals_q"""))
    print("  periods/security quartiles: %s" % q("""SELECT
        percentile_disc(0.25) WITHIN GROUP (ORDER BY n), percentile_disc(0.5) WITHIN GROUP (ORDER BY n),
        percentile_disc(0.75) WITHIN GROUP (ORDER BY n)
        FROM (SELECT count(*) n FROM fundamentals_q GROUP BY security_id) t"""))
    print("  KHC 2016 spot (expect NI 896/950/842/944, lag_class filing): %s" % q("""
        SELECT fiscal_period_end, period, net_income/1e6, lag_class FROM fundamentals_q f
        JOIN symbol_map sm ON sm.security_id=f.security_id AND sm.symbol='KHC'
        WHERE fiscal_period_end BETWEEN '2016-01-01' AND '2016-12-31' ORDER BY 1"""))
    db.close()


if __name__ == "__main__":
    main()
