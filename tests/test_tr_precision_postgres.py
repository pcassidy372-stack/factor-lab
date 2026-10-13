"""Real synthetic PostgreSQL persistence and normally imported writer modules."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext, ROUND_DOWN
import importlib
import pytest
from psycopg2.extras import execute_values
from factorlab import tr_precision as tr
from factorlab import ingest
from tr_database import tr_database

D=Decimal
METHOD='tr-v2-window-priority'

@pytest.fixture
def target(record_property,monkeypatch):
    with tr_database(record_property) as value:
        monkeypatch.setattr(ingest,'conn',value['connect'])
        yield value


def query(target,sql,args=()):
    cx=target['connect']()
    try:
        with cx:
            with cx.cursor() as cur:
                cur.execute(sql,args)
                return cur.fetchall() if cur.description else None
    finally:cx.close()


def store(target,rows):
    cx=target['connect']()
    try:
        with cx:
            with cx.cursor() as cur:
                execute_values(cur,'INSERT INTO public.tr_index_d VALUES %s',rows)
    finally:cx.close()

@pytest.mark.parametrize('value',['0.0000004','1e-50','1e-300','1.2345678901234567890123456789012345678901234567890'])
def test_numeric_exact_commit_close_independent_read(target,value):
    state=tr.persist(value)
    store(target,[(1,'2026-01-01',state,METHOD)])
    assert query(target,'SELECT tr,method_version FROM tr_index_d WHERE security_id=1')==[(state,METHOD)]
    assert tr.persist(query(target,'SELECT tr FROM tr_index_d WHERE security_id=1')[0][0])==state


def test_seventy_real_restarts_and_small_gross(target):
    expected=tr.persist(100);a=date(2026,1,1)
    store(target,[(1,a,expected,METHOD)])
    for i in range(70):
        b=a+timedelta(days=1)
        persisted=query(target,'SELECT d,tr FROM tr_index_d WHERE security_id=1 ORDER BY d DESC LIMIT 1')[0]
        gross=tr.gross_return(13,7,1,'0.03')
        expected=tr.advance(expected,gross,seed_date=a,price_date=a,next_date=b)
        with localcontext() as ctx:
            ctx.prec=6;ctx.rounding=ROUND_DOWN
            actual=tr.advance(persisted[1],gross,seed_date=persisted[0],price_date=a,next_date=b)
        store(target,[(1,b,actual,METHOD)])
        assert query(target,'SELECT tr FROM tr_index_d WHERE security_id=1 AND d=%s',(b,))==[(expected,)]
        a=b
    assert 0<expected<D('0.000001')
    tiny=tr.advance(100,tr.gross_return('1e30',1),seed_date=a,price_date=a,next_date=a+timedelta(days=1))
    store(target,[(1,a+timedelta(days=1),tiny,METHOD)])
    assert query(target,'SELECT tr FROM tr_index_d WHERE security_id=1 ORDER BY d DESC LIMIT 1')==[(D('1e-28'),)]

@pytest.mark.parametrize('bad',['0','NaN','Infinity','-Infinity','-1','1e-400','1e400'])
def test_stored_unusable_seed_refused_without_new_rows(target,bad):
    store(target,[(1,'2026-01-01',D(bad),METHOD)])
    seed=query(target,'SELECT tr FROM tr_index_d WHERE security_id=1')[0][0]
    with pytest.raises(tr.TRInputError):
        state=tr.advance(seed,1,seed_date='2026-01-01',price_date='2026-01-01',next_date='2026-01-02')
        store(target,[(1,'2026-01-02',state,METHOD)])
    assert query(target,'SELECT count(*) FROM tr_index_d WHERE security_id=1')==[(1,)]


def test_invalid_date_terminal_and_input_no_persistence(target):
    for seed in [None,True,'invalid']:
        with pytest.raises(tr.TRInputError):tr.persist(seed)
    with pytest.raises(tr.TRInputError):tr.advance(1,1,seed_date='2026-01-01',price_date='2026-01-02',next_date='2026-01-03')
    with pytest.raises(tr.TerminalTreatmentRequired):tr.gross_return(10,0)
    assert query(target,'SELECT count(*) FROM tr_index_d WHERE security_id=1')==[(0,)]

class SyntheticProvider:
    def __init__(self,prices,oracle=None,dividends=None,splits=None):
        self.prices=prices;self.oracle=oracle or {};self.dividends=dividends or [];self.splits=splits or []
    def get(self,logical,**kw):
        if logical in ('prices_unadjusted','prices_div_adjusted'):
            values=self.prices if logical=='prices_unadjusted' else self.oracle
            return [dict(date=d,adjClose=v,volume=100) for d,v in values.items() if kw.get('date_from','0000')<=d<=kw.get('date_to','9999')]
        if logical in ('dividends','dividends_calendar'):return self.dividends
        if logical in ('splits','splits_calendar'):return self.splits
        if logical=='mktcap_hist':return []
        raise AssertionError('Unexpected synthetic endpoint')


def run_writer(target,kind,provider):
    module=importlib.import_module('scripts.prices_'+kind)
    db=ingest.RDB()
    try:
        if kind=='backfill':module.process_security(db,provider,1,['SYNTHETIC'],'2026-01-01','2026-01-04')
        else:module.repair_one(provider,db,1,[('SYNTHETIC','2026-01-01','2026-01-04')],'2026-01-01','2026-01-04')
        assert db._cx.autocommit is True
    finally:db.close()

@pytest.mark.parametrize('kind',['backfill','repair'])
@pytest.mark.parametrize('case',['tiny','ordinary-actions','oracle','threshold-return','threshold-gap','split-no-oracle'])
def test_real_backfill_repair(target,kind,case):
    p={'2026-01-01':100,'2026-01-02':400};o={};div=[];spl=[];expected=[D(100),D(400)]
    if case=='tiny':p={'2026-01-01':1e12,'2026-01-02':1};expected=[D(100),D('1e-10')]
    if case=='ordinary-actions':
        p={'2026-01-01':10,'2026-01-02':11,'2026-01-03':D('5.5'),'2026-01-04':D('4.5')}
        spl=[dict(date='2026-01-03',numerator=2,denominator=1)];div=[dict(date='2026-01-04',dividend=1)]
        expected=[D(100),D(110),D(110),D(110)]
    if case=='oracle':o={'2026-01-01':100,'2026-01-02':110};expected=[D(100),D(110)]
    if case=='threshold-return':p['2026-01-02']=300;o={'2026-01-01':100,'2026-01-02':100};expected=[D(100),D(300)]
    if case=='threshold-gap':o={'2026-01-01':100,'2026-01-02':250}
    if case=='split-no-oracle':o={'2026-01-01':100,'2026-01-02':100};spl=[dict(date='2026-01-02',numerator=1,denominator=1)]
    provider=SyntheticProvider(p,o,div,spl)
    run_writer(target,kind,provider)
    label='tr-v1-close-div-split' if kind=='backfill' else METHOD
    assert query(target,'SELECT tr,method_version FROM tr_index_d WHERE security_id=1 ORDER BY d')==[(v,label) for v in expected]
    run_writer(target,kind,provider)
    assert query(target,'SELECT tr FROM tr_index_d WHERE security_id=1 ORDER BY d')==[(v,) for v in expected]
    assert query(target,'SELECT tr FROM tr_index_d WHERE security_id=2')==[(D(42),)]
    assert query(target,'SELECT close FROM prices_raw_d WHERE security_id=2')==[(D(42),)]


def daily(target,monkeypatch,until,provider):
    module=importlib.import_module('scripts.incremental')
    monkeypatch.setattr(module,'TODAY',until)
    monkeypatch.setattr(module,'NOW',datetime.combine(date.fromisoformat(until),datetime.min.time(),timezone.utc))
    monkeypatch.setattr(module,'FMPClient',lambda **kw:provider)
    db=ingest.RDB()
    try:return module.job_daily(db)
    finally:db.close()

@pytest.mark.parametrize('case',['valid','missing','misaligned','zero'])
def test_real_daily_scope_restart_and_rejection(target,monkeypatch,case):
    query(target,"INSERT INTO prices_raw_d(security_id,d,close,volume) VALUES (1,'2026-01-01',10,100)")
    if case!='missing':store(target,[(1,'2025-12-31' if case=='misaligned' else '2026-01-01',D(0) if case=='zero' else D('0.0000004'),METHOD)])
    provider=SyntheticProvider({'2026-01-02':11,'2026-01-03':12})
    result=daily(target,monkeypatch,'2026-01-02',provider)
    if case=='valid':
        assert result['prices']=={'ok':1}
        assert query(target,"SELECT tr FROM tr_index_d WHERE security_id=1 AND d='2026-01-02'")==[(D('0.00000044'),)]
        assert daily(target,monkeypatch,'2026-01-03',provider)['prices']=={'ok':1}
        assert query(target,"SELECT tr FROM tr_index_d WHERE security_id=1 AND d='2026-01-03'")==[(D('0.00000048'),)]
    else:
        assert result['prices']['error']==1 and result['prices']['error_TRInputError']==1
        assert query(target,"SELECT count(*) FROM prices_raw_d WHERE security_id=1 AND d>'2026-01-01'")==[(0,)]
        assert query(target,"SELECT count(*) FROM tr_index_d WHERE security_id=1 AND d>'2026-01-01'")==[(0,)]
    assert query(target,'SELECT tr FROM tr_index_d WHERE security_id=2')==[(D(42),)]


def test_real_autocommit_partial_failure_not_atomic(target,record_property):
    target['setup']("""CREATE FUNCTION public.reject_tr() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic failure' USING ERRCODE='23514'; END $$;
        CREATE TRIGGER synthetic_reject BEFORE INSERT ON tr_index_d FOR EACH ROW EXECUTE FUNCTION public.reject_tr()""")
    import psycopg2
    with pytest.raises(psycopg2.IntegrityError):
        run_writer(target,'repair',SyntheticProvider({'2026-01-01':10,'2026-01-02':11}))
    assert query(target,'SELECT count(*) FROM prices_raw_d WHERE security_id=1')==[(2,)]
    assert query(target,'SELECT count(*) FROM tr_index_d WHERE security_id=1')==[(0,)]
    assert query(target,'SELECT tr FROM tr_index_d WHERE security_id=2')==[(D(42),)]
    record_property('real_statement_boundary','repair DELETEs and price INSERT committed before failing TR INSERT; not atomic')


def test_real_rdb_operational_replay(target,record_property):
    target['setup']("""CREATE SEQUENCE public.synthetic_retry;
      GRANT USAGE,SELECT ON SEQUENCE public.synthetic_retry TO PUBLIC;
      CREATE FUNCTION public.synthetic_disconnect() RETURNS void LANGUAGE plpgsql AS $$ BEGIN
      IF nextval('public.synthetic_retry')=1 THEN RAISE EXCEPTION 'synthetic operational failure' USING ERRCODE='08006'; END IF; END $$""")
    db=ingest.RDB()
    def unit(cur):
        cur.execute("INSERT INTO tr_index_d VALUES (1,'2026-01-01',0.0000004,'tr-v2-window-priority') ON CONFLICT DO NOTHING")
        cur.execute('SELECT public.synthetic_disconnect()')
    try:db.safe(unit)
    finally:db.close()
    assert query(target,'SELECT tr FROM tr_index_d WHERE security_id=1')==[(D('0.0000004'),)]
    assert query(target,'SELECT last_value FROM public.synthetic_retry')==[(2,)]
    record_property('retry_scope','real PostgreSQL 08006 exception, fresh RDB connection and replay; not a physical network/commit-ack loss')

@pytest.mark.parametrize('method',['tr-v1-close-div-split','tr-v2-window-priority'])
def test_unchanged_monthly_consumer_with_stored_tiny_levels(target,method):
    from monthly_samples import monthly_sample
    from factorlab.monthly_producer import plan_universe,calculate_factors
    payload=monthly_sample(100)
    rows=[(r['security_id'],r['d'],tr.persist(D(str(r['tr']))*D('1e-100')),method) for r in payload['returns']]
    store(target,rows)
    got=query(target,"SELECT security_id,d,tr,method_version FROM tr_index_d WHERE d>='2019-01-01' ORDER BY security_id,d")
    payload['returns']=[dict(security_id=s,d=d.isoformat(),tr=v,method_version=m) for s,d,v,m in got]
    universe=plan_universe(payload)
    result,coverage=calculate_factors(payload,universe)
    assert len(result)==500
    assert coverage['mom_12_1']=={'eligible':100,'raw_present':100}
    assert coverage['vol_12m']=={'eligible':100,'raw_present':100}
    payload['returns'][0]['method_version']='unknown'
    with pytest.raises(Exception,match='unknown_total_return_method'):calculate_factors(payload,universe)
