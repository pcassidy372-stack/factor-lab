"""End-to-end raw snapshot -> calculation -> publication in disposable PostgreSQL."""
import copy
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
pytest.importorskip('psycopg2')
from psycopg2.extras import Json, execute_values

from test_publication_postgres import database, query
from monthly_samples import monthly_sample
from factorlab.monthly_producer import FrozenMonthlyInputs, FIELDS, prepare_publication, plan_universe
from factorlab.monthly_snapshot import capture_month
from factorlab.monthly_scheduler import run_monthly_period
from factorlab.publications import PublicationStore
from factorlab.publication_contract import PublicationError, CORE_FACTORS

pytestmark = pytest.mark.postgres
REVISION = 'a'*40


def seed(connect):
    payload=monthly_sample()
    cx=connect()
    try:
        with cx:
            with cx.cursor() as cur:
                execute_values(cur,'INSERT INTO securities (security_id,issuer_id) VALUES %s ON CONFLICT DO NOTHING',
                    [(i,1) for i in payload['security_ids']])
                bykey={(r['security_id'],r['d']):r for r in payload['prices']}
                for sec in payload['security_ids']:
                    for d in payload['month_grid']:
                        bykey.setdefault((sec,d),dict(security_id=sec,d=d,close=10+sec/100,volume=1e6))
                execute_values(cur,'INSERT INTO prices_raw_d (security_id,d,close,volume) VALUES %s',
                    [(r['security_id'],r['d'],r['close'],r['volume']) for r in bykey.values()])
                execute_values(cur,'INSERT INTO mktcap_m (asof,security_id,mktcap) VALUES %s',
                    [(r['asof'],r['security_id'],r['mktcap']) for r in payload['caps']])
                execute_values(cur,"INSERT INTO profile_snapshots (security_id,asof,sector,is_adr,raw) VALUES %s",
                    [(r['security_id'],r['asof'],r['sector'],r['is_adr'],Json({})) for r in payload['profiles']])
                execute_values(cur,'INSERT INTO symbol_map(security_id,symbol,valid_from,valid_to,source) VALUES %s',
                    [(r['security_id'],r['symbol'],r['valid_from'],r['valid_to'],'synthetic') for r in payload['symbols']])
                keys=('security_id','fiscal_period_end','vintage_id','accepted_date','observed_at',
                      'timing_pit','value_pit','source_hash','mapping_version','currency')+FIELDS
                execute_values(cur,'INSERT INTO fundamentals_q ('+','.join(keys)+',period,raw) VALUES %s',
                    [tuple(r[k] for k in keys)+('Q1',Json({})) for r in payload['fundamentals']])
                execute_values(cur,'INSERT INTO tr_index_d (security_id,d,tr,method_version) VALUES %s',
                    [(r['security_id'],r['d'],r['tr'],r['method_version']) for r in payload['returns']])
                execute_values(cur,'INSERT INTO surprises (security_id,report_date,eps_actual,eps_est,sue) VALUES %s',
                    [(r['security_id'],r['report_date'],r['eps_actual'],r['eps_est'],r['sue']) for r in payload['surprises']])
                execute_values(cur,'INSERT INTO benchmarks_m (asof,symbol,tr) VALUES %s',
                    [(r['asof'],r['symbol'],r['tr']) for r in payload['benchmarks']])
                for r in payload['registry']:
                    cur.execute('''INSERT INTO factor_definitions(factor_id,version,family,formula_text,formula_hash,params,prior_sign)
                        VALUES (%s,%s,'synthetic','fixture',%s,%s,1) ON CONFLICT(factor_id) DO UPDATE
                        SET version=EXCLUDED.version,formula_hash=EXCLUDED.formula_hash,params=EXCLUDED.params''',
                        (r['factor_id'],r['version'],r['formula_hash'],Json(r['params'])))
    finally:
        cx.close()
    return payload


def legacy_state(connect):
    return {t:query(connect,'SELECT md5(string_agg(row_to_json(t)::text,\'|\' ORDER BY row_to_json(t)::text)) FROM '+t+' t')[0][0]
            for t in ('universe_snapshots','factor_values','factor_ic','factor_ls','job_log')}


def test_raw_collector_and_actual_publisher_preserve_populated_legacy_tables(database):
    seed(database)
    query(database,"INSERT INTO job_log (job,period_key,status,detail) VALUES ('monthly','2020-09','ok','{\"old\":true}')")
    before=legacy_state(database)
    snapshot=capture_month(database,'2020-09')
    p=snapshot.unpack()
    assert p['asof']=='2020-08-31' and len(p['security_ids'])==100
    assert len(p['prices'])==6300 and len(p['month_grid'])==13
    assert datetime.fromisoformat(p['observed_at']).tzinfo is not None
    result=run_monthly_period(database,'2020-09',REVISION)
    assert result['factor_rows']==500 and result['universe_rows']==100
    assert result['trading_authority'] is result['source_completeness_certified'] is False
    assert before==legacy_state(database)
    assert query(database,'SELECT status FROM fl_monthly_outcomes')==[('published',)]


