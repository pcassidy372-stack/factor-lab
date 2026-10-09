"""Synthetic-only public lifecycle and native SUE parity."""
import copy
import importlib
import json
import math
import os
import statistics
from datetime import date, timedelta
from decimal import Decimal

import pytest
from factorlab import sue_plan as p
from factorlab.sue_acquire import acquire, reconcile, HTTPS
from factorlab.sue_store import Store, publish, read, write_json
from scripts.sue_acquisition import main
from tests.sue_observations import preview


def scope():
    return {'period': '2026-10', 'asof': '2026-09-30', 'scope_identity': 'synthetic-scope',
            'before_identity': 'synthetic-before', 'semantics': 'UNVERIFIED_PROVIDER_DATE_AND_PIT',
            'members': [{'id': 'SYNTHETIC_1', 'eligible': True, 'held': False, 'marker': 'present',
                         'windows': [{'symbol': 'SYNTH_X', 'start': '2020-01-01', 'end': '2026-09-30'}],
                         'before': [], 'before_complete': True}]}


def limits(**overrides):
    return dict(logical=1, attempts=3, retries=2, seconds=120, spacing='0.15', response_bytes=p.MAX_BYTES, **{}) | overrides


def plan(): return p.make_plan(scope(), ['SYNTHETIC_1'], limits())


def events(n=9):
    return [{'date': (date(2024, 1, 1) + timedelta(days=100 * i)).isoformat(),
             'epsActual': str((i % 5) - 2), 'epsEstimated': '0', 'symbol': 'SYNTH_X'} for i in range(n)]


class Clock:
    def __init__(self): self.t = 1900000000.
    def __call__(self): return self.t
    def sleep(self, n): self.t += n


class Transport:
    def __init__(self, responses): self.responses = iter(responses); self.calls = []; self.closed = False
    def get(self, *args):
        self.calls.append((args[0], args[2], args[3]))
        r = next(self.responses)
        if isinstance(r, Exception): raise r
        return r
    def close(self): self.closed = True


@pytest.fixture
def root(tmp_path):
    tmp_path.chmod(0o700); return tmp_path


def run(root, responses, **kw):
    t = Transport(responses); c = Clock()
    result = acquire(plan(), root, 'operation', 'synthetic-review', 'SYNTHETIC_KEY',
                     credential=lambda _: 'invented-key-not-real', transport_factory=lambda: t,
                     clock=c, sleep=c.sleep, **kw)
    return result, t, c


@pytest.mark.parametrize('marker', ['absent', 'present', 'empty', 'failed'])
def test_markers_never_exclude(marker):
    s = scope(); s['members'][0]['marker'] = marker
    assert p.make_plan(s, ['SYNTHETIC_1'], limits())['body']['coverage']['eligible'] == 1


@pytest.mark.parametrize('value', ['0', '-2', '3'])
def test_coverage_not_positive_signals(value):
    s = scope(); s['members'][0]['before'] = [{'date': '2026-05-13', 'actual': '0', 'estimate': '0', 'sue': value}, {'date': '2026-09-30', 'actual': None, 'estimate': None, 'sue': None}]
    assert p.validate_scope(s)['usable'] == 1


def test_held_keeps_denominator():
    s = scope(); held = copy.deepcopy(s['members'][0]); held.update(id='SYNTHETIC_2', held=True)
    held['windows'][0]['symbol'] = 'SYNTH_Y'; s['members'].append(held)
    result = p.make_plan(s, ['SYNTHETIC_1'], limits())
    assert result['body']['coverage'] == dict(eligible=2, usable=0, missing=2, unknown_before=0, identity_flags={}, held=1, window_start='2026-05-13', asof='2026-09-30')
    with pytest.raises(p.Invalid, match='identity_held'): p.make_plan(s, ['SYNTHETIC_2'], limits())


