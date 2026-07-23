"""factorlab.fmp_client — the single FMP mapping layer (spec section 7).

Every endpoint is a logical name with ordered candidate paths (stable API
first, legacy v3/v4 fallback). The first working candidate is cached to
artifacts/endpoint_resolution.json. Vendor path drift costs an edit to
this file and nothing else.
"""
import json
import os
import time
from pathlib import Path

import requests

BASE = "https://financialmodelingprep.com"
ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "artifacts"
RESOLUTION_FILE = ART / "endpoint_resolution.json"

# logical -> ordered candidates: (path_template, {param_name: value_template})
# tokens: {symbol} {limit} {page} {name} {date_from} {date_to}
ENDPOINTS = {
    "profile": [
        ("/stable/profile", {"symbol": "{symbol}"}),
        ("/api/v3/profile/{symbol}", {}),
    ],
    "symbol_change": [
        ("/stable/symbol-change", {}),
        ("/api/v4/symbol_change", {}),
    ],
    "delisted": [
        ("/stable/delisted-companies", {"page": "{page}", "limit": "100"}),
        ("/api/v3/delisted-companies", {"page": "{page}"}),
    ],
    "income_q": [
        ("/stable/income-statement", {"symbol": "{symbol}", "period": "quarter", "limit": "{limit}"}),
        ("/api/v3/income-statement/{symbol}", {"period": "quarter", "limit": "{limit}"}),
    ],
    "balance_q": [
        ("/stable/balance-sheet-statement", {"symbol": "{symbol}", "period": "quarter", "limit": "{limit}"}),
        ("/api/v3/balance-sheet-statement/{symbol}", {"period": "quarter", "limit": "{limit}"}),
    ],
    "cashflow_q": [
        ("/stable/cash-flow-statement", {"symbol": "{symbol}", "period": "quarter", "limit": "{limit}"}),
        ("/api/v3/cash-flow-statement/{symbol}", {"period": "quarter", "limit": "{limit}"}),
    ],
    "income_as_reported": [
        ("/stable/income-statement-as-reported", {"symbol": "{symbol}", "period": "quarter", "limit": "{limit}"}),
        ("/api/v3/income-statement-as-reported/{symbol}", {"period": "quarter", "limit": "{limit}"}),
    ],
    "prices_full": [
        ("/stable/historical-price-eod/full", {"symbol": "{symbol}", "from": "{date_from}", "to": "{date_to}"}),
        ("/api/v3/historical-price-full/{symbol}", {"from": "{date_from}", "to": "{date_to}"}),
    ],
    "prices_unadjusted": [
        ("/stable/historical-price-eod/non-split-adjusted", {"symbol": "{symbol}", "from": "{date_from}", "to": "{date_to}"}),
    ],
    "prices_div_adjusted": [
        ("/stable/historical-price-eod/dividend-adjusted", {"symbol": "{symbol}", "from": "{date_from}", "to": "{date_to}"}),
    ],
    "dividends": [
        ("/stable/dividends", {"symbol": "{symbol}", "limit": "{limit}"}),
        ("/api/v3/historical-price-full/stock_dividend/{symbol}", {}),
    ],
    "splits": [
        ("/stable/splits", {"symbol": "{symbol}"}),
        ("/api/v3/historical-price-full/stock_split/{symbol}", {}),
    ],
    "mktcap_hist": [
        ("/stable/historical-market-capitalization", {"symbol": "{symbol}", "limit": "{limit}"}),
        ("/api/v3/historical-market-capitalization/{symbol}", {"limit": "{limit}"}),
    ],
    "mna_search": [
        ("/stable/mergers-acquisitions-search", {"name": "{name}"}),
        ("/api/v4/mergers-acquisitions/search", {"name": "{name}"}),
    ],
    "surprises": [
        ("/stable/earnings-surprises", {"symbol": "{symbol}"}),
        ("/api/v3/earnings-surprises/{symbol}", {}),
    ],
    "estimates": [
        ("/stable/analyst-estimates", {"symbol": "{symbol}", "period": "annual", "limit": "{limit}"}),
        ("/api/v3/analyst-estimates/{symbol}", {"limit": "{limit}"}),
    ],
    "insider": [
        ("/stable/insider-trading/search", {"symbol": "{symbol}", "page": "0"}),
        ("/api/v4/insider-trading", {"symbol": "{symbol}", "page": "0"}),
    ],
    "inst_ownership": [
        ("/stable/institutional-ownership/symbol-ownership", {"symbol": "{symbol}"}),
        ("/api/v4/institutional-ownership/symbol-ownership", {"symbol": "{symbol}", "includeCurrentQuarter": "false"}),
    ],
    "treasury": [
        ("/stable/treasury-rates", {}),
        ("/api/v4/treasury", {}),
    ],
}


