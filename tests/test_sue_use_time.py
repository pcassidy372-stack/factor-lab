"""Exact numeric receipt-time authority; all observations are synthetic."""
import pytest
from factorlab.sue_use_contract import assess,Invalid
from sue_time_samples import timed_package
from sue_use_samples import change,CUTOFF


def test_fractional_finish_after_whole_second_cutoff_refuses():
    with pytest.raises(Invalid,match='after_cutoff'):
        assess(timed_package(),'2020-09-02T12:00:00Z')


def test_fractional_finish_beyond_issuer_right_edge_refuses():
    package=timed_package()
    change(package,'issuer',lambda x:x.update(valid_through='2020-09-02T12:00:00Z'))
    with pytest.raises(Invalid,match='issuer_observation_window'):assess(package,CUTOFF)

from decimal import Decimal,localcontext,ROUND_DOWN,ROUND_UP
from factorlab.sue_use_contract import stamp,exact_epoch,instant_epoch,package_from_files
from factorlab.sue_plan import canonical,load,digest
from sue_use_samples import publish_files
from scripts.sue_use_assessment import main

@pytest.mark.parametrize('epoch,expected',[
 ('1599048000','2020-09-02T12:00:00+00:00'),
 ('1599048000.000001','2020-09-02T12:00:00.000001+00:00'),
 ('1599048000.0000004','2020-09-02T12:00:00.000001+00:00'),
 ('1599048000.9999999','2020-09-02T12:00:01+00:00'),
 ('1599091199.9999999','2020-09-03T00:00:00+00:00'),
 ('-0.0000004','1970-01-01T00:00:00+00:00'),
 ('-0.0000014','1969-12-31T23:59:59.999999+00:00'),
 ('-62135596800','0001-01-01T00:00:00+00:00'),
 ('253402300799.999999','9999-12-31T23:59:59.999999+00:00'),
])
def test_directed_microsecond_output(epoch,expected):
    assert stamp(Decimal(epoch))==expected
    assert instant_epoch(expected)>=exact_epoch(Decimal(epoch))
    assert instant_epoch(expected)-exact_epoch(Decimal(epoch))<exact_epoch(Decimal('0.000001'))

@pytest.mark.parametrize('finish,cutoff,allowed',[
 ('1599047999.9999999','2020-09-02T12:00:00Z',True),
 ('1599048000','2020-09-02T12:00:00Z',True),
 ('1599048000.0000004','2020-09-02T12:00:00Z',False),
 ('1599048000.0000004','2020-09-02T12:00:00.000001Z',True),
 ('1599048000.000001','2020-09-02T08:00:00.000001-04:00',True),
])
def test_before_at_after_exact_cutoffs(finish,cutoff,allowed):
    package=timed_package(start='1599047999',finish=finish)
    if allowed:assert assess(package,cutoff)['not_before']==stamp(Decimal(finish))
    else:
        with pytest.raises(Invalid,match='after_cutoff'):assess(package,cutoff)

@pytest.mark.parametrize('start,finish,left,right,allowed',[
 ('1599047999.9999996','1599048000','2020-09-02T12:00:00Z','2020-09-02T12:00:01Z',False),
 ('1599048000','1599048000.0000004','2020-09-02T12:00:00Z','2020-09-02T12:00:00.000001Z',True),
 ('1599048000','1599048000.000001','2020-09-02T12:00:00Z','2020-09-02T12:00:00.000001Z',True),
 ('1599048000','1599048000.0000011','2020-09-02T12:00:00Z','2020-09-02T12:00:00.000001Z',False),
])
def test_exact_issuer_containment_both_edges(start,finish,left,right,allowed):
    package=timed_package(start,finish)
    change(package,'issuer',lambda x:x.update(valid_from=left,valid_through=right))
    if allowed:assert assess(package,CUTOFF)['rows']
    else:
        with pytest.raises(Invalid,match='issuer_observation_window'):assess(package,CUTOFF)

@pytest.mark.parametrize('value',[None,True,1.0,'bad','NaN','Infinity','-Infinity','1e100000000','1e-100000000','1'*129,'-62135596800.1','253402300799.9999991',10**100])
def test_invalid_bounded_epochs_refuse(value):
    with pytest.raises(Invalid):stamp(value)

@pytest.mark.parametrize('precision,rounding',[(1,ROUND_DOWN),(3,ROUND_UP),(50,ROUND_DOWN)])
def test_decimal_context_and_original_hashes_unchanged(precision,rounding):
    package=timed_package();raw=canonical(package);expected=assess(package,CUTOFF)
    with localcontext() as ctx:
        ctx.prec=precision;ctx.rounding=rounding
        assert assess(package,CUTOFF)==expected
        assert stamp(Decimal('1599048000.0000004'))=='2020-09-02T12:00:00.000001+00:00'
    assert canonical(package)==raw
    assert canonical(load(raw))==raw
    assert b'1599048000.0000004' in raw


def test_public_command_exact_boundary_and_deterministic_replay(tmp_path):
    package=timed_package();ref=publish_files(tmp_path,package);before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    out=tmp_path/'too-early.json'
    args=['--root',str(tmp_path),'--manifest',ref,'--output',str(out),'--cutoff','2020-09-02T12:00:00Z']
    assert main(args)==2 and not out.exists()
    args[-1]='2020-09-02T08:00:00.000001-04:00'
    assert main(args)==0
    actual=load(out.read_bytes());assert actual==assess(package_from_files(tmp_path,ref),'2020-09-02T12:00:00.000001Z')
    assert all((tmp_path/n).read_bytes()==b for n,b in before.items())
    assert out.stat().st_mode&0o777==0o600


def test_legacy_assessment_version_is_not_implicitly_upgraded():
    package=timed_package()
    manifest=load(package['manifest'].encode());manifest['version']='sue-observed-research-use-v1';package['manifest']=canonical(manifest).decode()
    with pytest.raises(Invalid,match='sue_use_version'):assess(package,CUTOFF)


def test_prepare_and_direct_calculation_use_conservative_completion():
    from factorlab.monthly_producer import prepare_publication,calculate_factors,plan_universe,FrozenMonthlyInputs
    from factorlab.publication_contract import PublicationError
    from monthly_samples import monthly_sample
    from sue_use_samples import attach
    p=monthly_sample(1);attach(p);p['sue_evidence']=timed_package();p['research_cutoff']=CUTOFF;p['observed_at']='2020-09-02T12:00:00Z'
    for call in [lambda:prepare_publication(FrozenMonthlyInputs.freeze(p),'a'*40),lambda:calculate_factors(p,plan_universe(p))]:
        with pytest.raises(PublicationError,match='snapshot_availability'):call()
    p['observed_at']='2020-09-02T12:00:00.000001Z'
    assert calculate_factors(p,plan_universe(p))
