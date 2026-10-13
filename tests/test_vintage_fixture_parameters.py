"""CI-shaped control parameters; application explicit-target policy stays strict."""
import os
from uuid import uuid4
import pytest
from psycopg2.extensions import make_dsn
from vintage_database import fixture_options
from test_vintage_cli_postgres import cli, private
from vintage_samples import candidate
from test_benchmark_loader import GRID
from factorlab import benchmark_vintages as v

BASE = dict(host='127.0.0.1', dbname='factorlab_ci', user='factorlab_ci',
            password='SYNTHETIC-not-a-credential')

@pytest.mark.parametrize('port', [None, '5432', '25432'])
@pytest.mark.parametrize('mode', [None, 'disable', 'prefer', 'require', 'verify-ca', 'verify-full'])
def test_fixture_materializes_only_accepted_defaults(monkeypatch, port, mode):
    import psycopg2
    def forbidden(*a, **kw):
        pytest.fail('pure parameter validation must not connect')
    monkeypatch.setattr(psycopg2, 'connect', forbidden)
    supplied = dict(BASE)
    if port is not None: supplied['port'] = port
    if mode is not None: supplied['sslmode'] = mode
    result = fixture_options(make_dsn(**supplied))
    assert result == dict(BASE, port=port or '5432', sslmode=mode or 'prefer')
    assert supplied == {k: result[k] for k in supplied}

@pytest.mark.parametrize('change', [
    {'host': 'remote.invalid'}, {'host': '127.0.0.1,remote.invalid'},
    {'hostaddr': '127.0.0.1'}, {'service': 'foreign'},
    {'dbname': 'other'}, {'user': 'postgres'}, {'password': ''},
    {'port': '5432,5433'}, {'port': '0'}, {'port': '65536'},
    {'sslmode': 'allow'}, {'options': '-c search_path=public'},
])
def test_fixture_rejects_invalid_target_before_connection(monkeypatch, change):
    import psycopg2
    calls = []
    def forbidden(*a, **kw):
        calls.append(True)
        raise AssertionError('connection forbidden')
    monkeypatch.setattr(psycopg2, 'connect', forbidden)
    with pytest.raises(ValueError, match='Explicit loopback factorlab_ci'):
        fixture_options(make_dsn(**dict(BASE, **change)))
    assert calls == []

@pytest.mark.parametrize('missing', ['port', 'sslmode'])
def test_user_cli_still_requires_explicit_parameters(cli, monkeypatch, tmp_path, capsys, missing):
    import psycopg2
    d, p = candidate(GRID, '2026-10')
    doc = private(tmp_path/'candidate.json', d)
    payload = private(tmp_path/'payload.json', p)
    cfg = dict(BASE, port='5432', sslmode='prefer')
    del cfg[missing]
    monkeypatch.setenv('SYNTHETIC_VINTAGE_CONNECTION', make_dsn(**cfg))
    calls = []
    classifications = []
    original_require = v.require
    def observed_require(condition, code):
        if not condition: classifications.append(code)
        return original_require(condition, code)
    def forbidden(*a, **kw):
        calls.append(True)
        raise AssertionError('native connection forbidden')
    monkeypatch.setattr(v, 'require', observed_require)
    monkeypatch.setattr(psycopg2, 'connect', forbidden)
    output = tmp_path/'plan.json'
    assert cli.main(['--connection-env', 'SYNTHETIC_VINTAGE_CONNECTION',
        'import-plan', '--candidate', str(doc), '--payload', str(payload),
        '--document-sha256', v.digest(d), '--payload-sha256', v.digest(p),
        '--operation-id', str(uuid4()), '--output', str(output)]) == 2
    assert calls == []
    assert classifications == ['explicit_target_required']
    assert not output.exists()
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == 'benchmark_vintage_operation_failed\n'
