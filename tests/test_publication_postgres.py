"""Real PostgreSQL publication regressions; strict disposable-target allowlist."""
import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from factorlab.publication_contract import (CORE_FACTORS, FACTOR_KEYS, UNIVERSE_KEYS,
    PublicationError, prepare_rows)
from factorlab.publications import PublicationStore
from publication_samples import sample

pytestmark = pytest.mark.postgres
TABLES = ['fl_publication_requests','fl_publication_attempts','fl_publication_outcomes',
          'fl_dataset_generations','fl_universe_generations','fl_factor_generations',
          'fl_publication_receipts']


@pytest.fixture
def database():
    url = os.environ.get('FACTORLAB_TEST_DATABASE_URL')
    if not url:
        pytest.skip('No explicit disposable PostgreSQL target; not an integration pass')
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import parse_dsn
    options = parse_dsn(url)
    if (options.get('host') != '127.0.0.1' or options.get('dbname') != 'factorlab_ci'
            or options.get('user') != 'factorlab_ci'
            or set(options) - {'host','dbname','user','password','port','sslmode'}):
        pytest.fail('Only explicit loopback factorlab_ci is permitted; target withheld')
    schema = 'fl_publication_test_' + uuid4().hex
    admin = psycopg2.connect(url, connect_timeout=5)
    made = False
    def connect():
        cx = psycopg2.connect(url, connect_timeout=5)
        with cx.cursor() as cur:
            cur.execute(sql.SQL('SET search_path TO {}, pg_catalog').format(sql.Identifier(schema)))
            cur.execute("SET statement_timeout='15s'")
            cur.execute("SET lock_timeout='5s'")
        cx.commit()
        return cx
    try:
        with admin:
            with admin.cursor() as cur:
                cur.execute('SELECT current_database(),current_user')
                assert cur.fetchone() == ('factorlab_ci','factorlab_ci')
                cur.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        made = True
        cx = connect()
        try:
            from factorlab.migrations import MIGRATIONS
            with cx:
                with cx.cursor() as cur:
                    for n in sorted(MIGRATIONS):
                        cur.execute(MIGRATIONS[n])
                    cur.execute("INSERT INTO issuers (issuer_id,cik,name) VALUES (1,'123','Synthetic')")
                    cur.execute('INSERT INTO securities(security_id,issuer_id) VALUES (1,1),(2,1),(3,1)')
                    # Preserve actual legacy rows, not just zero-count tables.
                    cur.execute("INSERT INTO universe_snapshots VALUES ('2019-07-31',1,1,1,1,true,'small')")
                    cur.execute("""INSERT INTO factor_definitions(factor_id,version,family,formula_text,
                        formula_hash,params,prior_sign) VALUES ('sue',1,'synthetic','fixture','fixture','{}',1)""")
                    cur.execute("INSERT INTO factor_values VALUES ('2019-07-31',1,'sue',1,0,0,0)")
                    cur.execute("INSERT INTO factor_ic VALUES ('sue','2019-07-31',1,0,1)")
                    cur.execute("INSERT INTO factor_ls VALUES ('sue','2019-07-31',0,0,0,1)")
        finally:
            cx.close()
        yield connect
    finally:
        if made:
            with admin:
                with admin.cursor() as cur:
                    cur.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        admin.close()


def query(connect, text, params=()):
    cx = connect()
    try:
        with cx:
            with cx.cursor() as cur:
                cur.execute(text, params)
                return cur.fetchall() if cur.description else None
    finally:
        cx.close()


def counts(connect):
    return {t: query(connect,'SELECT count(*) FROM ' + t)[0][0] for t in TABLES}


def publish(store, data=None):
    s,u,f = data or sample()
    return store.publish(s, lambda _: (u,f))


def staged(attempt, *, factors=True, all_null=False, wrong_date=False):
    """Bypass Python checks to exercise database constraints themselves."""
    from psycopg2.extras import execute_values
    s,u,f = sample()
    u,f = prepare_rows(s,u,f)
    if all_null:
        for r in f:
            if r['factor_id'] == 'sue':
                r.update(raw=None,rank_norm=None,z_sector=None,z_sector_size=None,
                         missing_reason='source_value_missing')
    if wrong_date:
        f[0]['asof'] = '2019-07-31'
    with attempt.cx.cursor() as cur:
        cur.execute('INSERT INTO fl_dataset_generations (generation_id) VALUES (%s)', (attempt.attempt_id,))
        for table,keys,data in [('fl_universe_generations',UNIVERSE_KEYS,u)] + (
                [('fl_factor_generations',FACTOR_KEYS,f)] if factors else []):
            execute_values(cur,'INSERT INTO ' + table + ' (generation_id,' + ','.join(keys) + ') VALUES %s',
                           [(attempt.attempt_id,) + tuple(r[k] for k in keys) for r in data])
        cur.execute("""INSERT INTO fl_publication_outcomes (attempt_id,status,generation_id)
                       VALUES (%s,'published',%s)""", (attempt.attempt_id,attempt.attempt_id))


