"""Session 6: prices backfill (Phase 0, spec s6). Per security:
raw OHLCV (non-split-adjusted) under EVERY symbol it has held ->
prices_raw_d; dividends + splits -> corp_actions; self-built TR ->
tr_index_d; reconciliation vs dividend-adjusted oracle -> price_recon.
Then R14: close open windows / date delistings from last trade.

Batch-first (execute_values), per-security idempotent, price_recon = resume
marker. Env: PRICES_LIMIT=N for trial. START = 2011-01-01.
"""
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.fmp_client import FMPClient
from factorlab.ingest import RDB

START = "2011-01-01"
TODAY = date.today().isoformat()
CHUNKS = [("2011-01-01", "2015-12-31"), ("2016-01-01", "2020-12-31"), ("2021-01-01", TODAY)]
TRV = "tr-v1-close-div-split"


def banner(t):
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8)


def split_ratio(sp):
    num, den = sp.get("numerator"), sp.get("denominator")
    if num and den:
        try:
            return float(num) / float(den)
        except (TypeError, ValueError):
            pass
    lbl = str(sp.get("label") or sp.get("splitRatio") or "")
    for sep in (":", "/", "-"):
        if sep in lbl:
            try:
                a, b = lbl.split(sep)[:2]
                return float(a) / float(b)
            except (TypeError, ValueError):
                pass
    return None


def fetch_series(c, symbols, lo, hi):
    """Merged daily series + events across all symbols this security held."""
    raw, ora = {}, {}
    div_by, split_by = defaultdict(float), {}
    n_div = n_split = 0
    for sym in symbols:
        for cf, ct in CHUNKS:
            if ct < lo or cf > hi:
                continue
            f, t = max(cf, lo), min(ct, hi)
            for row in c.get("prices_unadjusted", symbol=sym, date_from=f, date_to=t, allow_empty=True):
                d, v = row.get("date"), row.get("adjClose")
                if d and v:
                    raw[d] = (float(row.get("adjOpen") or 0) or None,
                              float(row.get("adjHigh") or 0) or None,
                              float(row.get("adjLow") or 0) or None,
                              float(v), float(row.get("volume") or 0))
            for row in c.get("prices_div_adjusted", symbol=sym, date_from=f, date_to=t, allow_empty=True):
                d, v = row.get("date"), row.get("adjClose")
                if d and v:
                    ora[d] = float(v)
        try:
            divs = c.get("dividends", symbol=sym, limit=1000, allow_empty=True)
        except Exception:
            divs = []
        for dv in divs:
            d = dv.get("date")
            amt = dv.get("dividend") or dv.get("adjDividend")
            if d and amt and lo <= d <= hi and d not in split_by:
                if div_by[d] == 0.0:
                    n_div += 1
                div_by[d] = max(div_by[d], float(amt))
        try:
            spls = c.get("splits", symbol=sym, allow_empty=True)
        except Exception:
            spls = []
        for sp in spls:
            d, r = sp.get("date"), split_ratio(sp)
            if d and r and lo <= d <= hi and d not in split_by:
                split_by[d] = r
                n_split += 1
    return raw, ora, dict(div_by), split_by, n_div, n_split


def rets_from(px, div_by, split_by):
    dates = sorted(px)
    out = {}
    for i in range(1, len(dates)):
        d0, d1 = dates[i - 1], dates[i]
        out[d1] = (px[d1] * split_by.get(d1, 1.0) + div_by.get(d1, 0.0)) / px[d0] - 1.0
    return out, dates


