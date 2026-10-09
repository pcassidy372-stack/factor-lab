"""R1–R3: synthetic public lifecycle, owned workers and observation identity."""
import copy
import json
import os
import time
from pathlib import Path
from datetime import date,timedelta
import pytest
import requests
from factorlab import sue_plan as p
from factorlab.sue_store import write_json,read,Store
from factorlab.sue_acquire import acquire,reconcile,HTTPS,validate_observation
from factorlab.sue_worker import WorkerFailure
from factorlab.sue_proposal import verify_proposal,write_proposal,resource_bounds
from scripts.sue_acquisition import main
from tests.test_sue_acquisition import scope,limits,Clock,Transport,events


@pytest.fixture
def root(tmp_path):tmp_path.chmod(0o700);return tmp_path


def large_scope(n=50):
    s=scope();s['members']=[]
    for i in range(n):
        m=copy.deepcopy(scope()['members'][0]);m['id']='SYNTH_'+str(i);m['windows']=[{'symbol':'SYNTHSYM_'+str(i),'start':'1990-01-01','end':'2026-09-30'}];s['members'].append(m)
    return s


def payload(symbol):
    return p.canonical([{'date':(date(1997,1,1)+timedelta(days=i*90)).isoformat(),'epsActual':str(i%7),'epsEstimated':'0','symbol':symbol} for i in range(120)])


def make(root,n=1):
    s=large_scope(n)
    for name,obj in [('scope',s),('selected',[m['id'] for m in s['members']]),('limits',limits(logical=n,attempts=n*3))]:write_json(root/name,obj)
    assert main(['plan','--scope',str(root/'scope'),'--selected',str(root/'selected'),'--limits',str(root/'limits'),'--output',str(root/'plan')])==0
    return p.load(read(root/'plan'))


def campaign(root,plan,operation='one',at=1900000000.):
    c=Clock();c.t=at
    class Fake:
        calls=0
        def get(self,symbol,*a):self.calls+=1;return 200,payload(symbol)
        def close(self):pass
    fake=Fake()
    assert main(['acquire','--plan',str(root/'plan'),'--root',str(root),'--operation',operation,'--approval','synthetic-review','--credential-env','SYNTH_KEY'],transport_factory=lambda:fake,credential=lambda _:'synthetic-only-key',clock=c,sleep=c.sleep)==0
    return fake


def test_public_50_by_120_shards(root,record_property):
    plan=make(root,50);inputs={n:read(root/n) for n in ('scope','selected','limits','plan')};fake=campaign(root,plan)
    common=['--plan',str(root/'plan'),'--root',str(root),'--operation','one']
    assert main(['reconcile',*common,'--output',str(root/'reconciled')])==0
    assert main(['propose',*common,'--output',str(root/'proposal')])==0
    manifest=verify_proposal(root/'proposal')['body'];assert manifest['event_count']==6000 and len(manifest['shards'])==50
    assert manifest['coverage']['eligible']==50 and fake.calls==50
    all_dates=0
    for ref in manifest['shards']:
        f=root/'proposal.shards'/ref['file'];assert f.stat().st_mode&0o777==0o600 and f.stat().st_size<=p.MAX_BYTES
        b=p.load(read(f))['body'];all_dates+=len(b['events'])
        for e in b['events']:
            # Compact prefix map recovers all preceding changed dates, not only8.
            prefix=b['changed_dates'][:e['changed_prefix_count']]
            assert all(d<e['proposed']['date'] for d in prefix)
            assert len(e['prior_valid_dates'])<=8
    assert all_dates==6000
    before={str(f.relative_to(root)):f.read_bytes() for f in (root/'proposal.shards').iterdir()}
    assert main(['propose',*common,'--output',str(root/'proposal')])==0
    assert before=={str(f.relative_to(root)):f.read_bytes() for f in (root/'proposal.shards').iterdir()}
    assert all(read(root/n)==v for n,v in inputs.items())
    record_property('sue_output',json.dumps({'events':6000,'shards':50,'all_hashes_verified':True,'raw_requests':50,'no_reacquisition':True,'shard_bytes':manifest['shard_bytes']}))


@pytest.mark.parametrize('stage',['shard','before_completion'])
def test_partial_proposal_resume_without_reacquire(root,stage):
    plan=make(root,3);fake=campaign(root,plan);_,obs=reconcile(plan,root,'one')
    def interrupt(value):
        if value==stage:raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):write_proposal(plan,obs,root/'proposal',interrupt)
    assert not (root/'proposal').exists()
    with pytest.raises(FileNotFoundError):verify_proposal(root/'proposal')
    paths=list((root/'proposal.shards').glob('*.json'));saved={f.name:f.read_bytes() for f in paths}
    doc=write_proposal(plan,reconcile(plan,root,'one')[1],root/'proposal')
    assert doc['body']['event_count']==360 and fake.calls==3
    assert all((root/'proposal.shards'/n).read_bytes()==raw for n,raw in saved.items())


