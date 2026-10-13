"""Repository-owned synthetic benchmark fixture; explicit CI control target only.

The control role needs CREATEDB/CREATEROLE (the existing CI service is disposable).
Each case owns unique public-schema relations in a separate database. Runtime
connections authenticate as a separate restricted LOGIN, never the setup role.
"""
from contextlib import contextmanager
import os
import secrets
from uuid import uuid4
import pytest


def control_options(url):
    from psycopg2.extensions import parse_dsn
    try:
        opts = parse_dsn(url)
        valid = (opts.get('host') == '127.0.0.1' and opts.get('dbname') == 'factorlab_ci'
                 and opts.get('user') == 'factorlab_ci' and opts.get('password')
                 and str(int(opts.get('port', '5432'))) == opts.get('port', '5432')
                 and 0 < int(opts.get('port', '5432')) < 65536
                 and not set(opts) - {'host','port','dbname','user','password','sslmode'}
                 and opts.get('sslmode', 'prefer') in ('disable','prefer','require','verify-ca','verify-full'))
    except Exception:
        valid = False
    if not valid:
        raise ValueError('Explicit loopback factorlab_ci control target required; values withheld')
    return opts


@contextmanager
def benchmark_database(record):
    url = os.environ.get('FACTORLAB_TEST_DATABASE_URL')
    if not url:
        pytest.skip('No explicit disposable PostgreSQL target; not an integration pass')
    # Configured targets must fail on missing drivers, malformed identity or connection/setup failure.
    import psycopg2
    from psycopg2 import sql
    opts = control_options(url)
    from factorlab.migrations import MIGRATIONS
    from test_benchmark_loader import GRID, ROWS, MISSING
    token = uuid4().hex
    database, role = 'fl_benchmark_db_' + token, 'fl_benchmark_writer_' + token
    marker = 'factorlab synthetic benchmark fixture ' + token
    record('benchmark_resource_intent', database + ':' + role)
    admin = psycopg2.connect(**opts, connect_timeout=5)
    admin.autocommit = True
    opened, role_made, db_made = [], False, False
    def admin_query(text, args=()):
        with admin.cursor() as cur:
            cur.execute(text,args)
            return cur.fetchall() if cur.description else None
    def verify_db():
        rows = admin_query("""SELECT pg_catalog.pg_get_userbyid(datdba),
            pg_catalog.shobj_description(oid,'pg_database') FROM pg_catalog.pg_database WHERE datname=%s""", (database,))
        assert rows == [('factorlab_ci',marker)], 'Benchmark database identity changed; cleanup refused'
    try:
        assert admin_query('SELECT current_database(),current_user') == [('factorlab_ci','factorlab_ci')]
        admin_query("SET statement_timeout='15s'"); admin_query("SET lock_timeout='2s'")
        assert admin_query('SELECT 1 FROM pg_catalog.pg_database WHERE datname=%s',(database,)) == []
        assert admin_query('SELECT 1 FROM pg_catalog.pg_roles WHERE rolname=%s',(role,)) == []
        password = secrets.token_hex(24)
        admin_query(sql.SQL('CREATE ROLE {} LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS').format(sql.Identifier(role)),(password,))
        role_made = True
        admin_query(sql.SQL('COMMENT ON ROLE {} IS %s').format(sql.Identifier(role)),(marker,))
        admin_query(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(database)))
        db_made = True
        admin_query(sql.SQL('COMMENT ON DATABASE {} IS %s').format(sql.Identifier(database)),(marker,))
        verify_db()
        admin_query(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(database)))
        admin_query(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(database),sql.Identifier(role)))
        setup = psycopg2.connect(**dict(opts,dbname=database),connect_timeout=5)
        try:
            with setup:
                with setup.cursor() as cur:
                    cur.execute("SET LOCAL statement_timeout='15s'")
                    cur.execute("SET LOCAL lock_timeout='2s'")
                    cur.execute('SELECT current_database(),current_user')
                    assert cur.fetchone() == (database,'factorlab_ci')
                    cur.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
                    cur.execute(MIGRATIONS[10])
                    cur.execute('CREATE TABLE public.prices_raw_d(security_id int NOT NULL,d date NOT NULL,close numeric,volume bigint,PRIMARY KEY(security_id,d))')
                    cur.execute('CREATE TABLE public.universe_snapshots(asof date)')
                    cur.execute("INSERT INTO public.universe_snapshots VALUES ('2001-01-01')")
                    for d in GRID:
                        cur.execute('INSERT INTO public.prices_raw_d SELECT i,%s,10,10000 FROM generate_series(1,100) i',(d,))
                    for d in ['2025-11-29','2026-02-28']:
                        cur.execute('INSERT INTO public.prices_raw_d SELECT i,%s,10,10000 FROM generate_series(1,99) i',(d,))
                    for r in ROWS:
                        if r['date'] not in MISSING:
                            cur.execute("INSERT INTO public.benchmarks_m VALUES (%s,'SPY',%s)",(r['date'],r['adjClose']))
                    cur.execute("INSERT INTO public.benchmarks_m VALUES ('2000-01-01','SPY',42),('2026-09-30','QQQ',43)")
                    for grant in ['GRANT USAGE ON SCHEMA public TO {}',
                                  'GRANT SELECT ON public.prices_raw_d,public.benchmarks_m TO {}',
                                  'GRANT INSERT ON public.benchmarks_m TO {}']:
                        cur.execute(sql.SQL(grant).format(sql.Identifier(role)))
        finally:
            setup.close()
        runtime = dict(opts,dbname=database,user=role,password=password)
        def connect():
            cx = psycopg2.connect(**runtime,connect_timeout=5)
            opened.append(cx)
            return cx
        check = connect()
        try:
            with check.cursor() as cur:
                cur.execute('SELECT current_database(),current_user')
                assert cur.fetchone() == (database,role)
                cur.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=current_user')
                assert cur.fetchone() == (False,False,False,False)
                cur.execute('SELECT count(*) FROM pg_catalog.pg_auth_members WHERE member=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)')
                assert cur.fetchone() == (0,)
        finally:
            check.close()
        yield {'connect':connect}
    finally:
        for cx in opened:
            cx.close()
        try:
            if db_made:
                verify_db()
                admin_query(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))
            if role_made:
                rows=admin_query("SELECT pg_catalog.shobj_description(oid,'pg_authid'),rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=%s",(role,))
                assert rows == [(marker,False,False,False,False)], 'Benchmark role identity changed; cleanup refused'
                admin_query(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
            record('benchmark_cleanup','removed: '+database+':'+role)
        finally:
            admin.close()
