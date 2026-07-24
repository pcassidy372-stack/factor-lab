"""Session 5: identity backfill (Phase 0). Census + delisted symbols ->
issuers / securities / symbol_map / profile_snapshots / delistings, with
change-chain history and a quarantine for everything ambiguous.

Idempotent per symbol; restart-safe (DB is the checkpoint).
Env: IDENTITY_LIMIT=N for a trial slice.
"""
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.fmp_client import FMPClient, ART
from factorlab.ingest import RDB
from factorlab.torture_sample import SAMPLE

TODAY = date.today().isoformat()
MAJORS = {"NYSE", "NASDAQ", "AMEX", "NYSE AMERICAN", "NYSEARCA"}
SENTINEL = "1900-01-01"


def banner(t):
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8)


def load_feeds(c):
    p = ART / "symbol_change_full.csv"
    if p.exists():
        changes = list(csv.DictReader(open(p)))
    else:
        st, data = c._call("/stable/symbol-change", {"limit": "10000"})
        changes = data if isinstance(data, list) else []
    p = ART / "delisted_full.csv"
    if p.exists():
        delisted = list(csv.DictReader(open(p)))
    else:
        delisted = []
        for page in range(300):
            rows = c.get("delisted", page=page, allow_empty=True)
            if not rows:
                break
            delisted.extend(rows)
    p = ART / "mna_latest_cache.json"
    deals = json.loads(p.read_text()) if p.exists() else []
    return changes, delisted, deals


def census_symbols(c):
    keep = []
    for ex in ("NYSE", "NASDAQ", "AMEX"):
        rows = c.get("census", exchange=ex, allow_empty=True)
        n_etf = sum(1 for r in rows if r.get("isEtf"))
        n_fund = sum(1 for r in rows if r.get("isFund"))
        kept = [r.get("symbol") for r in rows
                if r.get("symbol") and not r.get("isEtf") and not r.get("isFund")]
        print("  screener %s: rows=%d (etf=%d fund=%d) kept=%d%s" % (
            ex, len(rows), n_etf, n_fund, len(kept),
            "  WARNING: at limit - possible truncation" if len(rows) >= 5000 else ""))
        keep.extend(kept)
    keep = sorted(set(keep))
    print("  census total kept: %d" % len(keep))
    return keep