def test_output_disk_failure_is_partial_and_resumable(root,monkeypatch):
    plan=make(root,2);campaign(root,plan);_,obs=reconcile(plan,root,'one')
    import factorlab.sue_proposal as module
    original=module.publish
    def failing(path,raw):
        if str(path).endswith('complete.json'):raise OSError('synthetic disk full')
        return original(path,raw)
    monkeypatch.setattr(module,'publish',failing)
    with pytest.raises(OSError):write_proposal(plan,obs,root/'proposal')
    assert not (root/'proposal').exists()
    monkeypatch.setattr(module,'publish',original)
    assert write_proposal(plan,obs,root/'proposal')['body']['event_count']==240


def test_capacity_failure_before_credentials(root,monkeypatch):
    plan=make(root)
    class Empty:f_bavail=0;f_frsize=4096
    monkeypatch.setattr(os,'statvfs',lambda _:Empty())
    def forbidden(*a):raise AssertionError('credential or session')
    with pytest.raises(p.Invalid,match='capacity_precondition'):
        acquire(plan,root,'one','synthetic','SYNTH_KEY',credential=forbidden,transport_factory=forbidden)
    assert not (root/'one').exists()


@pytest.mark.parametrize('status',[401,403,400,404,302])
def test_headers_terminal_even_with_unreadable_body(root,monkeypatch,record_property,status):
    plan=make(root,2)
    class Response:
        status_code=status
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def iter_content(self,n):raise requests.exceptions.ChunkedEncodingError('must never read refused body')
    class Session:
        def get(self,*a,**k):return Response()
        def close(self):pass
    monkeypatch.setattr(requests,'Session',Session);c=Clock()
    result=acquire(plan,root,'one','synthetic','SYNTH_KEY',credential=lambda _:'synthetic-only-key',clock=c,sleep=c.sleep)
    attempts=Store(root,'one').attempts();assert len(attempts)==1 and len(result['results'])==1
    receipt=attempts[0][2];assert receipt['status']==status and receipt['stop'] and not receipt['worker']['alive']
    assert receipt['worker']['exit_code']==0
    record_property('owned_worker',p.canonical(receipt['worker']).decode())
    with pytest.raises(p.Invalid,match='campaign_stopped'):
        acquire(plan,root,'one','synthetic','SYNTH_KEY',credential=lambda _:(_ for _ in ()).throw(AssertionError('credential')))


@pytest.mark.parametrize('where',['headers','body','decode'])
def test_real_owned_worker_cancellation(monkeypatch,record_property,where):
    class Response:
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def iter_content(self,n):
            time.sleep(5)  # no yielded bytes; includes a modeled blocked decoder
            yield b'[]'
    class Session:
        def get(self,*a,**k):
            if where=='headers':time.sleep(5)
            return Response()
        def close(self):pass
    monkeypatch.setattr(requests,'Session',Session);t=HTTPS();seen=[];start=time.monotonic()
    with pytest.raises(WorkerFailure) as exc:t.get('SYNTH','synthetic-only-key',.12,1024,seen.append)
    elapsed=time.monotonic()-start;r=exc.value.receipt
    assert elapsed<2 and r['terminated'] and not r['alive'] and r['exit_code'] is not None
    assert exc.value.code=='worker_deadline_unknown'
    assert seen==([] if where=='headers' else [200])
    record_property('owned_worker_cancellation',json.dumps({**r,'parent_elapsed':elapsed,'stub_phase':where,'real_process':True,'network':False}))


