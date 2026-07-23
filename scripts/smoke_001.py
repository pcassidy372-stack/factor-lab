"""Smoke test for migration 001: seed the two EDGAR-confirmed chimeras
(BBBY, SBNY) as the first hand-verified symbol_map entries, then prove
date-effective resolution: same ticker, different security_id by asof."""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn
from factorlab.fmp_client import ART


def feed_change_date(new_symbol):
    p = ART / "symbol_change_full.csv"
    if not p.exists():
        return None
    with open(p) as f:
        for row in csv.DictReader(f):
            if row.get("newSymbol") == new_symbol:
                return row.get("date")
    return None


def main():
    cx = conn()
    cx.autocommit = True
    cur = cx.cursor()

    def issuer(cik, name):
        cur.execute("""INSERT INTO issuers (cik, name) VALUES (%s, %s)
                       ON CONFLICT (cik) DO UPDATE SET name = EXCLUDED.name
                       RETURNING issuer_id""", (cik, name))
        return cur.fetchone()[0]

    def security(issuer_id, status):
        cur.execute("""SELECT security_id FROM securities WHERE issuer_id=%s""", (issuer_id,))
        r = cur.fetchone()
        if r:
            return r[0]
        cur.execute("""INSERT INTO securities (issuer_id, status) VALUES (%s, %s)
                       RETURNING security_id""", (issuer_id, status))
        return cur.fetchone()[0]

    def map_symbol(sec, sym, vfrom, vto, source):
        cur.execute("""INSERT INTO symbol_map (security_id, symbol, valid_from, valid_to, source)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (symbol, valid_from) DO NOTHING""",
                    (sec, sym, vfrom, vto, source))

    # --- BBBY: two issuers, one ticker (R12, EDGAR-confirmed) ---
    orig = issuer("0000886158", "20230930-DK-Butterfly-1, Inc. (orig. Bed Bath & Beyond)")
    orig_sec = security(orig, "shell")
    map_symbol(orig_sec, "BBBY", "1992-06-01", "2023-05-03", "manual-edgar-verified")
    cur.execute("""INSERT INTO delistings (security_id, delist_date, delist_reason,
                   terminal_method, source)
                   VALUES (%s, '2023-05-03', 'bankruptcy', 'rung3-flagged', 'manual')
                   ON CONFLICT (security_id) DO NOTHING""", (orig_sec,))

    reuse_date = feed_change_date("BBBY") or "2025-01-01"
    src = "feed" if reuse_date != "2025-01-01" else "manual-VERIFY-DATE"
    new = issuer("0001130713", "BED BATH & BEYOND, INC. (ex-Overstock/Beyond)")
    new_sec = security(new, "active")
    map_symbol(new_sec, "BBBY", reuse_date, None, src)
    print("BBBY reuse valid_from = %s (source=%s)" % (reuse_date, src))

    # --- SBNY: failed bank, ticker reused (R12) ---
    sb = issuer("0001288784", "Signature Bank (failed 2023) - VERIFY CIK")
    sb_sec = security(sb, "delisted")
    map_symbol(sb_sec, "SBNY", "2004-03-23", "2023-03-13", "manual-VERIFY")
    cur.execute("""INSERT INTO delistings (security_id, delist_date, delist_reason,
                   terminal_method, source)
                   VALUES (%s, '2023-03-13', 'bankruptcy', 'rung3-flagged', 'manual')
                   ON CONFLICT (security_id) DO NOTHING""", (sb_sec,))
    sb_reuse = feed_change_date("SBNY")
    if sb_reuse:
        print("SBNY reuse found in feed: valid_from = %s (new issuer row deferred to ingestion)" % sb_reuse)
    else:
        print("SBNY reuse NOT in feed - flag for manual mapping at ingestion")

    # --- the proof: date-effective resolution ---
    print("\nresolution proof (symbol='BBBY'):")
    for asof in ("2022-06-01", "2026-06-01"):
        cur.execute("""SELECT sm.security_id, i.name
                       FROM symbol_map sm
                       JOIN securities s ON s.security_id = sm.security_id
                       JOIN issuers i    ON i.issuer_id   = s.issuer_id
                       WHERE sm.symbol = 'BBBY'
                         AND sm.valid_from <= %s
                         AND (sm.valid_to IS NULL OR sm.valid_to >= %s)""", (asof, asof))
        rows = cur.fetchall()
        print("  asof %s -> %s" % (asof, rows))
    cur.execute("SELECT count(*) FROM symbol_map")
    print("\nsymbol_map rows: %s  -- smoke OK if the two asofs resolve to DIFFERENT ids" % cur.fetchone()[0])
    cx.close()


if __name__ == "__main__":
    main()
