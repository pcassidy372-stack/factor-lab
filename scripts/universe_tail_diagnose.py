"""Decompose the 194 recon-tail universe members: instrument type by symbol
convention (-P*/-UN/-WS/-WT preferred/unit/warrant suffixes; 5-letter U/W
enders = NASDAQ unit/warrant), plus full-universe contamination count,
coverage-hole classes, and META's excluded-month reasons."""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn

SUFFIX = re.compile(r"-(P[A-Z]?|UN|WS|WT|R|U)$")


def classify(symbols):
    syms = symbols.split(",")
    tags = set()
    for s in syms:
        if SUFFIX.search(s):
            tags.add("pref/unit/warrant(-suffix)")
        elif len(s) == 5 and s[4] == "U":
            tags.add("unit(5U)")
        elif len(s) == 5 and s[4] == "W":
            tags.add("warrant(5W)")
        elif len(s) == 5 and s[4] == "R":
            tags.add("right(5R)")
    return sorted(tags)[0] if tags else "common?"


def main():
    cx = conn()
    cur = cx.cursor()

    print("=" * 8, "A. THE 194 DECOMPOSED", "=" * 8)
    cur.execute("""
        SELECT r.security_id, r.match_pct, r.n_days,
               (SELECT string_agg(DISTINCT sm.symbol, ',') FROM symbol_map sm
                WHERE sm.security_id = r.security_id),
               (SELECT count(*) FROM universe_snapshots u
                WHERE u.security_id = r.security_id AND u.in_universe),
               (SELECT max(u.mktcap) FROM universe_snapshots u
                WHERE u.security_id = r.security_id AND u.in_universe)
        FROM price_recon r
        WHERE r.match_pct IS NOT NULL AND r.match_pct < 95
          AND EXISTS (SELECT 1 FROM universe_snapshots u
                      WHERE u.security_id = r.security_id AND u.in_universe)""")
    rows = cur.fetchall()
    buckets = Counter()
    commons = []
    for sec, mp, nd, syms, months, mc in rows:
        b = classify(syms or "")
        buckets[b] += 1
        if b == "common?":
            commons.append((months, float(mp), sec, syms, float(mc or 0) / 1e9))
    print("  buckets: %s" % dict(buckets))
    print("  'common?' residue: %d — top by months-in-universe:" % len(commons))
    for months, mp, sec, syms, mc in sorted(commons, reverse=True)[:20]:
        print("    sec=%-6s %-18s months=%-4d match=%.1f%% maxcap=$%.1fB" % (sec, syms, months, mp, mc))

    print("\n" + "=" * 8, "B. FULL-UNIVERSE INSTRUMENT CONTAMINATION", "=" * 8)
    cur.execute("""
        SELECT u.security_id,
               (SELECT string_agg(DISTINCT sm.symbol, ',') FROM symbol_map sm
                WHERE sm.security_id = u.security_id),
               count(*)
        FROM universe_snapshots u WHERE u.in_universe
        GROUP BY u.security_id""")
    contam = Counter()
    contam_months = Counter()
    for sec, syms, months in cur.fetchall():
        b = classify(syms or "")
        contam[b] += 1
        contam_months[b] += months
    print("  in-universe securities by class: %s" % dict(contam))
    print("  in-universe member-months by class: %s" % dict(contam_months))

    print("\n" + "=" * 8, "C. COVERAGE HOLES (176) BY CLASS", "=" * 8)
    cur.execute("""
        SELECT (SELECT string_agg(DISTINCT sm.symbol, ',') FROM symbol_map sm
                WHERE sm.security_id = p.security_id)
        FROM (SELECT DISTINCT security_id FROM prices_raw_d) p
        LEFT JOIN (SELECT security_id FROM mktcap_ingest WHERE n_months > 0) m
        USING (security_id) WHERE m.security_id IS NULL""")
    holes = Counter(classify(r[0] or "") for r in cur.fetchall())
    print("  holes by class: %s" % dict(holes))

    print("\n" + "=" * 8, "D. META EXCLUDED MONTHS — WHY", "=" * 8)
    cur.execute("""SELECT asof, mktcap, adv_63d, price, in_universe
                   FROM universe_snapshots WHERE security_id = 6087 ORDER BY asof""")
    reasons = defaultdict(int)
    n_in = 0
    for asof, mc, adv, px, inu in cur.fetchall():
        if inu:
            n_in += 1
            continue
        if mc is None:
            reasons["mktcap-missing"] += 1
        elif adv is None:
            reasons["adv-warmup"] += 1
        elif float(mc) < 300e6:
            reasons["cap"] += 1
        elif float(px) < 3:
            reasons["price"] += 1
        else:
            reasons["adv/staleness"] += 1
    print("  in=%d; excluded by reason: %s" % (n_in, dict(reasons)))
    cx.close()


if __name__ == "__main__":
    main()