@pytest.mark.parametrize('change', ['reversed', 'overlap', 'tie', 'cross', 'multiple'])
def test_identity_validation(change):
    s = scope(); m = s['members'][0]
    if change == 'reversed': m['windows'][0]['end'] = '2019-01-01'
    elif change == 'cross':
        n = copy.deepcopy(m); n['id'] = 'SYNTHETIC_2'; s['members'].append(n)
    else:
        w = copy.deepcopy(m['windows'][0]); w['symbol'] = 'SYNTH_Y'
        if change == 'multiple': w.update(start='2010-01-01', end='2019-01-01')
        elif change == 'overlap': w['end'] = '2026-09-29'
        m['windows'].append(w)
    with pytest.raises(p.Invalid): p.make_plan(s, ['SYNTHETIC_1'], limits())


def test_disjoint_reuse_same_request_distinct_attribution():
    s = scope(); n = copy.deepcopy(s['members'][0]); n['id'] = 'SYNTHETIC_2'; n['windows'][0].update(start='2010-01-01', end='2019-12-31'); s['members'].append(n)
    b = p.make_plan(s, ['SYNTHETIC_1', 'SYNTHETIC_2'], limits())['body']
    assert len(b['requests']) == 1 and len(b['requests'][0]['attributions']) == 2


@pytest.mark.parametrize('period,asof', [('bad', '2026-09-30'), ('2026-13','2026-09-30'), ('2099-01','2098-12-31'), ('2026-10','2026-08-31')])
def test_period_rejection(period, asof):
    s = scope(); s.update(period=period, asof=asof)
    with pytest.raises(p.Invalid): p.make_plan(s, ['SYNTHETIC_1'], limits())


@pytest.mark.parametrize('n', [0, 3, 4, 8, 9])
def test_native_parity(n):
    rows = events(n + 1); values, _ = p.calculate(rows, '2020-01-01', '2030-01-01')
    hist = []; expected = []
    for r in rows:
        diff = float(r['epsActual']) - float(r['epsEstimated'])
        v = None
        if len(hist) >= 4:
            sd = statistics.pstdev(hist[-8:])
            if sd > 1e-9: v = str(round(diff / sd, 4))
        expected.append(v); hist.append(diff)
    assert [r['sue'] for r in values] == expected


def test_current_exclusion_future_null_zero_negative():
    rows = events(); rows[1]['epsActual'] = None
    values, future = p.calculate(rows, '2026-05-13', '2026-09-30')
    assert values[1]['reason'] == 'missing_pair'
    last = rows[-1].copy(); last['date'] = '2027-01-01'; rows.append(last)
    assert p.calculate(rows, '2026-05-13', '2026-09-30') == (values, future + 1)
    base = events(5); a, _ = p.calculate(base, '2020-01-01', '2030-01-01'); base[-1]['epsActual'] = '100'
    b, _ = p.calculate(base, '2020-01-01', '2030-01-01')
    assert Decimal(b[-1]['sue']) == Decimal(str(round(100 / statistics.pstdev([-2., -1., 0., 1.]), 4)))
    assert a[-1]['sue'] != b[-1]['sue']


@pytest.mark.parametrize('value', ['0', '0.00000000001'])
def test_tiny_deviation(value):
    rows = events();
    for r in rows: r['epsActual'] = value
    assert all(r['sue'] is None for r in p.calculate(rows,'2020-01-01','2030-01-01')[0])


@pytest.mark.parametrize('raw', [b'{}', b'{"error":"x"}', b'[{"date":"bad","epsActual":1,"epsEstimated":1}]', b'[{"date":"2026-01-01","epsActual":true,"epsEstimated":1}]', b'[{"date":"2026-01-01","epsActual":NaN,"epsEstimated":1}]', b'[{"date":"2026-01-01","epsActual":1}]', b'[{"date":"2026-01-01","date":"2026-01-02","epsActual":1,"epsEstimated":1}]'])
def test_response_rejection(raw):
    with pytest.raises(p.Invalid): p.parse_response(raw, 'SYNTH_X')


def test_duplicates_and_metadata():
    rows = events(1); rows[0]['lastUpdated'] = 'unverified'; rows *= 2
    parsed, duplicates = p.parse_response(p.canonical(rows), 'SYNTH_X')
    assert duplicates == 1 and parsed[0]['lastUpdated'] == 'unverified'
    rows = [copy.deepcopy(r) for r in rows]; rows[-1]['epsActual'] = '99'
    with pytest.raises(p.Invalid, match='conflicting_duplicate'): p.parse_response(p.canonical(rows), 'SYNTH_X')