def test_numeric_parity_against_unchanged_legacy_engine_on_same_frozen_inputs(database,monkeypatch):
    payload=seed(database)
    snapshot=capture_month(database,'2020-09')
    spec,producer=prepare_publication(snapshot,REVISION)
    universe,factors=producer(spec)
    query(database,"DELETE FROM universe_snapshots") # test-only oracle setup, never production
    cx=database()
    try:
        with cx:
            with cx.cursor() as cur:
                execute_values(cur,'INSERT INTO universe_snapshots VALUES %s',
                    [(d,r['security_id'],r['mktcap'],r['adv_63d'],r['price'],r['in_universe'],r['size_bucket'])
                     for d in payload['month_grid'] for r in universe])
                for fid in ('ebit_ev','accruals','asset_growth','bp','trk_core_1m','beta_36m'):
                    cur.execute('''INSERT INTO factor_definitions(factor_id,version,family,formula_text,formula_hash,params,prior_sign)
                        VALUES (%s,1,'synthetic','fixture','fixture',%s,1)''', (fid,Json({'excl_financials': fid in ('ebit_ev','accruals','asset_growth')})))
    finally: cx.close()
    path=Path(__file__).resolve().parents[1]/'scripts/factor_compute_v2.py'
    module_spec=importlib.util.spec_from_file_location('legacy_monthly_parity',path)
    old=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(old)
    monkeypatch.setattr(old,'conn',database)
    old.main() # unchanged legacy math oracle, only this disposable database
    oracle={(s,f):tuple(float(v) for v in vals) for s,f,*vals in query(database,
        "SELECT security_id,factor_id,raw,rank_norm,z_sector,z_sector_size FROM factor_values WHERE asof='2020-08-31' AND factor_id=ANY(%s)", (list(CORE_FACTORS),))}
    present={(r['security_id'],r['factor_id']):tuple(r[k] for k in ('raw','rank_norm','z_sector','z_sector_size'))
             for r in factors if r['raw'] is not None}
    assert set(present)==set(oracle)
    for k in present: assert present[k]==pytest.approx(oracle[k],rel=1e-12,abs=1e-12)


def test_failed_computation_is_durable_and_retries_without_rewriting_job_log(database):
    seed(database)
    query(database,"INSERT INTO job_log(job,period_key,status,detail) VALUES ('monthly','2020-09','ok','{}')")
    before=legacy_state(database)
    good=capture_month(database,'2020-09')
    bad=good.unpack();bad['surprises']=[]
    with pytest.raises(PublicationError,match='insufficient_factor_coverage_sue'):
        run_monthly_period(database,'2020-09',REVISION,capture=lambda *a:FrozenMonthlyInputs.freeze(bad))
    assert query(database,'SELECT count(*) FROM fl_dataset_generations')==[(0,)]
    assert query(database,'SELECT status FROM fl_monthly_outcomes')==[('failed',)]
    second=run_monthly_period(database,'2020-09',REVISION,capture=lambda *a:good)
    assert second['status']=='published'
    assert query(database,'SELECT status FROM fl_monthly_outcomes ORDER BY finished_at')==[('failed',),('published',)]
    assert legacy_state(database)==before


def test_same_code_completed_period_skips_vendor_refresh_and_recalculation(database):
    seed(database)
    first=run_monthly_period(database,'2020-09',REVISION)
    def must_not_run(*a): raise AssertionError('completed period reran a producer')
    second=run_monthly_period(database,'2020-09',REVISION,refresh=must_not_run,capture=must_not_run)
    assert second['status']=='reused' and second['generation_id']==first['generation_id']
    assert second['available_at']==first['available_at']
    assert query(database,'SELECT count(*) FROM fl_dataset_generations')==[(1,)]


def test_committed_generation_missing_receipt_is_reconciled_without_recompute(database):
    seed(database)
    snapshot=capture_month(database,'2020-09');spec,producer=prepare_publication(snapshot,REVISION)
    store=PublicationStore(database)
    with store.build(spec) as attempt: generation=attempt.write(*producer(spec))
    assert query(database,'SELECT count(*) FROM fl_publication_receipts')==[(0,)]
    def forbidden(*a): raise AssertionError('must reconcile instead of replay')
    result=run_monthly_period(database,'2020-09',REVISION,capture=forbidden,refresh=forbidden)
    assert result['generation_id']==str(generation) and result['status']=='reused'
    assert query(database,'SELECT count(*) FROM fl_publication_receipts')==[(1,)]


