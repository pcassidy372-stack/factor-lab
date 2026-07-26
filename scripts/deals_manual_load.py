"""Load data/deals_manual.csv into delistings: rung1-deal-manual with
terminal_return computed from OUR price tables (consideration at close vs
target's last trade). YELL-class rows (no terms) stay rung3-flagged."""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn


def sec_for(cur, sym, asof):
    cur.execute("""SELECT security_id FROM symbol_map WHERE symbol=%s
                   AND valid_from <= %s AND (valid_to IS NULL OR valid_to >= %s)
                   ORDER BY valid_from DESC LIMIT 1""", (sym, asof, asof))
    r = cur.fetchone()
    return r[0] if r else None


def close_on(cur, sec, asof):
    cur.execute("""SELECT close FROM prices_raw_d WHERE security_id=%s AND d <= %s
                   ORDER BY d DESC LIMIT 1""", (sec, asof))
    r = cur.fetchone()
    return float(r[0]) if r else None


def main():
    cx = conn()
    cx.autocommit = True
    cur = cx.cursor()
    for row in csv.DictReader(open(Path(__file__).resolve().parent.parent / "data/deals_manual.csv")):
        sym, d = row["symbol"], row["close_date"]
        sec = sec_for(cur, sym, d) or sec_for(cur, sym, "2021-01-01")
        if not sec:
            print("  %s: NO SECURITY RESOLVED - skip" % sym)
            continue
        if not row["acquirer_symbol"]:
            print("  %s: no terms (rung3-flagged stands): %s" % (sym, row["note"]))
            continue
        cash = float(row["cash_per_share"] or 0)
        ratio = float(row["stock_ratio"] or 0)
        stock_val = 0.0
        if ratio:
            asec = sec_for(cur, row["acquirer_symbol"], d)
            apx = close_on(cur, asec, d) if asec else None
            if apx is None:
                print("  %s: acquirer %s price missing - inserting terms w/o return" % (
                    sym, row["acquirer_symbol"]))
            else:
                stock_val = ratio * apx
        last = close_on(cur, sec, d)
        tr = round((cash + stock_val) / last - 1.0, 4) if last else None
        cur.execute("""INSERT INTO delistings (security_id, delist_date, delist_reason,
                       terminal_return, terminal_method, source)
                       VALUES (%s,%s,'merger',%s,'rung1-deal-manual','deals_manual.csv')
                       ON CONFLICT (security_id) DO UPDATE SET delist_date=EXCLUDED.delist_date,
                       delist_reason='merger', terminal_return=EXCLUDED.terminal_return,
                       terminal_method='rung1-deal-manual', source='deals_manual.csv'""",
                    (sec, d, tr))
        print("  %s sec=%s: consideration=%.2f last=%.2f terminal_return=%s" % (
            sym, sec, cash + stock_val, last or -1, tr))
    cx.close()


if __name__ == "__main__":
    main()
