"""Session 10: the self-feeding loop. Run hourly by Railway cron; each job
claims its period_key in job_log (idempotent), does its work, records detail.

daily   (weekdays >= 23:00 UTC): dividend/split calendars -> corp_actions;
        per-active-security price append; TR extension (R16-class jumps are
        flagged to job_log, never auto-repaired without an oracle).
weekly  (Sat >= 12:00 UTC): strict-PIT statement sweep — new periods and
        live-caught restatements land as NEW VINTAGES with value_pit=true;
        estimates snapshot.
monthly (day >= 2, >= 13:00 UTC): mktcap top-up, universe rebuild
        (deterministic), golden gate run, result logged.
Force locally: python scripts/incremental.py --force daily|weekly|monthly
"""
import json
import subprocess
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.fmp_client import FMPClient
from factorlab.ingest import RDB

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()


def claim(db, job, key):
    def unit(cur):
        cur.execute("""DELETE FROM job_log WHERE job=%s AND period_key=%s
                       AND status='running' AND ran_at < now() - interval '6 hours'""",
                    (job, key))
        cur.execute("""INSERT INTO job_log (job, period_key, status)
                       VALUES (%s,%s,'running') ON CONFLICT DO NOTHING""", (job, key))
        return cur.rowcount == 1
    return db.safe(unit)


def finish(db, job, key, status, detail):
    db.safe(lambda cur: cur.execute(
        """UPDATE job_log SET status=%s, detail=%s, ran_at=now()
           WHERE job=%s AND period_key=%s""",
        (status, json.dumps(detail, default=str), job, key)))


def active_secs(db):
    return db.safe(lambda cur: (cur.execute("""
        SELECT DISTINCT ON (sm.security_id) sm.security_id, sm.symbol
        FROM symbol_map sm JOIN securities s USING (security_id)
        WHERE sm.valid_to IS NULL AND s.status='active'
        ORDER BY sm.security_id, sm.valid_from DESC"""), cur.fetchall())[1])


def job_daily(db):
    c = FMPClient(min_interval=0.1)
    detail = {}
    # 1. events via calendars (last 5 days covers weekends/retries)
    f = (NOW.date() - timedelta(days=5)).isoformat()
    sym2sec = {sym: sec for sec, sym in active_secs(db)}
    acts = []
    for logical, kind in (("dividends_calendar", "div_cash"), ("splits_calendar", "split")):
        try:
            rows = FMPClient(min_interval=0.1).get(logical, date_from=f, date_to=TODAY,
                                                   allow_empty=True)
        except Exception as e:
            detail[logical] = "ERR %s" % str(e)[:60]
            rows = []
        for r in rows:
            sec = sym2sec.get(r.get("symbol"))
            d = r.get("date")
            if not sec or not d:
                continue
            if kind == "split":
                num, den = r.get("numerator"), r.get("denominator")
                ratio = (float(num) / float(den)) if num and den else None
                if ratio:
                    acts.append((sec, d, "split", ratio, None, "calendar"))
            else:
                amt = r.get("dividend") or r.get("adjDividend")
                if amt:
                    acts.append((sec, d, "div_cash", None, float(amt), "calendar"))
    if acts:
        db.safe(lambda cur: execute_values(cur, """INSERT INTO corp_actions
            (security_id, ex_date, action_type, ratio, amount, source) VALUES %s
            ON CONFLICT (security_id, ex_date, action_type) DO NOTHING""", acts))
    detail["events"] = len(acts)

    # 2. price append + TR extension, parallel
    lasts = db.safe(lambda cur: (cur.execute("""
        SELECT security_id, max(d) FROM prices_raw_d GROUP BY 1"""),
        dict(cur.fetchall()))[1])
    targets = [(sec, sym, str(lasts.get(sec, date(2011, 1, 1)))) for sec, sym in active_secs(db)]
    counts = defaultdict(int)
    lock = threading.Lock()

    def one(c, wdb, sec, sym, last_d):
        if last_d >= TODAY:
            return "current"
        f2 = (date.fromisoformat(last_d) + timedelta(days=1)).isoformat()
        rows = c.get("prices_unadjusted", symbol=sym, date_from=f2, date_to=TODAY,
                     allow_empty=True)
        new = sorted((r["date"], float(r.get("adjOpen") or 0) or None,
                      float(r.get("adjHigh") or 0) or None, float(r.get("adjLow") or 0) or None,
                      float(r["adjClose"]), float(r.get("volume") or 0))
                     for r in rows if r.get("date") and r.get("adjClose") and r["date"] > last_d)
        if not new:
            return "nodata"

        def unit(cur):
            execute_values(cur, """INSERT INTO prices_raw_d
                (security_id, d, open, high, low, close, volume) VALUES %s
                ON CONFLICT (security_id, d) DO NOTHING""",
                [(sec,) + r for r in new])
            cur.execute("""SELECT d, tr FROM tr_index_d WHERE security_id=%s
                           ORDER BY d DESC LIMIT 1""", (sec,))
            r0 = cur.fetchone()
            cur.execute("SELECT close FROM prices_raw_d WHERE security_id=%s AND d=%s",
                        (sec, last_d))
            base = cur.fetchone()
            if not r0 or not base:
                return
            level, prev_close = float(r0[1]), float(base[0])
            cur.execute("""SELECT ex_date, action_type, ratio, amount FROM corp_actions
                           WHERE security_id=%s AND ex_date > %s""", (sec, last_d))
            ev = {}
            for d_, t_, ra, am in cur.fetchall():
                ev.setdefault(str(d_), {}).update({t_: float(ra or am or 0)})
            tr_rows = []
            for d_, _, _, _, close, _ in new:
                e = ev.get(d_, {})
                ret = (close * e.get("split", 1.0) + e.get("div_cash", 0.0)) / prev_close - 1.0
                if abs(ret) > 2.0 and "split" not in e:
                    with lock:
                        counts["JUMP-FLAG"] += 1
                level *= (1.0 + ret)
                tr_rows.append((sec, d_, round(level, 6), "tr-v2-window-priority"))
                prev_close = close
            if tr_rows:
                execute_values(cur, """INSERT INTO tr_index_d
                    (security_id, d, tr, method_version) VALUES %s
                    ON CONFLICT (security_id, d) DO NOTHING""", tr_rows)
        wdb.safe(unit)
        return "ok"

    def work(batch):
        wc = FMPClient(min_interval=0.12)
        wdb = RDB()
        for sec, sym, last_d in batch:
            tag = "error"
            try:
                tag = one(wc, wdb, sec, sym, last_d)
            except Exception:
                pass
            with lock:
                counts[tag] += 1
        wdb.close()

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(work, [targets[i::6] for i in range(6)]))
    detail["prices"] = dict(counts)
    return detail


