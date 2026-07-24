"""Merge same-security overlapping symbol_map rows into single spanning rows."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn


def main():
    cx = conn()
    cx.autocommit = True
    cur = cx.cursor()
    total = 0
    for _ in range(10):
        cur.execute("""SELECT a.symbol, a.security_id, a.valid_from, a.valid_to,
                              b.valid_from, b.valid_to
                       FROM symbol_map a JOIN symbol_map b
                       ON a.symbol=b.symbol AND a.security_id=b.security_id
                       AND a.valid_from < b.valid_from
                       AND b.valid_from <= COALESCE(a.valid_to, DATE '9999-12-31')
                       LIMIT 200""")
        pairs = cur.fetchall()
        if not pairs:
            break
        for sym, sec, fa, ta, fb, tb in pairs:
            new_to = None if (ta is None or tb is None) else max(ta, tb)
            cur.execute("DELETE FROM symbol_map WHERE symbol=%s AND security_id=%s AND valid_from=%s",
                        (sym, sec, fb))
            cur.execute("""UPDATE symbol_map SET valid_to=%s
                           WHERE symbol=%s AND security_id=%s AND valid_from=%s""",
                        (new_to, sym, sec, fa))
            total += 1
    cur.execute("""SELECT count(*) FROM symbol_map a JOIN symbol_map b
                   ON a.symbol=b.symbol AND a.security_id=b.security_id
                   AND a.valid_from < b.valid_from
                   AND b.valid_from <= COALESCE(a.valid_to, DATE '9999-12-31')""")
    print("merged %d; residual same-security overlaps: %d" % (total, cur.fetchone()[0]))
    cx.close()


if __name__ == "__main__":
    main()
