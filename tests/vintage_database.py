"""Owned per-case public database; real, independent restricted runtime logins."""
from contextlib import contextmanager
import os,secrets
from uuid import uuid4
import pytest
from benchmark_database import control_options

def fixture_options(url):
    """Materialize only defaults accepted by the strict disposable-target guard.

    Runtime DSNs feed the public CLI, which deliberately requires explicit route
    and TLS parameters. This is fixture policy, not an application fallback.
    """
    opts = control_options(url)
    return {'port': '5432', 'sslmode': 'prefer', **opts}

@contextmanager
def vintage_database(record=lambda *a:None):
    url=os.environ.get('FACTORLAB_TEST_DATABASE_URL')
    if not url:pytest.skip('No explicit disposable PostgreSQL target; not an integration pass')
    import psycopg2
    from psycopg2 import sql
    from factorlab.migrations import MIGRATIONS
    opts=fixture_options(url);token=uuid4().hex;db='fl_vintage_db_'+token;marker='factorlab synthetic vintage fixture '+token
    roles={k:'fl_vintage_'+k+'_'+token for k in ('importer','selector','reader','publisher')}
    passwords={k:secrets.token_hex(24) for k in roles};opened=[];made=[];dbmade=False
    admin=psycopg2.connect(**opts,connect_timeout=5);admin.autocommit=True
    def query(q,args=()):
        with admin.cursor() as c:c.execute(q,args);return c.fetchall() if c.description else None
    def options(kind='setup'):
        cfg=dict(opts,dbname=db)
        if kind!='setup':cfg.update(user=roles[kind],password=passwords[kind])
        return cfg
    def connect(kind='setup'):
        cx=psycopg2.connect(**options(kind),connect_timeout=5);opened.append(cx);return cx
    record('vintage_resource_intent',db)
    try:
        assert query('SELECT current_database(),current_user')==[('factorlab_ci','factorlab_ci')]
        query("SET statement_timeout='15s'");query("SET lock_timeout='2s'")
        for k,role in roles.items():
            query(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD %s').format(sql.Identifier(role)),(passwords[k],));made.append(role)
            query(sql.SQL('COMMENT ON ROLE {} IS %s').format(sql.Identifier(role)),(marker,))
        query(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(db)));dbmade=True
        query(sql.SQL('COMMENT ON DATABASE {} IS %s').format(sql.Identifier(db)),(marker,))
        query(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(db)))
        for role in roles.values():query(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(db),sql.Identifier(role)))
        cx=connect()
        try:
            with cx:
                with cx.cursor() as c:
                    c.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC');c.execute('SET LOCAL search_path=public,pg_temp')
                    for n in sorted(MIGRATIONS):c.execute(MIGRATIONS[n])
                    c.execute("INSERT INTO issuers(issuer_id,cik,name) VALUES(1,'123','Synthetic')")
                    c.execute('INSERT INTO securities(security_id,issuer_id) VALUES(1,1),(2,1),(3,1)')
                    c.execute("INSERT INTO universe_snapshots VALUES ('2019-07-31',1,1,1,1,true,'small')")
                    c.execute("INSERT INTO factor_definitions(factor_id,version,family,formula_text,formula_hash,params,prior_sign) VALUES('sue',1,'synthetic','fixture','fixture','{}',1)")
                    c.execute("INSERT INTO factor_values VALUES('2019-07-31',1,'sue',1,0,0,0)");c.execute("INSERT INTO factor_ic VALUES('sue','2019-07-31',1,0,1)");c.execute("INSERT INTO factor_ls VALUES('sue','2019-07-31',0,0,0,1)")
                    c.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public'")
                    tables=[r[0] for r in c.fetchall()]
                    for k,role in roles.items():
                        c.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
                        # Explicit resolved fixture tables, no future-table/default grants.
                        read=['benchmark_vintages','benchmark_vintage_values','benchmark_vintage_selections']
                        if k in ('reader','publisher'):
                            read += ['prices_raw_d','mktcap_m','profile_snapshots','symbol_map','fundamentals_q','tr_index_d','surprises','factor_definitions']
                            read += [t for t in tables if t.startswith('fl_')]
                        c.execute(sql.SQL('GRANT SELECT ON {} TO {}').format(sql.SQL(',').join(sql.Identifier('public',t) for t in read),sql.Identifier(role)))
                        writes={'importer':['benchmark_vintages','benchmark_vintage_values'],'selector':['benchmark_vintage_selections'],'reader':[], 'publisher':[t for t in tables if t.startswith('fl_')] }[k]
                        if k in ('reader','publisher'):c.execute(sql.SQL('GRANT SELECT ON public.fl_published_datasets TO {}').format(sql.Identifier(role)))
                        if writes:c.execute(sql.SQL('GRANT INSERT ON {} TO {}').format(sql.SQL(',').join(sql.Identifier('public',t) for t in writes),sql.Identifier(role)))
        finally:cx.close()
        setup=lambda:connect();setup.roles=roles;setup.runtime=lambda k:lambda:connect(k);setup.marker=marker;setup.connection_options=options
        yield setup
    finally:
        for cx in opened:cx.close()
        try:
            if dbmade:
                assert query("SELECT pg_get_userbyid(datdba),shobj_description(oid,'pg_database') FROM pg_database WHERE datname=%s",(db,))==[('factorlab_ci',marker)]
                query(sql.SQL('DROP DATABASE {}').format(sql.Identifier(db)))
            for role in made:
                assert query("SELECT shobj_description(oid,'pg_authid'),rolsuper,rolcreaterole,rolcreatedb,rolbypassrls FROM pg_roles WHERE rolname=%s",(role,))==[(marker,False,False,False,False)]
                query(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
            record('vintage_cleanup','owned database and roles removed')
        finally:admin.close()
