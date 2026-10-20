import copy
from datetime import datetime,timezone,timedelta
import pytest
from factorlab.sue_use_contract import assess, package_from_files, utc, Invalid, production_resolver
from factorlab.sue_plan import load
from sue_use_samples import evidence,attach,publish_files,change,CUTOFF
from monthly_samples import monthly_sample
from scripts.sue_use_assessment import main

@pytest.fixture
def package():return evidence(monthly_sample(2))

def test_post_release_option_a_not_original_consensus(package):
    a=assess(package,CUTOFF)
    assert a['use']=='observed-revised-history-research' and not a['historical_pre_event_qualified']
    assert a['not_before']=='2020-09-02T13:00:00+00:00' and a['rows'][0]['report_date']=='2020-08-01'
    change(package,'policy',lambda x:x.update(use='original-consensus'))
    with pytest.raises(Invalid,match='original_consensus_unsupported'):assess(package,CUTOFF)

@pytest.mark.parametrize('cutoff',['2020-09-02T12:59:59+00:00','2020-08-01T00:00:00+00:00'])
def test_observation_and_validation_cutoff(package,cutoff):
    with pytest.raises(Invalid,match='after_cutoff'):assess(package,cutoff)

@pytest.mark.parametrize('value',[None,'2020-09-02T13:00:00','bad'])
def test_naive_or_invalid_time(package,value):
    change(package,'policy',lambda x:x.update(available_at=value))
    with pytest.raises(Invalid,match='aware_time'):assess(package,CUTOFF)

@pytest.mark.parametrize('which,update,reason',[
 ('issuer',lambda x:x.update(current_holder_security_id=999),'current_issuer_mismatch'),
 ('issuer',lambda x:x.update(continuity='ticker_reuse'),'issuer_continuity'),
 ('events',lambda x:x['records'][0].update(date_meaning='unknown_fiscal_fallback'),'date_link_unresolved'),
 ('events',lambda x:x['records'][0].update(event_link='unresolved_reschedule'),'date_link_unresolved'),
 ('events',lambda x:x['records'].pop(0),'dependency_evidence_incomplete'),
 ('events',lambda x:x['records'][0].update(currency='OTHER'),'incompatible_basis'),
 ('events',lambda x:x['records'][0].update(share_basis='PRE_SPLIT'),'incompatible_basis'),
 ('events',lambda x:x.update(security_id=999),'event_security'),
 ('policy',lambda x:x.update(qualified=True),'invalid_shape'),
])
def test_evidence_refusals(package,which,update,reason):
    change(package,which,update)
    with pytest.raises(Invalid,match=reason):assess(package,CUTOFF)

def test_supported_continuous_rename(package):
    change(package,'issuer',lambda x:x.update(continuity='evidenced_continuous_rename'))
    assert assess(package,CUTOFF)['rows']

def test_zero_negative_null_and_empty_distinct():
    p=monthly_sample(3);p['surprises'][0]['sue']=0;p['surprises'][1]['sue']=-2;p['surprises'][2]['sue']=None
    a=assess(evidence(p),CUTOFF);assert [r['sue'] for r in a['rows']]==['0','-2',None]
    p['surprises']=[];a=assess(evidence(p),CUTOFF)
    assert not a['rows'] and {r['state'] for r in a['outcomes']}=={'validated-empty'} and len(a['scope_ids'])==3

def test_files_public_command_and_no_overwrite(package,tmp_path,capsys):
    ref=publish_files(tmp_path,package);before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    out=tmp_path/'assessment.json'
    assert main(['--root',str(tmp_path),'--manifest',ref,'--cutoff',CUTOFF,'--output',str(out)])==0
    assert out.stat().st_mode&0o777==0o600
    assert main(['--root',str(tmp_path),'--manifest',ref,'--cutoff',CUTOFF,'--output',str(out)])==2
    assert all((tmp_path/n).read_bytes()==b for n,b in before.items())
    assert assess(package_from_files(tmp_path,ref),CUTOFF)==assess(package,CUTOFF)

@pytest.mark.parametrize('mode',['missing','tampered','swapped'])
def test_files_refuse(package,tmp_path,mode):
    ref=publish_files(tmp_path,package);m=load(package['manifest'].encode());p=tmp_path/m['subjects'][0]['issuer']
    if mode=='missing':p.unlink()
    elif mode=='tampered':p.write_text('{}')
    else:p.write_text(package['files'][m['policy']])
    with pytest.raises((Invalid,OSError)):package_from_files(tmp_path,ref)

def test_same_numbers_different_observation_identity():
    p=monthly_sample(1);a=assess(evidence(p,'first'),CUTOFF);b=assess(evidence(p,'second'),CUTOFF)
    assert a['rows']==b['rows'] and a['observation_ids']!=b['observation_ids'] and a['evidence_identity']!=b['evidence_identity']

def test_canonical_time_retains_microseconds():
    a=datetime(2020,9,2,13,0,0,123456,tzinfo=timezone.utc)
    assert utc(a)==utc(a.astimezone(timezone(timedelta(hours=-4))))=='2020-09-02T13:00:00.123456+00:00'

def test_invalid_cli_before_io(monkeypatch):
    import scripts.sue_use_assessment as cli
    monkeypatch.setattr(cli,'package_from_files',lambda *a:pytest.fail('file IO before validation'))
    assert cli.main(['--root','missing','--manifest','bad','--cutoff','bad','--output','missing'])==2
    with pytest.raises(SystemExit) as e:cli.main(['--help'])
    assert e.value.code==0

def test_production_resolution_is_not_enabled():
    with pytest.raises(Invalid,match='production_resolution_unavailable'):production_resolver()