class FMPError(RuntimeError):
    pass


class FMPClient:
    def __init__(self, api_key=None, min_interval=0.25):
        self.key = api_key or os.environ.get("FMP_API_KEY")
        if not self.key:
            raise FMPError("FMP_API_KEY not set (run under `railway run` or export it)")
        self.min_interval = min_interval
        self._last = 0.0
        ART.mkdir(exist_ok=True)
        self._resolution = {}
        if RESOLUTION_FILE.exists():
            self._resolution = json.loads(RESOLUTION_FILE.read_text())

    # -- low level ---------------------------------------------------------
    def _throttle(self):
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def _call(self, path, params):
        for attempt in range(4):
            self._throttle()
            r = requests.get(BASE + path, params={**params, "apikey": self.key}, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** (attempt + 1))
                continue
            try:
                data = r.json()
            except ValueError:
                return r.status_code, None
            return r.status_code, data
        return 429, None

    @staticmethod
    def _ok(status, data):
        if status != 200 or data is None:
            return False
        if isinstance(data, dict) and ("Error Message" in data or "error" in data):
            return False
        return True

    @staticmethod
    def _size(data):
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            if "historical" in data and isinstance(data["historical"], list):
                return len(data["historical"])
            return 1
        return 0

    @staticmethod
    def _fill(template_path, template_params, kw):
        path = template_path
        for token, val in kw.items():
            path = path.replace("{%s}" % token, str(val))
        if "{" in path:
            raise FMPError("missing token for path %s; have %s" % (template_path, sorted(kw)))
        params = {}
        for name, vt in template_params.items():
            filled = vt
            for token, val in kw.items():
                filled = filled.replace("{%s}" % token, str(val))
            if "{" in filled:
                continue  # optional param not supplied -> drop it
            params[name] = filled
        return path, params

    # -- public ------------------------------------------------------------
    def probe(self, logical, **kw):
        """Try EVERY candidate; return per-candidate report. Never raises."""
        out = []
        for template_path, template_params in ENDPOINTS[logical]:
            try:
                path, params = self._fill(template_path, template_params, kw)
                status, data = self._call(path, params)
                ok = self._ok(status, data)
                n = self._size(data) if ok else 0
                row0 = None
                if ok:
                    rows = data.get("historical") if isinstance(data, dict) and "historical" in data else data
                    if isinstance(rows, list) and rows:
                        row0 = rows[0]
                    elif isinstance(rows, dict):
                        row0 = rows
                keys = sorted(row0.keys())[:14] if isinstance(row0, dict) else []
                out.append({"path": template_path, "status": status, "ok": ok, "n": n, "keys": keys})
            except Exception as e:
                out.append({"path": template_path, "status": None, "ok": False, "n": 0, "err": str(e)[:80]})
        return out

    def get(self, logical, allow_empty=False, **kw):
        """Resolved fetch. Returns a row list (v3 'historical' envelope unwrapped)."""
        candidates = ENDPOINTS[logical]
        order = list(range(len(candidates)))
        if logical in self._resolution and self._resolution[logical] < len(candidates):
            i = self._resolution[logical]
            order = [i] + [j for j in order if j != i]
        empty_ok, last = None, None
        for i in order:
            tpath, tparams = candidates[i]
            try:
                path, params = self._fill(tpath, tparams, kw)
            except FMPError:
                continue
            status, data = self._call(path, params)
            if self._ok(status, data):
                if self._size(data) > 0:
                    if self._resolution.get(logical) != i:
                        self._resolution[logical] = i
                        RESOLUTION_FILE.write_text(json.dumps(self._resolution, indent=2))
                    rows = data["historical"] if isinstance(data, dict) and "historical" in data else data
                    return rows if isinstance(rows, list) else [rows]
                if empty_ok is None:
                    empty_ok = i
            last = (path, status)
        if allow_empty and empty_ok is not None:
            return []
        raise FMPError("no candidate resolved for %s; last=%s" % (logical, last))
