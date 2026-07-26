"""Ground-truth momentum audit. Hand-builds raw 12-1 decile LS from
tr_index_d + universe with explicit dates; prints named months; tests
UMD keying at lags -1/0/+1; compares to the stored factor_ls series.
Read-only."""
import io
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn

FRENCH = ("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
          "F-F_Momentum_Factor_CSV.zip")


def main():
    cx = conn()
    cur = cx.cursor()
    cur.execute("SELECT DISTINCT asof FROM universe_snapshots ORDER BY asof")
    asofs = [str(r[0]) for r in cur.fetchall()]
    cur.execute("SELECT asof, security_id FROM universe_snapshots WHERE in_universe")
    uni = defaultdict(set)
    for a, sec in cur.fetchall():
        uni[str(a)].add(sec)
    cur.execute("SELECT security_id, d, tr FROM tr_index_d WHERE d = ANY(%s::date[])", (asofs,))
    TR = defaultdict(dict)
    for sec, d, tr in cur.fetchall():
        TR[sec][str(d)] = float(tr)

    hand = {}
    for ai in range(12, len(asofs) - 1):
        a, a1, a12, nx = asofs[ai], asofs[ai - 1], asofs[ai - 12], asofs[ai + 1]
        rows = []
        for sec in uni.get(a, ()):  
            g = TR.get(sec, {})
            t0, t1, ta, tn = g.get(a12), g.get(a1), g.get(a), g.get(nx)
            if t0 and t1 and ta and tn:
                rows.append((t1 / t0 - 1.0, tn / ta - 1.0))
        if len(rows) < 200:
            continue
        rows.sort(key=lambda x: x[0])
        k = len(rows) // 10
        bot = np.mean([r for _, r in rows[:k]])
        top = np.mean([r for _, r in rows[-k:]])
        hand[a] = {"ls": float(top - bot), "n": len(rows),
                   "ret_month": nx[:7], "sig_month": a[:7]}
    print("hand series: %d months" % len(hand))
    for probe in ("2020-10-30", "2020-11-30", "2020-12-31", "2021-01-29",
                  "2022-11-30", "2022-12-30", "2026-05-29", "2026-06-30"):
        m = [a for a in hand if a.startswith(probe[:7])]
        for a in m:
            h = hand[a]
            print("  asof %s (ret accrues %s): hand LS=%+.4f n=%d" % (
                a, h["ret_month"], h["ls"], h["n"]))

    z = zipfile.ZipFile(io.BytesIO(requests.get(FRENCH, timeout=60).content))
    txt = z.read(z.namelist()[0]).decode("latin-1")
    umd = {}
    for line in txt.splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) == 2 and len(p[0]) == 6 and p[0].isdigit():
            try:
                umd[p[0]] = float(p[1]) / 100.0
            except ValueError:
                pass
    print("\nFrench UMD, named months:")
    for k in ("202010", "202011", "202012", "202101", "202211", "202212", "202605", "202606"):
        print("  %s: %+.4f" % (k, umd.get(k, float("nan"))))

    def corr(key_by):
        pairs = []
        for a, h in hand.items():
            k = h[key_by].replace("-", "")
            if k in umd:
                pairs.append((h["ls"], umd[k]))
        if len(pairs) < 24:
            return None, 0
        x = np.array([p[0] for p in pairs])
        y = np.array([p[1] for p in pairs])
        return float(np.corrcoef(x, y)[0, 1]), len(pairs)
    c_ret, n_ret = corr("ret_month")
    c_sig, n_sig = corr("sig_month")
    print("\nhand vs French keyed by RETURN month (correct): corr=%s n=%d" % (c_ret, n_ret))
    print("hand vs French keyed by SIGNAL month (shipped eval's keying): corr=%s n=%d" % (c_sig, n_sig))

    cur.execute("SELECT asof, ls_ret FROM factor_ls WHERE factor_id='mom_12_1' ORDER BY asof")
    stored = {str(a): float(v) for a, v in cur.fetchall()}
    both = sorted(set(stored) & set(hand))
    if len(both) >= 24:
        x = np.array([stored[a] for a in both])
        y = np.array([hand[a]["ls"] for a in both])
        print("stored(z_sector_size) vs hand(raw): corr=%.3f n=%d" % (
            float(np.corrcoef(x, y)[0, 1]), len(both)))
        print("stored at named months:")
        for a in both:
            if a[:7] in ("2020-11", "2021-01", "2022-12", "2026-06"):
                print("  %s stored=%+.4f hand=%+.4f" % (a, stored[a], hand[a]["ls"]))
    cx.close()


if __name__ == "__main__":
    main()
