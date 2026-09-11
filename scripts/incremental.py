"""Session 10: the self-feeding loop. Run hourly by Railway cron; each job
claims its period_key in job_log (idempotent), does its work, records detail.

daily   (weekdays >= 23:00 UTC): dividend/split calendars -> corp_actions;
        per-active-security price append; TR extension (R16-class jumps are
        flagged to job_log, never auto-repaired without an oracle).
weekly  (Sat >= 12:00 UTC): statement observations append NEW VINTAGES;
        estimates snapshot. Historical value availability remains uncertified.
monthly (day >= 2, >= 13:00 UTC): mktcap top-up, universe rebuild
        (deterministic), golden gate run, result logged.
Force locally: python scripts/incremental.py --force daily|weekly|monthly
"""
import argparse
import json
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
from factorlab.job_health import (JobFailure, checked_script, due_jobs,
                                  execute_job, require_complete, safe_error)
from factorlab.weekly_fundamentals import ingest_security

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
            detail[logical] = safe_error(e)
            detail["source_errors"] = detail.get("source_errors", 0) + 1
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
    require_complete("daily", detail)
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
            except Exception as exc:
                with lock:
                    counts["error_" + safe_error(exc)["error_type"]] += 1
            with lock:
                counts[tag] += 1
        wdb.close()

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(work, [targets[i::6] for i in range(6)]))
    detail["prices"] = dict(counts)
    return detail


def job_weekly(db):
    detail = {}
    members = db.safe(lambda cur: (cur.execute("""
        SELECT DISTINCT sm.security_id, sm.symbol, i.cik FROM symbol_map sm
        JOIN securities s USING (security_id) JOIN issuers i USING (issuer_id)
        WHERE sm.valid_to IS NULL AND s.status='active'
          AND sm.security_id IN (SELECT DISTINCT security_id FROM universe_snapshots
                                 WHERE in_universe)"""), cur.fetchall())[1])
    if not members:
        raise JobFailure("empty_weekly_scope", {"statements": {}})
    counts = defaultdict(int)
    lock = threading.Lock()

    def work(batch):
        wc = FMPClient(min_interval=0.12)
        wdb = RDB()
        try:
            for sec, sym, cik in batch:
                try:
                    outcomes = ingest_security(wc, wdb, sec, sym, cik)
                    with lock:
                        for key, value in outcomes.items():
                            counts[key] += value
                        counts["ok"] += 1
                except Exception as exc:
                    error = safe_error(exc)
                    with lock:
                        counts["error"] += 1
                        counts["error_" + error["error_type"]] += 1
                        if "sqlstate" in error:
                            counts["sqlstate_" + error["sqlstate"]] += 1
        finally:
            wdb.close()

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(work, [members[i::6] for i in range(6)]))
    detail["statements"] = dict(counts)
    # Estimates can accrue independently, but a statement error still fails the job.
    try:
        checked_script(ROOT, "scripts/estimates_snapshot.py", 3600)
    except JobFailure as exc:
        raise JobFailure(exc.code, dict(detail, **exc.detail)) from exc
    detail["estimates"] = "completed"
    require_complete("weekly", detail)
    return detail


def run_factor_chain():
    """Legacy full rebuild, NOT a versioned publisher; release remains draft."""
    for script in ("scripts/factor_compute_v2.py", "scripts/factor_eval.py",
                   "scripts/golden_gate.py"):
        checked_script(ROOT, script, 7200)
        print(json.dumps({"stage": script, "returncode": 0}), flush=True)


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
        except Exception as exc:
            detail["source_errors"] = detail.get("source_errors", 0) + 1
            detail["last_source_error"] = safe_error(exc)
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
    require_complete("monthly", detail)
    if not targets or not rows:
        raise JobFailure("empty_monthly_scope", detail)
    if rows:
        db.safe(lambda cur: execute_values(cur, """INSERT INTO mktcap_m
            (asof, security_id, mktcap) VALUES %s
            ON CONFLICT (asof, security_id) DO UPDATE SET mktcap=EXCLUDED.mktcap""",
            rows, page_size=2000))
    detail["mktcap_cells"] = len(rows)
    env = dict(**__import__("os").environ, SKIP_MKTCAP="1")
    try:
        checked_script(ROOT, "scripts/universe_build.py", 3600, env=env)
        detail["universe"] = "completed"
        run_factor_chain()
    except JobFailure as exc:
        raise JobFailure(exc.code, dict(detail, **exc.detail)) from exc
    detail["factor_chain"] = "OK"
    detail["golden_gate"] = "PASS"  # The checked chain already ran the gate.
    return detail


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", choices=("daily", "weekly", "monthly"))
    args = parser.parse_args(argv)
    due = due_jobs(NOW, args.force)
    if not due:
        print("nothing due (utc=%s)" % NOW.isoformat())
        return 0
    db = RDB()
    jobs = {"daily": job_daily, "weekly": job_weekly, "monthly": job_monthly}
    failed = False
    try:
        for job, key in due:
            if not execute_job(db, job, key, jobs[job], claim=claim, finish=finish):
                failed = True
    except Exception as exc:
        print(json.dumps(dict(level="error", **safe_error(exc))), flush=True)
        failed = True
    finally:
        db.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