def process_security(db, c, sec, symbols, lo, hi):
    raw, ora, div_by, split_by, n_div, n_split = fetch_series(c, symbols, lo, hi)
    if not raw:
        db.safe(lambda cur: cur.execute(
            """INSERT INTO price_recon (security_id, n_days, match_pct, n_prices, n_div, n_split)
               VALUES (%s, 0, NULL, 0, %s, %s) ON CONFLICT (security_id) DO NOTHING""",
            (sec, n_div, n_split)))
        return "empty"
    closes = {d: v[3] for d, v in raw.items()}
    rets, dates = rets_from(closes, div_by, split_by)
    orets_pre, _ = rets_from(ora, {}, {})
    n_seam = 0
    for d in list(rets):
        r = rets[d]
        if abs(r) > 2.0 and d not in split_by and d in orets_pre \
                and abs(r - orets_pre[d]) > 1.5:
            rets[d] = orets_pre[d]          # R16: oracle-corroborated seam repair
            n_seam += 1
    level = 100.0
    tr_rows = [(sec, dates[0], 100.0, TRV)]
    for d in dates[1:]:
        level *= (1.0 + rets[d])
        tr_rows.append((sec, d, round(level, 6), TRV))
    orets = orets_pre
    common_all = sorted(set(rets) & set(orets))
    bad = [d for d in common_all if abs(orets[d]) > 1.0 and abs(rets[d]) < 0.2]
    n_oracle_bad = len(bad)                 # R17: oracle-insane days excluded
    common = [d for d in common_all if d not in set(bad)]
    prev = {d1: closes[d0] for d0, d1 in zip(dates, dates[1:])}
    mism = [d for d in common
            if abs(rets[d] - orets[d]) > max(0.001, 0.011 / prev.get(d, 1e9))]
    match = round(100.0 * (1 - len(mism) / len(common)), 2) if common else None
    if match is not None and n_oracle_bad > max(10, 0.02 * len(common_all)):
        match = None                        # R17b: oracle unusable for this name

    price_rows = [(sec, d, v[0], v[1], v[2], v[3], v[4]) for d, v in sorted(raw.items())]
    act_rows = [(sec, d, "split", r, None, "feed") for d, r in split_by.items()] + \
               [(sec, d, "div_cash", None, a, "feed") for d, a in div_by.items() if a > 0]

    def unit(cur):
        execute_values(cur,
                       """INSERT INTO prices_raw_d (security_id, d, open, high, low, close, volume)
                          VALUES %s ON CONFLICT (security_id, d) DO NOTHING""",
                       price_rows, page_size=5000)
        if act_rows:
            execute_values(cur,
                           """INSERT INTO corp_actions (security_id, ex_date, action_type, ratio, amount, source)
                              VALUES %s ON CONFLICT (security_id, ex_date, action_type) DO NOTHING""",
                           act_rows, page_size=2000)
        cur.execute("DELETE FROM tr_index_d WHERE security_id=%s", (sec,))
        execute_values(cur,
                       "INSERT INTO tr_index_d (security_id, d, tr, method_version) VALUES %s",
                       tr_rows, page_size=5000)
        cur.execute("""INSERT INTO price_recon (security_id, n_days, match_pct, first_mismatch,
                       n_prices, n_div, n_split, n_seam, n_oracle_bad)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (security_id) DO UPDATE SET n_days=EXCLUDED.n_days,
                       match_pct=EXCLUDED.match_pct, first_mismatch=EXCLUDED.first_mismatch,
                       n_prices=EXCLUDED.n_prices, n_div=EXCLUDED.n_div,
                       n_split=EXCLUDED.n_split, n_seam=EXCLUDED.n_seam,
                       n_oracle_bad=EXCLUDED.n_oracle_bad, ran_at=now()""",
                    (sec, len(common), match, mism[0] if mism else None,
                     len(price_rows), n_div, n_split, n_seam, n_oracle_bad))
    db.safe(unit)
    if match is None:
        return "no-oracle"
    return "ok" if match >= 95.0 else "low-recon"


