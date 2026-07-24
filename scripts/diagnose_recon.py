"""Diagnose low-recon securities: are mismatches penny-rounding noise or
real event misses (e.g. missed reverse splits)? Prints worst days, price
context, and counterfactual match_pct under a price-aware tolerance."""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn
from factorlab.fmp_client import FMPClient

SECS = [51, 45, 58, 36, 55]
CHUNKS = [("2011-01-01", "2015-12-31"), ("2016-01-01", "2020-12-31"), ("2021-01-01", "2026-07-23")]


def main():
    c = FMPClient(min_interval=0.1)
    cx = conn()
    cur = cx.cursor()
    for sec in SECS:
        cur.execute("SELECT DISTINCT symbol FROM symbol_map WHERE security_id=%s", (sec,))
        syms = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT d, close FROM prices_raw_d WHERE security_id=%s ORDER BY d", (sec,))
        px = {str(d): float(v) for d, v in cur.fetchall()}
        cur.execute("SELECT ex_date, action_type, ratio, amount FROM corp_actions WHERE security_id=%s", (sec,))
        div_by, split_by = defaultdict(float), {}
        for d, t, r, a in cur.fetchall():
            if t == "split" and r:
                split_by[str(d)] = float(r)
            elif a:
                div_by[str(d)] += float(a)
        ora = {}
        for sym in syms:
            for f, t in CHUNKS:
                for row in c.get("prices_div_adjusted", symbol=sym, date_from=f, date_to=t, allow_empty=True):
                    if row.get("date") and row.get("adjClose"):
                        ora[row["date"]] = float(row["adjClose"])
        dates = sorted(px)
        rets, orets = {}, {}
        for i in range(1, len(dates)):
            d0, d1 = dates[i - 1], dates[i]
            rets[d1] = (px[d1] * split_by.get(d1, 1.0) + div_by.get(d1, 0.0)) / px[d0] - 1.0, px[d0]
        od = sorted(ora)
        for i in range(1, len(od)):
            orets[od[i]] = ora[od[i]] / ora[od[i - 1]] - 1.0
        common = [d for d in rets if d in orets]
        flat, aware, big = [], [], []
        for d in common:
            r, p0 = rets[d]
            diff = abs(r - orets[d])
            if diff > 0.001:
                flat.append((diff, d, p0, r, orets[d]))
            if diff > max(0.001, 0.011 / p0):
                aware.append((diff, d, p0))
            if diff > 0.05:
                big.append((diff, d, p0, r, orets[d]))
        n = len(common)
        print("sec=%s syms=%s days=%d | flat-mismatch=%d (%.1f%% match) | "
              "price-aware-mismatch=%d (%.1f%% match) | >5%% event-class=%d" % (
                  sec, syms, n, len(flat), 100 * (1 - len(flat) / n) if n else 0,
                  len(aware), 100 * (1 - len(aware) / n) if n else 0, len(big)))
        med_mis = sorted(x[2] for x in flat)[len(flat) // 2] if flat else None
        print("   median prev-price on mismatch days: %s" % med_mis)
        for diff, d, p0, r, o in sorted(big, reverse=True)[:6]:
            print("   EVENT? %s prev_px=%.4f self=%.4f oracle=%.4f diff=%.4f" % (d, p0, r, o, diff))
    cx.close()


if __name__ == "__main__":
    main()
