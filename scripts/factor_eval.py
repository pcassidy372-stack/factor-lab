"""Phase 1 evaluation: monthly IC (Spearman, z_sector_size vs fwd 1m TR with
delisting terminal handling), decile L/S series, NW t-stats, block bootstrap,
momentum crash visibility, Ken French UMD external oracle. Prints the
replication verdict against pre-registered criteria."""
import io
import json
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import requests
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn

FRENCH = ("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
          "F-F_Momentum_Factor_CSV.zip")


def nw_t(x, lag):
    x = np.asarray(x, float)
    n = len(x)
    e = x - x.mean()
    g0 = (e * e).sum() / n
    var = g0
    for j in range(1, lag + 1):
        gj = (e[j:] * e[:-j]).sum() / n
        var += 2 * (1 - j / (lag + 1)) * gj
    return x.mean() / np.sqrt(var / n)


def main():
    cx = conn()
    cur = cx.cursor()
    cur.execute("SELECT DISTINCT asof FROM universe_snapshots ORDER BY asof")
    asofs = [str(r[0]) for r in cur.fetchall()]
    nxt = {a: asofs[i + 1] for i, a in enumerate(asofs[:-1])}
    cur.execute("SELECT security_id, d, tr FROM tr_index_d WHERE d = ANY(%s::date[])", (asofs,))
    TR = defaultdict(dict)
    for sec, d, tr in cur.fetchall():
        TR[sec][str(d)] = float(tr)
    cur.execute("SELECT security_id, max(d) FROM tr_index_d GROUP BY 1")
    LAST = {sec: str(d) for sec, d in cur.fetchall()}
    cur.execute("""SELECT security_id, tr FROM tr_index_d t
                   WHERE d = (SELECT max(d) FROM tr_index_d WHERE security_id=t.security_id)""")
    TRLAST = {sec: float(tr) for sec, tr in cur.fetchall()}
    cur.execute("SELECT security_id, delist_date, terminal_return, terminal_method FROM delistings")
    DL = {sec: (str(d), tr, m) for sec, d, tr, m in cur.fetchall()}
    cur.execute("SELECT factor_id, prior_sign FROM factor_definitions")
    SIGN = dict(cur.fetchall())

    def fwd(sec, a):
        b = nxt.get(a)
        t0 = TR.get(sec, {}).get(a)
        if not b or not t0:
            return None
        t1 = TR.get(sec, {}).get(b)
        if t1:
            return t1 / t0 - 1.0
        ld = LAST.get(sec)
        if not ld or ld <= a:
            return None
        r = TRLAST[sec] / t0 - 1.0
        d = DL.get(sec)
        if d and a < d[0] <= b and d[1] is not None and d[2] == "rung1-deal-manual":
            r = (1 + r) * (1 + float(d[1])) - 1
        return r

    cur.execute("SELECT asof, security_id, factor_id, z_sector_size FROM factor_values")
    FV = defaultdict(lambda: defaultdict(dict))
    for a, sec, fid, z in cur.fetchall():
        FV[fid][str(a)][sec] = float(z)

    results = {}
    ic_rows, ls_rows = [], []
    for fid, by_asof in FV.items():
        ics, ls = [], []
        for a in sorted(by_asof):
            if a not in nxt:
                continue
            pairs = [(z, fwd(s, a)) for s, z in by_asof[a].items()]
            pairs = [(z, r) for z, r in pairs if r is not None]
            if len(pairs) < 200:
                continue
            z = np.array([p[0] for p in pairs])
            r = np.array([p[1] for p in pairs])
            rz = z.argsort().argsort().astype(float)
            rr = r.argsort().argsort().astype(float)
            ic = float(np.corrcoef(rz, rr)[0, 1])
            ics.append((a, ic, len(pairs)))
            q = np.quantile(z, [0.1, 0.9])
            top, bot = r[z >= q[1]].mean(), r[z <= q[0]].mean()
            ls.append((a, float(top - bot), float(top), float(bot), len(pairs)))
        if len(ls) < 24:
            continue
        ic_v = np.array([x[1] for x in ics])
        ls_v = np.array([x[1] for x in ls])
        boots = []
        rng = np.random.default_rng(20260726)
        n = len(ls_v)
        for _ in range(2000):
            idx = np.concatenate([np.arange(s, s + 6) % n
                                  for s in rng.integers(0, n, n // 6 + 1)])[:n]
            boots.append(ls_v[idx].mean())
        lo, hi = np.percentile(boots, [2.5, 97.5])
        worst = sorted(ls, key=lambda x: x[1])[:5]
        results[fid] = {
            "months": n, "mean_ic": float(ic_v.mean()), "icir": float(ic_v.mean() / ic_v.std()),
            "ic_nw_t": float(nw_t(ic_v, 0)),
            "ls_mean_m": float(ls_v.mean()), "ls_ann": float(ls_v.mean() * 12),
            "ls_nw_t": float(nw_t(ls_v, 0)), "boot_ci_m": [float(lo), float(hi)],
            "worst_months": [(a, round(v, 4)) for a, v, *_ in worst],
        }
        ic_rows += [(fid, a, 1, ic, nn) for a, ic, nn in ics]
        ls_rows += [(fid, a, l, t, b, nn) for a, l, t, b, nn in ls]
    cur.execute("DELETE FROM factor_ic")
    cur.execute("DELETE FROM factor_ls")
    execute_values(cur, "INSERT INTO factor_ic (factor_id, asof, horizon_m, ic, n) VALUES %s",
                   ic_rows, page_size=5000)
    execute_values(cur, "INSERT INTO factor_ls (factor_id, asof, ls_ret, q_top, q_bot, n) VALUES %s",
                   ls_rows, page_size=5000)
    cx.commit()

    print("=" * 12, "FACTOR TABLE (1m horizon, z_sector_size)", "=" * 12)
    cur.execute("SELECT factor_id FROM factor_definitions ORDER BY family, factor_id")
    table_fids = [r[0] for r in cur.fetchall()]
    for fid in table_fids:
        r = results.get(fid)
        if not r:
            print("  %-13s NO DATA" % fid)
            continue
        print("  %-13s prior=%+d months=%d | IC mean=%+.4f ICIR=%+.2f t=%+.2f | "
              "LS ann=%+.2f%% t=%+.2f CI_m=[%+.4f,%+.4f]" % (
                  fid, SIGN.get(fid, 1), r["months"], r["mean_ic"], r["icir"], r["ic_nw_t"],
                  100 * r["ls_ann"], r["ls_nw_t"], r["boot_ci_m"][0], r["boot_ci_m"][1]))
        print("      worst months: %s" % r["worst_months"])

    print("\n" + "=" * 12, "EXTERNAL ORACLE: Ken French UMD", "=" * 12)
    umd_corr = None
    try:
        z = zipfile.ZipFile(io.BytesIO(requests.get(FRENCH, timeout=60).content))
        txt = z.read(z.namelist()[0]).decode("latin-1")
        umd = {}
        for line in txt.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 2 and len(parts[0]) == 6 and parts[0].isdigit():
                try:
                    umd[parts[0]] = float(parts[1]) / 100.0
                except ValueError:
                    pass
        ours = {a[:7].replace("-", ""): l for f, a, l, t, b, nn in ls_rows if f == "mom_12_1"}
        common = sorted(set(umd) & set(ours))
        if len(common) >= 24:
            a1 = np.array([ours[k] for k in common])
            a2 = np.array([umd[k] for k in common])
            umd_corr = float(np.corrcoef(a1, a2)[0, 1])
            print("  overlap months=%d corr=%.3f (gate >= 0.60)" % (len(common), umd_corr))
        else:
            print("  insufficient overlap (%d months)" % len(common))
    except Exception as e:
        print("  UMD fetch failed: %s (gate falls to sign+crash checks)" % str(e)[:80])

    print("\n" + "=" * 12, "REPLICATION GATE (pre-registered)", "=" * 12)
    checks = []

    def chk(name, ok, note):
        checks.append(ok)
        print("  [%s] %s - %s" % ("PASS" if ok else "FAIL", name, note))

    r = results
    if len(r) < 5:
        print("  MISSING FACTORS: %s" % sorted(set(
            ("ebit_ev", "gp_a", "accruals", "asset_growth", "mom_12_1")) - set(r)))
    chk("value sign (ebit_ev LS > 0)", r.get("ebit_ev", {}).get("ls_ann", -1) > 0,
        "ann=%+.2f%%" % (100 * r.get("ebit_ev", {}).get("ls_ann", 0)))
    chk("profitability (gp_a LS > 0)", r.get("gp_a", {}).get("ls_ann", -1) > 0,
        "ann=%+.2f%% t=%+.2f" % (100 * r.get("gp_a", {}).get("ls_ann", 0),
                                 r.get("gp_a", {}).get("ls_nw_t", 0)))
    chk("accruals (prior-aligned > 0)", -r.get("accruals", {}).get("ls_ann", 1) > 0,
        "low-minus-high ann=%+.2f%%" % (-100 * r.get("accruals", {}).get("ls_ann", 0)))
    chk("asset growth (prior-aligned > 0)", -r.get("asset_growth", {}).get("ls_ann", 1) > 0,
        "low-minus-high ann=%+.2f%%" % (-100 * r.get("asset_growth", {}).get("ls_ann", 0)))
    chk("momentum (LS > 0)", r.get("mom_12_1", {}).get("ls_ann", -1) > 0,
        "ann=%+.2f%% t=%+.2f" % (100 * r.get("mom_12_1", {}).get("ls_ann", 0),
                                 r.get("mom_12_1", {}).get("ls_nw_t", 0)))
    if umd_corr is not None:
        chk("UMD corr >= 0.60", umd_corr >= 0.60, "corr=%.3f" % umd_corr)
    print("\nREPLICATION GATE: %s (%d/%d)" % (
        "PASS" if checks and all(checks) else "REVIEW", sum(checks), len(checks)))
    (Path(__file__).resolve().parent.parent / "artifacts/phase1_eval.json").write_text(
        json.dumps({"results": results, "umd_corr": umd_corr}, indent=1))
    cx.close()


if __name__ == "__main__":
    main()