def main():
    c = FMPClient(min_interval=0.1)
    db = RDB()
    banner("0. TARGETS")
    rows = db.safe(lambda cur: (cur.execute("""
        SELECT sm.security_id, sm.symbol, sm.valid_from, sm.valid_to
        FROM symbol_map sm"""), cur.fetchall())[1])
    by_sec = defaultdict(list)
    for sec, sym, vf, vt in rows:
        by_sec[sec].append((sym, str(vf), str(vt) if vt else TODAY))
    done = db.safe(lambda cur: (cur.execute("SELECT security_id FROM price_recon"),
                                {r[0] for r in cur.fetchall()})[1])
    todo = []
    for sec, wins in by_sec.items():
        if sec in done:
            continue
        lo = max(START, min(w[1] for w in wins))
        hi = min(TODAY, max(w[2] for w in wins))
        if lo > hi:
            lo = START
        symbols = sorted({w[0] for w in wins},
                         key=lambda s: max(w[2] for w in wins if w[0] == s),
                         reverse=True)
        todo.append((sec, symbols, lo, hi))
    limit = int(os.environ.get("PRICES_LIMIT", "0"))
    if limit:
        todo = todo[:limit]
    print("  securities total=%d done=%d processing now=%d" % (len(by_sec), len(done), len(todo)))

    banner("1. SWEEP")
    counts = defaultdict(int)
    for i, (sec, symbols, lo, hi) in enumerate(todo):
        try:
            counts[process_security(db, c, sec, symbols, lo, hi)] += 1
        except Exception as e:
            counts["error"] += 1
            print("  sec=%s %s ERROR %s" % (sec, symbols[:2], str(e)[:90]))
        if (i + 1) % 100 == 0:
            print("  ...%d/%d %s" % (i + 1, len(todo), dict(counts)))
    print("  sweep done: %s" % dict(counts))

    banner("2. R14 - CLOSE OPEN WINDOWS FROM PRICE ENDS")
    def r14(cur):
        cur.execute("""SELECT s.security_id, sm.symbol, sm.valid_from, max(p.d)
                       FROM securities s
                       JOIN symbol_map sm ON sm.security_id=s.security_id AND sm.valid_to IS NULL
                       JOIN prices_raw_d p ON p.security_id=s.security_id
                       WHERE s.status='delisted'
                       GROUP BY 1,2,3 HAVING max(p.d) < (CURRENT_DATE - 30)""")
        acted = 0
        for sec, sym, vf, last in cur.fetchall():
            cur.execute("""UPDATE symbol_map SET valid_to=%s
                           WHERE security_id=%s AND symbol=%s AND valid_from=%s""",
                        (last, sec, sym, vf))
            cur.execute("""INSERT INTO delistings (security_id, delist_date, delist_reason,
                           terminal_method, source)
                           VALUES (%s,%s,'unknown','rung3-flagged','price-end')
                           ON CONFLICT (security_id) DO NOTHING""", (sec, last))
            cur.execute("""UPDATE identity_quarantine SET resolved=true
                           WHERE symbol=%s AND issue='inactive-no-delist-date'""", (sym,))
            acted += 1
        return acted
    print("  windows closed / delistings dated: %d" % db.safe(r14))

    banner("3. REPORT")
    def q1(sql):
        return db.safe(lambda cur: (cur.execute(sql), cur.fetchall())[1])
    print("  prices_raw_d: %s rows / %s securities" % (
        q1("SELECT count(*) FROM prices_raw_d")[0][0],
        q1("SELECT count(DISTINCT security_id) FROM prices_raw_d")[0][0]))
    print("  corp_actions: %s  tr_index_d: %s rows" % (
        q1("SELECT count(*) FROM corp_actions")[0][0],
        q1("SELECT count(*) FROM tr_index_d")[0][0]))
    print("  recon: %s" % q1("""SELECT CASE WHEN match_pct IS NULL THEN 'no-oracle'
        WHEN match_pct >= 99 THEN '99+' WHEN match_pct >= 95 THEN '95-99'
        ELSE '<95' END, count(*) FROM price_recon GROUP BY 1 ORDER BY 1"""))
    print("  worst recon: %s" % q1("""SELECT security_id, match_pct, first_mismatch
        FROM price_recon WHERE match_pct IS NOT NULL AND n_days > 100
        ORDER BY match_pct LIMIT 10"""))
    print("  META-chain span: %s" % q1("SELECT min(d), max(d) FROM prices_raw_d WHERE security_id=6087"))
    db.close()


if __name__ == "__main__":
    main()
