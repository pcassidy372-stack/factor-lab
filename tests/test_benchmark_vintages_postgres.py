from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from factorlab import benchmark_vintages as v
from factorlab.publication_contract import PublicationError
from vintage_samples import candidate,install
from test_benchmark_loader import GRID
from test_publication_postgres import database,query
pytestmark=pytest.mark.postgres

def imported(db):
    d,p=candidate(GRID,'2026-10');vid=str(uuid4());t=v.inspect_target(db,allowed_roles=list(db.roles.values()))
    args=(vid,d,p,v.digest(d),v.digest(p),t)
    v.import_vintage(db.runtime('importer'),*args);return args

def test_import_without_selection_replay_and_permissions(database):
    args=imported(database);assert query(database,'SELECT count(*) FROM benchmark_vintage_values')==[(13,)]
    assert query(database,'SELECT count(*) FROM benchmark_vintage_selections')==[(0,)]
    assert v.import_vintage(database.runtime('importer'),*args)['status']=='verified_replay'
    assert v.reconcile_import(database.runtime('reader'),*args)['status']=='complete_committed_import'
    d,p=candidate(GRID,'2026-10',[200+i for i in range(13)])
    with pytest.raises(PublicationError,match='operation_content_conflict'):v.import_vintage(database.runtime('importer'),args[0],d,p,v.digest(d),v.digest(p),args[-1])

@pytest.mark.parametrize('table',['benchmark_vintages','benchmark_vintage_values','benchmark_vintage_selections'])
@pytest.mark.parametrize('verb',['UPDATE','DELETE','TRUNCATE'])
def test_direct_sql_immutable_even_owner(database,table,verb):
    imported(database)
    field='vintage_id' if table!='benchmark_vintage_selections' else 'event_id'
    q={'UPDATE':f'UPDATE {table} SET {field}={field}','DELETE':f'DELETE FROM {table}','TRUNCATE':f'TRUNCATE {table} CASCADE'}[verb]
    import psycopg2
    with pytest.raises(psycopg2.errors.CheckViolation):query(database,q)

@pytest.mark.parametrize('role',['importer','selector','reader','publisher'])
def test_restricted_no_row_write_and_ddl(database,role):
    import psycopg2
    cx=database.runtime(role)()
    try:
        for q in ['DELETE FROM public.benchmarks_m WHERE false','UPDATE public.benchmark_vintages SET period=period WHERE false','CREATE TABLE public.forbidden(x int)']:
            with pytest.raises(psycopg2.errors.InsufficientPrivilege) as error:
                with cx:
                    with cx.cursor() as c:c.execute(q)
            assert error.value.pgcode=='42501'
    finally:cx.close()

def test_incomplete_import_rollback_and_late_insert(database):
    args=imported(database);import psycopg2
    with pytest.raises(psycopg2.errors.CheckViolation):query(database,"INSERT INTO benchmark_vintage_values VALUES(%s,'2026-09-29','SPY',112)",(args[0],))
    cx=database()
    try:
        with pytest.raises(psycopg2.errors.CheckViolation):
            with cx:
                with cx.cursor() as c:
                    c.execute('INSERT INTO benchmark_vintages SELECT %s,period,document,payload,document_sha256,payload_sha256,grid,levels,source_start,source_end,imported_at,import_xid FROM benchmark_vintages WHERE vintage_id=%s',(str(uuid4()),args[0]))
        assert query(database,'SELECT count(*) FROM benchmark_vintages')==[(1,)]
    finally:cx.close()

def test_initial_and_later_selection_races_and_reconcile(database):
    args=imported(database);vid,t=args[0],args[-1]
    def run(pred):
        event=str(uuid4())
        try:v.select_vintage(database.runtime('selector'),event,'2026-10',vid,pred,'synthetic',t);return event
        except __import__('psycopg2').Error as exc:
            assert exc.pgcode in ('23505','40001','55P03')
            return None
    with ThreadPoolExecutor(2) as pool:r=list(pool.map(run,[None,None]))
    assert sum(x is not None for x in r)==1;first=next(x for x in r if x)
    with ThreadPoolExecutor(2) as pool:r=list(pool.map(run,[first,first]))
    assert sum(x is not None for x in r)==1
    assert query(database,'SELECT count(*) FROM benchmark_vintage_selections')==[(2,)]
    assert v.reconcile_selection(database.runtime('reader'),first,'2026-10',vid,None,'synthetic',t)['status']=='complete_committed_selection'
    with pytest.raises(PublicationError):v.select_vintage(database.runtime('selector'),first,'2026-10',vid,None,'changed',t)

