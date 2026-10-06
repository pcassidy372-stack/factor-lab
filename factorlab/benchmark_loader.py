"""Exact-date SPY adjClose plans. No I/O or credential lookup at import.

This repairs missing benchmark cells; it does not authorize historical corrections.
The connection factory must select one database explicitly, without fallback.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

SOURCE_REVISION = '3be83349b1aacab0467dac62b373106a088fa7e7'
CALENDAR_SQL = """SELECT max(d) AS d FROM
 (SELECT d FROM public.prices_raw_d WHERE d >= %s AND d < %s
  GROUP BY d HAVING count(*) >= 100) sessions
 GROUP BY date_trunc('month', d) ORDER BY 1"""
LOCK_KEY = 694130214  # cooperative benchmark writers; SERIALIZABLE also guards other writers


class BenchmarkError(RuntimeError):
    """Fixed, credential-free failure classification."""


class CommitUncertain(BenchmarkError):
    """Do not retry writes. Reconcile this exact plan through a fresh connection."""


def require(ok, code):
    if not ok:
        raise BenchmarkError(code)


def day(value):
    require(type(value) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value), 'invalid_date')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise BenchmarkError('invalid_date') from None


def month_bounds(period, today):
    require(type(period) is str and re.fullmatch(r'\d{4}-\d{2}', period), 'invalid_period')
    try:
        end = date.fromisoformat(period + '-01')
        begin = (end - timedelta(days=1)).replace(day=1)
        start = date(begin.year - 1, begin.month, 1)
    except ValueError:
        raise BenchmarkError('invalid_period') from None
    require(end <= today, 'completed_period_required')
    return start, end


def validate_grid(period, grid, today):
    start, end = month_bounds(period, today)
    require(type(grid) is list and len(grid) == 13, 'incomplete_calendar')
    ds = [day(d) for d in grid]
    require(ds == sorted(set(ds)), 'ambiguous_calendar')
    require(all(start <= d < end for d in ds), 'calendar_outside_period')
    require([d.year * 12 + d.month for d in ds] ==
            list(range(start.year * 12 + start.month, start.year * 12 + start.month + 13)),
            'nonconsecutive_calendar')
    return ds


def numeric(value):
    require(type(value) in (str, int, float, Decimal), 'invalid_adjusted_price')
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise BenchmarkError('invalid_adjusted_price') from None
    require(n.is_finite() and n > 0, 'invalid_adjusted_price')
    # No float conversion, epsilon or rounding; benchmarks_m.tr is unconstrained NUMERIC.
    require(n.adjusted() < 131072 and n.as_tuple().exponent >= -16383, 'numeric_out_of_range')
    return n


def decimal_text(value):
    n = numeric(value)
    s = format(n, 'f')
    return s.rstrip('0').rstrip('.') if '.' in s else s


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def request_scope(grid):
    return {'endpoint': 'prices_div_adjusted', 'symbol': 'SPY',
            'date_from': grid[0], 'date_to': grid[-1], 'value_field': 'adjClose'}


def validate_response(rows, grid):
    require(type(rows) is list and bool(rows), 'invalid_provider_schema_or_empty')
    values = {}
    for row in rows:
        require(type(row) is dict and 'date' in row and 'adjClose' in row, 'invalid_provider_row')
        require('symbol' not in row or row['symbol'] == 'SPY', 'wrong_provider_symbol')
        d = day(row['date']).isoformat()
        value = decimal_text(row['adjClose'])
        require(d not in values or values[d] == value, 'conflicting_provider_duplicate')
        values[d] = value  # truly identical duplicates collapse, independent of response order
    require(set(grid) <= values.keys(), 'missing_exact_benchmark_dates')
    return values


def fetch_values(client, grid):
    scope = request_scope(grid)
    try:
        rows = client.get(scope['endpoint'], symbol='SPY', date_from=scope['date_from'],
                          date_to=scope['date_to'], allow_empty=False)
    except Exception:
        raise BenchmarkError('provider_request_failed') from None
    return validate_response(rows, grid)


def make_plan(period, grid, rows, existing, target, observed_at):
    try:
        now = datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
        require(now.tzinfo is not None and now.utcoffset() == timedelta(0), 'invalid_observation_time')
    except (ValueError, TypeError, AttributeError):
        raise BenchmarkError('invalid_observation_time') from None
    validate_grid(period, grid, now.date())
    values = validate_response(rows, grid)
    require(set(existing) <= set(grid), 'existing_outside_grid')
    prior = {d: decimal_text(v) for d, v in sorted(existing.items())}
    selected = {d: values[d] for d in grid}
    plan = dict(format_version=1, period=period, grid=grid, source_revision=SOURCE_REVISION,
                target=target, request_scope=request_scope(grid), observed_at=now.isoformat(),
                payload_sha256=digest(values), values=selected, expected_existing=prior,
                missing_keys=[d for d in grid if d not in prior],
                matching_overlaps=[d for d in grid if d in prior and prior[d] == selected[d]],
                conflicting_overlaps=[d for d in grid if d in prior and prior[d] != selected[d]])
    plan['plan_sha256'] = digest(plan)
    return plan


def check_plan(plan, today):
    require(type(plan) is dict, 'invalid_plan')
    keys = {'format_version','period','grid','source_revision','target','request_scope','observed_at',
            'payload_sha256','values','expected_existing','missing_keys','matching_overlaps',
            'conflicting_overlaps','plan_sha256'}
    require(set(plan) == keys and plan['format_version'] == 1 and
            plan['source_revision'] == SOURCE_REVISION, 'invalid_plan_identity')
    require(digest({k:v for k,v in plan.items() if k != 'plan_sha256'}) == plan['plan_sha256'], 'plan_hash_mismatch')
    validate_grid(plan['period'], plan['grid'], today)
    require(plan['request_scope'] == request_scope(plan['grid']), 'invalid_request_scope')
    require(set(plan['values']) == set(plan['grid']), 'incomplete_plan_values')
    # Rebuild classifications; payload digest binds normalized vendor response including off-grid rows.
    rebuilt = make_plan(plan['period'], plan['grid'],
                        [{'date':d,'adjClose':v} for d,v in plan['values'].items()],
                        plan['expected_existing'], plan['target'], plan['observed_at'])
    for k in keys - {'payload_sha256','plan_sha256'}:
        require(plan[k] == rebuilt[k], 'invalid_plan_fields')
    require(type(plan['payload_sha256']) is str and re.fullmatch('[0-9a-f]{64}',plan['payload_sha256']), 'invalid_payload_hash')
    require(not plan['conflicting_overlaps'], 'adjusted_price_vintage_conflict')


def _start(cx, readonly):
    from psycopg2.extensions import STATUS_READY
    require(not cx.closed and cx.status == STATUS_READY and not cx.autocommit,
            'idle_owned_transaction_required')
    cx.set_session(readonly=readonly, isolation_level='SERIALIZABLE' if not readonly else 'REPEATABLE READ')
    cur = cx.cursor()
    cur.execute("SET LOCAL statement_timeout = '15s'")
    cur.execute("SET LOCAL lock_timeout = '2s'")
    cur.execute("SET LOCAL idle_in_transaction_session_timeout = '30s'")
    cur.execute("SET LOCAL search_path = pg_catalog, public, pg_temp")
    return cur


def _target(cur):
    cur.execute("""SELECT current_database(), d.oid, c.oid FROM pg_catalog.pg_database d
      JOIN pg_catalog.pg_class c ON c.relnamespace='public'::regnamespace
      WHERE d.datname=current_database() AND c.relname='benchmarks_m' AND c.relkind='r'
        AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity""")
    row = cur.fetchone(); require(row is not None, 'unsafe_or_missing_benchmark_table')
    cur.execute("""SELECT attname, format_type(atttypid,atttypmod), attnotnull
      FROM pg_catalog.pg_attribute WHERE attrelid='public.benchmarks_m'::regclass
      AND attnum>0 AND NOT attisdropped ORDER BY attnum""")
    require(cur.fetchall() == [('asof','date',True),('symbol','text',True),('tr','numeric',True)], 'benchmark_schema_mismatch')
    cur.execute("""SELECT pg_get_constraintdef(oid) FROM pg_catalog.pg_constraint
      WHERE conrelid='public.benchmarks_m'::regclass AND contype='p'""")
    require(cur.fetchall() == [('PRIMARY KEY (asof, symbol)',)], 'benchmark_key_mismatch')
    cur.execute("SELECT count(*) FROM pg_catalog.pg_trigger WHERE tgrelid='public.benchmarks_m'::regclass AND NOT tgisinternal")
    require(cur.fetchone()[0] == 0, 'unreviewed_benchmark_trigger')
    cur.execute("SELECT count(*) FROM pg_catalog.pg_rewrite WHERE ev_class='public.benchmarks_m'::regclass")
    require(cur.fetchone()[0] == 0, 'unreviewed_benchmark_rule')
    return {'database':row[0], 'database_oid':row[1], 'table_oid':row[2], 'schema':'public', 'table':'benchmarks_m'}


def _calendar(cur, period):
    cur.execute('SELECT clock_timestamp()'); now = cur.fetchone()[0].astimezone(timezone.utc)
    bounds = month_bounds(period, now.date())
    cur.execute(CALENDAR_SQL, bounds)
    grid = [r[0].isoformat() for r in cur.fetchall()]
    validate_grid(period, grid, now.date())
    return grid, now


def _existing(cur, grid):
    cur.execute("SELECT asof,tr FROM public.benchmarks_m WHERE symbol='SPY' AND asof=ANY(%s::date[]) ORDER BY asof", (grid,))
    return {d.isoformat():decimal_text(v) for d,v in cur.fetchall()}


def _connect(connect):
    try:
        return connect()
    except Exception:
        raise BenchmarkError('database_connection_failed') from None


def read_state(connect, period):
    cx = _connect(connect)
    try:
        with _start(cx, True) as cur:
            target = _target(cur); grid, now = _calendar(cur, period)
            return target, grid, _existing(cur,grid), now.isoformat()
    except BenchmarkError:
        raise
    except Exception:
        raise BenchmarkError('database_read_failed') from None
    finally:
        cx.close()  # rolls back owned read snapshot


def apply_plan(connect, plan):
    """One owned transaction; any failure before commit rolls back. Never retries."""
    check_plan(plan, datetime.now(timezone.utc).date())
    cx = _connect(connect); committing = False
    try:
        with _start(cx, False) as cur:
            cur.execute('SELECT pg_catalog.pg_advisory_xact_lock(%s)', (LOCK_KEY,))
            require(_target(cur) == plan['target'], 'target_changed')
            grid, _ = _calendar(cur,plan['period']); require(grid == plan['grid'], 'calendar_changed')
            existing = _existing(cur,grid)
            require(all(existing.get(d) == v for d,v in plan['expected_existing'].items()), 'expected_overlap_changed')
            require(all(v == plan['values'][d] for d,v in existing.items()), 'adjusted_price_vintage_conflict')
            missing = [d for d in grid if d not in existing]
            for d in missing:
                cur.execute("INSERT INTO public.benchmarks_m(asof,symbol,tr) VALUES (%s,'SPY',%s)",
                            (d, Decimal(plan['values'][d])))
            require(_existing(cur,grid) == plan['values'], 'final_readback_mismatch')
        committing = True
        cx.commit()
        return {'outcome':'committed' if missing else 'verified_noop', 'inserted':len(missing), 'plan_sha256':plan['plan_sha256']}
    except BenchmarkError:
        raise
    except Exception:
        if committing:
            raise CommitUncertain('commit_outcome_unknown_reconcile_without_retry') from None
        raise BenchmarkError('transaction_failed_rolled_back') from None
    finally:
        cx.close()  # rollback on pre-commit failure, including cancellation; no reconnect


def reconcile_plan(connect, plan):
    """Fresh read only connection: exact values, not proof which actor committed them."""
    check_plan(plan, datetime.now(timezone.utc).date())
    target, grid, existing, _ = read_state(connect, plan['period'])
    require(target == plan['target'] and grid == plan['grid'], 'reconciliation_target_or_calendar_changed')
    conflicts = [d for d,v in existing.items() if v != plan['values'][d]]
    missing = [d for d in grid if d not in existing]
    return {'outcome':'conflict' if conflicts else ('incomplete' if missing else 'exact_grid_present'),
            'missing_keys':missing,'conflicting_keys':conflicts,'plan_sha256':plan['plan_sha256']}