def test_monthly_lock_is_not_stolen_and_abandoned_attempt_is_preserved(database):
    seed(database)
    from factorlab.monthly_scheduler import MONTHLY_LOCK_NAMESPACE
    cx=database()
    try:
        with cx:
            with cx.cursor() as cur:
                cur.execute('SELECT pg_advisory_lock(%s,202009)',(MONTHLY_LOCK_NAMESPACE,))
                old=uuid4()
                cur.execute('INSERT INTO fl_monthly_attempts(attempt_id,period_key,source_revision) VALUES (%s,%s,%s)',
                    (str(old),'2020-09',REVISION))
        with pytest.raises(PublicationError,match='monthly_scope_busy'):
            run_monthly_period(database,'2020-09',REVISION)
        assert query(database,'SELECT count(*) FROM fl_monthly_outcomes')==[(0,)]
    finally: cx.close()
    run_monthly_period(database,'2020-09',REVISION)
    assert query(database,'SELECT status FROM fl_monthly_outcomes WHERE attempt_id=%s',(str(old),))==[('interrupted',)]


def test_read_snapshot_is_stable_across_concurrent_source_updates(database):
    seed(database)
    original=query(database,"SELECT mktcap FROM mktcap_m WHERE security_id=1")[0][0]
    class Cursor:
        def __init__(self,c): self.c=c
        def __getattr__(self,k): return getattr(self.c,k)
        def __enter__(self): self.c.__enter__();return self
        def __exit__(self,*a): return self.c.__exit__(*a)
        def execute(self,text,args=()):
            self.c.execute(text,args)
            if 'CROSS JOIN LATERAL' in text:
                query(database,"UPDATE mktcap_m SET mktcap=900000000 WHERE security_id=1")
    class Connection:
        def __init__(self): self.cx=database()
        def __getattr__(self,k): return getattr(self.cx,k)
        def cursor(self): return Cursor(self.cx.cursor())
    data=capture_month(Connection,'2020-09').unpack()
    assert float(next(r for r in data['caps'] if r['security_id']==1)['mktcap'])==float(original)
    assert query(database,"SELECT mktcap FROM mktcap_m WHERE security_id=1")[0][0]==900000000


def test_source_connection_rejects_writes_during_capture_and_closes(database):
    seed(database)
    opened=[]
    class Cursor:
        def __init__(self,c): self.c=c
        def __getattr__(self,k): return getattr(self.c,k)
        def __enter__(self): self.c.__enter__();return self
        def __exit__(self,*a): return self.c.__exit__(*a)
        def execute(self,text,args=()):
            if 'CROSS JOIN LATERAL' in text:
                self.c.execute("DELETE FROM prices_raw_d")
            self.c.execute(text,args)
    class Connection:
        def __init__(self): self.cx=database();opened.append(self.cx)
        def __getattr__(self,k): return getattr(self.cx,k)
        def cursor(self): return Cursor(self.cx.cursor())
    import psycopg2
    with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction): capture_month(Connection,'2020-09')
    assert opened[0].closed
    assert query(database,'SELECT count(*) FROM prices_raw_d')[0][0]>0


def test_benchmark_gap_fails_before_any_generation(database):
    seed(database); query(database,"DELETE FROM benchmarks_m WHERE asof='2020-08-31'")
    with pytest.raises(PublicationError,match='incomplete_benchmark_grid'):
        run_monthly_period(database,'2020-09',REVISION)
    assert query(database,'SELECT count(*) FROM fl_dataset_generations')==[(0,)]
    assert query(database,'SELECT status FROM fl_monthly_outcomes')==[('failed',)]


def test_no_wrong_month_fallback(database):
    seed(database)
    with pytest.raises(PublicationError,match='missing_monthly_price_calendar'):
        run_monthly_period(database,'2020-10',REVISION)


def test_late_real_producer_publication_cannot_satisfy_earlier_cutoff(database):
    seed(database); result=run_monthly_period(database,'2020-09',REVISION)
    store=PublicationStore(database)
    with pytest.raises(PublicationError,match='no_exact_date_publication_before_cutoff'):
        store.select('2020-08-31','2020-09-01T23:07:20+00:00')
    assert str(store.select('2020-08-31',datetime.now(timezone.utc))['generation_id'])==result['generation_id']


@pytest.mark.parametrize('table',['fl_monthly_attempts','fl_monthly_outcomes'])
@pytest.mark.parametrize('verb',['UPDATE','DELETE','TRUNCATE'])
def test_new_monthly_history_is_immutable(database,table,verb):
    seed(database);run_monthly_period(database,'2020-09',REVISION)
    text=({'UPDATE':'UPDATE '+table+' SET attempt_id=attempt_id','DELETE':'DELETE FROM '+table,'TRUNCATE':'TRUNCATE '+table})[verb]
    import psycopg2
    with pytest.raises(psycopg2.Error): query(database,text)


def test_migration_015_is_required_before_raw_refresh(database):
    query(database,'DROP TABLE fl_monthly_outcomes') # isolated fixture only
    touched=[]
    import psycopg2
    with pytest.raises(psycopg2.errors.UndefinedTable):
        run_monthly_period(database,'2020-09',REVISION,refresh=lambda:touched.append(True))
    assert not touched
