"""Phase -1 torture sample (spec section 16). ~90 names chosen to break
assumptions: identity continuity, vintage preservation, return semantics,
delisting economics. Overlapping categories are the point.
FMP share-class format uses dashes (BRK-B). Notes flag anything the probe
must VERIFY rather than trust."""
import csv
from pathlib import Path

ART = Path(__file__).resolve().parent.parent / "artifacts"


def s(symbol, cats, note=""):
    return {"symbol": symbol, "cats": cats, "note": note}


SAMPLE = [
    # -- clean controls
    s("AAPL", ["clean", "split"], "4:1 split 2020-08-31 - price semantics sentinel"),
    s("MSFT", ["clean"]),
    s("PG", ["clean"]),
    s("JNJ", ["clean", "spinoff_parent"], "KVUE split-off 2023"),
    s("XOM", ["clean", "acquirer"], "PXD all-stock 2024"),
    # -- restatement / filing-timeliness
    s("KHC", ["restatement"], "2019 restatement of 2016-17 - vintage sentinel"),
    s("GE", ["restatement", "spinoff_parent"], "insurance-charge era; GEHC/GEV spins"),
    s("SMCI", ["restatement"], "2024 10-K delay + auditor resignation - acceptedDate gaps"),
    s("NKLA", ["restatement", "distress"]),
    s("HTZ", ["restatement", "bankruptcy", "identity"],
      "2020 ch11 -> OTC ticker -> 2021 re-IPO; equity RECOVERED - tests the -100% assumption"),
    s("LKNCY", ["restatement", "delisted_otc"], "Luckin fraud; NASDAQ delist -> OTC recovery"),
    s("TUP", ["restatement", "bankruptcy"], "2024 ch11"),
    # -- ticker changes / renames
    s("META", ["ticker_change"], "FB -> META 2022"),
    s("XYZ", ["ticker_change"], "SQ -> XYZ 2025 (Block)"),
    s("ELV", ["ticker_change"], "ANTM -> ELV 2022"),
    s("BALL", ["ticker_change"], "BLL -> BALL 2022"),
    s("WTW", ["ticker_change"], "WLTW -> WTW 2022"),
    s("PARA", ["ticker_change", "dual_class", "mna"], "VIAC -> PARA 2022; Skydance close 2025 -> verify current ticker"),
    s("WBD", ["ticker_change", "spinoff_child", "mna"], "DISCA + T WarnerMedia 2022"),
    s("RTX", ["ticker_change", "mna", "spinoff_parent"], "UTX+RTN 2020; CARR/OTIS spun"),
    s("LIN", ["ticker_change", "mna"], "Praxair merger; Irish domicile"),
    # -- bankruptcies / failures / delistings
    s("BBBY", ["bankruptcy"], "2023 ch11 - delisting economics sentinel"),
    s("RAD", ["bankruptcy"], "2023"),
    s("SIVB", ["bankruptcy", "financial"], "SVB failure 2023"),
    s("FRC", ["bankruptcy", "financial"], "First Republic 2023 -> JPM"),
    s("SBNY", ["bankruptcy", "financial"], "Signature 2023"),
    s("WE", ["bankruptcy"], "WeWork 2023"),
    s("YELL", ["bankruptcy"], "Yellow 2023"),
    s("PRTY", ["bankruptcy"], "Party City 2023"),
    s("FSR", ["bankruptcy"], "Fisker 2024"),
    s("BIG", ["bankruptcy"], "Big Lots 2024"),
    s("SAVE", ["bankruptcy", "mna_broken"], "JBLU deal blocked -> ch11 2024"),
    # -- M&A consideration types
    s("ATVI", ["mna_cash"], "MSFT $95 cash 2023"),
    s("TWTR", ["mna_cash"], "$54.20 cash 2022"),
    s("CERN", ["mna_cash"], "ORCL 2022"),
    s("SGEN", ["mna_cash"], "PFE 2023"),
    s("VMW", ["mna_mixed"], "AVGO cash/stock election 2023"),
    s("ALXN", ["mna_mixed"], "AZN cash+ADS 2021"),
    s("XLNX", ["mna_stock"], "AMD all-stock 2022"),
    s("MXIM", ["mna_stock"], "ADI all-stock 2021"),
    s("PXD", ["mna_stock", "variable_div"], "XOM all-stock 2024; variable divs prior"),
    s("HES", ["mna_stock"], "CVX all-stock closed 2025 - verify"),
    s("JNPR", ["mna_cash"], "HPE cash closed 2025 - verify"),
    s("K", ["mna_cash", "spinoff_parent"], "KLG spun 2023; Mars close 2025 - verify"),
    # -- spinoff children
    s("GEV", ["spinoff_child"], "GE Vernova 2024"),
    s("GEHC", ["spinoff_child"], "GE HealthCare 2023"),
    s("KVUE", ["spinoff_child"], "JNJ 2023"),
    s("SOLV", ["spinoff_child"], "MMM 2024"),
    s("VLTO", ["spinoff_child"], "DHR 2023"),
    s("KLG", ["spinoff_child"], "Kellogg 2023"),
    s("OTIS", ["spinoff_child"], "UTX 2020"),
    s("KD", ["spinoff_child"], "IBM 2021"),
    # -- dual class
    s("GOOGL", ["dual_class", "split"], "20:1 2022; GOOG sibling"),
    s("GOOG", ["dual_class"]),
    s("BRK-B", ["dual_class", "financial"], "dash-format check"),
    s("UAA", ["dual_class", "identity"], "UA/UAA class shuffle 2016"),
    s("UA", ["dual_class"]),
    s("NWSA", ["dual_class"]),
    s("ZG", ["dual_class"], "Z sibling"),
    # -- special / variable dividends, ROC
    s("COST", ["special_div", "fiscal_odd"], "$15 special ex late-Dec 2023; Aug/Sep FYE"),
    s("CME", ["special_div"], "annual variable special"),
    s("TPL", ["special_div", "identity"], "trust -> corp conversion 2021; specials"),
    s("DDS", ["special_div"]),
    s("DVN", ["variable_div"], "fixed+variable 2021-22"),
    s("FANG", ["variable_div", "acquirer"], "Endeavor 2024"),
    s("OMF", ["special_div"]),
    s("MAIN", ["special_div"], "monthly + supplementals"),
    s("EPD", ["roc", "partnership"], "MLP, ROC-heavy; outside v1 universe - TR stress only"),
    # -- financials (validity-rule lane)
    s("JPM", ["financial"]),
    s("BAC", ["financial"]),
    s("GS", ["financial"]),
    s("SCHW", ["financial"]),
    s("AXP", ["financial"]),
    s("PNC", ["financial"]),
    s("ALL", ["financial"], "insurer"),
    s("MET", ["financial"], "insurer"),
    s("AIG", ["financial"], "insurer"),
    s("BX", ["financial", "identity"], "K-1 partnership -> C-corp 2019"),
    # -- negative EV / net cash (probe must verify the condition)
    s("VIR", ["neg_ev"], "verify cash > mktcap window 2023-24"),
    s("EDIT", ["neg_ev"], "verify"),
    s("SGMO", ["neg_ev"], "verify"),
    s("ATRA", ["neg_ev"], "verify"),
    # -- recent IPOs (short history)
    s("ARM", ["ipo"], "2023"),
    s("CAVA", ["ipo"], "2023"),
    s("RDDT", ["ipo"], "2024"),
    s("BIRK", ["ipo"], "2023"),
    s("KVYO", ["ipo"], "2023"),
    # -- fiscal oddities
    s("NVDA", ["fiscal_odd", "split"], "Jan FYE; 10:1 2024"),
    s("AVGO", ["fiscal_odd", "split"], "Oct/Nov FYE; 10:1 2024"),
    s("DE", ["fiscal_odd"], "Oct FYE"),
]


def by_cat():
    counts = {}
    for row in SAMPLE:
        for c in row["cats"]:
            counts[c] = counts.get(c, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


if __name__ == "__main__":
    syms = [r["symbol"] for r in SAMPLE]
    assert len(syms) == len(set(syms)), "duplicate symbol in SAMPLE"
    print("torture sample: %d names" % len(SAMPLE))
    for cat, n in by_cat().items():
        print("  %-16s %d" % (cat, n))
    ART.mkdir(exist_ok=True)
    out = ART / "torture_sample.csv"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "cats", "note"])
        for r in SAMPLE:
            w.writerow([r["symbol"], "|".join(r["cats"]), r["note"]])
    print("wrote %s" % out)
