"""Load SPY dividend-adjusted closes at the month-end asof grid into
benchmarks_m. Benchmark lane lives outside universe/prices by design."""
import sys
from pathlib import Path

from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn
from factorlab.fmp_client import FMPClient

CHUNKS = [("2010-06-01", "2015-12-31"), ("2016-01-01", "2020-12-31"), ("2021-01-01", "2026-07-25")]


def main():
    c = FMPClient(min_interval=0.15)
    cx = conn()
    cx.autocommit = True
    cur = cx.cursor()
    cur.execute("SELECT DISTINCT asof FROM universe_snapshots ORDER BY asof")
    asofs = {str(r[0]) for r in cur.fetchall()}
    px = {}
    for f, t in CHUNKS:
        for r in c.get("prices_div_adjusted", symbol="SPY", date_from=f, date_to=t, allow_empty=True):
            if r.get("date") and r.get("adjClose"):
                px[r["date"]] = float(r["adjClose"])
    dates = sorted(px)
    grid = []
    for a in sorted(asofs):
        prior = [d for d in dates if d <= a]
        if prior:
            grid.append((a, "SPY", px[prior[-1]]))
    execute_values(cur, """INSERT INTO benchmarks_m (asof, symbol, tr) VALUES %s
                   ON CONFLICT (asof, symbol) DO UPDATE SET tr=EXCLUDED.tr""", grid)
    print("SPY grid: %d asofs, %s .. %s" % (len(grid), grid[0][0], grid[-1][0]))
    cx.close()


if __name__ == "__main__":
    main()