def test_own_transaction_import_cannot_select(database):
    args=imported(database);import psycopg2
    new=str(uuid4());cx=database()
    try:
        with pytest.raises(psycopg2.errors.CheckViolation):
            with cx:
                with cx.cursor() as q:
                    q.execute('INSERT INTO benchmark_vintages SELECT %s,period,document,payload,document_sha256,payload_sha256,grid,levels,source_start,source_end,imported_at,import_xid FROM benchmark_vintages WHERE vintage_id=%s',(new,args[0]))
                    q.execute('INSERT INTO benchmark_vintage_values SELECT %s,asof,symbol,tr FROM benchmark_vintage_values WHERE vintage_id=%s',(new,args[0]))
                    q.execute("INSERT INTO benchmark_vintage_selections(event_id,period,vintage_id,approval_reference) VALUES(%s,'2026-10',%s,'synthetic')",(str(uuid4()),new))
    finally:cx.close()

def test_same_code_different_event_refused_before_confirmation(database):
    from test_monthly_producer_postgres import seed
    from factorlab.monthly_scheduler import run_monthly_period
    seed(database);t=v.inspect_target(database,allowed_roles=list(database.roles.values()));old=database.selection_event
    run_monthly_period(database.runtime('publisher'),'2020-09','a'*40,selection_event=old,vintage_id=database.vintage_id)
    new=str(uuid4());v.select_vintage(database.runtime('selector'),new,'2020-09',database.vintage_id,old,'new synthetic approval',t)
    before=query(database,'SELECT count(*) FROM fl_publication_receipts')
    with pytest.raises(PublicationError,match='existing_generation_benchmark_identity_mismatch'):
        run_monthly_period(database.runtime('publisher'),'2020-09','a'*40,selection_event=new,vintage_id=database.vintage_id)
    assert query(database,'SELECT count(*) FROM fl_publication_receipts')==before
    assert query(database,'SELECT count(*) FROM fl_dataset_generations')==[(1,)]

def test_uncertain_import_reconciles_without_retry(database):
    d,p=candidate(GRID,'2026-10');args=(str(uuid4()),d,p,v.digest(d),v.digest(p),v.inspect_target(database,allowed_roles=list(database.roles.values())))
    class Lost:
        def __init__(self):self.cx=database.runtime('importer')()
        def __getattr__(self,k):return getattr(self.cx,k)
        def commit(self):self.cx.commit();raise OSError('synthetic lost acknowledgement')
    with pytest.raises(v.Uncertain):v.import_vintage(Lost,*args)
    assert v.reconcile_import(database.runtime('reader'),*args)['status']=='complete_committed_import'

@pytest.mark.parametrize('fault',['wrong_symbol','wrong_value','wrong_period','duplicate_json'])
def test_database_rejects_direct_header_tampering(database,fault):
    import psycopg2,json
    d,p=candidate(GRID,'2026-10');obj=json.loads(d);raw=json.loads(p);period='2026-10';levels=[str(100+i) for i in range(13)]
    if fault=='wrong_symbol':raw[0]['symbol']='QQQ';p=v.canonical(raw);obj['provenance']['response_sha256']=v.digest(p);d=v.canonical(obj)
    if fault=='wrong_value':levels[0]='999'
    if fault=='wrong_period':period='2026-11'
    if fault=='duplicate_json':d=d.replace(b'"field":"adjClose"',b'"field":"adjClose","field":"adjClose"')
    cx=database()
    try:
        with pytest.raises(psycopg2.Error):
            with cx:
                with cx.cursor() as q:q.execute('''INSERT INTO benchmark_vintages(vintage_id,period,document,payload,document_sha256,payload_sha256,grid,levels,source_start,source_end)
                VALUES(%s,%s,%s,%s,%s,%s,%s::date[],%s::numeric[],%s,%s)''',(str(uuid4()),period,d,p,v.digest(d),v.digest(p),GRID,levels,obj['provenance']['retrieval_start'],obj['provenance']['retrieval_end']))
        assert query(database,'SELECT count(*) FROM benchmark_vintages')==[(0,)]
    finally:cx.close()

@pytest.mark.parametrize('change',['returns','surprises'])
def test_gates_not_bypassed_by_complete_vintage(database,change):
    from test_monthly_producer_postgres import seed
    from factorlab.monthly_scheduler import run_monthly_period
    seed(database)
    query(database, 'UPDATE tr_index_d SET tr=0 WHERE security_id=1' if change=='returns' else 'DELETE FROM surprises')
    with pytest.raises(PublicationError,match='invalid_return_grid_value|insufficient_factor_coverage_sue'):
        run_monthly_period(database,'2020-09','a'*40,selection_event=database.selection_event,vintage_id=database.vintage_id)
    assert query(database,'SELECT count(*) FROM fl_dataset_generations')==[(0,)]

