"""Factor engine v2 — supersedes factor_compute.py. Computes ALL registered
factors (canonical five + 1b five), eligibility from registry params
(excl_financials), same pipeline: winsorize 1/99 -> rank-normal ->
sector-demean -> sector+size neutralize. Deterministic full rebuild."""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn

ND = statistics.NormalDist()


def dur(a, b):
    from datetime import date
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def main():
    cx = conn()
    cur = cx.cursor()
    print("loading...")
    cur.execute("SELECT factor_id, params FROM factor_definitions")
    REG = {fid: (p if isinstance(p, dict) else json.loads(p)) for fid, p in cur.fetchall()}
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
    cur.execute("""SELECT security_id, fiscal_period_end, vintage_id, accepted_date::date,
                   revenue, gross_profit, ebit, net_income, cfo, total_assets,
                   total_debt, cash, equity, shares_dil
                   FROM fundamentals_q WHERE timing_pit""")
    F = defaultdict(dict)
    for row in cur.fetchall():
        sec, pe, v = row[0], str(row[1]), row[2]
        curr = F[sec].get(pe)
        if curr is None or v > curr[0]:
            F[sec][pe] = (v, str(row[3])) + tuple(None if x is None else float(x) for x in row[4:])
    for sec in F:
        F[sec] = sorted(((pe,) + rec for pe, rec in F[sec].items()))
    cur.execute("SELECT security_id, d, tr FROM tr_index_d WHERE d = ANY(%s::date[])", (asofs,))
    TR = defaultdict(dict)
    for sec, d, tr in cur.fetchall():
        TR[sec][str(d)] = float(tr)
    cur.execute("SELECT security_id, report_date, sue FROM surprises WHERE sue IS NOT NULL")
    SUE = defaultdict(list)
    for sec, d, s in cur.fetchall():
        SUE[sec].append((str(d), float(s)))
    for sec in SUE:
        SUE[sec].sort()
    cur.execute("SELECT asof, tr FROM benchmarks_m WHERE symbol='SPY' ORDER BY asof")
    SPY = {str(a): float(t) for a, t in cur.fetchall()}
    spy_ret = {}
    sk = sorted(SPY)
    for i in range(1, len(sk)):
        spy_ret[sk[i]] = SPY[sk[i]] / SPY[sk[i - 1]] - 1.0
    print("  asofs=%d F=%d TRcells=%d SUEsecs=%d SPYm=%d" % (
        len(asofs), len(F), sum(len(v) for v in TR.values()), len(SUE), len(spy_ret)))

    cur2 = cx.cursor()
    for t in ("factor_values", "factor_ic", "factor_ls"):
        cur2.execute("DELETE FROM %s" % t)
    cx.commit()

    def visible(sec, asof):
        rows = [r for r in F.get(sec, []) if r[2] <= asof]
        return rows[-6:]

    out = []
    for ai, asof in enumerate(asofs):
        members = uni.get(asof, {})
        if not members:
            continue
        raw = defaultdict(dict)
        for sec, mc in members.items():
            rows = visible(sec, asof)
            if rows:
                pe = rows[-1][0]
                _, _, _, rev, gp, ebit, ni, cfo, ta, td, cash, eq, sh = rows[-1]
                last4 = rows[-4:]
                ok_ttm = len(last4) == 4 and 240 <= dur(last4[0][0], last4[3][0]) <= 390
                prior = [r for r in rows if 300 <= dur(r[0], pe) <= 430]
                if ok_ttm and sec not in fin:
                    s4 = lambda i: (sum(r[i] for r in last4 if r[i] is not None)
                                    if all(r[i] is not None for r in last4) else None)
                    ebit_t, gp_t, ni_t, cfo_t = s4(5), s4(4), s4(6), s4(7)
                    if ebit_t is not None and ta and td is not None and cash is not None:
                        ev = mc + td - cash
                        if ev > max(1e8, 0.05 * mc):
                            raw["ebit_ev"][sec] = ebit_t / ev
                    if gp_t is not None and ta:
                        raw["gp_a"][sec] = gp_t / ta
                    ta_prior = prior[-1][8] if prior and prior[-1][8] else None
                    if ni_t is not None and cfo_t is not None and ta:
                        raw["accruals"][sec] = (ni_t - cfo_t) / ((ta + ta_prior) / 2 if ta_prior else ta)
                    if ta and ta_prior:
                        raw["asset_growth"][sec] = ta / ta_prior - 1.0
                if eq and eq > 0:
                    raw["bp"][sec] = eq / mc
                sh_prior = prior[-1][12] if prior and prior[-1][12] else None
                if sh and sh_prior and sh_prior > 0:
                    raw["net_issuance"][sec] = sh / sh_prior - 1.0
            ev = SUE.get(sec)
            if ev:
                cand = [x for x in ev if x[0] <= asof and dur(x[0], asof) <= 140]
                if cand:
                    raw["sue"][sec] = cand[-1][1]
            if ai >= 12:
                grid = TR.get(sec, {})
                t0, t1 = grid.get(asofs[ai - 12]), grid.get(asofs[ai - 1])
                if t0 and t1:
                    raw["mom_12_1"][sec] = t1 / t0 - 1.0
                rets = []
                for j in range(max(1, ai - 35), ai + 1):
                    a0, a1 = grid.get(asofs[j - 1]), grid.get(asofs[j])
                    rets.append((asofs[j], a1 / a0 - 1.0) if a0 and a1 else (asofs[j], None))
                r12 = [r for _, r in rets[-12:] if r is not None]
                if len(r12) == 12:
                    raw["vol_12m"][sec] = float(np.std(r12, ddof=1) * np.sqrt(12))
                pairs = [(r, spy_ret.get(a)) for a, r in rets
                         if r is not None and spy_ret.get(a) is not None]
                if len(pairs) >= 24:
                    rv = np.array([p[0] for p in pairs])
                    sv = np.array([p[1] for p in pairs])
                    var = sv.var()
                    if var > 1e-10:
                        raw["beta_36m"][sec] = float(np.cov(rv, sv)[0, 1] / var)

        for fid, vals in raw.items():
            excl_fin = REG.get(fid, {}).get("excl_financials", False)
            eligible = len(members) - (len(set(members) & fin) if excl_fin else 0)
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
            out.extend((asof, s, fid, float(a), float(b), float(c), float(d))
                       for s, a, b, c, d in zip(secs, v, rn, zs, zss))
        if (ai + 1) % 24 == 0:
            print("  asof %s: members=%d cells=%d" % (asof, len(members), len(out)))
        if len(out) >= 250000:
            execute_values(cur2, """INSERT INTO factor_values
                (asof, security_id, factor_id, raw, rank_norm, z_sector, z_sector_size)
                VALUES %s""", out, page_size=10000)
            cx.commit()
            out = []
    if out:
        execute_values(cur2, """INSERT INTO factor_values
            (asof, security_id, factor_id, raw, rank_norm, z_sector, z_sector_size)
            VALUES %s""", out, page_size=10000)
        cx.commit()
    cur2.execute("""SELECT factor_id, count(*), count(DISTINCT asof), min(asof)
                    FROM factor_values GROUP BY 1 ORDER BY 1""")
    for r in cur2.fetchall():
        print("  %s" % (r,))
    cx.close()


if __name__ == "__main__":
    main()
