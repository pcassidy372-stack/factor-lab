"""Public main + serialized plans + real restricted logins. No injected business ops."""
import base64
import importlib.util
import json
import os
import stat
from pathlib import Path
from uuid import uuid4
import pytest
from factorlab import benchmark_vintages as v
from vintage_samples import candidate
from vintage_database import vintage_database
from test_publication_postgres import database,query
from test_benchmark_loader import GRID

@pytest.fixture
def cli():
    spec=importlib.util.spec_from_file_location('tested_public_vintage_cli',Path(__file__).parents[1]/'scripts/benchmark_vintage.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def invoke(cli,monkeypatch,db,role,*args):
    from psycopg2.extensions import make_dsn
    monkeypatch.setenv('SYNTHETIC_VINTAGE_CONNECTION',make_dsn(**db.connection_options(role)))
    return cli.main(['--connection-env','SYNTHETIC_VINTAGE_CONNECTION',*map(str,args)])

def private(path,raw):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw)
    return path

def import_plan(cli,monkeypatch,db,tmp_path):
    d,p=candidate(GRID,'2026-10');op=str(uuid4())
    doc=private(tmp_path/'candidate.json',d);payload=private(tmp_path/'payload.json',p);plan=tmp_path/'import-plan.json'
    args=['import-plan','--candidate',doc,'--payload',payload,'--document-sha256',v.digest(d),'--payload-sha256',v.digest(p),'--operation-id',op,'--permit-role',db.roles['importer'],'--output',plan]
    assert invoke(cli,monkeypatch,db,'reader',*args)==0
    return plan,op,args

def selection_plan(cli,monkeypatch,db,tmp_path,vid,predecessor=None,name='select-plan.json'):
    op=str(uuid4());plan=tmp_path/name
    args=['select-plan','--period','2026-10','--vintage',vid,'--approval','SYNTHETIC approval','--operation-id',op,'--permit-role',db.roles['selector'],'--output',plan]
    args+=['--predecessor',predecessor] if predecessor else ['--empty-chain']
    assert invoke(cli,monkeypatch,db,'reader',*args)==0
    return plan,op

def counts(db):
    return tuple(query(db,'SELECT count(*) FROM public.'+t)[0][0] for t in ('benchmark_vintages','benchmark_vintage_values','benchmark_vintage_selections','fl_dataset_generations','fl_publication_receipts'))

def test_public_cli_serialized_lifecycle(database,cli,monkeypatch,tmp_path,capsys):
    before=query(database,'SELECT * FROM public.factor_values ORDER BY asof,security_id')
    plan,vid,args=import_plan(cli,monkeypatch,database,tmp_path)
    assert counts(database)==(0,0,0,0,0)
    assert stat.S_IMODE(plan.stat().st_mode)==0o600
    original=plan.read_bytes()
    assert invoke(cli,monkeypatch,database,'reader',*args)==2
    assert plan.read_bytes()==original
    assert invoke(cli,monkeypatch,database,'selector','apply','--plan',plan)==2 # role not permitted
    assert counts(database)==(0,0,0,0,0)
    for expected in ('imported_not_selected','verified_replay'):
        assert invoke(cli,monkeypatch,database,'importer','apply','--plan',plan)==0
        assert expected in capsys.readouterr().out
    assert counts(database)==(1,13,0,0,0)
    assert invoke(cli,monkeypatch,database,'reader','reconcile','--plan',plan)==0
    assert 'complete_committed_import' in capsys.readouterr().out
    select,eid=selection_plan(cli,monkeypatch,database,tmp_path,vid)
    assert counts(database)==(1,13,0,0,0)
    assert stat.S_IMODE(select.stat().st_mode)==0o600
    for _ in range(2):assert invoke(cli,monkeypatch,database,'selector','apply','--plan',select)==0
    assert invoke(cli,monkeypatch,database,'reader','reconcile','--plan',select)==0
    assert 'complete_committed_selection' in capsys.readouterr().out
    assert counts(database)==(1,13,1,0,0)
    assert query(database,'SELECT * FROM public.factor_values ORDER BY asof,security_id')==before
    for file in (plan,select):
        text=file.read_text()
        for role in ('reader','selector','importer'):
            assert database.connection_options(role)['password'] not in text
    # A distinct operation cannot create a second empty-chain event.
    stale,_=selection_plan(cli,monkeypatch,database,tmp_path,vid,name='stale.json')
    assert invoke(cli,monkeypatch,database,'selector','apply','--plan',stale)==2
    assert counts(database)==(1,13,1,0,0)