def test_existing_legacy_generation_refused(database):
    from test_monthly_producer_postgres import seed
    from monthly_samples import monthly_sample
    from factorlab.monthly_producer import FrozenMonthlyInputs,prepare_publication
    from factorlab.publications import PublicationStore
    from factorlab.monthly_scheduler import run_monthly_period
    seed(database);p=monthly_sample()
    from datetime import datetime,timezone
    p['observed_at']=datetime.now(timezone.utc).isoformat()
    spec,producer=prepare_publication(FrozenMonthlyInputs.freeze(p),'a'*40)
    PublicationStore(database).publish(spec,producer)
    with pytest.raises(PublicationError,match='existing_generation_benchmark_identity_mismatch'):
        run_monthly_period(database,'2020-09','a'*40,selection_event=database.selection_event,vintage_id=database.vintage_id)
    assert query(database,'SELECT count(*) FROM fl_dataset_generations')==[(1,)]

def test_pinned_selection_stable_during_new_selection_and_raw_update(database):
    from test_monthly_producer_postgres import seed
    from factorlab.monthly_snapshot import capture_month
    seed(database);old=database.selection_event;vid=database.vintage_id
    original=query(database,'SELECT mktcap FROM mktcap_m WHERE security_id=1')[0][0]
    class Cursor:
        def __init__(self,c):self.c=c
        def __getattr__(self,k):return getattr(self.c,k)
        def __enter__(self):self.c.__enter__();return self
        def __exit__(self,*a):return self.c.__exit__(*a)
        def execute(self,text,args=()):
            self.c.execute(text,args)
            if 'CROSS JOIN LATERAL' in text:
                v.select_vintage(database.runtime('selector'),str(uuid4()),'2020-09',vid,old,'concurrent synthetic',v.inspect_target(database,allowed_roles=list(database.roles.values())))
                query(database,'UPDATE mktcap_m SET mktcap=999999999 WHERE security_id=1')
    class Connection:
        def __init__(self):self.cx=database.runtime('reader')()
        def __getattr__(self,k):return getattr(self.cx,k)
        def cursor(self):return Cursor(self.cx.cursor())
    p=capture_month(Connection,'2020-09',selection_event=old,vintage_id=vid).unpack()
    assert p['benchmark_identity']['selection_event_id']==old
    assert float(next(r['mktcap'] for r in p['caps'] if r['security_id']==1))==float(original)


def test_uncertain_selection_and_stale_cross_period(database):
    import psycopg2
    args=imported(database);eid=str(uuid4());select=(eid,'2026-10',args[0],None,'synthetic',args[-1])
    class Lost:
        def __init__(self):self.cx=database.runtime('selector')()
        def __getattr__(self,k):return getattr(self.cx,k)
        def commit(self):self.cx.commit();raise OSError('synthetic lost selection acknowledgement')
    with pytest.raises(v.Uncertain):v.select_vintage(Lost,*select)
    assert v.reconcile_selection(database.runtime('reader'),*select)['status']=='complete_committed_selection'
    with pytest.raises(psycopg2.Error):v.select_vintage(database.runtime('selector'),str(uuid4()),'2026-11',args[0],eid,'synthetic',args[-1])
    with pytest.raises(psycopg2.Error):v.select_vintage(database.runtime('selector'),str(uuid4()),'2026-10',args[0],None,'synthetic',args[-1])

def test_original_table_rows_and_sequences_preserved_by_import_selection(database):
    tables=query(database,"SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT LIKE 'benchmark_vintage%%' ORDER BY tablename")
    def state():
        return {t:query(database,'SELECT md5(COALESCE(string_agg(row_to_json(r)::text,\'|\' ORDER BY row_to_json(r)::text),\'\')) FROM public.'+t+' r')[0][0] for (t,) in tables}
    before=state();seq=query(database,"SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")
    args=imported(database);v.select_vintage(database.runtime('selector'),str(uuid4()),'2026-10',args[0],None,'synthetic',args[-1])
    assert state()==before and query(database,"SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")==seq

def test_missing_approval_or_selection_refused(database):
    args=imported(database)
    with pytest.raises(PublicationError,match='approval_reference_required'):v.select_vintage(database.runtime('selector'),str(uuid4()),'2026-10',args[0],None,' ',args[-1])
    cx=database.runtime('reader')()
    try:
        cx.set_session(readonly=True,isolation_level='REPEATABLE READ')
        with cx.cursor() as q:
            with pytest.raises(PublicationError,match='explicit_selection_not_found'):v.read_selection(q,'2026-10',str(uuid4()),args[0])
    finally:cx.close()

def test_arbitrary_array_bounds_rejected(database):
    args=imported(database);import psycopg2
    with pytest.raises(psycopg2.Error):
        query(database,"""INSERT INTO benchmark_vintages
        SELECT %s,period,document,payload,document_sha256,payload_sha256,
        array_fill(grid[1],ARRAY[13],ARRAY[0]),levels,source_start,source_end,imported_at,import_xid
        FROM benchmark_vintages WHERE vintage_id=%s""",(str(uuid4()),args[0]))
    assert query(database,'SELECT count(*) FROM benchmark_vintages')==[(1,)]
