"""Synthetic retained mapping syntax is not provider-request authorization."""
import copy
import pytest
from factorlab import sue_plan as p
from factorlab.sue_store import read,write_json
from factorlab.sue_proposal import verify_proposal
from scripts.sue_acquisition import main
from tests.test_sue_acquisition import scope,limits,Clock,Transport,events


def mixed():
    s=scope()
    for i,(symbol,eligible,held) in enumerate([('SYNTH SPACE',True,True),('SYNTH/SLASH',False,True),('SYNTH OTHER',True,False)]):
        m=copy.deepcopy(s['members'][0]);m.update(id='RETAINED_'+str(i),eligible=eligible,held=held,before_complete=False)
        m['windows'][0]['symbol']=symbol;s['members'].append(m)
    return s


def test_retained_mapping_roundtrip():
    s=mixed();plan=p.make_plan(s,['SYNTHETIC_1'],limits());again=p.load(p.canonical(plan));p.validate_plan(again)
    assert again['body']['scope']==s and again['body']['requests'][0]['symbol']=='SYNTH_X'
    assert again['body']['coverage']['eligible']==3


@pytest.mark.parametrize('symbol',['SYNTH SPACE','SYNTH/SLASH',' SYNTH ','SYNTH//X'])
def test_selected_request_remains_conservative(symbol):
    s=scope();s['members'][0]['windows'][0]['symbol']=symbol
    p.validate_scope(s)
    with pytest.raises(p.Invalid,match='invalid_identifier'):p.make_plan(s,['SYNTHETIC_1'],limits())


@pytest.mark.parametrize('symbol',['','X'*129,'\x00','A\nB','A\tB','A\rB','A\x7fB','A\ud800B','SYNTH?KEY','SYNTH&X','SYNTH\\X','SYNTHé','SYNTH\u00a0X',None,23,True])
def test_malformed_mapping_refused(symbol):
    s=mixed();s['members'][1]['windows'][0]['symbol']=symbol
    with pytest.raises(p.Invalid):p.validate_scope(s)


def test_mapping_bound_verbatim():
    s=mixed();s['members'][1]['windows'][0]['symbol']='A'*126+' /'
    plan=p.make_plan(s,['SYNTHETIC_1'],limits());assert plan['body']['scope']==s


@pytest.mark.parametrize('member',['RETAINED_0','RETAINED_1'])
def test_held_ineligible_selection_denied(member):
    with pytest.raises(p.Invalid,match='identity_held'):p.make_plan(mixed(),[member],limits())


@pytest.mark.parametrize('value',['space id','slash/id','../x','X\nY'])
def test_generic_identifier_stays_strict(value):
    with pytest.raises(p.Invalid):p.text(value)


@pytest.mark.parametrize('change',['cross','reversed','overlap','tie'])
def test_raw_window_identity_checks(change):
    s=mixed();a=s['members'][1];b=s['members'][2]
    if change=='cross':b['windows'][0]['symbol']=a['windows'][0]['symbol'];b['held']=False
    elif change=='reversed':a['held']=False;a['windows'][0]['end']='2010-01-01'
    else:
        a['held']=False;w=copy.deepcopy(a['windows'][0]);w['start']='2021-01-01'
        if change=='overlap':w['end']='2026-09-29'
        a['windows'].append(w)
    with pytest.raises(p.Invalid):p.make_plan(s,['SYNTHETIC_1'],limits())
    a['held']=b['held']=True
    plan=p.make_plan(s,['SYNTHETIC_1'],limits());assert plan['body']['coverage']['identity_flags']


def test_full_sized_public_plan(tmp_path):
    tmp_path.chmod(0o700);s=mixed();seed=scope()['members'][0]
    for i in range(len(s['members']),5727):
        m=copy.deepcopy(seed);m.update(id='SYNTH_'+str(i),eligible=i<2767,held=False,before_complete=False);m['windows'][0]['symbol']='SYNTHSYM_'+str(i)
        if i<754:m['before']=[{'date':'2026-09-30','actual':'0','estimate':'0','sue':'-1'}]
        s['members'].append(m)
    assert sum(x['eligible'] for x in s['members'])==2766
    for n,v in [('scope',s),('selected',['SYNTHETIC_1']),('limits',limits())]:write_json(tmp_path/n,v)
    assert main(['plan','--scope',str(tmp_path/'scope'),'--selected',str(tmp_path/'selected'),'--limits',str(tmp_path/'limits'),'--output',str(tmp_path/'plan')])==0
    plan=p.load(read(tmp_path/'plan'));p.validate_plan(plan);assert plan['body']['scope']==s
    assert plan['body']['coverage']['eligible']==2766 and plan['body']['coverage']['usable']==750


def test_public_mocked_lifecycle_preserves_mapping(tmp_path):
    tmp_path.chmod(0o700);s=mixed()
    for n,v in [('scope',s),('selected',['SYNTHETIC_1']),('limits',limits())]:write_json(tmp_path/n,v)
    assert main(['plan','--scope',str(tmp_path/'scope'),'--selected',str(tmp_path/'selected'),'--limits',str(tmp_path/'limits'),'--output',str(tmp_path/'plan')])==0
    original=read(tmp_path/'plan');clock=Clock();t=Transport([(200,p.canonical(events()))]);common=['--plan',str(tmp_path/'plan'),'--root',str(tmp_path),'--operation','synthetic-operation']
    assert main(['acquire',*common,'--approval','synthetic-review','--credential-env','SYNTH_KEY'],transport_factory=lambda:t,credential=lambda _:'invented-only',clock=clock,sleep=clock.sleep)==0
    assert len(t.calls)==1 and t.calls[0][0]=='SYNTH_X'
    assert main(['reconcile',*common,'--output',str(tmp_path/'reconciled')])==0
    assert main(['propose',*common,'--output',str(tmp_path/'proposal')])==0
    result=verify_proposal(tmp_path/'proposal');assert len(result['body']['shards'])==1 and result['body']['coverage']['eligible']==3
    assert read(tmp_path/'plan')==original and p.load(original)['body']['scope']==s


@pytest.mark.parametrize('forgery',['request_only','scope_and_request','held_selection'])
def test_forged_plan_refuses_before_credentials(tmp_path,forgery):
    tmp_path.chmod(0o700);plan=p.make_plan(mixed(),['SYNTHETIC_1'],limits());b=plan['body']
    if forgery=='held_selection':b['selected']=['RETAINED_0']
    else:
        b['requests'][0]['symbol']='SYNTH/BAD'
        if forgery=='scope_and_request':
            b['scope']['members'][0]['windows'][0]['symbol']='SYNTH/BAD';b['requests'][0]['attributions'][0]['symbol']='SYNTH/BAD'
    plan=p.seal('sue-request-plan',b);write_json(tmp_path/'plan',plan);before=set(tmp_path.iterdir())
    def denied(*a,**k):raise AssertionError('credential/transport accessed')
    assert main(['acquire','--plan',str(tmp_path/'plan'),'--root',str(tmp_path),'--operation','op','--approval','synthetic','--credential-env','SYNTH_KEY'],credential=denied,transport_factory=denied)==2
    assert set(tmp_path.iterdir())==before
