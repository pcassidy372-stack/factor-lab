"""Recompute recon for all match_pct < 99 securities under R15b (two-sided
quantization tolerance). Raw side from DB; oracle refetched. Updates
price_recon in place; prints before/after distribution."""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn
from factorlab.fmp_client import FMPClient

CHUNKS = [("2011-01-01", "2015-12-31"), ("2016-01-01", "2020-12-31"), ("2021-01-01", "2026-07-24")]


def main():
    c = FMPClient(min_interval=0.1)
    cx = conn()
    cx.autocommit = True
    cur = cx.cursor()
    cur.execute("""SELECT security_id, match_pct FROM price_recon
                   WHERE match_pct IS NOT NULL AND match_pct < 99""")
    targets = cur.fetchall()
    print("rechecking %d securities under R15b" % len(targets))
    moved = defaultdict(int)
    for i, (sec, old_mp) in enumerate(targets):
        cur.execute("SELECT DISTINCT symbol FROM symbol_map WHERE security_id=%s", (sec,))
        syms = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT d, close FROM prices_raw_d WHERE security_id=%s ORDER BY d", (sec,))
        px = {str(d): float(v) for d, v in cur.fetchall() if v}
        cur.execute("SELECT ex_date, action_type, ratio, amount FROM corp_actions WHERE security_id=%s", (sec,))
        div_by, split_by = defaultdict(float), {}
        for d, t, r, a in cur.fetchall():
            if t == "split" and r:
                split_by[str(d)] = float(r)
            elif a:
                div_by[str(d)] += float(a)
        ora = {}
        try:
            for sym in syms:
                for f, t in CHUNKS:
                    for row in c.get("prices_div_adjusted", symbol=sym, date_from=f,
                                     date_to=t, allow_empty=True):
                        if row.get("date") and row.get("adjClose"):
                            ora[row["date"]] = float(row["adjClose"])
        except Exception:
            continue
        dates = sorted(px)
        rets = {}
        for j in range(1, len(dates)):
            d0, d1 = dates[j - 1], dates[j]
            rets[d1] = ((px[d1] * split_by.get(d1, 1.0) + div_by.get(d1, 0.0)) / px[d0] - 1.0, px[d0])
        od = sorted(ora)
        orets = {od[j]: (ora[od[j]] / ora[od[j - 1]] - 1.0, ora[od[j - 1]]) for j in range(1, len(od))}
        common_all = [d for d in rets if d in orets]
        bad = {d for d in common_all if abs(orets[d][0]) > 1.0 and abs(rets[d][0]) < 0.2}
        common = [d for d in common_all if d not in bad]
        if not common:
            continue
        mism = [d for d in common
                if abs(rets[d][0] - orets[d][0]) > max(0.001, 0.011 / rets[d][1], 0.011 / orets[d][1])]
        mism.sort()
        new_mp = round(100.0 * (1 - len(mism) / len(common)), 2)
        if len(bad) > max(10, 0.02 * len(common_all)):
            new_mp = None
        cur.execute("""UPDATE price_recon SET match_pct=%s, first_mismatch=%s, n_days=%s,
                       n_oracle_bad=%s, ran_at=now() WHERE security_id=%s""",
                    (new_mp, mism[0] if mism else None, len(common), len(bad), sec))
        ob = "%.0f" % float(old_mp)
        nb = "none" if new_mp is None else ("99+" if new_mp >= 99 else ("95+" if new_mp >= 95 else "<95"))
        moved["%s->%s" % (("<95" if float(old_mp) < 95 else "95+"), nb)] += 1
        if (i + 1) % 100 == 0:
            print("  ...%d/%d %s" % (i + 1, len(targets), dict(moved)))
    print("moved: %s" % dict(moved))
    cur.execute("""SELECT CASE WHEN match_pct IS NULL THEN 'no-oracle'
        WHEN match_pct >= 99 THEN '99+' WHEN match_pct >= 95 THEN '95-99'
        ELSE '<95' END, count(*) FROM price_recon GROUP BY 1 ORDER BY 1""")
    print("final distribution: %s" % cur.fetchall())
    cx.close()


if __name__ == "__main__":
    main()