def job_weekly(db):
    import hashlib
    detail = {}
    c_kw = dict(min_interval=0.12)
    members = db.safe(lambda cur: (cur.execute("""
        SELECT DISTINCT sm.security_id, sm.symbol, i.cik FROM symbol_map sm
        JOIN securities s USING (security_id) JOIN issuers i USING (issuer_id)
        WHERE sm.valid_to IS NULL AND s.status='active'
          AND sm.security_id IN (SELECT DISTINCT security_id FROM universe_snapshots
                                 WHERE in_universe)"""), cur.fetchall())[1])
    counts = defaultdict(int)
    lock = threading.Lock()

    def g(row, *keys):
        for k in keys:
            if row.get(k) is not None:
                return row[k]
        return None

    def one(c, wdb, sec, sym, cik):
        cik = str(cik or "").lstrip("0")
        stmts = {}
        for logical in ("income_q", "balance_q", "cashflow_q"):
            rows = c.get(logical, symbol=sym, limit=2, allow_empty=True)
            for r in rows:
                d = r.get("date")
                rcik = str(r.get("cik") or "").lstrip("0")
                if not d or (cik and rcik and rcik != cik):
                    continue
                stmts.setdefault(d, {})[logical] = r
        n_new = n_restate = 0
        for d, by in stmts.items():
            ir = by.get("income_q", {})
            br, cr = by.get("balance_q", {}), by.get("cashflow_q", {})
            if not ir:
                continue
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
            h = hashlib.sha256(json.dumps(curated, sort_keys=True, default=str)
                               .encode()).hexdigest()[:16]

            def unit(cur):
                nonlocal n_new, n_restate
                cur.execute("""SELECT max(vintage_id),
                               (array_agg(source_hash ORDER BY vintage_id DESC))[1]
                               FROM fundamentals_q WHERE security_id=%s
                               AND fiscal_period_end=%s""", (sec, d))
                mv, last_h = cur.fetchone()
                if mv is not None and last_h == h:
                    return
                accepted = ir.get("acceptedDate")
                lag_ok = accepted and (date.fromisoformat(accepted[:10]) -
                                       date.fromisoformat(d)).days > 10
                lag = None if not accepted else (date.fromisoformat(accepted[:10]) -
                                                 date.fromisoformat(d)).days
                lc = ("missing" if not lag_ok else
                      "release" if lag <= 25 else "filing" if lag <= 200 else "delinquent")
                cur.execute("""INSERT INTO fundamentals_q (security_id, fiscal_period_end,
                    period, vintage_id, accepted_date, filing_date, backfill, value_pit,
                    timing_pit, lag_class, source_hash, mapping_version, currency,
                    revenue, gross_profit, ebit, net_income, cfo, capex, total_assets,
                    total_debt, cash, equity, shares_dil, raw)
                    VALUES (%s,%s,%s,%s,%s,%s,false,true,%s,%s,%s,'m1',%s,
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING""",
                    (sec, d, ir.get("period") or "Q?", (mv or 0) + 1, accepted,
                     ir.get("filingDate"), bool(lag_ok), lc,
                     ir.get("reportedCurrency"), curated["revenue"],
                     curated["gross_profit"], curated["ebit"], curated["net_income"],
                     curated["cfo"], curated["capex"], curated["total_assets"],
                     curated["total_debt"], curated["cash"], curated["equity"],
                     curated["shares_dil"],
                     json.dumps({"income": ir, "balance": br, "cashflow": cr}, default=str)))
                if mv is None:
                    n_new += 1
                else:
                    n_restate += 1
            wdb.safe(unit)
        return n_new, n_restate

    def work(batch):
        wc = FMPClient(**c_kw)
        wdb = RDB()
        for sec, sym, cik in batch:
            try:
                a, b = one(wc, wdb, sec, sym, cik)
                with lock:
                    counts["new_vintages"] += a
                    counts["live_restatements"] += b
                    counts["ok"] += 1
            except Exception:
                with lock:
                    counts["error"] += 1
        wdb.close()

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(work, [members[i::6] for i in range(6)]))
    detail["statements"] = dict(counts)
    r = subprocess.run([sys.executable, str(ROOT / "scripts/estimates_snapshot.py")],
                       capture_output=True, text=True, timeout=3600)
    detail["estimates"] = (r.stdout.strip().splitlines() or ["?"])[-1]
    return detail


