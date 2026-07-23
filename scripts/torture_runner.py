"""Session 3: full torture-sample runner - Phase -1 exit-gate evidence.

Gates (spec v0.2 section 16, Phase -1):
  G1 identity continuity     - anchors present, chimera/reuse detection
  G2 TR reconciliation       - self-built TR vs dividend-adjusted oracle, >=95%/name
  G3 statement timestamps    - acceptedDate coverage + lag sanity
  G4 delisting resolvability - deal feed / delisted feed / flagged
Plus: vintage comparator (EDGAR XBRL) on KHC (fuzzy rerun), GE, SMCI.

Caches full symbol-change / delisted / M&A feeds to artifacts/ (Phase 0 seeds).
"""
import csv
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.fmp_client import FMPClient, ART
from factorlab.torture_sample import SAMPLE

UA = "factor_lab phase-1 research probe (contact: pcassidy372@gmail.com)"
RAW_FROM = "2021-01-01"
TODAY = date.today().isoformat()
DELIST_CATS = {"bankruptcy", "mna_cash", "mna_stock", "mna_mixed", "mna_broken"}
MAYBE_ACTIVE = {"delisted_otc", "identity"}  # LKNCY (OTC), HTZ (re-IPO) legitimately trade


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


def close_val(a, b):
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= max(1e6, 0.005 * abs(float(b)))


