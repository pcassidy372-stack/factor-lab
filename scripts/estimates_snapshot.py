"""8b-2: weekly estimates snapshot — CURRENT universe members, asof=today.
Idempotent upsert; rerun weekly (scheduled in session 10). This run starts
the revisions-history clock."""
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
import json

from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.fmp_client import FMPClient
from factorlab.ingest import RDB

TODAY = date.today().isoformat()


def g(r, *keys):
    for k in keys:
        if r.get(k) is not None:
            return r[k]
    return None


def main():
    db = RDB()
    members = db.safe(lambda cur: (cur.execute("""
        SELECT u.security_id, (SELECT sm.symbol FROM symbol_map sm
            WHERE sm.security_id=u.security_id AND sm.valid_to IS NULL LIMIT 1)
        FROM universe_snapshots u
        WHERE u.asof = (SELECT max(asof) FROM universe_snapshots) AND u.in_universe"""),
        cur.fetchall())[1])
    members = [(s, sym) for s, sym in members if sym]
    print("snapshotting %d current members, asof=%s" % (len(members), TODAY))
    counts = defaultdict(int)
    lock = threading.Lock()
    prog = [0]
    shown = [False]

    def work(batch):
        wc = FMPClient(min_interval=0.15)
        wdb = RDB()
        for sec, sym in batch:
            tag = "error"
            try:
                rows = wc.get("estimates", symbol=sym, limit=6, allow_empty=True)
                with lock:
                    if rows and not shown[0]:
                        shown[0] = True
                        print("  row0 keys: %s" % sorted(rows[0].keys()))
                recs = []
                for r in rows:
                    d = r.get("date")
                    if not d or d < TODAY[:4]:
                        continue
                    recs.append((TODAY, sec, d,
                                 g(r, "epsAvg", "estimatedEpsAvg"),
                                 g(r, "numAnalystsEps", "numberAnalystsEstimatedEps",
                                   "numberAnalystEstimatedEps"),
                                 g(r, "revenueAvg", "estimatedRevenueAvg"),
                                 g(r, "numAnalystsRevenue", "numberAnalystsEstimatedRevenue",
                                   "numberAnalystEstimatedRevenue"),
                                 json.dumps(r, default=str)))
                if recs:
                    wdb.safe(lambda cur: execute_values(cur, """
                        INSERT INTO estimates_snapshots (asof, security_id, fy_date,
                        eps_avg, eps_n, rev_avg, rev_n, raw) VALUES %s
                        ON CONFLICT (asof, security_id, fy_date) DO UPDATE SET
                        eps_avg=EXCLUDED.eps_avg, eps_n=EXCLUDED.eps_n,
                        rev_avg=EXCLUDED.rev_avg, rev_n=EXCLUDED.rev_n,
                        raw=EXCLUDED.raw""", recs, page_size=200))
                tag = "ok" if recs else "empty"
            except Exception as e:
                with lock:
                    print("  %s ERROR %s" % (sym, str(e)[:80]))
            with lock:
                counts[tag] += 1
                prog[0] += 1
                if prog[0] % 300 == 0:
                    print("  ...%d/%d %s" % (prog[0], len(members), dict(counts)))
        wdb.close()

    NW = 6
    with ThreadPoolExecutor(max_workers=NW) as ex:
        list(ex.map(work, [members[i::NW] for i in range(NW)]))
    print("done: %s" % dict(counts))
    stats = db.safe(lambda cur: (cur.execute("""
        SELECT count(DISTINCT security_id), count(*),
        count(*) FILTER (WHERE eps_n >= 3) FROM estimates_snapshots WHERE asof=%s""",
        (TODAY,)), cur.fetchone())[1])
    print("snapshot: %s securities, %s (sec,fy) cells, %s with >=3 analysts" % stats)
    db.close()


if __name__ == "__main__":
    main()