@pytest.mark.parametrize('value', ['1e9999', '-1e9999'])
def test_float_conversion_guard(value):
    r = events(1); r[0]['epsActual'] = value
    with pytest.raises(p.Invalid, match='float_range'): p.calculate(r, '2020-01-01','2030-01-01')


@pytest.mark.parametrize('status,raw,state', [(200,b'[]','validated-empty'),(200,p.canonical(events()),'incomplete-history'),(200,b'{}','schema-failure'),(401,b'no','request-failure'),(403,b'no','request-failure'),(429,b'quota exhausted','request-failure'),(302,b'moved','request-failure')])
def test_outcomes(root,status,raw,state):
    result,t,c = run(root,[(status,raw)])
    assert result['results'][0]['state'] == state and t.closed
    assert len(t.calls) == 1


def test_retries_bounds_and_replay(root):
    result,t,c = run(root,[(500,b'fail'),RuntimeError('private-url-key'),(200,b'[]')])
    assert result['complete'] and len(t.calls)==3 and c.t >= 1900000006
    def forbidden(*a): raise AssertionError('credential/session should not be touched')
    assert acquire(plan(),root,'operation','synthetic-review','SYNTHETIC_KEY',credential=forbidden,transport_factory=forbidden)['complete']


@pytest.mark.parametrize('stage', ['intent', 'body', 'receipt'])
def test_interruption_reconciliation(root,stage):
    def interrupt(s):
        if s==stage: raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): run(root,[(200,b'[]')],checkpoint=interrupt)
    summary, obs = reconcile(plan(),root,'operation')
    assert summary['complete'] == (stage=='receipt')
    if stage!='receipt':
        with pytest.raises(p.Invalid,match='uncertain_request'): run(root,[(200,b'[]')])
        result,t,c=run(root,[(200,b'[]')],retry_unknown='new-synthetic-authorization')
        assert result['complete'] and len(result['results'])==2


def test_exhaustion_persists_across_resumes(root):
    result,t,c=run(root,[(500,b'x')]*3)
    with pytest.raises(p.Invalid,match='campaign_budget'): run(root,[(200,b'[]')])
    assert len(result['results'])==3


def test_secret_echo_never_persisted(root):
    result,t,c=run(root,[(200,b'invented-key-not-real')])
    assert result['results'][0]['state']=='schema-failure'
    for f in root.rglob('*'):
        if f.is_file(): assert b'invented-key-not-real' not in f.read_bytes()
    assert not list(root.rglob('*.body'))


def test_tamper_and_operation_conflict(root):
    run(root,[(200,b'[]')]); body=root/'operation/a000001.body';body.write_bytes(b'[1]')
    with pytest.raises(p.Invalid,match='payload_tampering'):reconcile(plan(),root,'operation')
    other=plan();other['body']['scope']['before_identity']='changed';other=p.make_plan(other['body']['scope'],other['body']['selected'],other['body']['limits'])
    with pytest.raises(p.Invalid,match='operation_conflict'):reconcile(other,root,'operation')


def test_private_files_no_overwrite_symlink(root):
    f=root/'x';publish(f,b'[]');assert f.stat().st_mode&0o777==0o600
    with pytest.raises(FileExistsError):publish(f,b'new')
    q=root/'link';q.symlink_to(f)
    with pytest.raises(p.Invalid):read(q)