def test_complete_generation_and_fresh_connection_readback(database):
    store = PublicationStore(database)
    p = publish(store)
    assert counts(database) == dict(zip(TABLES,[1,1,1,1,3,10,1]))
    assert len(p['universe']) == 3 and len(p['factors']) == 10
    assert len(p['universe_sha256']) == len(p['factors_sha256']) == 64
    assert query(database,'SELECT sealed_at FROM fl_dataset_generations')[0][0] <= p['available_at']
    assert store.load_id(p['generation_id']) == p


def test_exact_retry_preserves_generation_receipt_and_hashes(database):
    store = PublicationStore(database)
    first, second = publish(store), publish(store)
    assert first == second
    assert counts(database) == dict(zip(TABLES,[1,2,2,1,3,10,1]))
    assert query(database,'SELECT status FROM fl_publication_outcomes ORDER BY finished_at') == [('published',),('reused',)]


def test_retry_with_changed_output_is_a_hard_conflict(database):
    store = PublicationStore(database)
    first = publish(store)
    s,u,f = sample()
    f[0]['raw'] = 42
    with pytest.raises(PublicationError, match='retry_payload_conflict'):
        publish(store,(s,u,f))
    assert store.load_id(first['generation_id']) == first
    assert query(database,"SELECT count(*) FROM fl_publication_outcomes WHERE status='failed'")[0][0] == 1


def test_new_source_revision_creates_new_generation_without_rewriting_old(database):
    store = PublicationStore(database)
    old = publish(store)
    s,u,f = sample()
    s['source_revision'] = '2'*40
    new = publish(store,(s,u,f))
    assert new['generation_id'] != old['generation_id']
    assert new['request_id'] != old['request_id']
    assert new['universe_sha256'] == old['universe_sha256']
    assert new['factors_sha256'] == old['factors_sha256']
    assert store.select(s['asof'],new['available_at'])['generation_id'] == new['generation_id']
    assert store.load_id(old['generation_id']) == old


def test_input_revision_also_changes_generation_identity(database):
    store = PublicationStore(database)
    old = publish(store)
    s,u,f = sample()
    s['input_fingerprints']['fundamentals'] = 'f'*64
    new = publish(store,(s,u,f))
    assert new['request_id'] != old['request_id']


def test_partial_write_failure_leaves_previous_publication_visible(database,monkeypatch):
    import psycopg2.extras
    store = PublicationStore(database)
    old = publish(store)
    s,u,f = sample()
    s['source_revision'] = '2'*40
    original, calls = psycopg2.extras.execute_values, []
    def fail_second(cur, sql, args, **kwargs):
        calls.append(sql)
        if len(calls) == 2:
            raise RuntimeError('synthetic interrupted factor write')
        value = original(cur,sql,args,**kwargs)
        # A separate reader during the uncommitted universe write sees old data.
        visible = store.select(s['asof'], datetime.now(timezone.utc))
        assert visible['generation_id'] == old['generation_id']
        return value
    monkeypatch.setattr(psycopg2.extras,'execute_values',fail_second)
    with pytest.raises(RuntimeError, match='synthetic interrupted'):
        publish(store,(s,u,f))
    assert counts(database)['fl_dataset_generations'] == 1
    assert store.load_id(old['generation_id']) == old
    assert query(database,"SELECT count(*) FROM fl_publication_outcomes WHERE status='failed'")[0][0] == 1


@pytest.mark.parametrize('mode', ['missing','all_null','wrong_date'])
def test_database_itself_rejects_incomplete_or_invalid_generation(database,mode):
    import psycopg2
    store = PublicationStore(database)
    s,_,_ = sample()
    with pytest.raises(psycopg2.Error):
        with store.build(s) as a:
            staged(a, factors=mode!='missing', all_null=mode=='all_null', wrong_date=mode=='wrong_date')
            a.cx.commit()
    assert counts(database)['fl_dataset_generations'] == 0
    assert query(database,'SELECT status FROM fl_publication_outcomes') == [('failed',)]


