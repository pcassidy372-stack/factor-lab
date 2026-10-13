"""R1 actual PostgreSQL representations; R2 modeled equal catalogs/different routes."""
from types import SimpleNamespace
import pytest
from factorlab import benchmark_vintages as v
from factorlab.monthly_producer import fingerprint
from test_publication_postgres import database
from vintage_samples import install
from test_benchmark_loader import GRID

def zoned(factory,zone):
    def connect():
        cx=factory()
        with cx.cursor() as q:q.execute('SET TIME ZONE %s',(zone,))
        cx.commit();return cx
    return connect

def selected(factory,event,vid):
    cx=factory()
    try:
        cx.set_session(readonly=True,isolation_level='REPEATABLE READ')
        with cx.cursor() as q:
            q.execute('SELECT current_setting(\'TimeZone\'),imported_at FROM public.benchmark_vintages WHERE vintage_id=%s',(vid,));zone,stamp=q.fetchone()
            return v.read_selection(q,'2026-10',event,vid),zone,stamp.utcoffset()
    finally:cx.close()

def test_r1_real_postgres_timezone_identity(database):
    event,vid=install(database,GRID,'2026-10')
    utc,z1,o1=selected(zoned(database.runtime('reader'),'UTC'),event,vid)
    ny,z2,o2=selected(zoned(database.runtime('reader'),'America/New_York'),event,vid)
    assert (z1,z2)==('UTC','America/New_York') and o1!=o2
    assert utc==ny
    assert fingerprint(utc)==fingerprint(ny)

class ModeledCursor:
    """No database executes here; equal local OIDs are intentionally modeled."""
    def __init__(self,host):
        params=dict(host=host,port='5432',dbname='factorlab_ci',user='reader',sslmode='disable')
        self.connection=SimpleNamespace(info=SimpleNamespace(dsn_parameters=params,ssl_in_use=False,ssl_attribute_names=[],host=host,port=5432,dbname="factorlab_ci",user="reader"))
    def execute(self,q,args=()):self.query=q
    def fetchone(self):
        if 'aclexplode' in self.query:return (0,)
        if 'count(*)' in self.query:return (3,)
        if 'session_user' in self.query:return ('reader','reader')
        if 'inet_server_addr' in self.query:return ('127.0.0.1',5432)
        return ('factorlab_ci',16384,16400)
    def fetchall(self):return [(n,False,['search_path=pg_catalog, public, pg_temp']) for n in ['bv_immutable','bv_json_unique','bv_header','bv_value','bv_complete','bv_select']]

def test_r2_modeled_equal_oids_different_routes():
    assert v.target(ModeledCursor('127.0.0.1'))!=v.target(ModeledCursor('127.0.0.2'))

@pytest.mark.parametrize('value',[None,'invalid','2026-01-01T00:00:00'])
def test_identity_timestamp_invalid_or_naive_rejected(value):
    with pytest.raises((ValueError,TypeError)):v.canonical_observation(value)

def test_identity_timestamp_precision_and_instant_sensitivity():
    from datetime import datetime,timezone,timedelta
    a=datetime(2026,10,1,12,0,0,123456,tzinfo=timezone.utc)
    b=a.astimezone(timezone(timedelta(hours=-4)))
    assert v.canonical_observation(a)==v.canonical_observation(b)=='2026-10-01T12:00:00.123456+00:00'
    assert fingerprint(v.canonical_observation(a))!=fingerprint(v.canonical_observation(a+timedelta(microseconds=1)))

@pytest.mark.parametrize('lost_receipt',[False,True])
def test_cross_timezone_collection_publication_and_reuse(database,lost_receipt):
    from test_monthly_producer_postgres import seed
    from test_publication_postgres import query
    from factorlab.monthly_snapshot import capture_month
    from factorlab.monthly_producer import prepare_publication
    from factorlab.monthly_scheduler import run_monthly_period
    from factorlab.publications import PublicationStore
    from factorlab.publication_contract import PublicationError
    from uuid import uuid4
    seed(database)
    args=dict(selection_event=database.selection_event,vintage_id=database.vintage_id)
    utc=capture_month(zoned(database.runtime('reader'),'UTC'),'2020-09',**args)
    ny=capture_month(zoned(database.runtime('reader'),'America/New_York'),'2020-09',**args)
    assert utc.unpack()['benchmark_identity']==ny.unpack()['benchmark_identity']
    spec,producer=prepare_publication(utc,'a'*40)
    store=PublicationStore(zoned(database.runtime('publisher'),'UTC'))
    if lost_receipt:
        with store.build(spec) as attempt:generation=str(attempt.write(*producer(spec)))
        assert query(database,'SELECT count(*) FROM fl_publication_receipts')==[(0,)]
    else:generation=str(store.publish(spec,producer)['generation_id'])
    def forbidden(*a,**kw):raise AssertionError('reuse must not refresh or capture')
    result=run_monthly_period(zoned(database.runtime('publisher'),'America/New_York'),'2020-09','a'*40,**args,refresh=forbidden,capture=forbidden)
    assert result['status']=='reused' and result['generation_id']==generation
    assert query(database,'SELECT count(*) FROM fl_dataset_generations')==[(1,)]
    assert query(database,'SELECT count(*) FROM fl_publication_receipts')==[(1,)]
    old=args['selection_event'];event=str(uuid4())
    t=v.inspect_target(database.runtime('reader'),allowed_roles=[database.roles['selector']])
    v.select_vintage(database.runtime('selector'),event,'2020-09',database.vintage_id,old,'different synthetic approval',t)
    with pytest.raises(PublicationError,match='existing_generation_benchmark_identity_mismatch'):
        run_monthly_period(zoned(database.runtime('publisher'),'UTC'),'2020-09','a'*40,selection_event=event,vintage_id=database.vintage_id,refresh=forbidden,capture=forbidden)
    assert query(database,'SELECT count(*) FROM fl_dataset_generations')==[(1,)]
    assert query(database,'SELECT count(*) FROM fl_publication_receipts')==[(1,)]


def test_modeled_collision_refused_before_any_write():
    from factorlab.publication_contract import PublicationError
    q=ModeledCursor('127.0.0.1');expected=v.target(q);role=expected.pop('authenticated_role')
    expected.update(planned_by=role,allowed_roles=[role])
    v.require_target(q,expected)
    with pytest.raises(PublicationError,match='target_changed'):
        v.require_target(ModeledCursor('127.0.0.2'),expected)
