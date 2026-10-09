"""Opt-in disposable PostgreSQL integration tests, never application DATABASE_URL."""
import copy
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_reliability import Client, statements

pytestmark = pytest.mark.postgres


@pytest.fixture
def pg(monkeypatch):
    url = os.environ.get("FACTORLAB_TEST_DATABASE_URL")
    if not url:
        pytest.skip("No explicit disposable PostgreSQL target; not an integration pass")
    import psycopg2  # Explicit target: a missing driver must fail, not silently skip.
    from psycopg2 import sql
    from psycopg2.extensions import parse_dsn
    opts = parse_dsn(url)
    if (opts.get("host") != "127.0.0.1" or opts.get("dbname") != "factorlab_ci"
            or opts.get("user") != "factorlab_ci"
            or set(opts) - {"host", "dbname", "user", "password", "port", "sslmode"}):
        pytest.fail("Test target must be explicit loopback factorlab_ci; target withheld")
    from factorlab import ingest
    from factorlab.migrations import MIGRATIONS
    schema = "fl_test_" + uuid.uuid4().hex
    def connect():
        cx = psycopg2.connect(url, connect_timeout=5)
        with cx.cursor() as cur:
            cur.execute(sql.SQL("SET search_path TO {}, pg_catalog").format(sql.Identifier(schema)))
        cx.commit()
        return cx
    admin = psycopg2.connect(url, connect_timeout=5)
    created = False
    try:
        with admin:
            with admin.cursor() as cur:
                cur.execute("SELECT current_database(), current_user")
                assert cur.fetchone() == ("factorlab_ci", "factorlab_ci")
                cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        created = True
        setup = connect()
        try:
            with setup:
                with setup.cursor() as cur:
                    cur.execute(MIGRATIONS[1])
                    cur.execute("INSERT INTO issuers (issuer_id,cik,name) VALUES (1,'123','Synthetic')")
                    cur.execute("INSERT INTO securities (security_id,issuer_id) VALUES (1,1)")
        finally:
            setup.close()
        monkeypatch.setattr(ingest, "conn", connect)
        yield connect
    finally:
        if created:
            with admin:
                with admin.cursor() as cur:
                    cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


def read(pg):
    cx = pg()
    try:
        with cx.cursor() as cur:
            cur.execute("SELECT vintage_id, source_hash, currency, net_income FROM fundamentals_q ORDER BY vintage_id")
            return cur.fetchall()
    finally:
        cx.close()


def run_one(statements):
    from factorlab.ingest import RDB
    from factorlab.weekly_fundamentals import ingest_security
    db = RDB()
    try:
        return ingest_security(Client(statements), db, 1, "SYNTH", "123")
    finally:
        db.close()


def test_real_binding_hash_currency_and_fresh_connection_retry(pg, statements):
    from factorlab.weekly_fundamentals import curate, curated_hash
    first, second = run_one(statements), run_one(statements)
    expected = curated_hash(curate(*(statements[n][0] for n in
                                    ("income_q", "balance_q", "cashflow_q"))))
    assert first["new_vintages"] == 1 and second["unchanged"] == 1
    assert read(pg) == [(1, expected, "USD", 10)]


def test_changed_values_append_without_changing_original(pg, statements):
    run_one(statements)
    before = read(pg)
    changed = copy.deepcopy(statements)
    changed["income_q"][0]["netIncome"] = 11
    assert run_one(changed)["live_restatements"] == 1
    after = read(pg)
    assert after[:1] == before and after[1][0] == 2 and after[1][3] == 11


def test_concurrent_identical_writers_insert_exactly_once(pg, statements):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: run_one(statements), range(4)))
    assert sum(r["new_vintages"] for r in results) == 1
    assert sum(r["unchanged"] for r in results) == 3
    assert len(read(pg)) == 1


def test_callback_exception_rolls_back_and_wrapper_can_be_reused(pg):
    from factorlab.ingest import RDB
    db = RDB()
    def bad(cur):
        cur.execute("INSERT INTO issuers (issuer_id,cik,name) VALUES (2,'999','Rollback')")
        raise ValueError("synthetic failure after write")
    try:
        with pytest.raises(ValueError):
            db.atomic(bad)
        assert db.atomic(lambda cur: (cur.execute("SELECT count(*) FROM issuers"), cur.fetchone())[1]) == (1,)
    finally:
        db.close()


def test_constraint_exception_rolls_back_and_wrapper_can_be_reused(pg):
    import psycopg2
    from factorlab.ingest import RDB
    db = RDB()
    try:
        with pytest.raises(psycopg2.errors.UniqueViolation):
            db.atomic(lambda cur: cur.execute("INSERT INTO issuers (issuer_id,cik,name) VALUES (1,'888','Duplicate')"))
        assert db.atomic(lambda cur: (cur.execute("SELECT count(*) FROM issuers"), cur.fetchone())[1]) == (1,)
    finally:
        db.close()


def test_old_22_placeholders_21_values_bug_is_rejected_by_real_driver(pg):
    cx = pg()
    try:
        with cx.cursor() as cur:
            with pytest.raises((IndexError, TypeError)):
                cur.execute("SELECT " + ", ".join(["%s"] * 22), tuple(range(21)))
    finally:
        cx.close()
