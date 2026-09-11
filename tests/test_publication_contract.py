import copy
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from factorlab.publication_contract import (PublicationError, eligible_receipt,
    instant, normalize_spec, numeric, prepare_rows, spec_fingerprint)
from publication_samples import sample


def test_fingerprint_and_row_order_are_deterministic():
    s, u, f = sample()
    assert spec_fingerprint(s) == spec_fingerprint(dict(reversed(list(s.items()))))
    assert prepare_rows(s, u, f) == prepare_rows(s, reversed(u), reversed(f))


def test_zero_is_a_value_not_a_missing_value():
    s, u, f = sample()
    a, b = prepare_rows(s, u, f)
    assert b[0]["raw"] == "0" and b[0]["missing_reason"] is None
    assert numeric(Decimal("-0.000")) == "0"


@pytest.mark.parametrize("value", [float('nan'), float('inf'), '-Infinity', True, {}, []])
def test_unsafe_numeric_values_are_rejected(value):
    with pytest.raises(PublicationError):
        numeric(value)


@pytest.mark.parametrize("value", ['2026-09-01', datetime(2026, 9, 1), None])
def test_timezone_is_mandatory(value):
    with pytest.raises(PublicationError):
        instant(value)


def test_equivalent_timezones_have_one_spec_fingerprint():
    s, _, _ = sample()
    s2 = copy.deepcopy(s)
    s2['inputs_observed_at'] = '2020-09-02T09:43:00-04:00'
    assert spec_fingerprint(s) == spec_fingerprint(s2)


@pytest.mark.parametrize("field,value", [('source_revision','short'), ('expected_universe',True),
    ('expected_investable',0), ('expected_universe',100001), ('asof','2020-99-01'),
    ('dataset','another'), ('factor_contract',{}), ('input_fingerprints',{})])
def test_malformed_spec_fails_closed(field, value):
    s, _, _ = sample()
    s[field] = value
    with pytest.raises(PublicationError):
        normalize_spec(s)


def test_extra_metadata_is_not_silently_persisted():
    s, _, _ = sample()
    s['connection_url'] = 'must-not-be-stored'
    with pytest.raises(PublicationError, match='invalid_spec_keys'):
        normalize_spec(s)


def test_formula_revision_is_not_silently_accepted():
    s, _, _ = sample()
    s['factor_contract']['sue']['version'] = 2
    with pytest.raises(PublicationError, match='factor_contract_drift'):
        normalize_spec(s)


def test_truncated_universe_and_factor_panels_fail():
    s, u, f = sample()
    with pytest.raises(PublicationError, match='universe_count_mismatch'):
        prepare_rows(s, u[:-1], f)
    with pytest.raises(PublicationError, match='factor_count_mismatch'):
        prepare_rows(s, u, f[:-1])


@pytest.mark.parametrize("part", ['universe','factors'])
def test_dates_and_duplicates_are_not_coerced(part):
    s, u, f = sample()
    rows = u if part == 'universe' else f
    rows[0]['asof'] = '2020-07-31'
    with pytest.raises(PublicationError, match='date_mismatch'):
        prepare_rows(s, u, f)
    rows[0]['asof'] = s['asof']
    rows[1] = dict(rows[0])
    with pytest.raises(PublicationError, match='duplicate'):
        prepare_rows(s, u, f)


def test_missingness_is_explicit_and_partial_missingness_is_not_zero_filled():
    s, u, f = sample()
    f[0].update(raw=None, rank_norm=None, z_sector=None, z_sector_size=None,
                missing_reason='insufficient_history')
    _, rows = prepare_rows(s, u, f)
    assert rows[0]['raw'] is None and rows[0]['missing_reason'] == 'insufficient_history'
    f[0]['missing_reason'] = None
    with pytest.raises(PublicationError, match='unexplained_missing_factor'):
        prepare_rows(s, u, f)


def test_each_required_factor_needs_nonnull_values():
    s, u, f = sample()
    for r in f:
        if r['factor_id'] == 'sue':
            r.update(raw=None, rank_norm=None, z_sector=None, z_sector_size=None,
                     missing_reason='source_value_missing')
    with pytest.raises(PublicationError, match='all_null_required_factor'):
        prepare_rows(s, u, f)


def test_normalized_values_require_raw_lineage():
    s, u, f = sample()
    f[0]['raw'] = None
    with pytest.raises(PublicationError, match='invalid_factor_lineage'):
        prepare_rows(s, u, f)


def test_excluded_securities_cannot_supply_factor_rows():
    s, u, f = sample()
    f[0]['security_id'] = 3
    with pytest.raises(PublicationError, match='outside_investable'):
        prepare_rows(s, u, f)


def test_september_1_cutoff_cannot_use_september_2_publication():
    s, _, _ = sample()
    receipt = dict(asof='2026-08-31', available_at='2026-09-02T13:43:08+00:00',
                   factor_contract=s['factor_contract'])
    assert not eligible_receipt(receipt, date(2026,8,31), instant('2026-09-01T23:07:20Z'))
    assert eligible_receipt(receipt, date(2026,8,31), instant('2026-09-02T23:09:55Z'))
    assert not eligible_receipt(receipt, date(2026,7,31), instant('2026-09-02T23:09:55Z'))


def test_cutoff_boundary_is_inclusive_but_never_uses_the_asof_date_as_publication_time():
    s, _, _ = sample()
    t = datetime(2026,9,2,tzinfo=timezone.utc)
    receipt = dict(asof='2026-08-31', available_at=t, factor_contract=s['factor_contract'])
    assert eligible_receipt(receipt,'2026-08-31',t)
    receipt['available_at'] = datetime(2026,9,3,tzinfo=timezone.utc)
    assert not eligible_receipt(receipt,'2026-08-31',t)


def test_high_precision_numeric_input_is_not_rounded_by_decimal_context():
    assert numeric(Decimal('123456789012345678901234567890.1234000')) == '123456789012345678901234567890.1234'