def test_cancelled_campaign_requires_explicit_retry_and_charges_time(root,monkeypatch):
    plan=make(root)
    class Response:
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def iter_content(self,n):time.sleep(5);yield b'[]'
    class Session:
        def get(self,*a,**k):return Response()
        def close(self):pass
    monkeypatch.setattr(requests,'Session',Session)
    # Restrict one request's worker without changing the approved campaign budget.
    original=HTTPS.get
    def bounded(self,symbol,key,timeout,bound,on_status):return original(self,symbol,key,min(timeout,.12),bound,on_status)
    monkeypatch.setattr(HTTPS,'get',bounded)
    result=acquire(plan,root,'one','synthetic','SYNTH_KEY',credential=lambda _:'synthetic-only-key')
    assert len(result['results'])==1 and result['results'][0]['state']=='interrupted-unknown-request'
    assert p.number(result['charged_seconds'])>0
    with pytest.raises(p.Invalid,match='uncertain_request'):
        acquire(plan,root,'one','synthetic','SYNTH_KEY',credential=lambda _:None)
    class Fake:
        def get(self,*a):return 200,b'[]'
        def close(self):pass
    second=acquire(plan,root,'one','synthetic','SYNTH_KEY',retry_unknown='reviewed-retry',credential=lambda _:'synthetic-only-key',transport_factory=Fake)
    assert second['complete'] and len(second['results'])==2 and p.number(second['charged_seconds'])>p.number(result['charged_seconds'])


@pytest.mark.parametrize('status,body,expected',[ (429,b'quota exhausted',1),(429,b'transient',3),(500,b'error',3),(503,b'error',3)])
def test_retry_policy(root,status,body,expected):
    plan=make(root);c=Clock();t=Transport([(status,body)]*3)
    result=acquire(plan,root,'one','synthetic','SYNTH_KEY',credential=lambda _:'synthetic-only-key',transport_factory=lambda:t,clock=c,sleep=c.sleep)
    assert len(t.calls)==expected and not result['complete']


def test_equal_content_distinct_observation_and_same_replay(root):
    plan=make(root);campaign(root,plan,'one');campaign(root,plan,'two',1900086400.)
    one=reconcile(plan,root,'one')[1];two=reconcile(plan,root,'two')[1]
    a=write_proposal(plan,one,root/'a');b=write_proposal(plan,two,root/'b')
    assert a!=b and a['body']['observations']!=b['body']['observations']
    def shard(doc,name):return p.load(read(root/(name+'.shards')/doc['body']['shards'][0]['file']))['body']
    x,y=shard(a,'a'),shard(b,'b')
    assert x['events'][0]['input_sha256']==y['events'][0]['input_sha256']
    assert [e['economic_content_id'] for e in x['events']]==[e['economic_content_id'] for e in y['events']]
    assert [e['status'] for e in x['events']]==[e['status'] for e in y['events']]
    assert x['events'][0]['operation_id']!=y['events'][0]['operation_id']
    assert write_proposal(plan,reconcile(plan,root,'one')[1],root/'a')==a


@pytest.mark.parametrize('field',['receipt','campaign','intent','interval','status','payload'])
def test_observation_links_refuse_corruption(root,field):
    plan=make(root);campaign(root,plan);obs=copy.deepcopy(reconcile(plan,root,'one')[1]['SYNTHSYM_0']);b=obs['lineage']['body']
    if field=='receipt':b['receipt_sha256']='0'*64
    if field=='campaign':b['campaign']['body']['approval']='other'
    if field=='intent':b['intent']['symbol']='OTHER'
    if field=='interval':b['observation_interval']['finished']+=86400
    if field=='status':b['status_receipt']['body']['status']=401
    if field=='payload':obs['raw']=b'[]'
    obs['lineage']=p.seal('sue-observation',b)
    with pytest.raises(p.Invalid):validate_observation(plan,'SYNTHSYM_0',obs)


def test_swapped_receipt_and_missing_legacy_lineage(root):
    plan=make(root);campaign(root,plan,'one');campaign(root,plan,'two',1900086400.)
    one=copy.deepcopy(reconcile(plan,root,'one')[1]['SYNTHSYM_0']);two=reconcile(plan,root,'two')[1]['SYNTHSYM_0']
    one['lineage']['body']['receipt']=two['lineage']['body']['receipt']
    one['lineage']['body']['receipt_sha256']=two['lineage']['body']['receipt_sha256'];one['lineage']=p.seal('sue-observation',one['lineage']['body'])
    with pytest.raises(p.Invalid):validate_observation(plan,'SYNTHSYM_0',one)
    with pytest.raises(p.Invalid):list(p.proposal_shards(plan,{'SYNTHSYM_0':payload('SYNTHSYM_0')}))
    legacy=copy.deepcopy(plan);legacy['version']='sue-plan-v1';write_json(root/'legacy',legacy)
    def forbidden(*a):raise AssertionError('side effect')
    assert main(['acquire','--plan',str(root/'legacy'),'--root',str(root),'--operation','bad','--approval','x','--credential-env','SYNTH_KEY'],credential=forbidden,transport_factory=forbidden)==2
    assert not (root/'bad').exists()


