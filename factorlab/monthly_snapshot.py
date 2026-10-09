"""Read one raw-input PostgreSQL snapshot for one completed monthly period.

Never reads legacy universe_snapshots/factor_values, opens an inherited transaction,
uses a reconnecting wrapper, fetches vendors, or writes. Connection ownership is
explicit and closed on every outcome. No source-completeness certification implied.
"""
from datetime import date, timedelta
import re

from .monthly_producer import (CORE_FACTORS, FIELDS, PRODUCER_VERSION, FrozenMonthlyInputs,
                               day, require, instant, number)


def prior_month(period):
    require(isinstance(period, str) and re.fullmatch(r'\d{4}-\d{2}', period), 'invalid_monthly_period')
    try:
        end = date.fromisoformat(period + '-01')
    except ValueError:
        raise ValueError('invalid_monthly_period') from None
    return (end - timedelta(days=1)).replace(day=1), end


def _rows(cur, query, params=()):
    cur.execute(query, params)
    keys = [d.name for d in cur.description]
    return [dict(zip(keys, row)) for row in cur.fetchall()]


def capture_month(connect, period, *, selection_event, vintage_id):
    from psycopg2.extensions import STATUS_READY
    from .benchmark_vintages import identifier, read_selection
    identifier(selection_event); identifier(vintage_id)
    begin, end = prior_month(period)
    cx = connect()
    try:
        require(not cx.closed and cx.status == STATUS_READY, 'snapshot_requires_idle_connection')
        cx.set_session(readonly=True, autocommit=False, isolation_level='REPEATABLE READ')
        with cx.cursor() as cur:
            cur.execute("SELECT current_setting('transaction_read_only'), clock_timestamp()")
            readonly, now = cur.fetchone()
            now = instant(now)
            require(readonly == 'on' and end <= now.date(), 'completed_month_required')
            # Preserve the legacy broad-market >=100-price session definition.
            # Do not select an arbitrary previous month when the requested one is absent.
            grid_start = date(begin.year - 1, begin.month, 1)
            calendar = _rows(cur, '''SELECT max(d) AS d FROM
                (SELECT d FROM prices_raw_d WHERE d >= %s AND d < %s
                 GROUP BY d HAVING count(*) >= 100) sessions
                GROUP BY date_trunc('month', d) ORDER BY 1''', (grid_start, end))
            require(len(calendar) == 13 and calendar[-1]['d'] >= begin, 'missing_monthly_price_calendar')
            asof = calendar[-1]['d']
            # Scope is independently queried before reading any payload rows.
            scope = _rows(cur, '''SELECT DISTINCT security_id FROM prices_raw_d
                WHERE d >= %s AND d <= %s ORDER BY security_id''', (begin, asof))
            ids = [r['security_id'] for r in scope]
            require(0 < len(ids) <= 100000, 'empty_or_oversized_month_scope')
            payload = dict(producer_version=PRODUCER_VERSION, asof=asof, month_grid=[r['d'] for r in calendar],
                           security_ids=ids, expected_universe=len(ids))
            payload['prices'] = _rows(cur, '''SELECT s.security_id, p.d, p.close, p.volume
                FROM unnest(%s::int[]) s(security_id) CROSS JOIN LATERAL
                (SELECT d,close,volume FROM prices_raw_d WHERE security_id=s.security_id AND d<=%s
                 ORDER BY d DESC LIMIT 63) p ORDER BY s.security_id,p.d''', (ids, asof))
            payload['caps'] = _rows(cur, '''SELECT security_id,asof,mktcap FROM mktcap_m
                WHERE security_id=ANY(%s) AND asof >= %s AND asof <= %s ORDER BY security_id,asof''',
                (ids, begin, asof))
            payload['profiles'] = _rows(cur, '''SELECT DISTINCT ON (security_id)
                security_id,asof,sector,is_adr FROM profile_snapshots
                WHERE security_id=ANY(%s) AND asof<=%s ORDER BY security_id,asof DESC''', (ids, now.date()))
            payload['symbols'] = _rows(cur, '''SELECT security_id,symbol,valid_from,valid_to FROM symbol_map
                WHERE security_id=ANY(%s) AND valid_from<=%s ORDER BY security_id,symbol,valid_from''',
                (ids, now.date()))
            payload['adr_history'] = _rows(cur, '''SELECT security_id,asof,is_adr FROM profile_snapshots
                WHERE security_id=ANY(%s) AND asof<=%s AND is_adr ORDER BY security_id,asof''', (ids, now.date()))
            payload['fundamentals'] = _rows(cur, '''SELECT security_id,fiscal_period_end,vintage_id,
                accepted_date,observed_at,timing_pit,value_pit,source_hash,mapping_version,currency,'''
                + ','.join(FIELDS) + ''' FROM fundamentals_q WHERE security_id=ANY(%s)
                AND timing_pit AND accepted_date IS NOT NULL AND fiscal_period_end<=%s
                ORDER BY security_id,fiscal_period_end,vintage_id''', (ids, asof))
            payload['returns'] = _rows(cur, '''SELECT security_id,d,tr,method_version FROM tr_index_d
                WHERE security_id=ANY(%s) AND d=ANY(%s::date[]) ORDER BY security_id,d''',
                (ids, payload['month_grid']))
            payload['surprises'] = _rows(cur, '''SELECT security_id,report_date,eps_actual,eps_est,sue
                FROM surprises WHERE security_id=ANY(%s) AND report_date >= %s AND report_date <= %s
                ORDER BY security_id,report_date''', (ids, asof - timedelta(days=140), asof))
            benchmark = read_selection(cur, period, selection_event, vintage_id)
            payload['benchmark_identity'] = benchmark
            payload['benchmarks'] = benchmark['rows']
            payload['registry'] = _rows(cur, '''SELECT factor_id,version,formula_hash,params,frozen
                FROM factor_definitions WHERE factor_id=ANY(%s) ORDER BY factor_id''', (list(CORE_FACTORS),))
            # Benchmark is a required upstream review input, not a sixth factor.
            require({r['asof'] for r in payload['benchmarks']} == {d.isoformat() for d in payload['month_grid']},
                    'incomplete_benchmark_grid')
            require(all(r['tr'] is not None and number(r['tr']) > 0
                        for r in payload['benchmarks']), 'invalid_benchmark_value')
            cur.execute('SELECT clock_timestamp(), pg_current_snapshot()::text')
            payload['observed_at'], payload['database_snapshot'] = cur.fetchone()
        return FrozenMonthlyInputs.freeze(payload)
    finally:
        cx.close()
