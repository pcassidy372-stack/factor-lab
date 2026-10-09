"""Real PostgreSQL transaction regressions. Requires an explicitly owned synthetic harness."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import threading
import pytest
from factorlab import benchmark_loader as b
from test_benchmark_loader import GRID,ROWS,MISSING,NOW

pytestmark = pytest.mark.postgres


@pytest.fixture
def target(record_property):
    from benchmark_database import benchmark_database
    with benchmark_database(record_property) as value:
        yield value


def make(target):
    connect=target['connect']; identity,grid,existing,_=b.read_state(connect,'2026-10')
    assert grid==GRID
    return b.make_plan('2026-10',grid,ROWS,existing,identity,NOW)


def cells(target):
    with target['connect']() as cx:
        with cx.cursor() as cur:
            cur.execute('SELECT asof,symbol,tr FROM public.benchmarks_m ORDER BY asof,symbol')
            return cur.fetchall()


def test_exact_calendar_99_100_weekends_and_legacy_independence(target):
    p=make(target); assert p['grid']==GRID and p['missing_keys']==MISSING
    # Fixture includes 99 witnesses on later November/February weekend dates, and unrelated legacy grid.
    from factorlab.monthly_snapshot import prior_month
    begin,end=prior_month('2026-10')
    with target['connect']() as cx:
        with cx.cursor() as cur:
            import ast
            from pathlib import Path
            source=Path(__file__).resolve().parents[1]/'factorlab/monthly_snapshot.py'
            sql=next(n.value for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.Constant) and isinstance(n.value,str) and 'SELECT max(d) AS d' in n.value)
            cur.execute(sql,(begin.replace(year=begin.year-1),end))
            assert [r[0].isoformat() for r in cur.fetchall()]==GRID


def test_atomic_missing_grid_identical_replay_and_legacy_preservation(target):
    before=cells(target); p=make(target)
    assert b.apply_plan(target['connect'],p)['inserted']==5
    assert b.apply_plan(target['connect'],p)['outcome']=='verified_noop'
    assert all(r in cells(target) for r in before)
    assert len(cells(target))==len(before)+5
    assert b.reconcile_plan(target['connect'],p)['outcome']=='exact_grid_present'


def test_differing_overlap_no_writes(target):
    p=make(target); p['values'][GRID[0]]='1000.1250000000000000001'
    p['plan_sha256']=b.digest({k:v for k,v in p.items() if k!='plan_sha256'})
    before=cells(target)
    with pytest.raises(b.BenchmarkError): b.apply_plan(target['connect'],p)
    assert cells(target)==before


class Wrapper:
    def __init__(self,cx,*,fail_insert=False,commit_mode=None): self.cx=cx; self.fail_insert=fail_insert; self.commit_mode=commit_mode
    def __getattr__(self,k): return getattr(self.cx,k)
    def cursor(self):
        base=self.cx.cursor(); outer=self
        class Cursor:
            def __enter__(self): base.__enter__(); return self
            def __exit__(self,*args): return base.__exit__(*args)
            def __getattr__(self,k): return getattr(base,k)
            def execute(self,q,p=None):
                result=base.execute(q,p)
                if outer.fail_insert and q.startswith('INSERT INTO public.benchmarks_m'): raise RuntimeError('injected after write')
                return result
        return Cursor()
    def commit(self):
        if self.commit_mode=='after': self.cx.commit()
        if self.commit_mode: raise RuntimeError('ack unavailable')
        return self.cx.commit()


def test_injected_failure_rolls_back_all_rows(target):
    before=cells(target); p=make(target)
    with pytest.raises(b.BenchmarkError,match='rolled_back'):
        b.apply_plan(lambda:Wrapper(target['connect'](),fail_insert=True),p)
    assert cells(target)==before


@pytest.mark.parametrize('mode,outcome',[('before','incomplete'),('after','exact_grid_present')])
def test_uncertain_commit_reconciles_fresh_without_retry(target,mode,outcome):
    p=make(target)
    with pytest.raises(b.CommitUncertain): b.apply_plan(lambda:Wrapper(target['connect'](),commit_mode=mode),p)
    assert b.reconcile_plan(target['connect'],p)['outcome']==outcome


def test_concurrent_conflicting_absent_keys_one_whole_grid(target):
    p=make(target); alternate=[dict(r,adjClose='2222.5') if r['date'] in MISSING else r for r in ROWS]
    q=b.make_plan('2026-10',GRID,alternate,p['expected_existing'],p['target'],NOW)
    barrier=threading.Barrier(2)
    def run(plan):
        barrier.wait(timeout=5)
        try: return b.apply_plan(target['connect'],plan)['outcome']
        except b.BenchmarkError: return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(run,[p,q]))
    assert sorted(results)==['committed','rejected']
    assert sorted(b.reconcile_plan(target['connect'],x)['outcome'] for x in [p,q])==['conflict','exact_grid_present']


def test_noncooperating_absent_key_conflict_rolls_back_plan(target):
    p=make(target)
    with target['connect']() as cx:
        with cx.cursor() as cur: cur.execute("INSERT INTO public.benchmarks_m VALUES (%s,'SPY',999)",(MISSING[0],))
    before=cells(target)
    with pytest.raises(b.BenchmarkError,match='vintage_conflict'): b.apply_plan(target['connect'],p)
    assert cells(target)==before


def test_target_drift_refused(target):
    p=make(target); p['target']['table_oid']+=1; p['plan_sha256']=b.digest({k:v for k,v in p.items() if k!='plan_sha256'})
    before=cells(target)
    with pytest.raises(b.BenchmarkError,match='target_changed'): b.apply_plan(target['connect'],p)
    assert cells(target)==before


def test_restricted_login_and_no_row_write_denials(target):
    with target['connect']() as cx:
        with cx.cursor() as cur:
            cur.execute('SELECT rolsuper,rolcreaterole,rolcreatedb,rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=current_user')
            assert cur.fetchone()==(False,False,False,False)
            cur.execute('SELECT count(*) FROM pg_catalog.pg_auth_members WHERE member=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)')
            assert cur.fetchone()[0]==0
            cur.execute('SHOW transaction_read_only'); assert cur.fetchone()[0]=='off'
    for query in ['UPDATE public.benchmarks_m SET tr=tr WHERE false','DELETE FROM public.benchmarks_m WHERE false','CREATE TABLE public.forbidden(x int)','INSERT INTO public.prices_raw_d(security_id,d,close,volume) VALUES (-1,current_date,1,1)']:
        cx=target['connect']()
        try:
            with pytest.raises(Exception) as ex:
                with cx.cursor() as cur: cur.execute(query)
            assert getattr(ex.value,'pgcode',None)=='42501'
        finally: cx.close()


def test_lock_wait_is_bounded(target):
    p=make(target); cx=target['connect']()
    try:
        with cx.cursor() as cur: cur.execute('SELECT pg_advisory_xact_lock(%s)',(b.LOCK_KEY,))
        with pytest.raises(b.BenchmarkError,match='rolled_back'): b.apply_plan(target['connect'],p)
    finally: cx.close()
    assert b.reconcile_plan(target['connect'],p)['outcome']=='incomplete'


def test_noncooperating_race_after_snapshot_is_atomic(target):
    p=make(target); before=cells(target); inserted=[]
    class Race(Wrapper):
        def cursor(self):
            base=super().cursor(); outer=self
            class Cursor:
                def __enter__(self): base.__enter__(); return self
                def __exit__(self,*args): return base.__exit__(*args)
                def __getattr__(self,k): return getattr(base,k)
                def execute(self,q,params=None):
                    if q.startswith('INSERT INTO public.benchmarks_m') and not inserted:
                        with target['connect']() as other:
                            with other.cursor() as cur:
                                cur.execute("INSERT INTO public.benchmarks_m VALUES (%s,'SPY',%s)",(MISSING[0],Decimal('1002.1250000000000000001')))
                        inserted.append(True)
                    return base.execute(q,params)
            return Cursor()
    with pytest.raises(b.BenchmarkError,match='rolled_back'):
        b.apply_plan(lambda:Race(target['connect']()),p)
    after=cells(target)
    assert len(after)==len(before)+1 and all(row in after for row in before)
    assert b.reconcile_plan(target['connect'],p)['outcome']=='conflict'


def test_numeric_precision_is_not_float_or_tolerance(target):
    p=make(target)
    rows=[dict(r,adjClose='1234.12345678901234567890123456789') if r['date'] in MISSING else r for r in ROWS]
    q=b.make_plan('2026-10',GRID,rows,p['expected_existing'],p['target'],NOW)
    assert b.apply_plan(target['connect'],q)['inserted']==5
    assert b.reconcile_plan(target['connect'],q)['outcome']=='exact_grid_present'
    assert all(value==Decimal('1234.12345678901234567890123456789') for d,symbol,value in cells(target) if str(d) in MISSING and symbol=='SPY')