def test_status_checkpoint_interruption_prevents_retry(root,monkeypatch):
    plan=make(root)
    class Response:
        status_code=401
        def __enter__(self):return self
        def __exit__(self,*a):pass
    class Session:
        def get(self,*a,**k):return Response()
        def close(self):pass
    monkeypatch.setattr(requests,'Session',Session)
    def interrupt(stage):
        if stage=='status':raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):acquire(plan,root,'one','synthetic','SYNTH_KEY',credential=lambda _:'synthetic-only-key',checkpoint=interrupt)
    result,_=reconcile(plan,root,'one');assert result['results'][0]['status']==401
    with pytest.raises(p.Invalid,match='campaign_stopped'):
        acquire(plan,root,'one','synthetic','SYNTH_KEY',retry_unknown='review',credential=lambda _:None)


def test_monotonic_budget_not_reset_by_wall_clock(root):
    plan=make(root);c=Clock()
    class Mono:
        t=0.
        def __call__(self):return self.t
    mono=Mono()
    class Fake:
        calls=0
        def get(self,*a):self.calls+=1;mono.t+=121;return 200,b'[]'
        def close(self):pass
    t=Fake()
    result=acquire(plan,root,'one','synthetic','SYNTH_KEY',credential=lambda _:'synthetic-only-key',transport_factory=lambda:t,clock=c,sleep=c.sleep,monotonic=mono)
    assert t.calls==1 and result['results'][0]['state']=='interrupted-unknown-request' and p.number(result['charged_seconds'])>=121
    with pytest.raises(p.Invalid,match='campaign_budget'):
        acquire(plan,root,'one','synthetic','SYNTH_KEY',retry_unknown='review',credential=lambda _:(_ for _ in ()).throw(AssertionError()),clock=c,sleep=c.sleep,monotonic=mono)


def test_resumed_wall_clock_rollback_refused(root):
    plan=make(root);c=Clock();t=Transport([(500,b'x')]*3)
    # Leave remaining global attempt budget but no repeats for the first symbol.
    original=plan['body'];s=large_scope(2)
    plan=p.make_plan(s,[m['id'] for m in s['members']],limits(logical=2,attempts=6))
    def interrupt(stage):
        if stage=='receipt':raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):acquire(plan,root,'one','synthetic','SYNTH_KEY',credential=lambda _:'synthetic-only-key',transport_factory=lambda:t,clock=c,sleep=c.sleep,checkpoint=interrupt)
    c.t-=10
    with pytest.raises(p.Invalid,match='wall_clock_rollback'):
        acquire(plan,root,'one','synthetic','SYNTH_KEY',credential=lambda _:None,clock=c,sleep=c.sleep)


def test_output_shard_tampering_never_overwritten(root):
    plan=make(root);campaign(root,plan);_,obs=reconcile(plan,root,'one');doc=write_proposal(plan,obs,root/'proposal')
    f=root/'proposal.shards'/doc['body']['shards'][0]['file'];f.write_bytes(b'{}')
    with pytest.raises(p.Invalid):verify_proposal(root/'proposal')
    with pytest.raises(p.Invalid,match='existing_shard_conflict'):write_proposal(plan,obs,root/'proposal')
    assert f.read_bytes()==b'{}'


def test_proposal_does_not_accept_invented_timing(root):
    plan=make(root);campaign(root,plan);obs=copy.deepcopy(reconcile(plan,root,'one')[1]['SYNTHSYM_0'])
    body=obs['lineage']['body'];receipt=body['receipt']['body'];receipt['finished']+=86400
    body['receipt']=p.seal('sue-attempt-receipt',receipt);body['receipt_sha256']=p.digest(p.canonical(body['receipt']))
    body['observation_interval']['finished']=receipt['finished'];obs['lineage']=p.seal('sue-observation',body)
    with pytest.raises(p.Invalid,match='observation_timing'):validate_observation(plan,'SYNTHSYM_0',obs)


def test_owned_worker_drops_inherited_environment(monkeypatch):
    monkeypatch.setenv('SYNTH_INHERITED_SECRET','not-real')
    class Response:
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def iter_content(self,n):yield b'[]'
    class Session:
        def get(self,*a,**k):
            assert set(os.environ)=={'PATH','PYTHONDONTWRITEBYTECODE'}
            assert self.trust_env is False
            return Response()
        def close(self):pass
    monkeypatch.setattr(requests,'Session',Session)
    t=HTTPS();assert t.get('SYNTH','synthetic-only-key',1,1024)==(200,b'[]')
    assert t.last_worker['exit_code']==0 and not t.last_worker['alive']
