"""Incident #2 audit: cross-check every layer against its own resume markers.
Read-only. Prints a repair list; changes nothing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn


def main():
    cx = conn()
    cur = cx.cursor()

    print("=" * 8, "A. PRICES vs price_recon markers", "=" * 8)
    cur.execute("""SELECT r.security_id, r.n_prices, COALESCE(p.n, 0)
                   FROM price_recon r LEFT JOIN
                   (SELECT security_id, count(*) n FROM prices_raw_d GROUP BY 1) p
                   USING (security_id) WHERE r.n_prices <> COALESCE(p.n, 0)""")
    bad = cur.fetchall()
    print("  securities where actual rows <> marker n_prices: %d" % len(bad))
    for sec, want, have in bad[:15]:
        cur.execute("SELECT string_agg(DISTINCT symbol, ',') FROM symbol_map WHERE security_id=%s", (sec,))
        print("    sec=%-6s %-14s marker=%s actual=%s delta=%+d" % (
            sec, cur.fetchone()[0], want, have, have - want))
    total_delta = sum(h - w for _, w, h in bad)
    print("  net row delta: %+d (missing-vs-markers should explain the -17,148)" % total_delta)

    print("\n" + "=" * 8, "B. TR vs prices per security", "=" * 8)
    cur.execute("""SELECT count(*) FROM
                   (SELECT security_id, count(*) np FROM prices_raw_d GROUP BY 1) p
                   JOIN (SELECT security_id, count(*) nt FROM tr_index_d GROUP BY 1) t
                   USING (security_id) WHERE np <> nt""")
    print("  securities where tr rows <> price rows: %s" % cur.fetchone()[0])

    print("\n" + "=" * 8, "C. FUNDAMENTALS vs fund_ingest + W30", "=" * 8)
    cur.execute("""SELECT count(*) FROM fund_ingest f
                   JOIN (SELECT security_id, count(*) n FROM fundamentals_q
                         WHERE vintage_id = 1 GROUP BY 1) q USING (security_id)
                   WHERE f.n_periods <> q.n""")
    print("  securities where vintage-1 rows <> marker n_periods: %s" % cur.fetchone()[0])
    cur.execute("SELECT count(*), count(*) FILTER (WHERE value_pit) FROM fundamentals_q")
    tot, vp = cur.fetchone()
    cur.execute("SELECT count(*) FROM fundamentals_q WHERE vintage_id > 1")
    print("  total=%s value_pit=%s vintages>1=%s  (W30 claimed +47 new, +3 restatements)" % (
        tot, vp, cur.fetchone()[0]))
    cur.execute("""SELECT job, period_key, status, detail->'statements', ran_at
                   FROM job_log ORDER BY ran_at""")
    print("  job_log:")
    for r in cur.fetchall():
        print("    %s" % (r,))

    print("\n" + "=" * 8, "D. UNIVERSE + BENCHMARK + REGISTRY", "=" * 8)
    cur.execute("SELECT count(*), count(DISTINCT asof) FROM universe_snapshots")
    print("  universe_snapshots: %s" % (cur.fetchone(),))
    cur.execute("SELECT count(*) FROM benchmarks_m")
    print("  benchmarks_m: %s" % cur.fetchone()[0])
    cur.execute("SELECT count(*) FROM factor_definitions")
    print("  factor_definitions: %s" % cur.fetchone()[0])

    print("\n" + "=" * 8, "E. SURPRISES vs sue_ingest", "=" * 8)
    cur.execute("""SELECT count(*) FROM sue_ingest s
                   JOIN (SELECT security_id, count(*) n FROM surprises GROUP BY 1) q
                   USING (security_id) WHERE s.n_rows <> q.n""")
    print("  securities where surprise rows <> marker: %s" % cur.fetchone()[0])
    cx.close()


if __name__ == "__main__":
    main()
