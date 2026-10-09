import copy,json
from uuid import uuid4
import pytest
from factorlab import benchmark_vintages as v
from factorlab.benchmark_loader import BenchmarkError
from factorlab.publication_contract import PublicationError
from vintage_samples import candidate
from test_benchmark_loader import GRID

def test_synthetic_candidate_exact():
    d,p=candidate(GRID,'2026-10');r=v.validate_candidate(d,p,v.digest(d),v.digest(p));assert len(r['grid'])==13

@pytest.mark.parametrize('fault',['hash','duplicate','oversize','symbol','value','period','grid','code','endpoint','scope','time','empty'])
def test_rejected_candidate(fault):
    d,p=candidate(GRID,'2026-10');doc=json.loads(d)
    if fault=='hash':expected='0'*64
    else:expected=None
    if fault=='duplicate':d=b'{"status":1,"status":2}'
    elif fault=='oversize':d=b' '*262145
    elif fault=='symbol':doc['symbol']='QQQ'
    elif fault=='value':doc['rows'][0]['adjClose']='100.000000000000001'
    elif fault=='period':doc['period']='2026-11'
    elif fault=='grid':doc['rows'][1]['date']=doc['rows'][0]['date']
    elif fault=='code':doc['provenance']['code_commit']='f'*40
    elif fault=='endpoint':doc['provenance']['request_endpoint']='https://invalid.example'
    elif fault=='scope':doc['provenance']['request_scope']['to']='2027-01-01'
    elif fault=='time':doc['provenance']['retrieval_end']='2999-01-01T00:00:00+00:00'
    elif fault=='empty':p=b'[]'
    if fault not in ('duplicate','oversize'):d=v.canonical(doc)
    with pytest.raises((ValueError,PublicationError,KeyError,BenchmarkError)):v.validate_candidate(d,p,expected or v.digest(d),v.digest(p))

def test_legacy_fingerprint_unchanged():
    from monthly_samples import monthly_sample
    from factorlab.monthly_producer import FrozenMonthlyInputs,prepare_publication,fingerprint
    p=monthly_sample();spec,_=prepare_publication(FrozenMonthlyInputs.freeze(p),'a'*40)
    assert spec['input_fingerprints']['benchmarks']==fingerprint(p['benchmarks'])

def test_missing_explicit_arguments_no_connection():
    from factorlab.monthly_snapshot import capture_month
    from factorlab.monthly_scheduler import run_monthly_period
    def denied():raise AssertionError('connection before explicit selection')
    with pytest.raises(TypeError):capture_month(denied,'2026-10')
    with pytest.raises(TypeError):run_monthly_period(denied,'2026-10','a'*40)

@pytest.mark.parametrize('args',[['--help'],[],['--connection-env','x','import-plan']])
def test_cli_help_invalid_no_connection(args,monkeypatch):
    import importlib.util,pathlib
    import factorlab.fmp_client # deliberate prior import; do not remove global module
    spec=importlib.util.spec_from_file_location('vintage_cli',pathlib.Path(__file__).parents[1]/'scripts/benchmark_vintage.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    def denied(*a,**k):raise AssertionError('unexpected side effect')
    monkeypatch.setattr(factorlab.fmp_client,'FMPClient',denied)
    import psycopg2
    monkeypatch.setattr(psycopg2,'connect',denied)
    with pytest.raises(SystemExit):mod.main(args,connect=denied)


def test_all_fifteen_migrations_remain_frozen():
    import hashlib
    from factorlab.migrations import MIGRATIONS
    expected={1: '37f44d87d677b6f261029a99dae83acb49cde3bcac4aca9a8ec5f8ce287a0878', 2: 'df017594f01cf5b5315abba1e01b8fd9a361742a724fdca5db461bf7f415da4d', 3: '0de41289728114933208ee8e4840b76a14ede7abf3c9ad23189878712b3550a9', 4: '363302271b9a2bb6ec56e44ad1a27c6db1823a869744e6fb1248ef8c73e4c144', 5: '86abb0a7d7181696339815a68251a390da14989cfecea602080d2c11a2e3d1e8', 6: '1c4617620473073c3bfaefaff7d1e962502150b5042f1bb8c3bae9285df0ba3a', 7: '6d8c059f37400d300356f5de5d44ef776d606b2a049531c5d13cdc9175f15700', 8: 'bdcffe1a6c490ba970c15cdc43d269418a126d7ded87521c8bbb7d191c4e5563', 9: 'e7bcc8b185c0e208dff731de92c8833d0124e3c254ed8e113fa2a3d9022a95b6', 10: '2fd14f2ecbfeeaf74a7f96f9f4ac3fb00ac9a13544609e709165fcdd5dec9408', 11: 'bcf1af52fdda6cb87d407fc0e4ecc0e0be456e215f0aeee1019132e8bb274955', 12: 'e67ab4d832efb30285d6984523865be687510bc0a88c858ca48d78f189d2c0e7', 13: '72019fabdfeb51d39449abe88f897edbf0ba6c1905eeabcb4cb33e3ca9ef2fc0', 14: 'a9ea061508f370d72052dd570701953f08a55f6af8b75ff185a88d9c531193d8', 15: '7268cddc3dd11fc6d64713fd9a52f28efda3d414a22f66eca8ccd14c060c2dc3'}
    assert {k:hashlib.sha256(MIGRATIONS[k].encode()).hexdigest() for k in expected}==expected