def test_new_path_and_legacy_replay_no_bypass():
    from factorlab.monthly_producer import FrozenMonthlyInputs,prepare_publication,replay_monthly,calculate_factors,plan_universe
    from factorlab.publication_contract import PublicationError
    p=monthly_sample(2);old=replay_monthly(FrozenMonthlyInputs.freeze(p),'a'*40)
    assert not old['publishing_authority']
    with pytest.raises(PublicationError,match='new_execution'):prepare_publication(FrozenMonthlyInputs.freeze(p),'a'*40)
    with pytest.raises(PublicationError,match='new_execution'):calculate_factors(p,plan_universe(p))
    attach(p)
    p['benchmark_identity']={'mode':'invalid'}
    with pytest.raises(PublicationError):prepare_publication(FrozenMonthlyInputs.freeze(p),'a'*40)
    p['surprises'][0]['sue']='999'
    with pytest.raises(Invalid,match='snapshot_rows'):calculate_factors(p,plan_universe(p))


def test_exact_numeric_identity_independent_of_decimal_context():
    from decimal import localcontext, ROUND_UP
    from factorlab.sue_use_contract import normalized_rows
    value='0.1234567890123456789012345678901234567890123456789'
    row=dict(security_id=1,report_date='2020-08-01',eps_actual=value,eps_est='-0.00',sue='1.00')
    with localcontext() as ctx:
        ctx.prec=3;ctx.rounding=ROUND_UP
        result=normalized_rows([row])[0]
    assert result['eps_actual']==value and result['eps_est']=='0' and result['sue']=='1'


def test_fractional_availability_ordering(package):
    change(package,'policy',lambda x:x.update(available_at='2020-09-02T13:00:00.000001+00:00'))
    with pytest.raises(Invalid,match='after_cutoff'):assess(package,'2020-09-02T13:00:00+00:00')
    assert assess(package,'2020-09-02T09:00:00.000001-04:00')['not_before']=='2020-09-02T13:00:00.000001+00:00'


def test_unsupported_time_precision_not_silently_truncated():
    with pytest.raises(Invalid,match='aware_time'):utc('2020-09-02T13:00:00.1234567Z')


def test_direct_v3_calculation_cannot_precede_observation():
    from factorlab.monthly_producer import calculate_factors,plan_universe
    from factorlab.publication_contract import PublicationError
    p=monthly_sample(1);attach(p);p['observed_at']='2020-08-01T00:00:00Z'
    with pytest.raises(PublicationError,match='snapshot_availability'):calculate_factors(p,plan_universe(p))


def test_public_cli_already_imported_client_no_credentials(monkeypatch):
    import factorlab.fmp_client as existing
    import os
    monkeypatch.setattr(existing,'FMPClient',lambda *a,**kw:pytest.fail('provider client'))
    monkeypatch.setattr(os,'getenv',lambda *a,**kw:pytest.fail('credential lookup'))
    assert main(['--root','absent','--manifest','invalid','--cutoff',CUTOFF,'--output','absent'])==2


def test_no_monthly_evidence_refuses_before_connect_or_refresh():
    from factorlab.monthly_scheduler import run_monthly_period
    from factorlab.publication_contract import PublicationError
    from uuid import UUID
    def denied(*a,**k):pytest.fail('connect or refresh before qualification')
    with pytest.raises(PublicationError,match='sue_use_evidence_required'):
        run_monthly_period(denied,'2020-09','a'*40,selection_event=UUID(int=1),vintage_id=UUID(int=2),refresh=denied)


def test_old_provider_last_checked_never_backdates_use():
    p=monthly_sample(1);pack=evidence(p,last_checked='1999-01-01')
    assert '1999-01-01' in str(pack)
    a=assess(pack,CUTOFF)
    assert a['not_before']=='2020-09-02T13:00:00+00:00'
    with pytest.raises(Invalid,match='after_cutoff'):assess(pack,'2020-08-02T00:00:00Z')


def test_insufficient_history_distinct_from_empty_or_missing_pair():
    a=assess(evidence(monthly_sample(1),omit_first_prior=True),CUTOFF)
    assert len(a['rows'])==1 and a['rows'][0]['sue'] is None
    assert a['outcomes'][0]['state']=='incomplete-history'
    assert a['outcomes'][0]['reasons'][-1]=='insufficient_history'


def test_receipt_failure_cannot_be_relabelled_empty(package):
    def corrupt(o):o['lineage']['body']['receipt']['body']['state']='request-failure'
    change(package,'observation',corrupt)
    with pytest.raises(Invalid):assess(package,CUTOFF)


@pytest.mark.parametrize('value',['1e100000000','1e-100000000'])
def test_numeric_identity_expansion_is_bounded(value):
    from factorlab.sue_use_contract import normalized_rows
    with pytest.raises(Invalid,match='numeric_identity_bound'):
        normalized_rows([dict(security_id=1,report_date='2020-08-01',eps_actual=value,eps_est=None,sue=None)])

@pytest.mark.parametrize('which,field',[('issuer','security_id'),('issuer','current_holder_security_id'),('events','security_id')])
def test_boolean_identity_is_not_security_one(package,which,field):
    change(package,which,lambda x:x.update({field:True}))
    with pytest.raises(Invalid,match='current_issuer_mismatch|event_security'):assess(package,CUTOFF)


def test_public_cli_reports_safe_actionable_refusal(package,tmp_path,capsys):
    change(package,'issuer',lambda x:x.update(continuity='unresolved'))
    ref=publish_files(tmp_path,package)
    assert main(['--root',str(tmp_path),'--manifest',ref,'--cutoff',CUTOFF,'--output',str(tmp_path/'out')])==2
    assert capsys.readouterr().out.strip()=='SUE use assessment refused: sue_use_issuer_continuity'
    assert not (tmp_path/'out').exists()
