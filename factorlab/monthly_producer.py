"""One-date monthly calculator over frozen raw inputs; no database or HTTP I/O.

The canonical five retain factor_compute_v2's numerical pipeline and its existing
60% eligible-coverage rule. This is not the daily raw-momentum strategy. Historical
inputs observed today do not become historically available merely by computing an
old asof. PublicationStore supplies the separate availability boundary.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Mapping

import numpy as np
from scipy.stats import rankdata

from .pit import visible_at
from .publication_contract import (CORE_FACTORS, DATASET, PublicationError, day,
                                   instant, normalize_spec, require)

PRODUCER_VERSION = 'monthly-canonical-five-v1'
FIELDS = ('revenue', 'gross_profit', 'ebit', 'net_income', 'cfo', 'capex',
          'total_assets', 'total_debt', 'cash', 'equity', 'shares_dil')
ND = statistics.NormalDist()
INSTRUMENT_SUFFIX = re.compile(r'-(P[A-Z]?|UN|WS|WT|R|U)$')


def _canonical(value):
    if isinstance(value, datetime):
        return instant(value).isoformat()
    if type(value) is date:
        return value.isoformat()
    if isinstance(value, Decimal):
        require(value.is_finite(), 'nonfinite_source_value')
        return str(value)
    if isinstance(value, float):
        require(math.isfinite(value), 'nonfinite_source_value')
    if isinstance(value, Mapping):
        return {k: _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonical(v) for v in value]
    return value


def canonical_json(value):
    return json.dumps(_canonical(value), sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def number(value):
    if value is None:
        return None
    require(not isinstance(value, bool), 'invalid_source_number')
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise PublicationError('invalid_source_number') from None
    require(math.isfinite(result), 'nonfinite_source_value')
    return result


def month_number(value):
    d = day(value)
    return d.year * 12 + d.month


@dataclass(frozen=True)
class FrozenMonthlyInputs:
    """Serialized copy prevents a caller from changing inputs after fingerprinting."""
    payload_json: str

    @classmethod
    def freeze(cls, payload):
        return cls(canonical_json(payload))

    def unpack(self):
        return json.loads(self.payload_json)


def plan_universe(payload):
    """Preserve 63-bar upper median, 40-bar minimum and legacy selection thresholds.

    Tie breaking for size buckets and duplicate-month caps is now explicit. Source
    rows later than the selected asof are never used, even within that month.
    """
    asof = day(payload['asof'])
    grouped = defaultdict(list)
    for row in payload['prices']:
        require(day(row['d']) <= asof, 'future_price_input')
        grouped[row['security_id']].append(row)
    ids = sorted(payload['security_ids'])
    require(len(ids) == len(set(ids)) == payload['expected_universe'], 'raw_scope_mismatch')
    require(set(grouped) == set(ids), 'price_scope_mismatch')
    profiles = {r['security_id']: r for r in payload['profiles']}
    require(len(profiles) == len(payload['profiles']) and set(profiles) <= set(ids), 'classification_scope_mismatch')
    excluded = {r['security_id'] for r in payload['adr_history'] if r['is_adr']}
    for r in payload['symbols']:
        sym = r['symbol']
        if INSTRUMENT_SUFFIX.search(sym) or (len(sym) == 5 and sym[4] in 'UWR'):
            excluded.add(r['security_id'])
    caps = {}
    for row in sorted(payload['caps'], key=lambda r: (r['security_id'], r['asof'])):
        d = day(row['asof'])
        require(d <= asof and (d.year, d.month) == (asof.year, asof.month), 'cap_date_mismatch')
        caps[row['security_id']] = number(row['mktcap'])
    universe = []
    for sec in ids:
        series = sorted(grouped[sec], key=lambda r: r['d'])
        require(len(series) <= 63 and len({r['d'] for r in series}) == len(series), 'price_window_shape')
        last = series[-1]
        require(month_number(last['d']) == month_number(asof), 'member_without_month_price')
        px = number(last['close'])
        # A missing source price/volume is not a zero-valued observation.
        dollars = [None if r['close'] is None or r['volume'] is None
                   else number(r['close']) * number(r['volume']) for r in series]
        adv = (sorted(dollars)[len(dollars) // 2]
               if len(dollars) >= 40 and all(v is not None for v in dollars) else None)
        mc = caps.get(sec)
        eligible = bool(mc is not None and adv is not None and px is not None
                        and mc >= 300e6 and adv >= 2e6 and px >= 3
                        and (asof - day(last['d'])).days <= 5 and sec not in excluded)
        universe.append(dict(asof=asof.isoformat(), security_id=sec, mktcap=mc,
                             adv_63d=adv, price=px, in_universe=eligible,
                             size_bucket=None, sector=profiles.get(sec, {}).get('sector') or 'Unknown'))
    members = sorted((r for r in universe if r['in_universe']), key=lambda r: (r['mktcap'], r['security_id']))
    require(members, 'empty_investable_universe')
    # PostgreSQL NTILE(3): larger buckets precede smaller buckets, including n<3.
    quotient, remainder = divmod(len(members), 3)
    offset = 0
    for bucket, name in enumerate(('small', 'mid', 'large')):
        count = quotient + int(bucket < remainder)
        for row in members[offset:offset + count]:
            row['size_bucket'] = name
        offset += count
    return universe


def _registered(payload):
    registry = {r['factor_id']: r for r in payload['registry']}
    for fid, contract in CORE_FACTORS.items():
        row = registry.get(fid)
        require(row is not None and row['version'] == contract['version']
                and row['formula_hash'] == contract['formula_hash'] and row['frozen'] is True,
                'factor_registry_drift')
        expected_exclusion = fid == 'gp_a'
        require(row['params'].get('excl_financials', False) is expected_exclusion,
                'factor_eligibility_drift')
    return registry


def calculate_factors(payload, universe):
    """Canonical-five extraction of the existing v2 formulas; no derived-row input."""
    asof = day(payload['asof']).isoformat()
    grid = payload['month_grid']
    require(len(grid) == 13 and grid[-1] == asof and len(set(grid)) == 13,
            'thirteen_month_grid_required')
    require(all(month_number(b) - month_number(a) == 1 for a, b in zip(grid, grid[1:])),
            'missing_month_in_return_grid')
    _registered(payload)
    members = {r['security_id']: r['mktcap'] for r in universe if r['in_universe']}
    sector = {r['security_id']: r['sector'] for r in universe}
    fin = {sec for sec in members if 'financ' in sector[sec].lower()}
    funds, tr, sue = defaultdict(list), defaultdict(dict), defaultdict(list)
    for row in payload['fundamentals']:
        require(row['timing_pit'] is True, 'fundamental_timing_flag_missing')
        funds[row['security_id']].append((row['fiscal_period_end'], row['vintage_id'],
            row['accepted_date']) + tuple(number(row[k]) for k in FIELDS))
    for row in payload['returns']:
        require(row['d'] in grid and row['tr'] is not None and number(row['tr']) > 0, 'invalid_return_grid_value')
        require(row['method_version'] in ('tr-v1-close-div-split', 'tr-v2-window-priority'), 'unknown_total_return_method')
        require(row['d'] not in tr[row['security_id']], 'duplicate_return_grid_value')
        tr[row['security_id']][row['d']] = number(row['tr'])
    for row in payload['surprises']:
        require(row['report_date'] <= asof, 'future_surprise_input')
        if row['sue'] is not None:
            sue[row['security_id']].append((row['report_date'], number(row['sue'])))
    raw = {fid: {} for fid in CORE_FACTORS}
    reason = {(sec, fid): 'source_value_missing' for sec in members for fid in CORE_FACTORS}
    for sec, mc in members.items():
        visible = visible_at(sorted(funds[sec]), asof)[-6:]
        if visible:
            pe, *rest = visible[-1]
            last4 = visible[-4:]
            complete = len(last4) == 4 and 240 <= (day(last4[-1][0]) - day(last4[0][0])).days <= 390
            prior = [r for r in visible if 300 <= (day(pe) - day(r[0])).days <= 430]
            ta, shares = visible[-1][9], visible[-1][13]
            if sec in fin:
                reason[sec, 'gp_a'] = 'not_applicable'
            elif complete and ta:
                if all(r[4] is not None for r in last4):
                    raw['gp_a'][sec] = sum(r[4] for r in last4) / ta
            else:
                reason[sec, 'gp_a'] = 'insufficient_history'
            sh_prior = prior[-1][13] if prior and prior[-1][13] else None
            if shares and sh_prior and sh_prior > 0:
                raw['net_issuance'][sec] = shares / sh_prior - 1.0
            elif not prior:
                reason[sec, 'net_issuance'] = 'insufficient_history'
        else:
            reason[sec, 'gp_a'] = 'not_applicable' if sec in fin else 'insufficient_history'
            reason[sec, 'net_issuance'] = 'insufficient_history'
        recent = sorted((d, s) for d, s in sue[sec] if (day(asof) - day(d)).days <= 140)
        if recent:
            raw['sue'][sec] = recent[-1][1]
        history = tr[sec]
        if grid[0] in history and grid[-2] in history:
            raw['mom_12_1'][sec] = history[grid[-2]] / history[grid[0]] - 1.0
        else:
            reason[sec, 'mom_12_1'] = 'insufficient_history'
        if all(d in history for d in grid):
            rets = [history[b] / history[a] - 1.0 for a, b in zip(grid, grid[1:])]
            raw['vol_12m'][sec] = float(np.std(rets, ddof=1) * np.sqrt(12))
        else:
            reason[sec, 'vol_12m'] = 'insufficient_history'
    out, coverage = [], {}
    for fid, vals in raw.items():
        eligible = len(members) - (len(fin) if fid == 'gp_a' else 0)
        coverage[fid] = {'eligible': eligible, 'raw_present': len(vals)}
        # This threshold is from the existing engine, not a new admission rule.
        require(eligible > 0 and len(vals) / eligible >= 0.6, 'insufficient_factor_coverage_' + fid)
        secs = sorted(vals)
        v = np.array([vals[s] for s in secs], float)
        require(np.isfinite(v).all(), 'nonfinite_factor_calculation')
        lo, hi = np.percentile(v, [1, 99])
        v = np.clip(v, lo, hi)
        rk = rankdata(v, method='average')
        rn = np.array([ND.inv_cdf(r / (len(v) + 1)) for r in rk])
        sects = np.array([sector[s] for s in secs])
        zs = rn.copy()
        for sg in sorted(set(sects)):
            m = sects == sg
            zs[m] -= zs[m].mean()
        if zs.std() > 1e-9:
            zs /= zs.std()
        x = np.log(np.array([members[s] for s in secs]))
        for sg in sorted(set(sects)):
            m = sects == sg
            x[m] -= x[m].mean()
        beta = (zs * x).sum() / max((x * x).sum(), 1e-12)
        zss = zs - beta * x
        if zss.std() > 1e-9:
            zss /= zss.std()
        values = {s: (float(a), float(b), float(c), float(d))
                  for s, a, b, c, d in zip(secs, v, rn, zs, zss)}
        for sec in sorted(members):
            a, b, c, d = values.get(sec, (None,) * 4)
            out.append(dict(asof=asof, security_id=sec, factor_id=fid, raw=a,
                            rank_norm=b, z_sector=c, z_sector_size=d,
                            missing_reason=None if sec in values else reason[sec, fid]))
    return out, coverage


def prepare_publication(snapshot: FrozenMonthlyInputs, source_revision: str):
    payload = snapshot.unpack()
    require(payload['producer_version'] == PRODUCER_VERSION, 'producer_contract_drift')
    universe = plan_universe(payload)
    groups = {
        'universe': {k: payload[k] for k in ('asof', 'month_grid', 'security_ids', 'prices', 'caps')},
        'classifications': {k: payload[k] for k in ('profiles', 'symbols', 'adr_history')},
        'fundamentals': payload['fundamentals'], 'returns': payload['returns'],
        'surprises': payload['surprises'], 'benchmarks': payload['benchmarks'],
        'factor_registry': payload['registry'],
    }
    spec = normalize_spec(dict(dataset=DATASET, asof=payload['asof'], source_revision=source_revision,
        inputs_observed_at=payload['observed_at'], input_fingerprints={k: fingerprint(v) for k, v in groups.items()},
        factor_contract=CORE_FACTORS, expected_universe=payload['expected_universe'],
        expected_investable=sum(r['in_universe'] for r in universe)))
    def producer(_spec):
        require(_spec == spec, 'producer_spec_mismatch')
        factors, _ = calculate_factors(payload, universe)
        return universe, factors
    return spec, producer