def test_proposals_noops_corrections_unknown_and_chain():
    s=scope(); raw=p.canonical(events()); b=p.make_plan(s,['SYNTHETIC_1'],limits())
    first=preview(b,{'SYNTH_X':raw})['body'];assert all(e['status']=='insert' for e in first['events'])
    s['members'][0]['before']=[e['proposed'] for e in first['events']]
    b=p.make_plan(s,['SYNTHETIC_1'],limits()); same=preview(b,{'SYNTH_X':raw})['body']
    assert all(e['status']=='noop' for e in same['events'])
    changed=events();changed[0]['epsActual']='20'
    correction=preview(b,{'SYNTH_X':p.canonical(changed)})['body']
    assert correction['events'][0]['status']=='correction'
    assert correction['events'][4]['status']=='correction' and correction['events'][4]['changed_prefix_count']
    s['members'][0]['before']=[];s['members'][0]['before_complete']=False
    assert all(e['status']=='unknown_before' for e in preview(p.make_plan(s,['SYNTHETIC_1'],limits()),{'SYNTH_X':raw})['body']['events'])
    assert correction['gaps'][0]['dependent_boundary'].startswith('UNKNOWN')


def test_public_serialized_lifecycle(root,capsys):
    for name,obj in [('scope',scope()),('selected',['SYNTHETIC_1']),('limits',limits())]:write_json(root/name,obj)
    assert main(['plan','--scope',str(root/'scope'),'--selected',str(root/'selected'),'--limits',str(root/'limits'),'--output',str(root/'plan')])==0
    original={n:read(root/n) for n in ['scope','selected','limits','plan']}
    c=Clock();t=Transport([(200,p.canonical(events()))])
    common=['--plan',str(root/'plan'),'--root',str(root),'--operation','public-op']
    assert main(['acquire',*common,'--approval','synthetic-only','--credential-env','SYNTH_KEY'],transport_factory=lambda:t,credential=lambda n:'invented-key-not-real',clock=c,sleep=c.sleep)==0
    assert main(['reconcile',*common,'--output',str(root/'reconciled')])==0
    assert main(['propose',*common,'--output',str(root/'proposed')])==0
    assert all(read(root/n)==raw for n,raw in original.items())
    assert not p.load(read(root/'proposed'))['body']['applicable']
    before_output=read(root/'proposed')
    assert main(['propose',*common,'--output',str(root/'proposed')])==0
    assert read(root/'proposed')==before_output
    assert 'invented-key-not-real' not in capsys.readouterr().out


@pytest.mark.parametrize('change', ['hash','kind','extra','operation','limits'])
def test_invalid_commands_before_credentials(root,change):
    doc=plan()
    if change=='hash':doc['sha256']='bad'
    if change=='kind':doc['kind']='apply'
    if change=='extra':doc['extra']=1
    if change=='limits':doc['body']['limits']['retries']=999
    write_json(root/'plan',doc)
    def forbidden(*a):raise AssertionError('side effect')
    assert main(['acquire','--plan',str(root/'plan'),'--root',str(root),'--operation','../bad' if change=='operation' else 'x','--approval','x','--credential-env','SYNTH_KEY'],credential=forbidden,transport_factory=forbidden)==2
    assert not (root/'x').exists()


def test_import_order_help_and_missing_arguments(monkeypatch):
    import factorlab.fmp_client as client
    monkeypatch.setattr(client,'FMPClient',lambda *a,**k: (_ for _ in ()).throw(AssertionError('client created')))
    original_get = os.environ.__class__.get
    def guarded_env(self, key, *args):
        if key in ('FMP_API_KEY', 'SYNTH_KEY', 'DATABASE_URL'): raise AssertionError('credential lookup')
        return original_get(self, key, *args)
    monkeypatch.setattr(os.environ.__class__, 'get', guarded_env)
    module=importlib.reload(importlib.import_module('scripts.sue_acquisition'))
    for argv in (['--help'],[],['acquire']):
        with pytest.raises(SystemExit):module.main(argv)


@pytest.mark.parametrize('field,value', [('retries',3),('attempts',0),('logical',0),('seconds',0),('spacing','0.149'),('response_bytes',p.MAX_BYTES+1)])
def test_explicit_bounds(field,value):
    with pytest.raises(p.Invalid):p.make_plan(scope(),['SYNTHETIC_1'],limits(**{field:value}))


