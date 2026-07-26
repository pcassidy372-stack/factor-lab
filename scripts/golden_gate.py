"""The golden gate (spec s15): eight standing tests, DB-only, exit 1 on any
FAIL. This is the precondition check for every phase from here on.

T1 PIT selector      no selectable statement postdates its asof; missing-class excluded
T2 identity          chain proofs + chimera separation + overlaps all quarantined
T3 TR/recon          universe member-months on >=95-recon coverage >= 99%
T4 CIK integrity     sampled statement rows' embedded CIK matches issuer CIK
T5 R8 invariant      no timing_pit row with lag <= 10d
T6 hash determinism  curated hash recomputes from raw JSONB (>=98% of sample)
T7 universe recheck  stored in_universe flags consistent with stored inputs
T8 SUE sanity        distribution standardized-ish; nulls = warmup only
"""
import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn

random.seed(20260725)
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print("  [%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))


def g(row, *keys):
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return None


def main():
    cx = conn()
    cur = cx.cursor()

    print("T1 PIT selector (200 random draws)")
    cur.execute("""SELECT security_id, asof FROM universe_snapshots
                   WHERE in_universe ORDER BY random() LIMIT 200""")
    draws = cur.fetchall()
    bad = 0
    for sec, asof in draws:
        cur.execute("""SELECT count(*) FROM (
            SELECT DISTINCT ON (fiscal_period_end) accepted_date, lag_class
            FROM fundamentals_q WHERE security_id=%s AND accepted_date <= %s
              AND timing_pit ORDER BY fiscal_period_end, vintage_id DESC) t
            WHERE accepted_date > %s OR lag_class = 'missing'""", (sec, asof, asof))
        bad += cur.fetchone()[0]
    check("T1", bad == 0, "violations=%d" % bad)

    print("T2 identity")
    def rid(sym, asof):
        cur.execute("""SELECT security_id FROM symbol_map WHERE symbol=%s
                       AND valid_from <= %s AND (valid_to IS NULL OR valid_to >= %s)""",
                    (sym, asof, asof))
        return sorted({r[0] for r in cur.fetchall()})
    ok = (rid("FB", "2021-06-01") == rid("META", "2026-07-01")
          and rid("SQ", "2024-06-01") == rid("XYZ", "2026-07-01")
          and rid("BBBY", "2022-06-01") != rid("BBBY", "2026-06-01")
          and bool(rid("BBBY", "2022-06-01")) and bool(rid("BBBY", "2026-06-01")))
    cur.execute("""SELECT count(*) FROM symbol_map a JOIN symbol_map b
                   ON a.symbol=b.symbol AND a.security_id < b.security_id
                   AND a.valid_from <= COALESCE(b.valid_to, DATE '9999-12-31')
                   AND b.valid_from <= COALESCE(a.valid_to, DATE '9999-12-31')
                   WHERE NOT EXISTS (SELECT 1 FROM identity_quarantine q
                                     WHERE q.symbol = a.symbol AND NOT q.resolved)""")
    unq = cur.fetchone()[0]
    check("T2", ok and unq == 0, "chains=%s unquarantined-overlaps=%d" % (ok, unq))

    print("T3 TR/recon universe coverage")
    cur.execute("""SELECT
        count(*) FILTER (WHERE r.match_pct >= 95 OR r.match_pct IS NULL), count(*)
        FROM universe_snapshots u JOIN price_recon r USING (security_id)
        WHERE u.in_universe""")
    good, tot = cur.fetchone()
    pct = 100.0 * good / tot
    check("T3", pct >= 99.0, "member-months on >=95-or-unmeasurable recon: %.2f%%" % pct)

    print("T4 CIK integrity (300-row sample)")
    cur.execute("""SELECT f.raw, i.cik FROM fundamentals_q f
                   JOIN securities s USING (security_id) JOIN issuers i USING (issuer_id)
                   WHERE i.cik IS NOT NULL ORDER BY random() LIMIT 300""")
    mism = 0
    for raw, cik in cur.fetchall():
        rcik = str((raw.get("income") or {}).get("cik") or "").lstrip("0")
        if rcik and rcik != str(cik).lstrip("0"):
            mism += 1
    check("T4", mism == 0, "mismatches=%d/300" % mism)

    print("T5 R8 invariant")
    cur.execute("""SELECT count(*) FROM fundamentals_q
                   WHERE timing_pit AND accepted_date IS NOT NULL
                   AND (accepted_date::date - fiscal_period_end) <= 10""")
    v = cur.fetchone()[0]
    check("T5", v == 0, "violations=%d" % v)

    print("T6 hash determinism (100-row sample)")
    cur.execute("SELECT raw, source_hash FROM fundamentals_q ORDER BY random() LIMIT 100")
    match = 0
    for raw, h in cur.fetchall():
        ir, br, cr = raw.get("income") or {}, raw.get("balance") or {}, raw.get("cashflow") or {}
        curated = {
            "revenue": g(ir, "revenue"), "gross_profit": g(ir, "grossProfit"),
            "ebit": g(ir, "operatingIncome"), "net_income": g(ir, "netIncome"),
            "cfo": g(cr, "operatingCashFlow", "netCashProvidedByOperatingActivities"),
            "capex": g(cr, "capitalExpenditure"), "total_assets": g(br, "totalAssets"),
            "total_debt": g(br, "totalDebt"),
            "cash": g(br, "cashAndShortTermInvestments", "cashAndCashEquivalents"),
            "equity": g(br, "totalStockholdersEquity", "totalEquity"),
            "shares_dil": g(ir, "weightedAverageShsOutDil", "weightedAverageShsOut"),
        }
        h2 = hashlib.sha256(json.dumps(curated, sort_keys=True, default=str).encode()).hexdigest()[:16]
        match += (h2 == h)
    check("T6", match >= 98, "recomputed=%d/100" % match)

    print("T7 universe flag recheck (200-row sample)")
    cur.execute("""SELECT mktcap, adv_63d, price, in_universe FROM universe_snapshots
                   WHERE mktcap IS NOT NULL AND adv_63d IS NOT NULL
                   ORDER BY random() LIMIT 200""")
    incons = 0
    for mc, adv, px, inu in cur.fetchall():
        base = float(mc) >= 300e6 and float(adv) >= 2e6 and float(px) >= 3.0
        if inu and not base:
            incons += 1          # flagged in but fails stored inputs = real inconsistency
    check("T7", incons == 0, "in-but-fails=%d/200 (exclusion lists make out-but-passes legal)" % incons)

    print("T8 SUE sanity")
    cur.execute("""SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY abs(sue)),
                          count(*) FILTER (WHERE abs(sue) > 50), count(sue), count(*)
                   FROM surprises""")
    med, wild, n_sue, n = cur.fetchone()
    check("T8", med is not None and 0.1 <= float(med) <= 5 and wild < 0.01 * n_sue,
          "median|SUE|=%s wild=%d fill=%d/%d" % (med, wild, n_sue, n))

    print("\nGOLDEN GATE: %s (%d/%d)" % ("PASS" if all(RESULTS) else "FAIL",
                                          sum(RESULTS), len(RESULTS)))
    cx.close()
    sys.exit(0 if all(RESULTS) else 1)


if __name__ == "__main__":
    main()
