"""Repair ticker-reuse contamination: for every multi-symbol security,
rebuild prices/corp_actions/TR/recon/mktcap under corrected fetch semantics:
open symbol = full range + priority; closed symbols = validity-windowed,
gap-fill only (setdefault). Audits chain edges vs the feed; missing edges ->
'chain-mismerge' quarantine. 6 workers; per-security idempotent.
"""
import csv
import json
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn
from factorlab.fmp_client import FMPClient, ART
from factorlab.ingest import RDB

TODAY = date.today().isoformat()
CHUNKS = [("2011-01-01", "2015-12-31"), ("2016-01-01", "2020-12-31"), ("2021-01-01", TODAY)]
TRV = "tr-v2-window-priority"


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


def fetch_windowed(c, wins, lo, hi):
    """wins: [(symbol, vfrom, vto_or_TODAY)] open-first. Returns merged series
    where the FIRST (open) symbol wins all collisions."""
    raw, ora, monthly = {}, {}, {}
    div_by, split_by = {}, {}
    for k, (sym, vf, vt) in enumerate(wins):
        slo = lo if k == 0 else max(lo, vf)
        shi = hi if k == 0 else min(hi, vt)
        if slo > shi:
            continue
        for cf, ct in CHUNKS:
            if ct < slo or cf > shi:
                continue
            f, t = max(cf, slo), min(ct, shi)
            for row in c.get("prices_unadjusted", symbol=sym, date_from=f, date_to=t, allow_empty=True):
                d, v = row.get("date"), row.get("adjClose")
                if d and v and (k == 0 or (vf <= d <= vt)):
                    raw.setdefault(d, (float(row.get("adjOpen") or 0) or None,
                                       float(row.get("adjHigh") or 0) or None,
                                       float(row.get("adjLow") or 0) or None,
                                       float(v), float(row.get("volume") or 0)))
            for row in c.get("prices_div_adjusted", symbol=sym, date_from=f, date_to=t, allow_empty=True):
                d, v = row.get("date"), row.get("adjClose")
                if d and v and (k == 0 or (vf <= d <= vt)):
                    ora.setdefault(d, float(v))
        try:
            divs = c.get("dividends", symbol=sym, limit=1000, allow_empty=True)
        except Exception:
            divs = []
        for dv in divs:
            d = dv.get("date")
            amt = dv.get("dividend") or dv.get("adjDividend")
            if d and amt and slo <= d <= shi and (k == 0 or (vf <= d <= vt)):
                div_by.setdefault(d, float(amt))
        try:
            spls = c.get("splits", symbol=sym, allow_empty=True)
        except Exception:
            spls = []
        for sp in spls:
            d, r = sp.get("date"), split_ratio(sp)
            if d and r and slo <= d <= shi and (k == 0 or (vf <= d <= vt)):
                split_by.setdefault(d, r)
        data = c.get("mktcap_hist", symbol=sym, limit=5000,
                     date_from="2011-01-01", date_to=TODAY, allow_empty=True)
        for r in data:
            d, v = r.get("date"), r.get("marketCap")
            if d and v and (k == 0 or (vf <= d <= vt)):
                ym = d[:7]
                if ym not in monthly or (d > monthly[ym][0] and monthly[ym][2] != 0):
                    pass
                if ym not in monthly:
                    monthly[ym] = (d, float(v), k)
                elif monthly[ym][2] == k and d > monthly[ym][0]:
                    monthly[ym] = (d, float(v), k)
    return raw, ora, div_by, split_by, monthly


def rets_from(px, div_by, split_by):
    dates = sorted(px)
    out = {}
    for i in range(1, len(dates)):
        d0, d1 = dates[i - 1], dates[i]
        out[d1] = (px[d1] * split_by.get(d1, 1.0) + div_by.get(d1, 0.0)) / px[d0] - 1.0
    return out, dates


