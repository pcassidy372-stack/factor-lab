"""Session 2: the vintage-preservation verdict (spec section 5, Phase -1).

Question: when FMP serves KHC's 2016-2017 figures today, are the VALUES the
originally-filed ones or the 2019-restated ones - and do the acceptedDate
stamps match the original filings or the restatement?

Ground truth: SEC XBRL companyconcept facts (every filed instance of each
figure, with accession, form, and filed date).

Plus: SMCI acceptedDate reality check, as-reported blob inspection, BBBY
CIK identity resolution, and path discovery for the session-1 404s.
"""
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.fmp_client import FMPClient, ART

UA = "factor_lab phase-1 research probe (contact: YOUR_EMAIL_HERE)"
KHC_CIK = "0001637459"
BBBY_SERVED_CIK = "0001130713"    # what FMP's BBBY profile returned in session 1
BBBY_ORIGINAL_CIK = "0000886158"  # believed original Bed Bath & Beyond - EDGAR settles it


def banner(t):
    print("\n" + "=" * 8 + " " + t + " " + "=" * 8)


def edgar(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return r.json()


def dur_days(a, b):
    ya, ma, da = map(int, a.split("-"))
    yb, mb, db = map(int, b.split("-"))
    return (date(yb, mb, db) - date(ya, ma, da)).days


def close(a, b):
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= max(1e6, 0.005 * abs(float(b)))


def main():
    assert "YOUR_EMAIL_HERE" not in UA, "set a real contact in UA - SEC requires it"
    c = FMPClient()
    report = {}

    banner("A. FMP-SERVED KHC HISTORY (2016-2017 focus)")
    q = c.get("income_q", symbol="KHC", limit=46)
    a = c.get("income_a", symbol="KHC", limit=10)
    fmp_rows = {}
    for r in q + a:
        d = r.get("date") or ""
        if d.startswith(("2016", "2017")):
            fmp_rows[(d, r.get("period"))] = r
            print("  %s %-3s filed=%s accepted=%s NI=%s Rev=%s" % (
                d, r.get("period"), r.get("filingDate"), r.get("acceptedDate"),
                r.get("netIncome"), r.get("revenue")))

    banner("B. EDGAR GROUND TRUTH vs FMP (NetIncomeLoss)")
    facts = edgar("https://data.sec.gov/api/xbrl/companyconcept/CIK%s/us-gaap/NetIncomeLoss.json" % KHC_CIK)
    by_period = defaultdict(list)
    for e in facts.get("units", {}).get("USD", []):
        if e.get("start") and str(e.get("end", "")).startswith(("2016", "2017")):
            by_period[(e["start"], e["end"])].append(e)
    verdicts = []
    for (start, end), entries in sorted(by_period.items(), key=lambda kv: kv[0][1]):
        d = dur_days(start, end)
        kind = "Q" if 60 <= d <= 100 else ("FY" if 330 <= d <= 380 else None)
        if kind is None:
            continue  # YTD and other durations
        entries = sorted(entries, key=lambda e: e["filed"])
        vals = []
        for e in entries:
            if not vals or vals[-1][1] != e["val"]:
                vals.append((e["filed"], e["val"], e["form"]))
        fmp = None
        for (d2, p), r in fmp_rows.items():
            if d2 == end and ((kind == "FY") == (p == "FY")):
                fmp = r
                break
        if fmp is None:
            continue
        fmp_ni = fmp.get("netIncome")
        orig_filed, orig_val = vals[0][0], vals[0][1]
        last_filed, last_val = vals[-1][0], vals[-1][1]
        restated = orig_val != last_val
        if restated and close(fmp_ni, orig_val):
            tag = "ORIGINAL-VINTAGE"
        elif restated and close(fmp_ni, last_val):
            tag = "RESTATED-VALUE"
        elif not restated and close(fmp_ni, orig_val):
            tag = "unrestated"
        else:
            tag = "NEITHER-investigate"
        acc = (fmp.get("acceptedDate") or "")[:10]
        acc_tag = "orig-date" if acc == orig_filed else ("restate-date" if acc == last_filed else "other:%s" % acc)
        print("  %s %-2s EDGAR=%s | FMP NI=%s -> %s / accepted=%s%s" % (
            end, kind, ["%s@%s(%s)" % (v, f, fo) for f, v, fo in vals],
            fmp_ni, tag, acc_tag, "  <-- RESTATED PERIOD" if restated else ""))
        verdicts.append({"end": end, "kind": kind, "vals": vals, "fmp_ni": fmp_ni,
                         "tag": tag, "acc_tag": acc_tag, "restated": restated})
    tags = {}
    for v in verdicts:
        tags[v["tag"]] = tags.get(v["tag"], 0) + 1
    print("  SUMMARY: %d matched periods, %d restated; tags=%s" % (
        len(verdicts), sum(1 for v in verdicts if v["restated"]), tags))
    report["verdicts"] = verdicts

    banner("C. SMCI acceptedDate REALITY CHECK (filing-delay era)")
    smci = []
    for r in c.get("income_q", symbol="SMCI", limit=10):
        end, acc = r.get("date"), (r.get("acceptedDate") or "")[:10]
        lag = dur_days(end, acc) if (end and acc) else None
        smci.append({"end": end, "accepted": acc, "lag_days": lag})
        print("  end=%s accepted=%s lag=%sd" % (end, acc, lag))
    print("  READ: delay-era filings should show lags far beyond the normal ~40d.")
    report["smci"] = smci

    banner("D. AS-REPORTED BLOB (KHC) - what is inside `data`?")
    try:
        ar = c.get("income_as_reported", symbol="KHC", limit=48)
        dates = [r.get("date") for r in ar if r.get("date")]
        old_rows = [r for r in ar if str(r.get("date", "")).startswith(("2016", "2017"))]
        print("  rows=%d reaching back to %s ; 2016-17 rows: %d" % (len(ar), min(dates, default="?"), len(old_rows)))
        row = (old_rows or ar)[-1]
        blob = row.get("data")
        if isinstance(blob, dict):
            keys = sorted(blob.keys())
            print("  row %s %s: data dict, %d keys" % (row.get("date"), row.get("period"), len(keys)))
            print("  accession/timestampish keys: %s" %
                  [k for k in keys if any(w in k.lower() for w in ("accept", "access", "filed", "filing"))])
            ni = [k for k in keys if "netincome" in k.lower().replace(" ", "").replace("_", "")]
            print("  net-income-ish: %s -> %s" % (ni[:3], [blob.get(k) for k in ni[:3]]))
        elif isinstance(blob, list):
            print("  row %s: data is a LIST of %d items; item0: %s" % (
                row.get("date"), len(blob), blob[0] if blob else None))
        else:
            print("  row %s: data type=%s" % (row.get("date"), type(blob).__name__))
    except Exception as e:
        print("  ERR %s" % e)

    banner("E. IDENTITY - whose CIK is FMP serving for BBBY?")
    for label, cik in [("FMP-served", BBBY_SERVED_CIK), ("believed-original", BBBY_ORIGINAL_CIK)]:
        try:
            sub = edgar("https://data.sec.gov/submissions/CIK%s.json" % cik)
            print("  %s %s -> name=%r tickers=%s" % (label, cik, sub.get("name"), sub.get("tickers")))
        except Exception as e:
            print("  %s %s -> ERR %s" % (label, cik, e))

    banner("F. GAP DISCOVERY - earnings / 13F / M&A / feed depths")
    for logical, kw in [("surprises", {"symbol": "AAPL", "limit": 12}),
                        ("inst_ownership", {"symbol": "AAPL", "year": 2025, "quarter": 1}),
                        ("inst_filing_dates", {"cik": "0001067983"}),
                        ("mna_latest", {"page": 0})]:
        for r in c.probe(logical, **kw):
            mark = "OK " if r["ok"] and r["n"] else ("ok0" if r["ok"] else "FAIL")
            print("  %-18s %s %4s n=%-5s %s keys=%s" % (
                logical, mark, r["status"], r["n"], r["path"], r.get("keys", [])[:8]))
    st, data = c._call("/stable/mergers-acquisitions-search", {"name": "Activision Blizzard"})
    print("  mna_search 'Activision Blizzard': %s n=%s" % (st, len(data) if isinstance(data, list) else "?"))
    for params in [{"limit": "2000"}, {"page": "1"}, {"from": "2022-06-01", "to": "2022-06-30"}]:
        st, data = c._call("/stable/symbol-change", params)
        n = len(data) if isinstance(data, list) else 0
        rng = (data[-1].get("date"), data[0].get("date")) if n else None
        hits = [r for r in (data or []) if isinstance(r, dict)
                and (r.get("newSymbol") == "META" or r.get("oldSymbol") == "SQ")]
        print("  symbol-change %s -> %s n=%s range=%s hits=%s" % (params, st, n, rng, hits[:2]))
    for page in (20, 50):
        st, data = c._call("/stable/delisted-companies", {"page": str(page), "limit": "100"})
        if isinstance(data, list) and data:
            print("  delisted page=%s: %s .. %s" % (page, data[0].get("delistedDate"), data[-1].get("delistedDate")))
        else:
            print("  delisted page=%s: status=%s n=0" % (page, st))

    out = ART / "vintage_probe.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    banner("DONE -> %s" % out)


if __name__ == "__main__":
    main()