def test_unconfirmed_complete_generation_is_not_selectable(database):
    store = PublicationStore(database)
    s,u,f = sample()
    with store.build(s) as a:
        gid = a.write(u,f)
    with pytest.raises(PublicationError, match='publication_not_confirmed'):
        store.load_id(gid)
    with pytest.raises(PublicationError, match='before_cutoff'):
        store.select(s['asof'],datetime.now(timezone.utc))
    before = query(database,'SELECT clock_timestamp()')[0][0]
    p = store.confirm(gid)
    assert p['available_at'] >= before


def test_receipt_cannot_be_created_in_the_sealing_transaction(database):
    import psycopg2
    store = PublicationStore(database)
    s,_,_ = sample()
    with pytest.raises(psycopg2.errors.CheckViolation, match='receipt_requires_committed'):
        with store.build(s) as a:
            staged(a)
            with a.cx.cursor() as cur:
                cur.execute('INSERT INTO fl_publication_receipts (generation_id) VALUES (%s)', (a.attempt_id,))
    assert counts(database)['fl_dataset_generations'] == 0


def test_caller_cannot_backdate_a_receipt(database):
    store = PublicationStore(database)
    s,u,f = sample()
    with store.build(s) as a:
        gid = a.write(u,f)
    before = query(database,'SELECT clock_timestamp()')[0][0]
    query(database,"""INSERT INTO fl_publication_receipts
        (generation_id,available_at,universe_sha256,factors_sha256)
        VALUES (%s,'2000-01-01','spoof','spoof')""", (str(gid),))
    p = store.load_id(gid)
    assert p['available_at'] >= before and p['universe_sha256'] != 'spoof'


def test_retry_after_seal_before_receipt_recovers_without_rewriting(database):
    store = PublicationStore(database)
    s,u,f = sample()
    with store.build(s) as a:
        gid = a.write(u,f)
    recovered = publish(store)
    assert recovered['generation_id'] == gid
    assert counts(database)['fl_dataset_generations'] == 1
    assert query(database,'SELECT status FROM fl_publication_outcomes ORDER BY finished_at') == [('published',),('reused',)]


def test_late_confirmation_of_older_generation_does_not_override_newer_generation(database):
    store = PublicationStore(database)
    s,u,f = sample()
    with store.build(s) as a:
        gid = a.write(u,f)
    s['source_revision'] = '2'*40
    new = publish(store,(s,u,f))
    old = store.confirm(gid)
    assert old['available_at'] >= new['available_at']
    selected = store.select(s['asof'],old['available_at'])
    assert selected['generation_id'] == new['generation_id']


def test_concurrent_duplicate_publishers_cannot_create_mixed_or_duplicate_generations(database):
    store = PublicationStore(database)
    def run(_):
        try:
            return publish(store)
        except PublicationError as exc:
            assert str(exc) == 'publication_scope_busy'
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(4)))
    successful = [r for r in results if r is not None]
    assert successful and len({r['generation_id'] for r in successful}) == 1
    assert counts(database)['fl_dataset_generations'] == 1
    # Explicit later retries, rather than silent retries on an uncertain commit.
    for r in results:
        if r is None:
            assert publish(store)['generation_id'] == successful[0]['generation_id']


def test_lost_lock_owner_leaves_interrupted_evidence_and_can_be_retried(database):
    store = PublicationStore(database)
    s,_,_ = sample()
    context = store.build(s)
    old = context.__enter__()
    old.cx.close()  # Simulates loss of the dedicated lock connection/process.
    try:
        result = publish(store)
        assert result['generation_id'] != old.attempt_id
        assert query(database,'SELECT status FROM fl_publication_outcomes WHERE attempt_id=%s',
                     (str(old.attempt_id),)) == [('interrupted',)]
    finally:
        context.__exit__(RuntimeError,RuntimeError('synthetic disconnect'),None)


def test_normal_exit_without_write_is_failed_not_healthy(database):
    store = PublicationStore(database)
    s,_,_ = sample()
    with pytest.raises(PublicationError, match='without_publication'):
        with store.build(s):
            pass
    assert query(database,'SELECT status FROM fl_publication_outcomes') == [('failed',)]
    assert publish(store)['generation_id'] is not None