def test_cli_connection_environment_change_is_rejected(database,cli,monkeypatch,tmp_path):
    plan,vid,_=import_plan(cli,monkeypatch,database,tmp_path)
    # Two actual owned databases on the same CI service; NOT a claimed OID collision.
    with vintage_database() as other:
        assert invoke(cli,monkeypatch,other,'importer','apply','--plan',plan)==2
        assert invoke(cli,monkeypatch,other,'reader','reconcile','--plan',plan)==2
        assert counts(other)==(0,0,0,0,0)
    assert counts(database)==(0,0,0,0,0)
    assert invoke(cli,monkeypatch,database,'importer','apply','--plan',plan)==0

def modeled_plan():
    from test_vintage_boundaries import ModeledCursor
    t=v.target(ModeledCursor('127.0.0.1'));role=t.pop('authenticated_role')
    t.update(planned_by=role,allowed_roles=[role])
    d,p=candidate(GRID,'2026-10')
    return dict(kind='import',operation_id=str(uuid4()),document=base64.b64encode(d).decode(),payload=base64.b64encode(p).decode(),document_sha=v.digest(d),payload_sha=v.digest(p),expected_target=t)

@pytest.mark.parametrize('fault',['hash','operation','kind','extra','shape','target','base64','values','select_period','select_predecessor','select_approval'])
@pytest.mark.parametrize('command',['apply','reconcile'])
def test_malformed_plan_precedes_credential_lookup_and_native_connect(cli,monkeypatch,tmp_path,capsys,fault,command):
    plan=modeled_plan()
    if fault.startswith('select_'):
        plan={k:plan[k] for k in ('operation_id','expected_target')}
        plan.update(kind='select',period='2026-10',vintage_id=str(uuid4()),predecessor=None,approval_reference='synthetic')
        if fault=='select_period':plan['period']='bad'
        if fault=='select_predecessor':plan['predecessor']=plan['operation_id']
        if fault=='select_approval':plan['approval_reference']=' '
    if fault=='operation':plan['operation_id']='not-a-uuid'
    if fault=='kind':plan['kind']='delete'
    if fault=='extra':plan['unexpected']='private://invented-secret'
    if fault=='shape':del plan['payload']
    if fault=='target':plan['expected_target']['route']['password']='invented-secret'
    if fault=='base64':plan['document']='!!!'
    if fault=='values':
        doc=v.decode(base64.b64decode(plan['document']),262144);doc['rows'][0]['adjClose']='-1'
        raw=v.canonical(doc);plan.update(document=base64.b64encode(raw).decode(),document_sha=v.digest(raw))
    plan['plan_sha256']='0'*64 if fault=='hash' else v.digest(v.canonical(plan))
    path=private(tmp_path/'malformed.json',v.canonical(plan));lookups=[];connections=[]
    class NoSecrets(dict):
        def __getitem__(self,key):
            if key in ('COLUMNS','LINES'):raise KeyError(key)
            lookups.append(key);raise AssertionError('credential lookup forbidden')
    import psycopg2
    def forbidden(*a,**kw):connections.append(True);raise AssertionError('native connection forbidden')
    with monkeypatch.context() as m:
        m.setattr(os,'environ',NoSecrets());m.setattr(psycopg2,'connect',forbidden)
        assert cli.main(['--connection-env','SYNTHETIC_VINTAGE_CONNECTION',command,'--plan',str(path)])==2
    assert lookups==connections==[]
    output=capsys.readouterr();assert output.out=='' and output.err=='benchmark_vintage_operation_failed\n'

