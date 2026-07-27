"""The one true point-in-time fundamentals selector (code-review P0-3/P0-4).
Load ALL vintages; at each asof, first filter to rows visible by the
16:00-ET information cutoff, then take the max vintage per fiscal period.
Used by factor_compute and by golden-gate T10's synthetic restatement test."""

FIELDS = ("revenue", "gross_profit", "ebit", "net_income", "cfo", "capex",
          "total_assets", "total_debt", "cash", "equity", "shares_dil")


def load_fundamentals(cur):
    """-> {sec: [(pe, vintage, accepted_iso_ts, f0..f10), ...]} ALL vintages."""
    cur.execute("""SELECT security_id, fiscal_period_end, vintage_id,
                   accepted_date, revenue, gross_profit, ebit, net_income,
                   cfo, capex, total_assets, total_debt, cash, equity, shares_dil
                   FROM fundamentals_q WHERE timing_pit""")
    out = {}
    for row in cur.fetchall():
        sec, pe, v, acc = row[0], str(row[1]), row[2], row[3]
        rec = (pe, v, str(acc)) + tuple(None if x is None else float(x) for x in row[4:])
        out.setdefault(sec, []).append(rec)
    for sec in out:
        out[sec].sort()
    return out


def visible_at(rows, asof):
    """Rows visible at asof close under the 16:00 ET cutoff, max vintage per
    period, ascending by period end. accepted timestamps are vendor ET."""
    cutoff_date, cutoff_time = asof, "16:00:00"
    best = {}
    for rec in rows:
        pe, v, acc = rec[0], rec[1], rec[2]
        d, t = acc[:10], (acc[11:19] or "00:00:00")
        if d > cutoff_date or (d == cutoff_date and t > cutoff_time):
            continue
        cur = best.get(pe)
        if cur is None or v > cur[1]:
            best[pe] = rec
    return [best[pe] for pe in sorted(best)]
