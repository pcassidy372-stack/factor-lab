import copy
from datetime import date

import numpy as np
import pytest

from factorlab.monthly_producer import (FrozenMonthlyInputs, calculate_factors,
    fingerprint, plan_universe, prepare_publication)
from factorlab.monthly_snapshot import prior_month
from factorlab.publication_contract import PublicationError, prepare_rows
from monthly_samples import monthly_sample


@pytest.fixture
def payload():
    return monthly_sample()


def test_actual_calculation_builds_complete_explicit_five_factor_grid(payload):
    frozen = FrozenMonthlyInputs.freeze(payload)
    spec, producer = prepare_publication(frozen, 'a'*40)
    u, f = producer(spec)
    assert len(u) == 100 and len(f) == 500
    assert spec['expected_universe'] == spec['expected_investable'] == 100
    assert len(prepare_rows(spec,u,f)[1]) == 500
    assert sum(r['missing_reason']=='not_applicable' for r in f) == 20
    assert len(set(spec['input_fingerprints'].values())) == 7


def test_freeze_copies_inputs_and_does_not_expose_mutable_payload(payload):
    frozen = FrozenMonthlyInputs.freeze(payload)
    before = frozen.unpack()
    payload['caps'][0]['mktcap'] = 1
    changed = frozen.unpack(); changed['caps'][0]['mktcap'] = 2
    assert frozen.unpack() == before


def test_factor_zero_is_not_missing(payload):
    u = plan_universe(payload)
    f,_ = calculate_factors(payload,u)
    zero = next(r for r in f if r['security_id']==30 and r['factor_id']=='sue')
    assert zero['raw'] == 0 and zero['missing_reason'] is None


def test_momentum_is_monthly_raw_twelve_minus_one_before_same_v2_normalization(payload):
    u = plan_universe(payload)
    f,_ = calculate_factors(payload,u)
    h={r['d']:r['tr'] for r in payload['returns'] if r['security_id']==54}
    expected=h[payload['month_grid'][-2]]/h[payload['month_grid'][0]]-1
    observed=next(r for r in f if r['security_id']==54 and r['factor_id']=='mom_12_1')['raw']
    assert observed == pytest.approx(expected)


def test_exact_gp_a_and_issuance_formula_at_interior_not_winsor_tail(payload):
    u=plan_universe(payload); f,_=calculate_factors(payload,u)
    lookup={(r['security_id'],r['factor_id']):r['raw'] for r in f}
    sec=47
    assert lookup[sec,'gp_a']==pytest.approx(sum(100+sec*(j+1) for j in range(2,6))/(1000+2*sec))
    assert lookup[sec,'net_issuance']==pytest.approx((1000+5*sec)/(1000+sec)-1)


def test_volatility_has_sample_degrees_of_freedom_and_annualization(payload):
    u=plan_universe(payload); f,_=calculate_factors(payload,u)
    h={r['d']:r['tr'] for r in payload['returns'] if r['security_id']==53}
    g=payload['month_grid']; rets=[h[b]/h[a]-1 for a,b in zip(g,g[1:])]
    observed=next(r for r in f if r['security_id']==53 and r['factor_id']=='vol_12m')['raw']
    assert observed==pytest.approx(np.std(rets,ddof=1)*np.sqrt(12))


def test_legacy_sixty_percent_rule_preserved(payload):
    payload['surprises']=payload['surprises'][:60]
    u=plan_universe(payload); factors,_=calculate_factors(payload,u)
    assert sum(r['raw'] is None for r in factors if r['factor_id']=='sue')==40
    payload['surprises'].pop()
    with pytest.raises(PublicationError,match='insufficient_factor_coverage_sue'):
        calculate_factors(payload,u)


@pytest.mark.parametrize('fid', ['gp_a','sue','mom_12_1','vol_12m','net_issuance'])
def test_all_missing_required_factor_refuses_publication(payload,fid):
    if fid in ('gp_a','net_issuance'): payload['fundamentals']=[]
    elif fid=='sue': payload['surprises']=[]
    else: payload['returns']=[]
    with pytest.raises(PublicationError,match='insufficient_factor_coverage'):
        calculate_factors(payload,plan_universe(payload))


@pytest.mark.parametrize('mutate,match', [
    (lambda p:p['month_grid'].pop(2),'thirteen_month'),
    (lambda p:p['month_grid'].__setitem__(2,'2018-10-31'),'missing_month'),
    (lambda p:p['registry'][0].__setitem__('formula_hash','wrong'),'registry_drift'),
    (lambda p:p['registry'][0]['params'].__setitem__('excl_financials',False),'eligibility_drift'),
    (lambda p:p['returns'][0].__setitem__('tr',-1),'invalid_return'),
    (lambda p:p['surprises'][0].__setitem__('report_date','2999-01-01'),'future_surprise'),
])
def test_bad_factor_inputs_fail_closed(payload,mutate,match):
    mutate(payload)
    with pytest.raises(PublicationError,match=match): calculate_factors(payload,plan_universe(payload))


