"""Offline SYNTHETIC prices; never recovered SPY observations."""
from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import pytest
from factorlab import benchmark_loader as b
from factorlab.monthly_snapshot import prior_month

GRID = ['2025-09-30','2025-10-31','2025-11-28','2025-12-31','2026-01-30',
        '2026-02-27','2026-03-31','2026-04-30','2026-05-29','2026-06-30',
        '2026-07-31','2026-08-31','2026-09-30']
MISSING = ['2025-11-28','2026-05-29','2026-07-31','2026-08-31','2026-09-30']
ROWS = [{'date':d,'symbol':'SPY','adjClose':str(1000+i)+'.125'} for i,d in enumerate(GRID)]
NOW = '2026-10-02T00:00:00+00:00'


def plan():
    return b.make_plan('2026-10',GRID,ROWS,{r['date']:r['adjClose'] for r in ROWS if r['date'] not in MISSING},{'synthetic':True},NOW)


def test_recorded_grid_plan_is_deterministic_and_bounded():
    p=plan(); assert p['missing_keys']==MISSING and len(p['matching_overlaps'])==8
    assert p['request_scope']['date_to']=='2026-09-30'
    assert p['request_scope']['date_from']=='2025-09-30'
    assert p==plan(); b.check_plan(p,date(2026,10,2))


@pytest.mark.parametrize('period',['2026-9','oops','2026-13','0000-01','2026-11'])
def test_bad_or_unfinished_period(period):
    with pytest.raises(b.BenchmarkError): b.month_bounds(period,date(2026,10,2))


@pytest.mark.parametrize('period',['2026-10','2026-01','2024-03'])
def test_calendar_bounds_agree_with_frozen_collector(period):
    begin,end=prior_month(period); start,got=b.month_bounds(period,date(2026,10,2))
    assert got==end and start==date(begin.year-1,begin.month,1)


@pytest.mark.parametrize('grid',[GRID[:-1],GRID[:-1]+[GRID[-2]],list(reversed(GRID)),GRID[:-1]+['2026-10-01'],GRID[:5]+['2026-01-31']+GRID[6:]])
def test_invalid_calendar_shapes(grid):
    with pytest.raises(b.BenchmarkError): b.validate_grid('2026-10',grid,date(2026,10,2))


@pytest.mark.parametrize('value',[None,True,False,0,-1,'NaN','Infinity','-Infinity',float('nan'),'bad',{},[]])
def test_invalid_prices(value):
    rows=[dict(r) for r in ROWS]; rows[-1]['adjClose']=value
    with pytest.raises(b.BenchmarkError): b.validate_response(rows,GRID)


@pytest.mark.parametrize('rows',[[],{}, {'historical':ROWS}, [{'error':'https://secret:password@invalid/?apikey=SECRET'}], ROWS[:-1]])
def test_incomplete_unknown_or_error_response(rows):
    with pytest.raises(b.BenchmarkError) as exc: b.validate_response(rows,GRID)
    assert 'SECRET' not in str(exc.value) and 'password' not in str(exc.value)


@pytest.mark.parametrize('change',[{'date':'2026-09-31'},{'date':'2026-9-30'},{'symbol':'QQQ'},{'adjClose':None}])
def test_invalid_row(change):
    rows=[dict(r) for r in ROWS]; rows[-1].update(change)
    with pytest.raises(b.BenchmarkError): b.validate_response(rows,GRID)


def test_no_prior_price_carry_forward():
    rows=ROWS[:-1]+[{'date':'2026-09-29','adjClose':'999'}]
    with pytest.raises(b.BenchmarkError,match='missing_exact'): b.validate_response(rows,GRID)


def test_duplicate_policy_and_non_grid_rows():
    assert b.validate_response(ROWS+[dict(ROWS[0],adjClose=Decimal('1000.1250'))],GRID)==b.validate_response(ROWS,GRID)
    assert b.validate_response(ROWS+[{'date':'2026-09-29','adjClose':'999'}],GRID)['2026-09-29']=='999'
    with pytest.raises(b.BenchmarkError,match='conflicting_provider_duplicate'):
        b.validate_response(ROWS+[dict(ROWS[0],adjClose='1000.126')],GRID)


def test_provider_request_and_secret_safe_error():
    class Client:
        def get(self,logical,**kw):
            assert logical=='prices_div_adjusted' and kw==dict(symbol='SPY',date_from=GRID[0],date_to=GRID[-1],allow_empty=False)
            return ROWS
    assert len(b.fetch_values(Client(),GRID))==13
    class Bad:
        def get(self,*a,**kw): raise RuntimeError('postgres://password@host https://x?apikey=SECRET')
    with pytest.raises(b.BenchmarkError,match='^provider_request_failed$'): b.fetch_values(Bad(),GRID)


def test_conflict_plan_and_tampering():
    p=plan(); p['values'][GRID[0]]='999'
    with pytest.raises(b.BenchmarkError,match='plan_hash'): b.check_plan(p,date(2026,10,2))
    p=b.make_plan('2026-10',GRID,ROWS,{GRID[0]:'999'},{},NOW)
    assert p['conflicting_overlaps']==[GRID[0]]
    with pytest.raises(b.BenchmarkError,match='vintage_conflict'): b.check_plan(p,date(2026,10,2))


