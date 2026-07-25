"""Session 7: PIT market caps + monthly universe snapshots (spec s5/s8).

Phase A: mktcap sweep per security (month-end reduced), resumable via
mktcap_ingest. Phase B: deterministic full rebuild of universe_snapshots
from prices + mktcap (DELETE + rebuild; streaming read, batch write).
Filters: mktcap >= $300M, 63d median dollar volume >= $2M, price >= $3,
staleness <= 5 calendar days. Size terciles per asof.
Env: MKTCAP_LIMIT=N trial; SKIP_MKTCAP=1 to jump to Phase B.
"""
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn
from factorlab.fmp_client import FMPClient
from factorlab.ingest import RDB

TODAY = date.today().isoformat()


def banner(t):
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8)


def phase_a(c, db):
    banner("A. MARKET CAP SWEEP")
    rows = db.safe(lambda cur: (cur.execute("""
        SELECT p.security_id, array_agg(DISTINCT sm.symbol)
        FROM (SELECT DISTINCT security_id FROM prices_raw_d) p
        JOIN symbol_map sm ON sm.security_id = p.security_id
        GROUP BY 1"""), cur.fetchall())[1])
    done = db.safe(lambda cur: (cur.execute("SELECT security_id FROM mktcap_ingest"),
                                {r[0] for r in cur.fetchall()})[1])
    todo = [(sec, syms) for sec, syms in rows if sec not in done]
    limit = int(os.environ.get("MKTCAP_LIMIT", "0"))
    if limit:
        todo = todo[:limit]
    print("  securities total=%d done=%d processing now=%d" % (len(rows), len(done), len(todo)))
    from concurrent.futures import ThreadPoolExecutor
    import threading
    counts = defaultdict(int)
    lock = threading.Lock()
    prog = [0]
    NW = 8

    def work(batch):
        wc = FMPClient(min_interval=0.15)
        wdb = RDB()
        for sec, syms in batch:
            _one(wc, wdb, sec, syms)
        wdb.close()

    def _one(c, db, sec, syms):
        tag = "error"
        try:
            monthly = {}
            for sym in syms:
                data = c.get("mktcap_hist", symbol=sym, limit=5000,
                             date_from="2011-01-01", date_to=TODAY, allow_empty=True)
                got = {r["date"]: float(r["marketCap"]) for r in data
                       if r.get("date") and r.get("marketCap")}
                if len(data) >= 5000 and got and min(got) > "2011-02-01":
                    older = c.get("mktcap_hist", symbol=sym, limit=5000,
                                  date_from="2011-01-01", date_to=min(got), allow_empty=True)
                    for r in older:
                        if r.get("date") and r.get("marketCap"):
                            got.setdefault(r["date"], float(r["marketCap"]))
                for d, v in got.items():
                    ym = d[:7]
                    if ym not in monthly or d > monthly[ym][0]:
                        monthly[ym] = (d, v)
            mrows = [(d, sec, v) for d, v in monthly.values() if d >= "2011-01-01"]

            def unit(cur):
                if mrows:
                    execute_values(cur, """INSERT INTO mktcap_m (asof, security_id, mktcap)
                                   VALUES %s ON CONFLICT (asof, security_id) DO NOTHING""",
                                   mrows, page_size=2000)
                cur.execute("""INSERT INTO mktcap_ingest (security_id, n_months) VALUES (%s,%s)
                               ON CONFLICT (security_id) DO UPDATE SET n_months=EXCLUDED.n_months,
                               ran_at=now()""", (sec, len(mrows)))
            db.safe(unit)
            tag = "ok" if mrows else "empty"
        except Exception as e:
            with lock:
                print("  sec=%s %s ERROR %s" % (sec, syms[:2], str(e)[:80]))
        with lock:
            counts[tag] += 1
            prog[0] += 1
            if prog[0] % 250 == 0:
                print("  ...%d/%d %s" % (prog[0], len(todo), dict(counts)))

    batches = [todo[i::NW] for i in range(NW)]
    with ThreadPoolExecutor(max_workers=NW) as ex:
        list(ex.map(work, batches))
    print("  sweep done: %s" % dict(counts))


def excluded_instruments(db):
    """Spec s5/s8: common stock only, no ADRs (v1). Symbol-convention +
    profile is_adr exclusion set, with counts printed."""
    import re
    suf = re.compile(r"-(P[A-Z]?|UN|WS|WT|R|U)$")
    rows = db.safe(lambda cur: (cur.execute(
        "SELECT security_id, string_agg(DISTINCT symbol, ',') FROM symbol_map GROUP BY 1"),
        cur.fetchall())[1])
    inst = set()
    for sec, syms in rows:
        for sym in (syms or "").split(","):
            if suf.search(sym) or (len(sym) == 5 and sym[4] in "UWR"):
                inst.add(sec)
                break
    adr = db.safe(lambda cur: (cur.execute(
        """SELECT DISTINCT security_id FROM profile_snapshots WHERE is_adr"""),
        {r[0] for r in cur.fetchall()})[1])
    print("  excluded: %d instrument-class, %d ADR (overlap %d)" % (
        len(inst), len(adr), len(inst & adr)))
    return inst | adr


