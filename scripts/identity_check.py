"""Post-backfill integrity check: explain the 9 spot-check misses and
census overlapping validity windows (same-security = cosmetic dup;
cross-security = dangerous, must be zero or adjudicated)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn

MISSES = ["NKLA", "LKNCY", "YELL", "CERN", "ALXN", "XLNX", "MXIM", "KLG", "SGMO"]


def main():
    cx = conn()
    cur = cx.cursor()

    print("=" * 8, "A. THE 9 SPOT-CHECK MISSES", "=" * 8)
    for sym in MISSES:
        cur.execute("""SELECT sm.security_id, sm.valid_from, sm.valid_to, sm.source, s.status
                       FROM symbol_map sm JOIN securities s USING (security_id)
                       WHERE sm.symbol=%s ORDER BY sm.valid_from""", (sym,))
        rows = cur.fetchall()
        cur.execute("SELECT issue FROM identity_quarantine WHERE symbol=%s", (sym,))
        q = [r[0] for r in cur.fetchall()]
        print("  %-6s rows=%s quarantine=%s" % (sym, rows if rows else "NONE", q))

    print("\n" + "=" * 8, "B. OVERLAP INTEGRITY", "=" * 8)
    cur.execute("""SELECT count(*) FROM symbol_map a JOIN symbol_map b
                   ON a.symbol=b.symbol AND a.security_id < b.security_id
                   AND a.valid_from <= COALESCE(b.valid_to, DATE '9999-12-31')
                   AND b.valid_from <= COALESCE(a.valid_to, DATE '9999-12-31')""")
    danger = cur.fetchone()[0]
    print("  CROSS-security overlaps (dangerous): %d" % danger)
    if danger:
        cur.execute("""SELECT a.symbol, a.security_id, a.valid_from, a.valid_to,
                              b.security_id, b.valid_from, b.valid_to
                       FROM symbol_map a JOIN symbol_map b
                       ON a.symbol=b.symbol AND a.security_id < b.security_id
                       AND a.valid_from <= COALESCE(b.valid_to, DATE '9999-12-31')
                       AND b.valid_from <= COALESCE(a.valid_to, DATE '9999-12-31')
                       ORDER BY a.symbol LIMIT 15""")
        for r in cur.fetchall():
            print("    %s" % (r,))
    cur.execute("""SELECT count(*) FROM symbol_map a JOIN symbol_map b
                   ON a.symbol=b.symbol AND a.security_id = b.security_id
                   AND a.valid_from < b.valid_from
                   AND b.valid_from <= COALESCE(a.valid_to, DATE '9999-12-31')""")
    print("  SAME-security overlaps (cosmetic dups): %d" % cur.fetchone()[0])

    print("\n" + "=" * 8, "C. RESOLUTION COVERAGE BY ASOF", "=" * 8)
    for asof in ("2016-06-30", "2020-06-30", "2023-06-30", "2026-07-01"):
        cur.execute("""SELECT count(DISTINCT symbol) FROM symbol_map
                       WHERE valid_from <= %s AND (valid_to IS NULL OR valid_to >= %s)""",
                    (asof, asof))
        print("  %s: %d symbols resolvable" % (asof, cur.fetchone()[0]))
    cx.close()


if __name__ == "__main__":
    main()
