"""8b-1: earnings surprises backfill (ever-in-universe) via /stable/earnings.
SUE = (actual-est) / stdev of prior 8 surprise diffs (min 4). R18-windowed."""
import os
import statistics
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


def main():
    db = RDB()
    rows = db.safe(lambda cur: (cur.execute("""
        SELECT sm.security_id, sm.symbol, sm.valid_from, sm.valid_to FROM symbol_map sm
        WHERE sm.security_id IN (SELECT DISTINCT security_id FROM universe_snapshots
                                 WHERE in_universe)"""), cur.fetchall())[1])
    by_sec = defaultdict(list)
    for sec, sym, vf, vt in rows:
        by_sec[sec].append((sym, str(vf), str(vt) if vt else TODAY))
    for v in by_sec.values():
        v.sort(key=lambda w: w[2], reverse=True)
    done = db.safe(lambda cur: (cur.execute("SELECT security_id FROM sue_ingest"),
                                {r[0] for r in cur.fetchall()})[1])
    todo = [(s, w) for s, w in by_sec.items() if s not in done]
    limit = int(os.environ.get("SUE_LIMIT", "0"))
    if limit:
        todo = todo[:limit]
    print("targets=%d done=%d now=%d" % (len(by_sec), len(done), len(todo)))
    counts = defaultdict(int)
    lock = threading.Lock()
    prog = [0]

    def one(c, wdb, sec, wins):
        ev = {}
        for k, (sym, vf, vt) in enumerate(wins):
            try:
                rows = c.get("surprises", symbol=sym, limit=120, allow_empty=True)
            except Exception:
                rows = []
            for r in rows:
                d, act = r.get("date"), r.get("epsActual")
                if not d or act is None:
                    continue
                if k > 0 and not (vf <= d <= vt):
                    continue
                ev.setdefault(d, r)
        recs = []
        hist = []
        for d in sorted(ev):
            r = ev[d]
            act, est = r.get("epsActual"), r.get("epsEstimated")
            sue = None
            if act is not None and est is not None:
                diff = float(act) - float(est)
                if len(hist) >= 4:
                    sd = statistics.pstdev(hist[-8:])
                    if sd > 1e-9:
                        sue = round(diff / sd, 4)
                hist.append(diff)
            recs.append((sec, d, act, est, r.get("revenueActual"), r.get("revenueEstimated"), sue))

        def unit(cur):
            if recs:
                execute_values(cur, """INSERT INTO surprises (security_id, report_date,
                    eps_actual, eps_est, rev_actual, rev_est, sue) VALUES %s
                    ON CONFLICT (security_id, report_date) DO NOTHING""", recs, page_size=500)
            cur.execute("""INSERT INTO sue_ingest (security_id, n_rows) VALUES (%s,%s)
                           ON CONFLICT (security_id) DO UPDATE SET n_rows=EXCLUDED.n_rows,
                           ran_at=now()""", (sec, len(recs)))
        wdb.safe(unit)
        return "ok" if recs else "empty"

    def work(batch):
        wc = FMPClient(min_interval=0.15)
        wdb = RDB()
        for sec, wins in batch:
            tag = "error"
            try:
                tag = one(wc, wdb, sec, wins)
            except Exception as e:
                with lock:
                    print("  sec=%s ERROR %s" % (sec, str(e)[:80]))
            with lock:
                counts[tag] += 1
                prog[0] += 1
                if prog[0] % 300 == 0:
                    print("  ...%d/%d %s" % (prog[0], len(todo), dict(counts)))
        wdb.close()

    NW = 6
    with ThreadPoolExecutor(max_workers=NW) as ex:
        list(ex.map(work, [todo[i::NW] for i in range(NW)]))
    print("done: %s" % dict(counts))
    n, s = db.safe(lambda cur: (cur.execute(
        "SELECT count(*), count(sue) FROM surprises"), cur.fetchone())[1])
    print("surprises rows: %s (with SUE: %s)" % (n, s))
    db.close()


if __name__ == "__main__":
    main()