def phase_b(db):
    banner("B. UNIVERSE REBUILD")
    excl = excluded_instruments(db)
    month_ends = db.safe(lambda cur: (cur.execute("""
        SELECT max(d) FROM prices_raw_d GROUP BY date_trunc('month', d) ORDER BY 1"""),
        [str(r[0]) for r in cur.fetchall()])[1])
    asof_by_ym = {a[:7]: a for a in month_ends}
    print("  month-end asofs: %d (%s .. %s)" % (len(month_ends), month_ends[0], month_ends[-1]))

    cap = {}
    def load_caps(cur):
        cur.execute("SELECT security_id, asof, mktcap FROM mktcap_m")
        for sec, a, v in cur.fetchall():
            cap[(sec, str(a)[:7])] = float(v)
    db.safe(load_caps)
    print("  mktcap cells loaded: %d" % len(cap))

    db.safe(lambda cur: cur.execute("DELETE FROM universe_snapshots"))
    scx = conn()
    scur = scx.cursor(name="pxstream")
    scur.itersize = 100000
    scur.execute("SELECT security_id, d, close, volume FROM prices_raw_d ORDER BY security_id, d")

    out, n_rows, cur_sec, series = [], 0, None, []

    excl = excl  # closure capture
    def flush_series(sec, series):
        rows = []
        by_ym = {}
        for idx, (d, c_, v_) in enumerate(series):
            ym = d[:7]
            by_ym[ym] = idx
        dollar = [c_ * v_ for _, c_, v_ in series]
        for ym, idx in sorted(by_ym.items()):
            asof = asof_by_ym.get(ym)
            if asof is None:
                continue
            d, px, _ = series[idx]
            lo = max(0, idx - 62)
            window = sorted(dollar[lo:idx + 1])
            if len(window) < 40:
                adv = None
            else:
                adv = window[len(window) // 2]
            mc = cap.get((sec, ym))
            gap = (date.fromisoformat(asof) - date.fromisoformat(d)).days
            inu = bool(mc and adv and mc >= 300e6 and adv >= 2e6 and px >= 3.0
                       and gap <= 5 and sec not in excl)
            rows.append((asof, sec, mc, adv, px, inu, None))
        return rows

    def write(rows):
        db.safe(lambda cur: execute_values(cur, """
            INSERT INTO universe_snapshots (asof, security_id, mktcap, adv_63d, price,
            in_universe, size_bucket) VALUES %s""", rows, page_size=10000))

    for sec, d, c_, v_ in scur:
        if sec != cur_sec:
            if cur_sec is not None:
                out.extend(flush_series(cur_sec, series))
                if len(out) >= 50000:
                    write(out)
                    n_rows += len(out)
                    out = []
            cur_sec, series = sec, []
        series.append((str(d), float(c_ or 0), float(v_ or 0)))
    if cur_sec is not None:
        out.extend(flush_series(cur_sec, series))
    if out:
        write(out)
        n_rows += len(out)
    scur.close()
    scx.close()
    print("  snapshot rows written: %d" % n_rows)

    db.safe(lambda cur: cur.execute("""
        UPDATE universe_snapshots u SET size_bucket = t.b FROM (
            SELECT asof, security_id,
                   CASE NTILE(3) OVER (PARTITION BY asof ORDER BY mktcap)
                        WHEN 1 THEN 'small' WHEN 2 THEN 'mid' ELSE 'large' END AS b
            FROM universe_snapshots WHERE in_universe) t
        WHERE u.asof = t.asof AND u.security_id = t.security_id"""))
    print("  size buckets assigned")


def report(db):
    banner("C. REPORT")
    def q(sql):
        return db.safe(lambda cur: (cur.execute(sql), cur.fetchall())[1])
    print("  universe count by year (June): %s" % q("""
        SELECT asof, count(*) FROM universe_snapshots WHERE in_universe
        AND EXTRACT(MONTH FROM asof)=6 GROUP BY 1 ORDER BY 1"""))
    print("  monthly count min/max: %s" % q("""
        SELECT min(n), max(n) FROM (SELECT asof, count(*) n FROM universe_snapshots
        WHERE in_universe GROUP BY 1) t"""))
    print("  biggest MoM jumps: %s" % q("""
        WITH m AS (SELECT asof, count(*) n FROM universe_snapshots WHERE in_universe GROUP BY 1)
        SELECT asof, n, n - lag(n) OVER (ORDER BY asof) d FROM m
        ORDER BY abs(n - lag(n) OVER (ORDER BY asof)) DESC NULLS LAST LIMIT 5"""))
    print("  RECON-TAIL OVERLAP (<95 names ever in universe): %s" % q("""
        SELECT count(DISTINCT u.security_id) FROM universe_snapshots u
        JOIN price_recon r ON r.security_id = u.security_id
        WHERE u.in_universe AND r.match_pct IS NOT NULL AND r.match_pct < 95"""))
    print("  NO-ORACLE OVERLAP: %s" % q("""
        SELECT count(DISTINCT u.security_id) FROM universe_snapshots u
        JOIN price_recon r ON r.security_id = u.security_id
        WHERE u.in_universe AND r.match_pct IS NULL AND r.n_days = 0 AND r.n_prices > 100"""))
    print("  AAPL months in universe: %s  META months: %s" % (
        q("""SELECT count(*) FROM universe_snapshots u JOIN symbol_map sm
             ON sm.security_id=u.security_id AND sm.symbol='AAPL'
             WHERE u.in_universe""")[0][0],
        q("""SELECT count(*) FROM universe_snapshots u JOIN symbol_map sm
             ON sm.security_id=u.security_id AND sm.symbol='META' AND sm.valid_to IS NULL
             WHERE u.in_universe""")[0][0]))
    print("  price-present but mktcap-missing (coverage holes): %s" % q("""
        SELECT count(*) FROM (SELECT DISTINCT security_id FROM prices_raw_d) p
        LEFT JOIN (SELECT security_id FROM mktcap_ingest WHERE n_months > 0) m USING (security_id)
        WHERE m.security_id IS NULL"""))


def main():
    db = RDB()
    if not os.environ.get("SKIP_MKTCAP"):
        phase_a(FMPClient(min_interval=0.1), db)
    phase_b(db)
    report(db)
    db.close()


if __name__ == "__main__":
    main()
