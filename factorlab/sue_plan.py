"""Snapshot-bound SUE proposals. No database, provider, or credential discovery."""
import hashlib
import json
import math
import re
import statistics
from pathlib import Path
from datetime import date, timedelta
from decimal import Decimal

VERSION = 'sue-plan-v2'
CODE_CONTRACT = 'native-sue-prior4-last8-pstdev-1e-9-round4-v1'
ENDPOINT = 'https://financialmodelingprep.com/stable/earnings'
MAX_BYTES = 5 * 1024 * 1024


class Invalid(ValueError):
    """Messages are fixed codes, never provider/input/credential text."""


def require(ok, code='invalid_document'):
    if not ok:
        raise Invalid(code)


def exact_keys(obj, keys):
    require(isinstance(obj, dict) and set(obj) == set(keys), 'invalid_shape')


def text(value):
    require(isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value)), 'invalid_identifier')
    return value


def day(value):
    require(isinstance(value, str) and bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)), 'invalid_date')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Invalid('invalid_date') from None


def number(value):
    require(not isinstance(value, bool) and isinstance(value, (str, int, Decimal)), 'invalid_number')
    try:
        n = Decimal(value)
    except Exception:
        raise Invalid('invalid_number') from None
    require(n.is_finite(), 'nonfinite_number')
    return n


def canonical(obj):
    def encode(v):
        if isinstance(v, dict):
            require(all(isinstance(k, str) for k in v), 'invalid_shape')
            return '{' + ','.join(json.dumps(k) + ':' + encode(v[k]) for k in sorted(v)) + '}'
        if isinstance(v, list): return '[' + ','.join(encode(x) for x in v) + ']'
        if isinstance(v, (Decimal, float)):
            n = Decimal(str(v)); require(n.is_finite(), 'nonfinite_number')
            return str(n)
        return json.dumps(v, ensure_ascii=True, allow_nan=False)
    return encode(obj).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load(raw):
    require(isinstance(raw, bytes) and len(raw) <= MAX_BYTES, 'document_bound')
    def pairs(items):
        out = {}
        for k, v in items:
            require(k not in out, 'duplicate_json_key')
            out[k] = v
        return out
    try:
        return json.loads(raw, parse_float=Decimal, parse_int=int,
                          parse_constant=lambda _: (_ for _ in ()).throw(Invalid('nonfinite_number')),
                          object_pairs_hook=pairs)
    except Invalid:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise Invalid('invalid_json') from None


def seal(kind, body):
    return {'kind': kind, 'version': VERSION, 'body': body, 'sha256': digest(canonical(body))}


def open_document(obj, kind):
    exact_keys(obj, ('kind', 'version', 'body', 'sha256'))
    require(obj['kind'] == kind and obj['version'] == VERSION, 'wrong_document_kind')
    require(obj['sha256'] == digest(canonical(obj['body'])), 'content_hash_mismatch')
    return obj['body']


def event(row):
    exact_keys(row, ('date', 'actual', 'estimate', 'sue'))
    day(row['date'])
    for key in ('actual', 'estimate', 'sue'):
        if row[key] is not None:
            number(row[key])
    return row