def test_inclusive_window_and_symbol_end():
    s=scope(); rows=events(5)
    rows.extend([{'date':d,'epsActual':'3','epsEstimated':'0','symbol':'SYNTH_X'} for d in ['2026-05-12','2026-05-13','2026-09-30','2026-10-01']])
    report=preview(p.make_plan(s,['SYNTHETIC_1'],limits()),{'SYNTH_X':p.canonical(rows)})['body']
    assert [r['proposed']['date'] for r in report['events'] if r['in_window']]==['2026-05-13','2026-09-30']
    assert report['gaps'][0]['out_of_symbol_window']==1
    assert report['new_observation_usable']==1 and report['coverage']['eligible']==1


def test_success_nonempty_and_fresh_reconcile(root):
    result,t,c=run(root,[(200,p.canonical(events(11)))])
    assert result['complete'] and result['results'][0]['state']=='successful-nonempty'
    assert reconcile(plan(),root,'operation')[0]==result


def test_time_budget_and_global_spacing(root):
    s=scope(); second=copy.deepcopy(s['members'][0]);second['id']='SYNTHETIC_2';second['windows'][0]['symbol']='SYNTH_Y';s['members'].append(second)
    doc=p.make_plan(s,['SYNTHETIC_1','SYNTHETIC_2'],limits(logical=2))
    c=Clock();t=Transport([(200,b'[]'),(200,b'[]')])
    assert acquire(doc,root,'op','synthetic','SYNTH_KEY',credential=lambda _:'invented-key-not-real',transport_factory=lambda:t,clock=c,sleep=c.sleep)['complete']
    attempts=Store(root,'op').attempts();assert Decimal(str(attempts[1][1]['started']))-Decimal(str(attempts[0][1]['started']))>=Decimal('.15')
    other=p.make_plan(scope(),['SYNTHETIC_1'],limits(seconds=1));c=Clock()
    class Slow(Transport):
        def get(self,*args):c.t+=2;return 200,b'[]'
    result=acquire(other,root,'slow','synthetic','SYNTH_KEY',credential=lambda _:'invented-key-not-real',transport_factory=lambda:Slow([]),clock=c,sleep=c.sleep)
    assert not result['complete']


def test_https_transport_options_without_network(monkeypatch):
    import requests
    seen={}
    class Response:
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def iter_content(self,n):yield b'[]'
    class Session:
        trust_env=True
        def get(self,url,**options):seen.update(url=url,options=options,trust_env=self.trust_env);return Response()
        def close(self):seen['closed']=True
    monkeypatch.setattr(requests,'Session',Session)
    t=HTTPS();assert t._read('SYNTH_X','invented-key-not-real',2,100,lambda _:None)==(200,b'[]');t.close()
    assert seen['url']==p.ENDPOINT and not seen['trust_env'] and seen['closed']
    assert seen['options']['verify'] and not seen['options']['allow_redirects'] and seen['options']['stream']
    assert seen['options']['params']==dict(symbol='SYNTH_X',limit=120,apikey='invented-key-not-real')


def test_missing_credential_not_empty(root):
    def no_session():raise AssertionError('session')
    with pytest.raises(p.Invalid,match='credential_unavailable'):
        acquire(plan(),root,'op','synthetic','SYNTH_KEY',credential=lambda _:None,transport_factory=no_session)
    assert not Store(root,'op').attempts()


def test_receipt_tampering_and_lock(root):
    run(root,[(200,b'[]')]);file=root/'operation/a000001.receipt';d=p.load(read(file));d['body']['state']='schema-failure';file.write_bytes(p.canonical(d))
    with pytest.raises(p.Invalid,match='content_hash'):reconcile(plan(),root,'operation')
    s=Store(root,'operation')
    with s.lock():
        with pytest.raises(p.Invalid,match='operation_locked'):
            with s.lock():pass


def test_lossless_lexemes_and_absent_null():
    raw=b'[{"date":"2026-01-01","epsActual":0.12345678901234567890123456789,"epsEstimated":null}]'
    rows,_=p.parse_response(raw,'SYNTH_X')
    assert str(rows[0]['epsActual'])=='0.12345678901234567890123456789'
    assert rows[0]['epsEstimated'] is None
    with pytest.raises(p.Invalid):p.parse_response(b'[{"date":"2026-01-01","epsActual":0}]','SYNTH_X')