def load_feeds(c):
    banner("0. FEED CACHES (symbol-change / delisted / M&A) -> artifacts/")
    st, data = c._call("/stable/symbol-change", {"limit": "10000"})
    changes = data if isinstance(data, list) else []
    print("  symbol-change: %d rows, range %s .. %s" % (
        len(changes), changes[-1].get("date") if changes else "?", changes[0].get("date") if changes else "?"))
    with open(ART / "symbol_change_full.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "oldSymbol", "newSymbol", "companyName"])
        for r in changes:
            w.writerow([r.get("date"), r.get("oldSymbol"), r.get("newSymbol"), r.get("companyName")])
    delisted = []
    for page in range(300):
        rows = c.get("delisted", page=page, allow_empty=True)
        if not rows:
            break
        delisted.extend(rows)
    print("  delisted: %d rows across %d pages, range %s .. %s" % (
        len(delisted), page, delisted[0].get("delistedDate") if delisted else "?",
        delisted[-1].get("delistedDate") if delisted else "?"))
    with open(ART / "delisted_full.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "companyName", "exchange", "ipoDate", "delistedDate"])
        for r in delisted:
            w.writerow([r.get("symbol"), r.get("companyName"), r.get("exchange"),
                        r.get("ipoDate"), r.get("delistedDate")])
    deals = []
    for page in range(20):
        rows = c.get("mna_latest", page=page, allow_empty=True)
        if not rows:
            break
        deals.extend(rows)
    ddates = sorted([d.get("acceptedDate", "") for d in deals if d.get("acceptedDate")])
    print("  mna_latest: %d deals, accepted range %s .. %s" % (
        len(deals), ddates[0][:10] if ddates else "?", ddates[-1][:10] if ddates else "?"))
    (ART / "mna_latest_cache.json").write_text(json.dumps(deals, indent=1))
    return changes, delisted, deals


def build_rets(rows, divs=None, splits=None):
    px = {}
    for r in rows:
        v = r.get("adjClose", r.get("close"))
        if r.get("date") and v:
            px[r["date"]] = float(v)
    div_by = defaultdict(float)
    for d in (divs or []):
        if d.get("date"):
            div_by[d["date"]] += float(d.get("dividend") or d.get("adjDividend") or 0)
    split_by = {}
    for sp in (splits or []):
        dt, ratio = sp.get("date"), None
        num, den = sp.get("numerator"), sp.get("denominator")
        if num and den:
            try:
                ratio = float(num) / float(den)
            except (TypeError, ValueError):
                pass
        if ratio is None:
            lbl = str(sp.get("label") or sp.get("splitRatio") or "")
            for sep in (":", "/", "-"):
                if sep in lbl:
                    try:
                        a, b = lbl.split(sep)[:2]
                        ratio = float(a) / float(b)
                        break
                    except (TypeError, ValueError):
                        pass
        if dt and ratio:
            split_by[dt] = ratio
    dates = sorted(px)
    rets = {}
    for i in range(1, len(dates)):
        d0, d1 = dates[i - 1], dates[i]
        rets[d1] = (px[d1] * split_by.get(d1, 1.0) + div_by.get(d1, 0.0)) / px[d0] - 1.0
    return rets, dates


def vintage_compare(c, sym, years):
    """Session-2 comparator, generalized: fuzzy +/-6d period matching."""
    out = {"symbol": sym, "rows": [], "tags": defaultdict(int)}
    prof = c.get("profile", symbol=sym)[0]
    cik = str(prof.get("cik") or "").lstrip("0").zfill(10)
    facts = edgar("https://data.sec.gov/api/xbrl/companyconcept/CIK%s/us-gaap/NetIncomeLoss.json" % cik)
    fmp = []
    for r in c.get("income_q", symbol=sym, limit=48) + c.get("income_a", symbol=sym, limit=12, allow_empty=True):
        if str(r.get("date", ""))[:4] in years:
            fmp.append(r)
    by_period = defaultdict(list)
    for e in facts.get("units", {}).get("USD", []):
        if e.get("start") and str(e.get("end", ""))[:4] in years:
            by_period[(e["start"], e["end"])].append(e)
    for (start, end), entries in sorted(by_period.items(), key=lambda kv: kv[0][1]):
        d = dur_days(start, end)
        kind = "Q" if 60 <= d <= 100 else ("FY" if 330 <= d <= 380 else None)
        if kind is None:
            continue
        entries = sorted(entries, key=lambda e: e["filed"])
        vals = []
        for e in entries:
            if not vals or vals[-1][1] != e["val"]:
                vals.append((e["filed"], e["val"], e["form"]))
        match = None
        for r in fmp:
            if abs(dur_days(end, r["date"])) <= 6 and ((kind == "FY") == (r.get("period") == "FY")):
                match = r
                break
        if match is None:
            continue
        ni = match.get("netIncome")
        restated = vals[0][1] != vals[-1][1]
        if restated and close_val(ni, vals[0][1]):
            tag = "ORIGINAL-VINTAGE"
        elif restated and close_val(ni, vals[-1][1]):
            tag = "RESTATED-VALUE"
        elif not restated and close_val(ni, vals[0][1]):
            tag = "unrestated"
        else:
            tag = "NEITHER"
        acc = (match.get("acceptedDate") or "")[:10]
        acc_tag = "orig" if abs(dur_days(acc, vals[0][0])) <= 1 else (
            "restate" if abs(dur_days(acc, vals[-1][0])) <= 1 else "other")
        out["tags"]["%s/%s" % (tag, acc_tag)] += 1
        out["rows"].append({"end": end, "kind": kind, "vals": vals, "fmp_ni": ni,
                            "tag": tag, "acc": acc_tag, "restated": restated})
        if restated or tag == "NEITHER":
            print("    %s %s %-2s EDGAR=%s FMP=%s -> %s/%s" % (
                sym, end, kind, ["%s@%s(%s)" % (v, f, fo) for f, v, fo in vals], ni, tag, acc_tag))
    print("  %s tags: %s" % (sym, dict(out["tags"])))
    return out


def main():
    c = FMPClient()
    changes, delisted, deals = load_feeds(c)
    changed_syms = {r.get("oldSymbol") for r in changes} | {r.get("newSymbol") for r in changes}
    delisted_syms = {r.get("symbol") for r in delisted}
    deal_targets = {d.get("targetedSymbol") for d in deals if d.get("targetedSymbol")}

    banner("1-4. PER-NAME SWEEP (%d names)" % len(SAMPLE))
    results = []
    for item in SAMPLE:
        sym, cats = item["symbol"], set(item["cats"])
        res = {"symbol": sym, "cats": sorted(cats)}
        try:
            # G1 identity
            try:
                p = c.get("profile", symbol=sym)[0]
            except Exception:
                p = {}
            anchors = bool(p.get("cik")) and bool(p.get("isin") or p.get("cusip"))
            active = p.get("isActivelyTrading")
            ident_flags = []
            if not anchors:
                ident_flags.append("missing-anchors")
            if cats & DELIST_CATS and active and not (cats & MAYBE_ACTIVE):
                ident_flags.append("chimera-suspect(active)")
            if "ticker_change" in cats and sym not in changed_syms:
                ident_flags.append("change-not-in-feed")
            res["identity"] = {"cik": p.get("cik"), "active": active, "flags": ident_flags}

            # G2 TR reconciliation
            tr = {"match_pct": None, "n": 0, "first_mismatch": None}
            try:
                raw = c.get("prices_unadjusted", symbol=sym, date_from=RAW_FROM, date_to=TODAY, allow_empty=True)
                ora = c.get("prices_div_adjusted", symbol=sym, date_from=RAW_FROM, date_to=TODAY, allow_empty=True)
                divs = c.get("dividends", symbol=sym, limit=400, allow_empty=True)
                spls = c.get("splits", symbol=sym, allow_empty=True)
                rets, _ = build_rets(raw, divs, spls)
                orets, _ = build_rets(ora)
                common = sorted(set(rets) & set(orets))
                mism = [d for d in common if abs(rets[d] - orets[d]) > 0.001]
                if common:
                    tr = {"match_pct": round(100.0 * (1 - len(mism) / len(common)), 2),
                          "n": len(common), "first_mismatch": mism[0] if mism else None}
            except Exception as e:
                tr["err"] = str(e)[:60]
            res["tr"] = tr

            # G3 statements
            st = {"n": 0, "with_accepted": 0, "median_lag": None, "weird": []}
            try:
                rows = c.get("income_q", symbol=sym, limit=8, allow_empty=True)
                st["n"] = len(rows)
                lags = []
                for r in rows:
                    acc = (r.get("acceptedDate") or "")[:10]
                    if acc and r.get("date"):
                        st["with_accepted"] += 1
                        lg = dur_days(r["date"], acc)
                        lags.append(lg)
                        if lg < 20 or lg > 200:
                            st["weird"].append("%s:%dd" % (r["date"], lg))
                if lags:
                    st["median_lag"] = sorted(lags)[len(lags) // 2]
            except Exception as e:
                st["err"] = str(e)[:60]
            res["stmt"] = st

            # G4 delisting resolvability
            dl = None
            if cats & DELIST_CATS:
                if sym in deal_targets:
                    dl = "rung1-deal-feed"
                elif sym in delisted_syms:
                    dl = "rung2-delisted-feed"
                elif active:
                    dl = "reused/active-manual-map"
                else:
                    dl = "UNRESOLVED"
            res["delist"] = dl

            results.append(res)
            iflag = ",".join(ident_flags) if ident_flags else "OK"
            trs = "%s%%(n=%d)" % (tr["match_pct"], tr["n"]) if tr["match_pct"] is not None else "no-data"
            print("  %-6s ident=%-28s tr=%-16s stmt=%d/%d lag~%s %s%s" % (
                sym, iflag, trs, st["with_accepted"], st["n"], st["median_lag"],
                ("delist=" + dl) if dl else "",
                (" WEIRD:" + ";".join(st["weird"])) if st["weird"] else ""))
        except Exception as e:
            res["fatal"] = str(e)[:100]
            results.append(res)
            print("  %-6s FATAL %s" % (sym, e))

    banner("5. VINTAGE COMPARATOR - KHC(fuzzy) / GE / SMCI")
    vint = {}
    for sym, yrs in [("KHC", ("2016", "2017")), ("GE", ("2016", "2017", "2018")),
                     ("SMCI", ("2015", "2016", "2017"))]:
        try:
            vint[sym] = vintage_compare(c, sym, yrs)
        except Exception as e:
            print("  %s ERR %s" % (sym, e))
            vint[sym] = {"err": str(e)[:100]}

    banner("GATE SUMMARY")
    ok_ident = [r for r in results if not r.get("identity", {}).get("flags") and "fatal" not in r]
    flagged = [(r["symbol"], r["identity"]["flags"]) for r in results if r.get("identity", {}).get("flags")]
    print("  G1 identity: clean %d/%d; flagged: %s" % (len(ok_ident), len(results), flagged))
    evald = [r for r in results if r.get("tr", {}).get("match_pct") is not None]
    passing = [r for r in evald if r["tr"]["match_pct"] >= 95.0]
    worst = sorted(evald, key=lambda r: r["tr"]["match_pct"])[:12]
    print("  G2 TR: >=95%% on %d/%d evaluated; worst: %s" % (
        len(passing), len(evald),
        [(r["symbol"], r["tr"]["match_pct"], r["tr"]["first_mismatch"]) for r in worst]))
    full_acc = [r for r in results if r.get("stmt", {}).get("n") and r["stmt"]["with_accepted"] == r["stmt"]["n"]]
    weird = [(r["symbol"], r["stmt"]["weird"]) for r in results if r.get("stmt", {}).get("weird")]
    print("  G3 statements: full acceptedDate on %d names w/ data; weird lags: %s" % (len(full_acc), weird))
    applicable = [r for r in results if r.get("delist")]
    resolved = [r for r in applicable if r["delist"] != "UNRESOLVED"]
    print("  G4 delisting: resolved %d/%d; detail: %s" % (
        len(resolved), len(applicable), {r["symbol"]: r["delist"] for r in applicable}))

    (ART / "torture_results.json").write_text(json.dumps(
        {"results": results, "vintage": {k: (v if "err" in v else
         {"tags": dict(v["tags"]), "rows": v["rows"]}) for k, v in vint.items()}},
        indent=1, default=str))
    banner("DONE -> %s" % (ART / "torture_results.json"))


if __name__ == "__main__":
    main()
