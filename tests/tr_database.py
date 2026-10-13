"""Repository-owned synthetic TR fixture; explicit CI control target only.

The control role needs CREATEDB/CREATEROLE (the existing CI service is disposable).
Each case owns unique public-schema relations in a separate database. Runtime
connections authenticate as a separate restricted LOGIN, never the setup role.
"""
from contextlib import contextmanager
import os
import secrets
from uuid import uuid4
import pytest


from benchmark_database import control_options


@contextmanager
def tr_database(record):
    url = os.environ.get('FACTORLAB_TEST_DATABASE_URL')
    if not url:
        pytest.skip('No explicit disposable PostgreSQL target; not an integration pass')
    # Configured targets must fail on missing drivers, malformed identity or connection/setup failure.
    import psycopg2
    from psycopg2 import sql
    opts = control_options(url)
    from factorlab.migrations import MIGRATIONS
    token = uuid4().hex
    database, role = 'fl_tr_db_' + token, 'fl_tr_writer_' + token
    marker = 'factorlab synthetic tr fixture ' + token
    record('tr_resource_intent', database + ':' + role)
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
                    for migration in (1,3,4):
                        cur.execute(MIGRATIONS[migration])
                    cur.execute("INSERT INTO issuers(issuer_id,name) VALUES (1,'SYNTHETIC')")
                    cur.execute("INSERT INTO securities(security_id,issuer_id,status) SELECT i,1,CASE WHEN i=1 THEN 'active' ELSE 'delisted' END FROM generate_series(1,100) i")
                    cur.execute("INSERT INTO symbol_map VALUES (1,'SYNTHETIC','NYSE','2010-01-01',NULL,'synthetic')")
                    cur.execute("INSERT INTO price_recon(security_id) VALUES (1),(2)")
                    cur.execute("INSERT INTO prices_raw_d(security_id,d,close,volume) VALUES (2,'2000-01-01',42,100)")
                    cur.execute("INSERT INTO tr_index_d VALUES (2,'2000-01-01',42,'tr-v1-close-div-split')")
                    cur.execute("SELECT format_type(atttypid,atttypmod),attnotnull FROM pg_attribute WHERE attrelid='public.tr_index_d'::regclass AND attname='tr'")
                    assert cur.fetchone()==('numeric',True)
                    cur.execute("SELECT contype FROM pg_constraint WHERE conrelid='public.tr_index_d'::regclass")
                    assert {'p','f'} <= {r[0] for r in cur.fetchall()}
                    for grant in ['GRANT USAGE ON SCHEMA public TO {}',
                                  'GRANT SELECT ON public.securities,public.symbol_map TO {}',
                                  'GRANT SELECT,INSERT,DELETE ON public.prices_raw_d,public.tr_index_d,public.corp_actions TO {}',
                                  'GRANT SELECT,INSERT,UPDATE,DELETE ON public.price_recon,public.mktcap_m TO {}']:
                        cur.execute(sql.SQL(grant).format(sql.Identifier(role)))
                    cur.execute(sql.SQL('ALTER ROLE {} SET statement_timeout=15000').format(sql.Identifier(role)))
                    cur.execute(sql.SQL('ALTER ROLE {} SET lock_timeout=2000').format(sql.Identifier(role)))
                    cur.execute(sql.SQL('ALTER ROLE {} SET idle_in_transaction_session_timeout=30000').format(sql.Identifier(role)))
        finally:
            setup.close()
        runtime = dict(opts,port=opts.get('port','5432'),sslmode=opts.get('sslmode','prefer'),dbname=database,user=role,password=password)
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
        def setup_query(statement, parameters=()):
            verify_db()
            cx=psycopg2.connect(**dict(opts,dbname=database),connect_timeout=5)
            try:
                with cx:
                    with cx.cursor() as cur:
                        cur.execute(statement,parameters)
                        return cur.fetchall() if cur.description else None
            finally:cx.close()
        yield {'connect':connect,'setup':setup_query}

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
            record('tr_cleanup','removed: '+database+':'+role)
        finally:
            admin.close()