def test_liquidity_is_upper_median_of_up_to_sixty_three_bars(payload):
    for index,r in enumerate(r for r in payload['prices'] if r['security_id']==1):
        r['close']=10; r['volume']=index+1
    row=plan_universe(payload)[0]
    assert row['adv_63d']==320 and not row['in_universe']


def test_less_than_forty_price_bars_or_missing_volume_is_explicitly_excluded(payload):
    payload['prices']=[r for r in payload['prices'] if r['security_id']!=1]+[
        dict(security_id=1,d='2020-08-31',close=10,volume=1000000)]
    next(r for r in payload['prices'] if r['security_id']==2)['volume']=None
    rows=plan_universe(payload)
    assert not rows[0]['in_universe'] and rows[0]['adv_63d'] is None
    assert not rows[1]['in_universe'] and rows[1]['adv_63d'] is None


def test_size_ties_are_deterministic_and_preserve_ntile_sizes(payload):
    for r in payload['caps']: r['mktcap']=400e6
    payload['caps'].reverse()
    rows=plan_universe(payload)
    assert [sum(r['size_bucket']==b for r in rows) for b in ('small','mid','large')]==[34,33,33]
    assert rows[33]['size_bucket']=='small' and rows[34]['size_bucket']=='mid'


def test_duplicate_month_caps_resolve_to_latest_on_or_before_asof(payload):
    payload['caps'].append(dict(security_id=1,asof='2020-08-03',mktcap=1))
    assert plan_universe(payload)[0]['mktcap']==401e6
    payload['caps'].append(dict(security_id=1,asof='2020-09-01',mktcap=900e6))
    with pytest.raises(PublicationError,match='cap_date'): plan_universe(payload)


def test_excluded_instruments_and_adrs_are_not_silently_admitted(payload):
    payload['symbols'][0]['symbol']='FAKE-WT'
    payload['adr_history']=[dict(security_id=2,is_adr=True)]
    u=plan_universe(payload)
    assert not u[0]['in_universe'] and not u[1]['in_universe']


def test_future_price_is_not_selected_from_same_month(payload):
    payload['prices'][0]['d']='2020-09-01'
    with pytest.raises(PublicationError,match='future_price'): plan_universe(payload)


def test_accepted_later_restatement_not_selected_by_old_asof(payload):
    original,_=calculate_factors(payload,plan_universe(payload))
    new=copy.deepcopy(payload['fundamentals'][5]); new.update(vintage_id=2,accepted_date='2020-09-01T12:00:00+00:00',gross_profit=1e9)
    payload['fundamentals'].append(new)
    after,_=calculate_factors(payload,plan_universe(payload))
    assert after==original


def test_hash_covers_source_vintage_and_classifications_without_rounding(payload):
    a,_=prepare_publication(FrozenMonthlyInputs.freeze(payload),'a'*40)
    payload['fundamentals'][0]['source_hash']='changed'
    b,_=prepare_publication(FrozenMonthlyInputs.freeze(payload),'a'*40)
    assert a['input_fingerprints']['fundamentals']!=b['input_fingerprints']['fundamentals']
    assert fingerprint({'a':1.00000000000001})!=fingerprint({'a':1.00000000000002})


@pytest.mark.parametrize('bad',['2026-13','abc','2026-9','2026-09-01',None])
def test_invalid_period_never_selects_a_different_month(bad):
    with pytest.raises(ValueError): prior_month(bad)


def test_january_period_targets_december():
    assert prior_month('2026-01')==(date(2025,12,1),date(2026,1,1))


def test_missing_profile_is_explicit_unknown_not_fabricated_sector(payload):
    payload['profiles']=[r for r in payload['profiles'] if r['security_id']!=1]
    assert plan_universe(payload)[0]['sector']=='Unknown'


def test_unrecognized_return_method_is_not_silently_consumed(payload):
    payload['returns'][0]['method_version']='unknown-method'
    with pytest.raises(PublicationError,match='unknown_total_return_method'):
        calculate_factors(payload,plan_universe(payload))


def test_missing_code_revision_refused_before_connection():
    from factorlab.monthly_scheduler import run_monthly_period
    def forbidden(): raise AssertionError('invalid source opened database')
    with pytest.raises(PublicationError,match='explicit_factorlab_code_sha_required'):
        run_monthly_period(forbidden,'2026-09','')