def validate_scope(scope, today=None):
    exact_keys(scope, ('period', 'asof', 'scope_identity', 'before_identity', 'semantics', 'members'))
    require(scope['semantics'] == 'UNVERIFIED_PROVIDER_DATE_AND_PIT', 'semantics_required')
    text(scope['scope_identity']); text(scope['before_identity'])
    require(isinstance(scope['period'], str) and re.fullmatch(r'\d{4}-\d{2}', scope['period']), 'invalid_period')
    first = day(scope['period'] + '-01')
    require(first <= (today or date.today()), 'unfinished_period')
    asof = day(scope['asof'])
    prev = first - timedelta(days=1)
    require((asof.year, asof.month) == (prev.year, prev.month), 'period_asof_mismatch')
    members = scope['members']; require(isinstance(members, list) and 0 < len(members) <= 10000, 'scope_bound')
    ids = set(); intervals = {}; usable = 0; missing = 0; held = 0; unknown = 0; flags = {}; held_ids = set()
    for m in members:
        exact_keys(m, ('id', 'eligible', 'held', 'marker', 'windows', 'before', 'before_complete'))
        text(m['id']); require(m['id'] not in ids, 'duplicate_security'); ids.add(m['id'])
        require(type(m['eligible']) is bool and type(m['held']) is bool and type(m['before_complete']) is bool)
        if m['held']: held_ids.add(m['id'])
        def issue(code):
            flags.setdefault(m['id'], []).append(code)
            require(m['held'], code)
        require(m['marker'] in ('absent', 'present', 'empty', 'failed'), 'invalid_marker')
        require(isinstance(m['windows'], list) and len(m['windows']) <= 20, 'window_bound')
        windows = []
        for w in m['windows']:
            exact_keys(w, ('symbol', 'start', 'end')); text(w['symbol'])
            a, b = day(w['start']), day(w['end'])
            if a > b: issue('reversed_window')
            windows.append((a, b))
            intervals.setdefault(w['symbol'], []).append((a, b, m['id']))
        for i, (a, b) in enumerate(windows):
            for c, d in windows[:i]:
                if b == d or not (b < c or d < a): issue('ambiguous_windows')
        if len(windows) != 1: issue('identity_review_required')
        require(isinstance(m['before'], list) and len(m['before']) <= 1000, 'history_bound')
        dates = set(); has = False
        for r in m['before']:
            event(r); require(r['date'] not in dates, 'duplicate_event'); dates.add(r['date'])
            require(day(r['date']) <= asof, 'future_before_event')
            if asof - timedelta(days=140) <= day(r['date']) <= asof and r['sue'] is not None:
                has = True
        if m['eligible']:
            usable += has; missing += not has and m['before_complete']; unknown += not has and not m['before_complete']; held += m['held']
    for rows in intervals.values():
        for i, (a, b, sid) in enumerate(rows):
            for c, d, other in rows[:i]:
                if sid != other and not (b < c or d < a):
                    require(sid in held_ids and other in held_ids, 'cross_security_symbol_overlap')
                    for x in (sid, other): flags.setdefault(x, []).append('cross_security_symbol_overlap')
    return {'eligible': usable + missing + unknown, 'usable': usable, 'missing': missing, 'unknown_before': unknown, 'held': held,
            'identity_flags': {sid: sorted(set(v)) for sid, v in flags.items()},
            'window_start': (asof - timedelta(days=140)).isoformat(), 'asof': asof.isoformat()}


def validate_limits(limits):
    exact_keys(limits, ('logical', 'attempts', 'retries', 'seconds', 'spacing', 'response_bytes'))
    for k in ('logical', 'attempts', 'retries', 'seconds', 'response_bytes'):
        require(type(limits[k]) is int, 'invalid_limits')
    require(1 <= limits['logical'] <= 10000 and 1 <= limits['attempts'] <= 30000, 'invalid_limits')
    require(0 <= limits['retries'] <= 2 and 1 <= limits['seconds'] <= 7200, 'invalid_limits')
    require(not isinstance(limits['spacing'], bool) and isinstance(limits['spacing'], (int, Decimal, str)) and Decimal(limits['spacing']) >= Decimal('0.15') and Decimal(limits['spacing']) <= 60, 'invalid_limits')
    require(1 <= limits['response_bytes'] <= MAX_BYTES, 'invalid_limits')


def implementation_identity():
    root = Path(__file__).resolve().parent
    names = ('sue_plan.py', 'sue_store.py', 'sue_acquire.py', 'sue_worker.py', 'sue_proposal.py')
    return {**{name: digest((root / name).read_bytes()) for name in names},
            'scripts/sue_acquisition.py': digest((root.parent / 'scripts/sue_acquisition.py').read_bytes())}


def make_plan(scope, selected, limits, today=None):
    coverage = validate_scope(scope, today); validate_limits(limits)
    require(isinstance(selected, list) and len(selected) == len(set(selected)), 'invalid_selection')
    members = {m['id']: m for m in scope['members']}; requests = {}
    for sid in selected:
        require(sid in members, 'unknown_security'); m = members[sid]
        require(m['eligible'] and not m['held'], 'identity_held')
        for w in m['windows']:
            requests.setdefault(w['symbol'], []).append({'id': sid, **w})
    require(0 < len(requests) <= limits['logical'] and len(requests) <= limits['attempts'], 'request_budget')
    body = {'scope': scope, 'selected': selected, 'limits': limits, 'coverage': coverage,
            'requests': [{'symbol': s, 'limit': 120, 'attributions': requests[s]} for s in sorted(requests)],
            'endpoint': ENDPOINT, 'contract': CODE_CONTRACT, 'code_identity': implementation_identity(), 'applicable': False}
    return seal('sue-request-plan', body)


def validate_plan(obj):
    b = open_document(obj, 'sue-request-plan')
    exact_keys(b, ('scope', 'selected', 'limits', 'coverage', 'requests', 'endpoint', 'contract', 'code_identity', 'applicable'))
    rebuilt = make_plan(b['scope'], b['selected'], b['limits'])
    require(rebuilt == obj, 'plan_mismatch')
    return b


