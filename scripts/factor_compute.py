"""Phase 1 factor engine: monthly cross-sections for the frozen registry.
PIT statement selection (accepted<=asof, timing_pit, max vintage), TTM with
span guards, validity rules -> NA not tails, then the pipeline: winsorize
1/99 -> rank-normal -> sector-demean -> sector+size neutralize.
Deterministic full rebuild (DELETE + recompute)."""
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn

ND = statistics.NormalDist()


def main():
    cx = conn()
    cur = cx.cursor()
    print("loading...")
    cur.execute("SELECT DISTINCT asof FROM universe_snapshots ORDER BY asof")
    asofs = [str(r[0]) for r in cur.fetchall()]
    cur.execute("""SELECT asof, security_id, mktcap FROM universe_snapshots
                   WHERE in_universe AND mktcap IS NOT NULL""")
    uni = defaultdict(dict)
    for a, sec, mc in cur.fetchall():
        uni[str(a)][sec] = float(mc)
    cur.execute("""SELECT DISTINCT ON (security_id) security_id, sector
                   FROM profile_snapshots ORDER BY security_id, asof DESC""")
    sector = {sec: (s or "Unknown") for sec, s in cur.fetchall()}
    fin = {sec for sec, s in sector.items() if "financ" in s.lower()}
    cur.execute("""SELECT security_id, fiscal_period_end, vintage_id,
                   accepted_date::date, revenue, gross_profit, ebit, net_income,
                   cfo, total_assets, total_debt, cash
                   FROM fundamentals_q WHERE timing_pit""")
    F = defaultdict(dict)
    for row in cur.fetchall():
        sec, pe, v = row[0], str(row[1]), row[2]
        curr = F[sec].get(pe)
        if curr is None or v > curr[0]:
            F[sec][pe] = (v, str(row[3])) + tuple(None if x is None else float(x) for x in row[4:])
    for sec in F:
        F[sec] = sorted(((pe,) + rec for pe, rec in F[sec].items()))
    print("  asofs=%d, fundamentals for %d securities" % (len(asofs), len(F)))
    all_secs = sorted({s for a in uni.values() for s in a})
    cur.execute("""SELECT security_id, d, tr FROM tr_index_d
                   WHERE d = ANY(%s::date[])""", (asofs,))
    TR = defaultdict(dict)
    for sec, d, tr in cur.fetchall():
        TR[sec][str(d)] = float(tr)
    print("  tr grid cells: %d" % sum(len(v) for v in TR.values()))

    def visible(sec, asof):
        rows = [r for r in F.get(sec, []) if r[2] <= asof]
        return rows[-6:]

    def dur(a, b):
        from datetime import date
        return (date.fromisoformat(b) - date.fromisoformat(a)).days

    cur2 = cx.cursor()
    cur2.execute("DELETE FROM factor_values")
    cur2.execute("DELETE FROM factor_ic")
    cur2.execute("DELETE FROM factor_ls")
    cx.commit()

    out_rows = []
    for ai, asof in enumerate(asofs):
        members = uni.get(asof, {})
        if not members:
            continue
        raw = {f: {} for f in ("ebit_ev", "gp_a", "accruals", "asset_growth", "mom_12_1")}
        for sec, mc in members.items():
            rows = visible(sec, asof)
            if rows:
                last4 = rows[-4:]
                ok_ttm = len(last4) == 4 and 240 <= dur(last4[0][0], last4[3][0]) <= 390
                pe, _, _, rev, gp, ebit, ni, cfo, ta, td, cash = rows[-1]
                if ok_ttm and sec not in fin:
                    s = lambda i: (sum(r[i] for r in last4 if r[i] is not None)
                                   if all(r[i] is not None for r in last4) else None)
                    ebit_t, gp_t, ni_t, cfo_t = s(5), s(4), s(6), s(7)
                    if ebit_t is not None and ta and td is not None and cash is not None:
                        ev = mc + td - cash
                        if ev > max(1e8, 0.05 * mc):
                            raw["ebit_ev"][sec] = ebit_t / ev
                    if gp_t is not None and ta:
                        raw["gp_a"][sec] = gp_t / ta
                    prior = [r for r in rows if 300 <= dur(r[0], pe) <= 430]
                    ta_prior = prior[-1][8] if prior and prior[-1][8] else None
                    if ni_t is not None and cfo_t is not None and ta:
                        base = (ta + ta_prior) / 2 if ta_prior else ta
                        raw["accruals"][sec] = (ni_t - cfo_t) / base
                    if ta and ta_prior:
                        raw["asset_growth"][sec] = ta / ta_prior - 1.0
            if ai >= 12:
                t0, t1 = TR.get(sec, {}).get(asofs[ai - 12]), TR.get(sec, {}).get(asofs[ai - 1])
                if t0 and t1:
                    raw["mom_12_1"][sec] = t1 / t0 - 1.0

        for fid, vals in raw.items():
            eligible = len(members) - (len(set(members) & fin) if fid != "mom_12_1" else 0)
            if eligible == 0 or len(vals) / eligible < 0.6:
                continue
            secs = sorted(vals)
            v = np.array([vals[s] for s in secs], float)
            lo, hi = np.percentile(v, [1, 99])
            v = np.clip(v, lo, hi)
            order = v.argsort().argsort()
            rn = np.array([ND.inv_cdf((r + 0.5) / len(v)) for r in order])
            sects = np.array([sector.get(s, "Unknown") for s in secs])
            zs = rn.copy()
            for sg in set(sects):
                m = sects == sg
                zs[m] -= zs[m].mean()
            if zs.std() > 1e-9:
                zs /= zs.std()
            lmc = np.log(np.array([members[s] for s in secs]))
            x = lmc.copy()
            for sg in set(sects):
                m = sects == sg
                x[m] -= x[m].mean()
            beta = (zs * x).sum() / max((x * x).sum(), 1e-12)
            zss = zs - beta * x
            if zss.std() > 1e-9:
                zss /= zss.std()
            out_rows.extend((asof, s, fid, float(a), float(b), float(c), float(d))
                            for s, a, b, c, d in zip(secs, v, rn, zs, zss))
        if (ai + 1) % 24 == 0:
            print("  asof %s: members=%d cells so far=%d" % (asof, len(members), len(out_rows)))
        if len(out_rows) >= 200000:
            execute_values(cur2, """INSERT INTO factor_values
                (asof, security_id, factor_id, raw, rank_norm, z_sector, z_sector_size)
                VALUES %s""", out_rows, page_size=10000)
            cx.commit()
            out_rows = []
    if out_rows:
        execute_values(cur2, """INSERT INTO factor_values
            (asof, security_id, factor_id, raw, rank_norm, z_sector, z_sector_size)
            VALUES %s""", out_rows, page_size=10000)
        cx.commit()
    cur2.execute("SELECT factor_id, count(*), count(DISTINCT asof) FROM factor_values GROUP BY 1")
    print("factor_values:", cur2.fetchall())
    cx.close()


if __name__ == "__main__":
    main()