def resolve_symbol(cur, sym, c, ctx):
    """One idempotent unit: create/complete identity for one symbol."""
    change_to = ctx["change_to"]
    delist_rows = ctx["delist_by_sym"].get(sym, [])
    deal_targets = ctx["deal_targets"]

    def quarantine(issue, detail):
        cur.execute("""INSERT INTO identity_quarantine (symbol, issue, detail)
                       SELECT %s, %s, %s WHERE NOT EXISTS
                       (SELECT 1 FROM identity_quarantine WHERE symbol=%s AND issue=%s)""",
                    (sym, issue, json.dumps(detail, default=str), sym, issue))

    def get_issuer(cik, name):
        if cik:
            cur.execute("""INSERT INTO issuers (cik, name) VALUES (%s, %s)
                           ON CONFLICT (cik) DO UPDATE SET name = EXCLUDED.name
                           RETURNING issuer_id""", (cik, name))
            return cur.fetchone()[0]
        cur.execute("SELECT issuer_id FROM issuers WHERE cik IS NULL AND name=%s", (name,))
        r = cur.fetchone()
        if r:
            return r[0]
        cur.execute("INSERT INTO issuers (cik, name) VALUES (NULL, %s) RETURNING issuer_id", (name,))
        return cur.fetchone()[0]

    def get_security(issuer_id, cusip, isin, status, first_seen, last_seen):
        if cusip:
            cur.execute("SELECT security_id FROM securities WHERE issuer_id=%s AND cusip=%s",
                        (issuer_id, cusip))
        elif isin:
            cur.execute("SELECT security_id FROM securities WHERE issuer_id=%s AND isin=%s",
                        (issuer_id, isin))
        else:
            cur.execute("SELECT security_id FROM securities WHERE issuer_id=%s LIMIT 1", (issuer_id,))
        r = cur.fetchone()
        if r:
            cur.execute("""UPDATE securities SET status=%s,
                           first_seen=COALESCE(first_seen,%s), last_seen=COALESCE(%s,last_seen)
                           WHERE security_id=%s""", (status, first_seen, last_seen, r[0]))
            return r[0]
        cur.execute("""INSERT INTO securities (issuer_id, isin, cusip, status, first_seen, last_seen)
                       VALUES (%s,%s,%s,%s,%s,%s) RETURNING security_id""",
                    (issuer_id, isin, cusip, status, first_seen, last_seen))
        return cur.fetchone()[0]

    def map_row(sec, vfrom, vto, source):
        cur.execute("SELECT security_id FROM symbol_map WHERE symbol=%s AND valid_to IS NULL", (sym,))
        r = cur.fetchone()
        if vto is None and r and r[0] != sec:
            quarantine("open-conflict", {"existing_sec": r[0], "new_sec": sec})
            return
        cur.execute("""INSERT INTO symbol_map (security_id, symbol, valid_from, valid_to, source)
                       VALUES (%s,%s,%s,%s,%s) ON CONFLICT (symbol, valid_from) DO NOTHING""",
                    (sec, sym, vfrom, vto, source))

    if len(delist_rows) > 1:
        quarantine("multi-delist-history", {"rows": delist_rows})

    prof_rows = c.get("profile", symbol=sym, allow_empty=True)
    p = prof_rows[0] if prof_rows else None
    dl = delist_rows[-1] if delist_rows else None  # cache is date-desc; last = oldest; use max date
    if delist_rows:
        dl = max(delist_rows, key=lambda r: r.get("delistedDate") or "")

    if p is None:
        if dl:
            iid = get_issuer(None, dl.get("companyName") or sym)
            sec = get_security(iid, None, None, "delisted", dl.get("ipoDate") or None,
                               dl.get("delistedDate"))
            map_row(sec, dl.get("ipoDate") or SENTINEL, dl.get("delistedDate"), "delisted-feed")
            reason = "merger" if sym in deal_targets else "unknown"
            method = "rung1-pending-terms" if reason == "merger" else "rung3-flagged"
            cur.execute("""INSERT INTO delistings (security_id, delist_date, delist_reason,
                           terminal_method, source) VALUES (%s,%s,%s,%s,'feed')
                           ON CONFLICT (security_id) DO NOTHING""",
                        (sec, dl.get("delistedDate"), reason, method))
            quarantine("no-profile-anchors", {"delisted": dl})
        else:
            quarantine("no-profile", {})
        return "noprofile"

    cik = p.get("cik")
    active = bool(p.get("isActivelyTrading"))
    ipo = p.get("ipoDate") or None
    delist_date = dl.get("delistedDate") if dl else None
    reuse = bool(dl) and active
    chg = change_to.get(sym)

    iid = get_issuer(cik, p.get("companyName") or p.get("name") or sym)
    status = "active" if active else "delisted"
    sec = get_security(iid, p.get("cusip"), p.get("isin"), status, ipo,
                       None if active else delist_date)

    if reuse:
        if chg and delist_date and chg > delist_date:
            map_row(sec, chg, None, "feed")  # evidenced reuse (BBBY pattern)
            old_iid = get_issuer(None, (dl.get("companyName") or sym) + " (pre-reuse)")
            old_sec = get_security(old_iid, None, None, "delisted",
                                   dl.get("ipoDate") or None, delist_date)
            map_row_old_from = dl.get("ipoDate") or SENTINEL
            cur.execute("""INSERT INTO symbol_map (security_id, symbol, valid_from, valid_to, source)
                           VALUES (%s,%s,%s,%s,'delisted-feed')
                           ON CONFLICT (symbol, valid_from) DO NOTHING""",
                        (old_sec, sym, map_row_old_from, delist_date))
            cur.execute("""INSERT INTO delistings (security_id, delist_date, delist_reason,
                           terminal_method, source) VALUES (%s,%s,'unknown','rung3-flagged','feed')
                           ON CONFLICT (security_id) DO NOTHING""", (old_sec, delist_date))
            quarantine("reuse-historical-anchors", {"delisted": dl, "reuse_from": chg})
        else:
            map_row(sec, delist_date or chg or ipo or SENTINEL, None, "reuse-suspect")
            quarantine("reuse-suspect-unverified", {"delisted": dl, "change": chg})
        return "reuse"

    if active:
        vfrom = chg or ipo or SENTINEL
        src = "feed" if chg else ("profile-ipo" if ipo else "fallback")
        map_row(sec, vfrom, None, src)
    else:
        map_row(sec, ipo or SENTINEL, delist_date, "profile+feed" if dl else "profile-noend")
        if delist_date:
            reason = "merger" if sym in deal_targets else "unknown"
            method = "rung1-pending-terms" if reason == "merger" else "rung3-flagged"
            cur.execute("""INSERT INTO delistings (security_id, delist_date, delist_reason,
                           terminal_method, source) VALUES (%s,%s,%s,%s,'feed')
                           ON CONFLICT (security_id) DO NOTHING""",
                        (sec, delist_date, reason, method))
        else:
            quarantine("inactive-no-delist-date", {})

    cur.execute("""INSERT INTO profile_snapshots (security_id, asof, symbol, sector, industry,
                   exchange, is_active, is_adr, country, currency, ipo_date, raw)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (security_id, asof) DO UPDATE SET raw = EXCLUDED.raw""",
                (sec, TODAY, sym, p.get("sector"), p.get("industry"),
                 p.get("exchangeShortName") or p.get("exchange"), active,
                 p.get("isAdr"), p.get("country"), p.get("currency"), ipo,
                 json.dumps(p, default=str)))
    return "ok"