@pytest.mark.parametrize('table',TABLES)
@pytest.mark.parametrize('operation',['UPDATE','DELETE','TRUNCATE'])
def test_publication_and_attempt_history_are_immutable(database,table,operation):
    import psycopg2
    publish(PublicationStore(database))
    if operation == 'UPDATE':
        key = {'fl_publication_requests':'request_id','fl_publication_attempts':'attempt_id',
               'fl_publication_outcomes':'attempt_id'}.get(table,'generation_id')
        statement = 'UPDATE ' + table + ' SET ' + key + '=' + key
    else:
        statement = ('DELETE FROM ' if operation=='DELETE' else 'TRUNCATE ') + table
    with pytest.raises(psycopg2.Error):
        query(database,statement)


def test_sealed_generation_rejects_late_children_even_with_new_security(database):
    import psycopg2
    p = publish(PublicationStore(database))
    with pytest.raises(psycopg2.errors.CheckViolation, match='late_children'):
        query(database,"""INSERT INTO fl_factor_generations
            (generation_id,asof,security_id,factor_id,missing_reason)
            VALUES (%s,'2020-08-31',3,'sue','not_applicable')""", (str(p['generation_id']),))


def test_no_prior_date_fallback_and_no_prepublication_cutoff(database):
    store = PublicationStore(database)
    p = publish(store)
    assert store.readiness('2020-07-31',p['available_at'])['status'] == 'unavailable'
    assert store.readiness('2020-08-31',p['available_at']-timedelta(microseconds=1))['status'] == 'unavailable'
    h = store.readiness('2020-08-31',p['available_at'])
    assert h['status'] == 'ready' and h['trading_authority'] is False
    assert h['source_completeness_certified'] is False


def test_partial_missingness_is_visible_in_health_and_not_imputed(database):
    store = PublicationStore(database)
    s,u,f = sample()
    f[0].update(raw=None,rank_norm=None,z_sector=None,z_sector_size=None,missing_reason='not_applicable')
    p = publish(store,(s,u,f))
    health = store.readiness(s['asof'],p['available_at'])
    assert health['factor_coverage']['gp_a'] == {'rows':2,'nonnull':1,'missing':{'not_applicable':1}}
    assert store.load_id(p['generation_id'])['factors'][0]['raw'] is None


def test_legacy_universe_and_research_tables_are_untouched(database):
    tables = ['universe_snapshots','factor_values','factor_ic','factor_ls','job_log']
    before = {t:query(database,'SELECT * FROM '+t) for t in tables}
    publish(PublicationStore(database))
    assert {t:query(database,'SELECT * FROM '+t) for t in tables} == before


@pytest.mark.parametrize('field,value',[('source_revision',None),('expected_universe',None),
    ('inputs_observed_at','2999-01-01T00:00:00+00:00'),('factor_contract',{}),
    ('asof','2999-08-31')])
def test_database_rejects_invalid_specs_without_trusting_python(database,field,value):
    import psycopg2
    s,_,_ = sample()
    s[field] = value
    with pytest.raises(psycopg2.Error):
        query(database,'INSERT INTO fl_publication_requests (spec) VALUES (%s::jsonb)',(json.dumps(s),))
    assert counts(database)['fl_publication_requests'] == 0


def test_early_constraint_flush_cannot_allow_extra_same_transaction_children(database):
    import psycopg2
    store = PublicationStore(database)
    s,_,_ = sample()
    with pytest.raises(psycopg2.errors.CheckViolation, match='late_children'):
        with store.build(s) as a:
            staged(a)
            with a.cx.cursor() as cur:
                cur.execute('SET CONSTRAINTS ALL IMMEDIATE')
                cur.execute("""INSERT INTO fl_factor_generations
                    (generation_id,asof,security_id,factor_id,missing_reason)
                    VALUES (%s,'2020-08-31',3,'sue','not_applicable')""",(a.attempt_id,))
    assert counts(database)['fl_dataset_generations'] == 0


def test_lost_ack_after_commit_is_not_replayed_or_marked_failed(database,monkeypatch):
    from factorlab.publications import BuildAttempt
    store = PublicationStore(database)
    original = BuildAttempt.write
    def lost_ack(self, universe, factors):
        original(self,universe,factors)
        raise RuntimeError('synthetic lost commit acknowledgment')
    monkeypatch.setattr(BuildAttempt,'write',lost_ack)
    with pytest.raises(RuntimeError, match='lost commit acknowledgment'):
        publish(store)
    assert query(database,'SELECT status FROM fl_publication_outcomes') == [('published',)]
    assert counts(database)['fl_dataset_generations'] == 1
    assert counts(database)['fl_publication_receipts'] == 0
    monkeypatch.setattr(BuildAttempt,'write',original)
    recovered = publish(store)
    assert recovered['generation_id'] is not None
    assert counts(database)['fl_dataset_generations'] == 1