def script():
    path=Path(__file__).resolve().parents[1]/'scripts/benchmark_load.py'
    spec=importlib.util.spec_from_file_location('benchmark_cli_test',path)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize('args',[[],['--help'],['plan'],['apply'],['plan','--period','bad','--output','unused','--connection-env','EXPLICIT'],['plan','--period','9999-01','--output','unused','--connection-env','EXPLICIT']])
def test_import_help_and_invalid_arguments_are_inert(args,monkeypatch,capsys):
    _assert_inert(args,monkeypatch,capsys)


def _assert_inert(args,monkeypatch,capsys):
    # Deliberately import first: legitimate scheduler/reliability collection may already do so.
    import os
    import builtins
    import socket
    import psycopg2
    import requests
    from factorlab import fmp_client
    calls=[]
    def denied(*a,**k):
        calls.append(True)
        raise AssertionError('forbidden side effect')
    original_env=os.environ
    class GuardedEnvironment(dict):
        def __getitem__(self,key):
            if key in ('EXPLICIT','FMP_API_KEY','DATABASE_URL','DATABASE_PUBLIC_URL') or key.startswith('PG'):
                return denied()
            return original_env[key]
        def get(self,key,default=None):
            try: return self[key]
            except KeyError: return default
    original_open=builtins.open
    original_path_open=Path.open
    original_mkdir=Path.mkdir
    def guarded_open(file,*a,**kw):
        if Path(file).resolve().is_relative_to(fmp_client.ART.resolve()): return denied()
        return original_open(file,*a,**kw)
    def guarded_path_open(path,*a,**kw):
        if path.resolve().is_relative_to(fmp_client.ART.resolve()): return denied()
        return original_path_open(path,*a,**kw)
    def guarded_mkdir(path,*a,**kw):
        if path.resolve().is_relative_to(fmp_client.ART.resolve()): return denied()
        return original_mkdir(path,*a,**kw)
    with monkeypatch.context() as patch:
        patch.setattr(os,'environ',GuardedEnvironment())
        patch.setattr(fmp_client.FMPClient,'__init__',denied)
        patch.setattr(fmp_client.FMPClient,'get',denied)
        patch.setattr(psycopg2,'connect',denied)
        patch.setattr(psycopg2,'_connect',denied)
        patch.setattr(psycopg2._psycopg,'_connect',denied)
        patch.setattr(requests.sessions.Session,'request',denied)
        patch.setattr(socket.socket,'connect',denied)
        patch.setattr(socket,'getaddrinfo',denied)
        patch.setattr(builtins,'open',guarded_open)
        patch.setattr(Path,'open',guarded_path_open)
        patch.setattr(Path,'mkdir',guarded_mkdir)
        m=script()  # instrument both import and actual non-injected CLI entrypoint
        try: result=m.main(args)
        except SystemExit as e: result=e.code
    assert result in (0,2) and calls==[]
    assert sys.modules['factorlab.fmp_client'] is fmp_client
    assert 'forbidden side effect' not in capsys.readouterr().err


def test_already_imported_client_then_help_is_inert(monkeypatch,capsys):
    from factorlab import fmp_client
    existing=sys.modules['factorlab.fmp_client']
    _assert_inert(['--help'],monkeypatch,capsys)
    assert sys.modules['factorlab.fmp_client'] is existing is fmp_client


def test_connection_errors_do_not_expose_credentials():
    def bad(): raise RuntimeError('postgres://password@host/db?token=SECRET')
    with pytest.raises(b.BenchmarkError,match='^database_connection_failed$'):
        b.read_state(bad,'2026-10')


def test_invalid_period_does_not_read_credentials(monkeypatch,tmp_path):
    m=script()
    class DeniedEnvironment(dict):
        def __getitem__(self,key):
            assert key in ('COLUMNS','LINES'), 'credential lookup'
            return '80'
        def get(self,key,default=None):
            assert key in ('LANGUAGE','LC_ALL','LC_MESSAGES','LANG'), 'credential lookup'
            return default
    with monkeypatch.context() as patch:
        patch.setattr(m.os,'environ',DeniedEnvironment())
        assert m.main(['plan','--period','bad','--output',str(tmp_path/'plan.json'),'--connection-env','EXPLICIT'])==2


@pytest.mark.parametrize('url',[
    'host=example.invalid dbname=factorlab_ci user=factorlab_ci password=synthetic',
    'host=127.0.0.1 dbname=other user=factorlab_ci password=synthetic',
    'host=127.0.0.1 dbname=factorlab_ci user=postgres password=synthetic',
    'host=127.0.0.1 dbname=factorlab_ci user=factorlab_ci password=synthetic service=other',
    'not a DSN',
])
def test_configured_benchmark_target_is_fail_closed(url):
    from benchmark_database import control_options
    with pytest.raises(ValueError,match='target required'):
        control_options(url)
