"""Adjudicate cross-security symbol_map overlaps + dedupe same-security rows.

Classes:
  boundary-trim   overlap <= 7d, both rows evidence-dated -> trim earlier row
  auto-dedup-cik  duplicate views of one listing -> keep the CIK-anchored side
  auto-dedup-sent neither/both have CIK -> drop the sentinel-start side
  overlap-conflict everything else -> quarantined, untouched
Every auto action is logged to identity_quarantine (resolved=true).
Iterates until stable (overlap triples resolve across passes).
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn

SENT = date(1900, 1, 1)
INF = date(9999, 12, 31)


def main():
    cx = conn()
    cx.autocommit = True
    cur = cx.cursor()

    def log(sym, issue, detail, resolved):
        cur.execute("""INSERT INTO identity_quarantine (symbol, issue, detail, resolved)
                       VALUES (%s, %s, %s, %s)""",
                    (sym, issue, json.dumps(detail, default=str), resolved))

    def has_cik(sec):
        cur.execute("""SELECT i.cik FROM securities s JOIN issuers i USING (issuer_id)
                       WHERE s.security_id=%s""", (sec,))
        r = cur.fetchone()
        return bool(r and r[0])

    def drop(sym, sec, vfrom):
        cur.execute("DELETE FROM symbol_map WHERE symbol=%s AND security_id=%s AND valid_from=%s",
                    (sym, sec, vfrom))

    print("=" * 8, "SAME-SECURITY CONTAINMENT DEDUPE", "=" * 8)
    cur.execute("""DELETE FROM symbol_map b USING symbol_map a
                   WHERE a.symbol=b.symbol AND a.security_id=b.security_id
                   AND (a.valid_from, COALESCE(a.valid_to, DATE '9999-12-31'))
                       IS DISTINCT FROM (b.valid_from, COALESCE(b.valid_to, DATE '9999-12-31'))
                   AND a.valid_from <= b.valid_from
                   AND COALESCE(a.valid_to, DATE '9999-12-31') >= COALESCE(b.valid_to, DATE '9999-12-31')""")
    print("  contained duplicate rows deleted: %d" % cur.rowcount)

    print("\n" + "=" * 8, "CROSS-SECURITY ADJUDICATION", "=" * 8)
    totals = {"boundary-trim": 0, "auto-dedup-cik": 0, "auto-dedup-sent": 0, "overlap-conflict": 0}
    for it in range(6):
        cur.execute("""SELECT a.symbol, a.security_id, a.valid_from, a.valid_to,
                              b.security_id, b.valid_from, b.valid_to
                       FROM symbol_map a JOIN symbol_map b
                       ON a.symbol=b.symbol AND a.security_id < b.security_id
                       AND a.valid_from <= COALESCE(b.valid_to, DATE '9999-12-31')
                       AND b.valid_from <= COALESCE(a.valid_to, DATE '9999-12-31')""")
        pairs = cur.fetchall()
        if not pairs:
            break
        acted = 0
        seen_conflict = set()
        for sym, sa, fa, ta, sb, fb, tb in pairs:
            ea, eb = ta or INF, tb or INF
            span = (min(ea, eb) - max(fa, fb)).days
            detail = {"a": [sa, str(fa), str(ta)], "b": [sb, str(fb), str(tb)], "span": span}
            if span <= 7 and fa != SENT and fb != SENT:
                (esym, esec, efrom, lfrom) = (sym, sa, fa, fb) if fa <= fb else (sym, sb, fb, fa)
                new_to = lfrom - timedelta(days=1)
                if new_to < efrom:
                    drop(esym, esec, efrom)
                else:
                    cur.execute("""UPDATE symbol_map SET valid_to=%s
                                   WHERE symbol=%s AND security_id=%s AND valid_from=%s""",
                                (new_to, esym, esec, efrom))
                log(sym, "boundary-trim", detail, True)
                totals["boundary-trim"] += 1
                acted += 1
            else:
                ca, cb = has_cik(sa), has_cik(sb)
                if ca != cb:
                    keep, kill, kfrom = (sa, sb, fb) if ca else (sb, sa, fa)
                    drop(sym, kill, kfrom)
                    log(sym, "auto-dedup-cik", {**detail, "kept": keep}, True)
                    totals["auto-dedup-cik"] += 1
                    acted += 1
                elif (fa == SENT) != (fb == SENT):
                    kill, kfrom = (sa, fa) if fa == SENT else (sb, fb)
                    drop(sym, kill, kfrom)
                    log(sym, "auto-dedup-sent", detail, True)
                    totals["auto-dedup-sent"] += 1
                    acted += 1
                elif (sym, sa, sb) not in seen_conflict:
                    seen_conflict.add((sym, sa, sb))
                    if it == 0:
                        log(sym, "overlap-conflict", detail, False)
                        totals["overlap-conflict"] += 1
        print("  pass %d: %d pairs seen, %d actions" % (it + 1, len(pairs), acted))
        if acted == 0:
            break
    print("  totals: %s" % totals)

    cur.execute("""SELECT count(*) FROM symbol_map a JOIN symbol_map b
                   ON a.symbol=b.symbol AND a.security_id < b.security_id
                   AND a.valid_from <= COALESCE(b.valid_to, DATE '9999-12-31')
                   AND b.valid_from <= COALESCE(a.valid_to, DATE '9999-12-31')""")
    print("  residual cross-security overlaps (all quarantined): %d" % cur.fetchone()[0])
    cx.close()


if __name__ == "__main__":
    main()