def repair_one(c, db, sec, wins, lo, hi):
    raw, ora, div_by, split_by, monthly = fetch_windowed(c, wins, lo, hi)
    if not raw:
        return "empty"
    closes = {d: v[3] for d, v in raw.items()}
    rets, dates = rets_from(closes, div_by, split_by)
    orets_pre, _ = rets_from(ora, {}, {})
    n_seam = 0
    for d in list(rets):
        r = rets[d]
        if abs(r) > 2.0 and d not in split_by and d in orets_pre and abs(r - orets_pre[d]) > 1.5:
            rets[d] = orets_pre[d]
            n_seam += 1
    level = 100.0
    tr_rows = [(sec, dates[0], 100.0, TRV)]
    for d in dates[1:]:
        level *= (1.0 + rets[d])
        tr_rows.append((sec, d, round(level, 6), TRV))
    common_all = sorted(set(rets) & set(orets_pre))
    bad = {d for d in common_all if abs(orets_pre[d]) > 1.0 and abs(rets[d]) < 0.2}
    common = [d for d in common_all if d not in bad]
    prev = {d1: closes[d0] for d0, d1 in zip(dates, dates[1:])}
    odates = sorted(ora)
    oprev = {d1: ora[d0] for d0, d1 in zip(odates, odates[1:])}
    mism = sorted(d for d in common
                  if abs(rets[d] - orets_pre[d]) > max(0.001, 0.011 / prev.get(d, 1e9),
                                                       0.011 / oprev.get(d, 1e9)))
    match = round(100.0 * (1 - len(mism) / len(common)), 2) if common else None
    if match is not None and len(bad) > max(10, 0.02 * len(common_all)):
        match = None

    price_rows = [(sec, d, v[0], v[1], v[2], v[3], v[4]) for d, v in sorted(raw.items())]
    act_rows = [(sec, d, "split", r, None, "feed-w") for d, r in split_by.items()] + \
               [(sec, d, "div_cash", None, a, "feed-w") for d, a in div_by.items() if a > 0]
    mc_rows = [(d, sec, v) for d, v, _ in monthly.values() if d >= "2011-01-01"]

    def unit(cur):
        for tbl in ("prices_raw_d", "tr_index_d", "corp_actions"):
            cur.execute("DELETE FROM %s WHERE security_id=%%s" % tbl, (sec,))
        cur.execute("DELETE FROM mktcap_m WHERE security_id=%s", (sec,))
        execute_values(cur, """INSERT INTO prices_raw_d (security_id, d, open, high, low, close, volume)
                       VALUES %s""", price_rows, page_size=5000)
        if act_rows:
            execute_values(cur, """INSERT INTO corp_actions (security_id, ex_date, action_type,
                           ratio, amount, source) VALUES %s""", act_rows, page_size=2000)
        execute_values(cur, "INSERT INTO tr_index_d (security_id, d, tr, method_version) VALUES %s",
                       tr_rows, page_size=5000)
        if mc_rows:
            execute_values(cur, """INSERT INTO mktcap_m (asof, security_id, mktcap) VALUES %s
                           ON CONFLICT (asof, security_id) DO UPDATE SET mktcap=EXCLUDED.mktcap""",
                           mc_rows, page_size=2000)
        cur.execute("""UPDATE price_recon SET n_days=%s, match_pct=%s, first_mismatch=%s,
                       n_prices=%s, n_seam=%s, n_oracle_bad=%s, ran_at=now()
                       WHERE security_id=%s""",
                    (len(common), match, mism[0] if mism else None, len(price_rows),
                     n_seam, len(bad), sec))
    db.safe(unit)
    return "ok" if (match is None or match >= 95) else "low"


def main():
    db = RDB()
    rows = db.safe(lambda cur: (cur.execute("""
        SELECT security_id, symbol, valid_from, valid_to FROM symbol_map
        WHERE security_id IN (SELECT security_id FROM symbol_map
                              GROUP BY 1 HAVING count(DISTINCT symbol) > 1)"""),
        cur.fetchall())[1])
    by_sec = defaultdict(list)
    for sec, sym, vf, vt in rows:
        by_sec[sec].append((sym, str(vf), str(vt) if vt else TODAY))
    targets = []
    for sec, wins in by_sec.items():
        wins.sort(key=lambda w: w[2], reverse=True)
        merged = []
        for sym, vf, vt in wins:
            for m in merged:
                if m[0] == sym:
                    m[1], m[2] = min(m[1], vf), max(m[2], vt)
                    break
            else:
                merged.append([sym, vf, vt])
        lo = max("2011-01-01", min(w[1] for w in merged))
        hi = TODAY
        if lo > hi:
            lo = "2011-01-01"
        targets.append((sec, [tuple(m) for m in merged], lo, hi))
    print("multi-symbol securities to repair: %d" % len(targets))

    counts = defaultdict(int)
    lock = threading.Lock()
    prog = [0]

    def work(batch):
        wc = FMPClient(min_interval=0.15)
        wdb = RDB()
        for sec, wins, lo, hi in batch:
            tag = "error"
            try:
                tag = repair_one(wc, wdb, sec, wins, lo, hi)
            except Exception as e:
                with lock:
                    print("  sec=%s %s ERROR %s" % (sec, [w[0] for w in wins][:3], str(e)[:80]))
            with lock:
                counts[tag] += 1
                prog[0] += 1
                if prog[0] % 200 == 0:
                    print("  ...%d/%d %s" % (prog[0], len(targets), dict(counts)))
        wdb.close()

    NW = 6
    batches = [targets[i::NW] for i in range(NW)]
    with ThreadPoolExecutor(max_workers=NW) as ex:
        list(ex.map(work, batches))
    print("repair done: %s" % dict(counts))

    print("\nCHAIN AUDIT (feed evidence per multi-symbol security in the flagged set):")
    edges = set()
    p = ART / "symbol_change_full.csv"
    if p.exists():
        for r in csv.DictReader(open(p)):
            edges.add((r.get("oldSymbol"), r.get("newSymbol")))
    def audit(cur):
        cur.execute("""SELECT r.security_id,
                       (SELECT string_agg(DISTINCT sm.symbol, ',') FROM symbol_map sm
                        WHERE sm.security_id=r.security_id)
                       FROM price_recon r WHERE r.match_pct < 95""")
        for sec, syms in cur.fetchall():
            ss = (syms or "").split(",")
            if len(ss) < 2:
                continue
            linked = any((a, b) in edges or (b, a) in edges for a in ss for b in ss if a != b)
            if not linked:
                cur.execute("""INSERT INTO identity_quarantine (symbol, issue, detail)
                               SELECT %s, 'chain-mismerge-suspect', %s WHERE NOT EXISTS
                               (SELECT 1 FROM identity_quarantine WHERE symbol=%s
                                AND issue='chain-mismerge-suspect')""",
                            (ss[0], json.dumps({"security_id": sec, "symbols": ss}), ss[0]))
                print("  MISMERGE-SUSPECT sec=%s %s (no feed edge links these)" % (sec, syms))
    db.safe(audit)
    db.close()


if __name__ == "__main__":
    main()
