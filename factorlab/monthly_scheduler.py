"""Actual monthly producer -> validated immutable publication, with durable retries.

No changes to legacy job_log, prior panels, research evaluations or MT3 decisions.
A separate locked attempt spans refresh, read-only capture, calculation and receipt.
Schema installation and production connection permissions are not performed here.
"""
from __future__ import annotations

import re
from datetime import date
from uuid import uuid4

from .monthly_producer import prepare_publication, require
from .monthly_snapshot import capture_month, prior_month
from .publications import PublicationStore

MONTHLY_LOCK_NAMESPACE = 214012


def _summary(publication, status, attempt_id):
    return {'status': status, 'attempt_id': str(attempt_id),
            'generation_id': str(publication['generation_id']),
            'asof': str(publication['asof']), 'available_at': str(publication['available_at']),
            'universe_rows': len(publication['universe']), 'factor_rows': len(publication['factors']),
            'trading_authority': False, 'historical_source_availability_certified': False, 'source_completeness_certified': False,
            'unknown_sector_rows': sum(r['sector']=='Unknown' for r in publication['universe'])}


def run_monthly_period(connect, period, source_revision, *, refresh=None, capture=capture_month):
    """A confirmed same-code monthly publication is reused, not silently refreshed.

    Changed code creates a new generation. Explicit data-only corrections under the
    same code require a separately reviewed correction workflow; --force is not one.
    Failed attempts retry on a later invocation; active sessions are never stolen.
    """
    require(isinstance(source_revision, str) and re.fullmatch('[0-9a-f]{40}', source_revision),
            'explicit_factorlab_code_sha_required')
    begin, end = prior_month(period)
    store = PublicationStore(connect)
    cx, attempt_id = store._open(), None
    try:
        with cx.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(%s,%s)',
                        (MONTHLY_LOCK_NAMESPACE, int(period.replace('-', ''))))
            require(cur.fetchone()[0], 'monthly_scope_busy')
        cx.commit()
        with cx.cursor() as cur:
            cur.execute('''INSERT INTO fl_monthly_outcomes (attempt_id,status,error_code)
                SELECT a.attempt_id,'interrupted','interrupted' FROM fl_monthly_attempts a
                LEFT JOIN fl_monthly_outcomes o USING(attempt_id)
                WHERE a.period_key=%s AND o.attempt_id IS NULL''', (period,))
            attempt_id = uuid4()
            cur.execute('''INSERT INTO fl_monthly_attempts (attempt_id,period_key,source_revision)
                           VALUES (%s,%s,%s)''', (attempt_id, period, source_revision))
            cur.execute('''SELECT g.generation_id FROM fl_dataset_generations g
                JOIN fl_publication_requests r USING(request_id)
                WHERE r.asof >= %s AND r.asof < %s AND r.spec->>'source_revision'=%s
                ORDER BY g.sealed_at DESC,g.generation_id DESC LIMIT 1''', (begin, end, source_revision))
            found = cur.fetchone()
        cx.commit()
        if found:
            # Reconcile an already committed generation even when its receipt was lost.
            # Never replay the producer merely because confirmation was interrupted.
            publication, status = store.confirm(found[0]), 'reused'
        else:
            if refresh is not None:
                refresh()  # Caller must validate all mandatory refresh stages.
            snapshot = capture(connect, period)
            spec, producer = prepare_publication(snapshot, source_revision)
            require(begin <= date.fromisoformat(spec['asof']) < end,
                    'producer_period_mismatch')
            publication, status = store.publish(spec, producer), 'published'
        with cx.cursor() as cur:
            cur.execute('''INSERT INTO fl_monthly_outcomes (attempt_id,status,generation_id)
                           VALUES (%s,%s,%s)''', (attempt_id, status, str(publication['generation_id'])))
        cx.commit()
        return _summary(publication, status, attempt_id)
    except BaseException:
        # No replay on an uncertain connection. Later lock owners reconcile starts.
        if attempt_id is not None and not cx.closed:
            try:
                cx.rollback()
                with cx.cursor() as cur:
                    cur.execute('''INSERT INTO fl_monthly_outcomes (attempt_id,status,error_code)
                        SELECT %s,'failed','monthly_failed' WHERE EXISTS
                        (SELECT 1 FROM fl_monthly_attempts WHERE attempt_id=%s)
                        ON CONFLICT DO NOTHING''', (attempt_id, attempt_id))
                cx.commit()
            except Exception:
                if not cx.closed:
                    cx.rollback()
        raise
    finally:
        cx.close()