def parse_response(raw, symbol):
    rows = load(raw); require(isinstance(rows, list) and len(rows) <= 120, 'response_schema')
    out = {}; duplicates = 0
    for r in rows:
        require(isinstance(r, dict) and {'date', 'epsActual', 'epsEstimated'} <= set(r), 'response_schema')
        require('symbol' not in r or r['symbol'] == symbol, 'wrong_symbol')
        day(r['date'])
        for k in ('epsActual', 'epsEstimated'):
            if r[k] is not None:
                number(r[k])
        # Exact parsed object equality retains unknown metadata; no first-wins conflict.
        if r['date'] in out:
            require(out[r['date']] == r, 'conflicting_duplicate'); duplicates += 1
        else:
            out[r['date']] = r
    return [out[d] for d in sorted(out)], duplicates


def calculate(rows, start, asof):
    history = []; out = []; future = 0
    for r in rows:
        d = day(r['date'])
        if d > day(asof):
            future += 1; continue
        actual, estimate = r['epsActual'], r['epsEstimated']; value = None
        reason = 'missing_pair'
        if actual is not None and estimate is not None:
            a, e = float(number(actual)), float(number(estimate)); diff = a - e
            require(all(math.isfinite(x) for x in (a, e, diff)), 'float_range')
            reason = 'insufficient_history'
            if len(history) >= 4:
                try:
                    sd = statistics.pstdev(history[-8:])
                    require(math.isfinite(sd), 'float_range')
                    reason = 'tiny_deviation'
                    if sd > 1e-9:
                        n = diff / sd; require(math.isfinite(n), 'float_range')
                        value = str(round(n, 4)); reason = 'usable'
                except (OverflowError, ValueError):
                    raise Invalid('float_range') from None
            history.append(diff)
        out.append({'date': r['date'], 'actual': None if actual is None else str(actual),
                    'estimate': None if estimate is None else str(estimate), 'sue': value,
                    'reason': reason, 'in_window': day(start) <= d <= day(asof)})
    return out, future


def proposal_shards(plan, observations):
    from .sue_acquire import validate_observation
    b = validate_plan(plan); members = {m['id']: m for m in b['scope']['members']}
    require(set(observations) == {r['symbol'] for r in b['requests']}, 'observation_set')
    for req in b['requests']:
        observation = observations[req['symbol']]
        raw, lineage = validate_observation(plan, req['symbol'], observation)
        rows, dup = parse_response(raw, req['symbol'])
        for w in req['attributions']:
            scoped = [r for r in rows if day(w['start']) <= day(r['date']) <= day(w['end'])]
            values, future = calculate(scoped, b['coverage']['window_start'], b['scope']['asof'])
            m = members[w['id']]; before = {r['date']: r for r in m['before']}
            proposals = []; changed = []; valid_prior = []
            for r in values:
                fields = {k: r[k] for k in ('date', 'actual', 'estimate', 'sue')}; old = before.get(r['date'])
                def same(a, c):
                    return a is None and c is None or a is not None and c is not None and number(a) == number(c)
                status = ('noop' if all(same(old[k], fields[k]) for k in ('actual','estimate','sue')) else 'correction') if old else ('insert' if m['before_complete'] else 'unknown_before')
                body = {'security': w['id'], 'before': old, 'proposed': fields, 'status': status,
                        'calculation': r['reason'], 'in_window': r['in_window'],
                        'changed_prefix_count': len(changed), 'prior_valid_dates': valid_prior[-8:],
                        'input_sha256': digest(raw), 'plan_sha256': plan['sha256']}
                body['economic_content_id'] = digest(canonical({'security': w['id'], 'before': old, 'proposed': fields, 'status': status}))
                body['observation_id'] = observation['lineage']['sha256']
                body['operation_id'] = digest(canonical(body)); proposals.append(body)
                if status != 'noop': changed.append(r['date'])
                if r['actual'] is not None and r['estimate'] is not None: valid_prior.append(r['date'])
            yield seal('sue-proposal-shard', {'plan_sha256': plan['sha256'], 'security': w['id'],
                'observation': observation['lineage'], 'events': proposals,
                'changed_dates': changed, 'identical_duplicates': dup,
                'gap': {'security': w['id'], 'post_asof_rows': future, 'out_of_symbol_window': len(rows)-len(scoped),
                    'history_completeness': 'UNKNOWN_LIMIT120_IS_NOT_COMPLETENESS', 'dependent_boundary': 'UNKNOWN_BEYOND_RETURNED_HISTORY',
                    'unreturned_before_dates': sorted(set(before)-{r['date'] for r in values})},
                'applicable': False, 'semantics': 'UNVERIFIED_PROVIDER_DATE_AND_PIT'})


def propose(plan, observations):
    """Small bounded in-memory convenience; CLI uses streaming write_proposal."""
    shards = []; size = 0
    for shard in proposal_shards(plan, observations):
        size += len(canonical(shard)); require(size <= MAX_BYTES, 'use_streaming_proposal')
        shards.append(shard)
    return seal('sue-proposal-preview', {'shards': shards, 'coverage': plan['body']['coverage'], 'applicable': False})