def test_no_cross_vintage_history_fill():
    s=scope();s['members'][0]['before']=[{'date':r['date'],'actual':r['epsActual'],'estimate':r['epsEstimated'],'sue':None} for r in events(5)]
    raw=p.canonical([{'date':'2026-09-30','epsActual':'3','epsEstimated':'0'}])
    result=preview(p.make_plan(s,['SYNTHETIC_1'],limits()),{'SYNTH_X':raw})['body']
    assert result['events'][0]['calculation']=='insufficient_history'
    assert len(result['gaps'][0]['unreturned_before_dates'])==5


def test_native_requests_error_is_secret_safe(root,monkeypatch):
    import requests
    from factorlab.sue_acquire import TransportFailure
    class Session:
        def get(self,*a,**k):raise requests.ConnectionError('https://secret.invalid?apikey=invented-key-not-real')
        def close(self):pass
    monkeypatch.setattr(requests,'Session',Session)
    c=Clock()
    result=acquire(plan(),root,'native-failure','synthetic','SYNTH_KEY',credential=lambda _:'invented-key-not-real',clock=c,sleep=c.sleep)
    assert len(result['results'])==3 and not result['complete']
    for f in root.rglob('*'):
        if f.is_file():assert b'secret.invalid' not in f.read_bytes() and b'invented-key-not-real' not in f.read_bytes()


@pytest.mark.parametrize('body',[b'{"apiKey":"other-secret"}',b'https://user:password@example.invalid',b'password=other-secret'])
def test_credential_shaped_bodies_not_retained(root,body):
    result,t,c=run(root,[(200,body)])
    assert result['results'][0]['state']=='schema-failure' and not list(root.rglob('*.body'))


def test_held_ambiguous_windows_preserved_without_blocking_resolved_subset():
    s=scope(); held=copy.deepcopy(s['members'][0]);held.update(id='SYNTHETIC_HELD', held=True)
    held['windows']=[{'symbol':'SYNTH_HELD','start':'2025-01-01','end':'2024-01-01'},
                     {'symbol':'SYNTH_OLD','start':'2020-01-01','end':'2026-09-30'},
                     {'symbol':'SYNTH_NEW','start':'2021-01-01','end':'2026-09-30'}]
    s['members'].append(held)
    b=p.make_plan(s,['SYNTHETIC_1'],limits())['body']
    assert b['coverage']['eligible']==2 and b['coverage']['held']==1
    assert set(b['coverage']['identity_flags']['SYNTHETIC_HELD'])=={'reversed_window','ambiguous_windows','identity_review_required'}
    assert [r['symbol'] for r in b['requests']]==['SYNTH_X']
    with pytest.raises(p.Invalid,match='identity_held'):p.make_plan(s,['SYNTHETIC_HELD'],limits())


def test_unknown_before_is_not_known_missing_coverage():
    s=scope();s['members'][0]['before_complete']=False
    c=p.make_plan(s,['SYNTHETIC_1'],limits())['body']['coverage']
    assert c['eligible']==1 and c['unknown_before']==1 and c['missing']==0


def test_bounded_canonical_extreme_decimal_exponents():
    raw=b'{"value":1e999999999}'
    assert len(p.canonical(p.load(raw)))<100
    with pytest.raises(p.Invalid,match='float_range'):
        p.calculate([{'date':'2026-09-30','epsActual':Decimal('1e999999999'),'epsEstimated':'0'}],'2026-05-13','2026-09-30')


def test_private_input_parent_required(root):
    public=root/'public';public.mkdir(mode=0o755);f=public/'file';f.write_bytes(b'[]');f.chmod(0o600)
    with pytest.raises(p.Invalid,match='private_root_required'):read(f)


def test_json_escaped_credential_echo_is_not_retained(root):
    key='invented-key-not-real'
    escaped=''.join('\\u%04x'%ord(c) for c in key)
    result,t,c=run(root,[(200,('{"detail":"'+escaped+'"}').encode())])
    assert result['results'][0]['state']=='schema-failure' and not list(root.rglob('*.body'))