def run_factor_chain():
    """Review #3 7.3: after universe refresh, recompute the full derived
    stack and gate it. Any nonzero rc fails the monthly job loudly."""
    import subprocess
    import sys as _sys
    for script in ("scripts/factor_compute_v2.py", "scripts/factor_eval.py",
                   "scripts/golden_gate.py"):
        rc = subprocess.call([_sys.executable, "-u", script])
        if rc != 0:
            raise RuntimeError("factor chain failed at %s (rc=%d)" % (script, rc))


def job_monthly(db):
    detail = {}
    c = FMPClient(min_interval=0.12)
    f = (NOW.date() - timedelta(days=45)).isoformat()
    rows = []
    targets = active_secs(db)
    for i, (sec, sym) in enumerate(targets):
        if (i + 1) % 500 == 0:
            print("  mktcap top-up ...%d/%d (%d cells)" % (i + 1, len(targets), len(rows)))
        try:
            data = c.get("mktcap_hist", symbol=sym, limit=60, date_from=f, date_to=TODAY,
                         allow_empty=True)
        except Exception:
            continue
        monthly = {}
        for r in data:
            d, v = r.get("date"), r.get("marketCap")
            if d and v:
                ym = d[:7]
                if ym not in monthly or d > monthly[ym][0]:
                    monthly[ym] = (d, float(v))
        rows.extend((d, sec, v) for d, v in monthly.values())
    rows = list({(d, sec): (d, sec, v) for d, sec, v in rows}.values())
    if rows:
        db.safe(lambda cur: execute_values(cur, """INSERT INTO mktcap_m
            (asof, security_id, mktcap) VALUES %s
            ON CONFLICT (asof, security_id) DO UPDATE SET mktcap=EXCLUDED.mktcap""",
            rows, page_size=2000))
    detail["mktcap_cells"] = len(rows)
    env = dict(**__import__("os").environ, SKIP_MKTCAP="1")
    r = subprocess.run([sys.executable, str(ROOT / "scripts/universe_build.py")],
                       capture_output=True, text=True, timeout=3600, env=env)
    detail["universe"] = (r.stdout.strip().splitlines() or ["?"])[-6:]
    r = subprocess.run([sys.executable, str(ROOT / "scripts/golden_gate.py")],
                       capture_output=True, text=True, timeout=1800)
    detail["golden_gate"] = "PASS" if r.returncode == 0 else "FAIL"
    detail["gate_tail"] = (r.stdout.strip().splitlines() or ["?"])[-1]
    return detail
    run_factor_chain()

def main():
    force = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--force" else None
    db = RDB()
    due = []
    wd, hr = NOW.weekday(), NOW.hour
    if force == "daily" or (wd < 5 and hr >= 23):
        due.append(("daily", TODAY, job_daily))
    if force == "weekly" or (wd == 5 and hr >= 12):
        due.append(("weekly", NOW.strftime("%G-W%V"), job_weekly))
    if force == "monthly" or (NOW.day >= 2 and hr >= 13):
        due.append(("monthly", NOW.strftime("%Y-%m"), job_monthly))
    if not due:
        print("nothing due (utc=%s)" % NOW.isoformat())
        db.close()
        return
    for job, key, fn in due:
        if not claim(db, job, key):
            print("%s %s already claimed" % (job, key))
            continue
        print("running %s %s" % (job, key))
        try:
            detail = fn(db)
            finish(db, job, key, "ok", detail)
            print("  %s ok: %s" % (job, json.dumps(detail, default=str)[:400]))
        except Exception as e:
            finish(db, job, key, "error", {"err": str(e)[:300]})
            print("  %s ERROR %s" % (job, e))
    db.close()


if __name__ == "__main__":
    main()