@pytest.mark.parametrize('operation',['import','select'])
@pytest.mark.parametrize('fault',['rollback','uncertain'])
def test_cli_transport_failure_and_fresh_reconciliation(database,cli,monkeypatch,tmp_path,operation,fault,capsys):
    import psycopg2
    plan,vid,_=import_plan(cli,monkeypatch,database,tmp_path)
    role='importer'
    if operation=='select':
        assert invoke(cli,monkeypatch,database,'importer','apply','--plan',plan)==0
        plan,_=selection_plan(cli,monkeypatch,database,tmp_path,vid);role='selector'
    original=psycopg2.connect
    class Cursor:
        def __init__(self,q):self.q=q
        def __getattr__(self,k):return getattr(self.q,k)
        def __enter__(self):self.q.__enter__();return self
        def __exit__(self,*a):return self.q.__exit__(*a)
        def execute(self,text,args=()):
            result=self.q.execute(text,args)
            if fault=='rollback' and text.startswith('INSERT INTO public.benchmark_'):raise OSError('synthetic secret=https://example.invalid?apikey=never-log')
            return result
    class Connection:
        def __init__(self,c):self.c=c
        def __getattr__(self,k):return getattr(self.c,k)
        def cursor(self,*a,**kw):return Cursor(self.c.cursor(*a,**kw))
        def commit(self):
            self.c.commit()
            if fault=='uncertain':raise OSError('synthetic lost acknowledgement')
    with monkeypatch.context() as m:
        m.setattr(psycopg2,'connect',lambda *a,**kw:Connection(original(*a,**kw)))
        assert invoke(cli,monkeypatch,database,role,'apply','--plan',plan)==(3 if fault=='uncertain' else 2)
    out=capsys.readouterr();assert 'never-log' not in out.err+out.out
    assert invoke(cli,monkeypatch,database,'reader','reconcile','--plan',plan)==0
    assert ('complete_committed_'+operation if fault=='uncertain' else 'absent') in capsys.readouterr().out
    expected=(1,13,1,0,0) if operation=='select' and fault=='uncertain' else ((1,13,0,0,0) if operation=='select' or fault=='uncertain' else (0,0,0,0,0))
    assert counts(database)==expected


def test_cli_operation_content_conflicts(database,cli,monkeypatch,tmp_path):
    plan,vid,_=import_plan(cli,monkeypatch,database,tmp_path)
    assert invoke(cli,monkeypatch,database,'importer','apply','--plan',plan)==0
    changed=json.loads(plan.read_text());doc,raw=candidate(GRID,'2026-10',[200+i for i in range(13)])
    changed.update(document=base64.b64encode(doc).decode(),payload=base64.b64encode(raw).decode(),document_sha=v.digest(doc),payload_sha=v.digest(raw))
    changed.pop('plan_sha256');changed['plan_sha256']=v.digest(v.canonical(changed))
    path=private(tmp_path/'changed-import.json',v.canonical(changed))
    for cmd,role in [('apply','importer'),('reconcile','reader')]:
        assert invoke(cli,monkeypatch,database,role,cmd,'--plan',path)==2
    select,eid=selection_plan(cli,monkeypatch,database,tmp_path,vid)
    assert invoke(cli,monkeypatch,database,'selector','apply','--plan',select)==0
    changed=json.loads(select.read_text());changed.pop('plan_sha256');changed['approval_reference']='different synthetic approval'
    changed['plan_sha256']=v.digest(v.canonical(changed));path=private(tmp_path/'changed-select.json',v.canonical(changed))
    for cmd,role in [('apply','selector'),('reconcile','reader')]:
        assert invoke(cli,monkeypatch,database,role,cmd,'--plan',path)==2
    assert counts(database)==(1,13,1,0,0)