def chain_pass(db, edges):
    """Walk rename chains backwards: old symbols become closed rows on the
    same security as their successor."""
    def unit(cur):
        n = 0
        cur.execute("SELECT symbol, security_id, valid_from FROM symbol_map WHERE source='feed' AND valid_to IS NULL")
        heads = cur.fetchall()
        for sym, sec, vfrom in heads:
            cur_sym, cur_date = sym, str(vfrom)
            seen = set()
            while cur_sym in edges and cur_sym not in seen:
                seen.add(cur_sym)
                old, d = edges[cur_sym]
                if str(d) != cur_date:
                    break  # edge doesn't correspond to this row's start; stop
                prev = edges.get(old)
                old_from = str(prev[1]) if prev else SENTINEL
                cur.execute("""INSERT INTO symbol_map (security_id, symbol, valid_from, valid_to, source)
                               VALUES (%s,%s,%s,%s,'feed-chain')
                               ON CONFLICT (symbol, valid_from) DO NOTHING""",
                            (sec, old, old_from, d))
                n += 1
                cur_sym, cur_date = old, old_from
        return n
    print("  chain rows inserted: %d" % db.safe(unit))


def main():
    c = FMPClient(min_interval=0.12)
    db = RDB()
    banner("0. FEEDS + CENSUS")
    changes, delisted, deals = load_feeds(c)
    change_to = {}
    edges = {}
    for r in changes:
        o, n, d = r.get("oldSymbol"), r.get("newSymbol"), r.get("date")
        if n and d and (n not in change_to or d > change_to[n]):
            change_to[n] = d
            if o:
                edges[n] = (o, d)
    delist_by_sym = defaultdict(list)
    for r in delisted:
        ex = (r.get("exchange") or "").upper()
        if ex in MAJORS and r.get("symbol"):
            delist_by_sym[r["symbol"]].append(r)
    deal_targets = {d.get("targetedSymbol") for d in deals if d.get("targetedSymbol")}
    actives = census_symbols(c)
    targets = sorted(set(actives) | set(delist_by_sym))
    print("  targets: %d active + %d delisted-feed = %d unique" % (
        len(actives), len(delist_by_sym), len(targets)))

    done = db.safe(lambda cur: (cur.execute("SELECT DISTINCT symbol FROM symbol_map"),
                                {r[0] for r in cur.fetchall()})[1])
    qdone = db.safe(lambda cur: (cur.execute("SELECT DISTINCT symbol FROM identity_quarantine"),
                                 {r[0] for r in cur.fetchall()})[1])
    todo_all = [s for s in targets if s not in done and s not in qdone]
    print("  already resolved/quarantined: %d of %d targets" % (
        len(targets) - len(todo_all), len(targets)))
    limit = int(os.environ.get("IDENTITY_LIMIT", "0"))
    todo = todo_all[:limit] if limit else todo_all
    print("  processing now: %d" % len(todo))

    banner("1. SWEEP")
    ctx = {"change_to": change_to, "delist_by_sym": delist_by_sym, "deal_targets": deal_targets}
    counts = defaultdict(int)
    for i, sym in enumerate(todo):
        try:
            out = db.safe(lambda cur, s=sym: resolve_symbol(cur, s, c, ctx))
            counts[out] += 1
        except Exception as e:
            counts["error"] += 1
            print("  %s ERROR %s" % (sym, str(e)[:90]))
        if (i + 1) % 250 == 0:
            print("  ...%d/%d %s" % (i + 1, len(todo), dict(counts)))
    print("  sweep done: %s" % dict(counts))

    banner("2. CHAIN PASS (old tickers -> closed rows)")
    chain_pass(db, edges)

    banner("3. REPORT")
    def q1(sql):
        return db.safe(lambda cur: (cur.execute(sql), cur.fetchall())[1])
    print("  issuers: %s  securities: %s" % (q1("SELECT count(*) FROM issuers")[0][0],
                                             q1("SELECT count(*) FROM securities")[0][0]))
    print("  symbol_map: %s open / %s closed" % (
        q1("SELECT count(*) FROM symbol_map WHERE valid_to IS NULL")[0][0],
        q1("SELECT count(*) FROM symbol_map WHERE valid_to IS NOT NULL")[0][0]))
    print("  quarantine by issue: %s" % q1(
        "SELECT issue, count(*) FROM identity_quarantine GROUP BY issue ORDER BY 2 DESC"))
    print("  delistings: %s by method %s" % (
        q1("SELECT count(*) FROM delistings")[0][0],
        q1("SELECT terminal_method, count(*) FROM delistings GROUP BY 1")))

    banner("4. TORTURE SPOT-CHECK")
    def rid(sym, asof):
        rows = q1("""SELECT sm.security_id FROM symbol_map sm
                     WHERE sm.symbol='%s' AND sm.valid_from <= '%s'
                     AND (sm.valid_to IS NULL OR sm.valid_to >= '%s')""" % (sym, asof, asof))
        return [r[0] for r in rows]
    unresolved = [s["symbol"] for s in SAMPLE if not rid(s["symbol"], TODAY) and not rid(s["symbol"], "2022-06-01")]
    print("  torture names resolving (today or 2022): %d/%d; unresolved: %s" % (
        len(SAMPLE) - len(unresolved), len(SAMPLE), unresolved))
    fb, meta = rid("FB", "2021-06-01"), rid("META", TODAY)
    sq, xyz = rid("SQ", "2024-06-01"), rid("XYZ", TODAY)
    print("  chain proof FB(2021)==META(now): %s  (%s vs %s)" % (bool(fb and meta and fb[0] == meta[0]), fb, meta))
    print("  chain proof SQ(2024)==XYZ(now): %s  (%s vs %s)" % (bool(sq and xyz and sq[0] == xyz[0]), sq, xyz))
    db.close()


if __name__ == "__main__":
    main()
