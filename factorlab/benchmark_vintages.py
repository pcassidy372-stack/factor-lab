"""Bounded benchmark-only replay and explicit approval events; no provider I/O.

Factories own fresh connections; callers select targets explicitly. Import is not
approval. Selection is not publication. No retry follows an uncertain commit.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
from pathlib import Path
from uuid import UUID
from .benchmark_loader import validate_grid, validate_response, numeric, decimal_text
from .publication_contract import require

MODE = 'benchmark-vintage-v1'
VALIDATOR_COMMIT = '2b1a66b580d2fa5708db1e32594d99e181c2a9ec'
VALIDATOR_TREE = '81a609372f65e03e647caa9f90ca3a79772a2b7e'
SOURCE_CONTRACT = '3be83349b1aacab0467dac62b373106a088fa7e7'
ENDPOINT = 'https://financialmodelingprep.com/stable/historical-price-eod/dividend-adjusted'
class Uncertain(RuntimeError): pass

def digest(b): return hashlib.sha256(b).hexdigest()
def canonical(v):return (json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True)+'\n').encode()
def identifier(v):
    try: return str(UUID(str(v)))
    except (ValueError,TypeError,AttributeError): raise ValueError('invalid_operation_identity') from None

def _pairs(pairs):
    out={}
    for k,v in pairs:
        require(k not in out,'duplicate_json_key');out[k]=v
    return out

def decode(raw,limit):
    require(type(raw) is bytes and 0<len(raw)<=limit,'document_size_limit')
    try: return json.loads(raw,parse_float=Decimal,parse_int=int,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),object_pairs_hook=_pairs)
    except (ValueError,RecursionError,UnicodeError): raise ValueError('invalid_bounded_json') from None

def instant(s):
    try: d=datetime.fromisoformat(s.replace('Z','+00:00'))
    except (ValueError,AttributeError):raise ValueError('invalid_observation_time') from None
    require(d.tzinfo is not None and d.utcoffset().total_seconds()==0,'utc_required');return d

def validate_candidate(document,payload,expected_document_sha,expected_payload_sha):
    require(digest(document)==expected_document_sha and digest(payload)==expected_payload_sha,'content_hash_mismatch')
    doc=decode(document,262144);raw=decode(payload,5242880)
    require(type(doc) is dict and set(doc)=={'status','period','symbol','field','rows','provenance'},'candidate_shape')
    require(doc['status']=='UNAPPLIED_UNAPPROVED_CANDIDATE' and doc['symbol']=='SPY' and doc['field']=='adjClose','candidate_semantics')
    rows=doc['rows'];p=doc['provenance']
    require(type(rows) is list and len(rows)==13 and all(type(r) is dict and set(r)=={'date','adjClose'} for r in rows),'candidate_rows')
    grid=[r['date'] for r in rows]
    start,end=instant(p['retrieval_start']),instant(p['retrieval_end'])
    require(start<=end<=datetime.now(timezone.utc),'observation_interval')
    validate_grid(doc['period'],grid,end.date())
    values=validate_response(raw,grid)
    require(all(numeric(r['adjClose'])==numeric(values[r['date']]) for r in rows),'candidate_payload_mismatch')
    require(p['response_sha256']==expected_payload_sha and p['request_endpoint']==ENDPOINT,'payload_semantics')
    require(p['request_scope']=={'symbol':'SPY','from':grid[0],'to':grid[-1]},'request_scope')
    require(p['code_commit']==VALIDATOR_COMMIT and p['code_tree']==VALIDATOR_TREE and p['source_contract']==SOURCE_CONTRACT,'validator_identity')
    require(p['validator_sha256']==digest(Path(__file__).with_name('benchmark_loader.py').read_bytes()),'validator_bytes')
    require(p['grid_sha256']==digest(canonical(grid)),'grid_hash')
    require(all(type(h) is str and len(h)==64 and all(c in '0123456789abcdef' for c in h) for h in p['input_hashes'].values()),'input_hash_identity')
    return {'period':doc['period'],'grid':grid,'levels':[decimal_text(r['adjClose']) for r in rows],
            'source_start':start,'source_end':end,'document_sha256':expected_document_sha,'payload_sha256':expected_payload_sha}

def _open(connect,readonly=False):
    from psycopg2.extensions import STATUS_READY
    c=connect()
    try:
        require(not c.closed and c.status==STATUS_READY and not c.autocommit,'fresh_owned_connection_required')
        c.set_session(readonly=readonly,isolation_level='REPEATABLE READ' if readonly else 'SERIALIZABLE')
        with c.cursor() as q:
            q.execute("SET LOCAL statement_timeout='15s'");q.execute("SET LOCAL lock_timeout='2s'");q.execute("SET LOCAL idle_in_transaction_session_timeout='30s'")
        return c
    except BaseException:c.close();raise

def canonical_observation(value):
    # Shared publication convention rejects naive/invalid values and retains precision.
    from .publication_contract import instant as publication_instant
    return publication_instant(value).isoformat()

def connection_identity(q):
    """Public libpq metadata of the OPEN connection, not an env name or raw DSN.

    Only selected non-secret fields leave this function. Passwords, SSL key/cert
    paths and the complete DSN parameter map are never persisted or hashed.
    """
    info=q.connection.info
    host,port,dbname,user=info.host,str(info.port),info.dbname,info.user
    params=info.dsn_parameters
    sslmode=params.get('sslmode');peer=params.get('requirepeer') or None
    require(type(host) is str and host and ',' not in host and type(dbname) is str and dbname,'single_actual_route_required')
    require(port.isdigit() and 0<int(port)<65536 and sslmode in ('disable','allow','prefer','require','verify-ca','verify-full'),'actual_transport_required')
    q.execute('SELECT session_user,current_user')
    session,effective=q.fetchone()
    require(session==effective==user,'authenticated_role_changed')
    require(not params.get('hostaddr') and not params.get('service'),'route_override_forbidden')
    q.execute('SELECT pg_catalog.host(pg_catalog.inet_server_addr()),pg_catalog.inet_server_port()')
    server_address,server_port=q.fetchone()
    tls=bool(info.ssl_in_use)
    transport={'sslmode':sslmode,'ssl_in_use':tls,'requirepeer':peer,
               'protocol':info.ssl_attribute('protocol') if tls else None,
               'cipher':info.ssl_attribute('cipher') if tls else None}
    return {'host':host,'port':str(int(port)),'database':dbname,'server_address':server_address,'server_port':server_port,'transport':transport},session

def validate_expected_target(expected):
    require(type(expected) is dict and set(expected)=={'version','database','database_oid','header_oid','route','planned_by','allowed_roles'},'target_shape')
    require(expected['version']==2 and type(expected['version']) is int,'target_version')
    require(all(type(expected[k]) is int and expected[k]>0 for k in ('database_oid','header_oid')),'catalog_identity')
    route=expected['route']
    require(type(route) is dict and set(route)=={'host','port','database','server_address','server_port','transport'},'route_shape')
    require(type(route['host']) is str and 0<len(route['host'])<=4096 and ',' not in route['host'] and not any(c.isspace() for c in route['host']),'route_host')
    require(type(route['port']) is str and route['port'].isdigit() and str(int(route['port']))==route['port'] and 0<int(route['port'])<65536,'route_port')
    require(type(expected['database']) is str and 0<len(expected['database'])<=63 and route['database']==expected['database'],'route_database')
    if route['host'].startswith('/'):
        require(route['server_address'] is None and route['server_port'] is None,'socket_route')
    else:
        import ipaddress
        require(type(route['server_address']) is str and str(ipaddress.ip_address(route['server_address']))==route['server_address'],'server_address')
        require(type(route['server_port']) is int and 0<route['server_port']<65536,'server_port')
    t=route['transport']
    require(type(t) is dict and set(t)=={'sslmode','ssl_in_use','requirepeer','protocol','cipher'},'transport_shape')
    require(t['sslmode'] in ('disable','allow','prefer','require','verify-ca','verify-full') and type(t['ssl_in_use']) is bool,'transport_policy')
    require(all(t[k] is None or (type(t[k]) is str and 0<len(t[k])<=256) for k in ('requirepeer','protocol','cipher')),'transport_metadata')
    require(t['ssl_in_use'] or (t['protocol'] is None and t['cipher'] is None),'transport_metadata')
    roles=expected['allowed_roles']
    require(type(roles) is list and 1<=len(roles)<=8 and all(type(x) is str and re.fullmatch('[A-Za-z_][A-Za-z0-9_-]{0,62}',x) for x in roles),'role_policy')
    require(roles==sorted(set(roles)) and expected['planned_by'] in roles,'role_policy')
    return expected

def require_target(q,expected):
    validate_expected_target(expected)
    actual=target(q);role=actual.pop('authenticated_role')
    require(actual=={k:expected[k] for k in actual} and role in expected['allowed_roles'],'target_changed')

def target(q):
    q.execute("""SELECT count(*) FROM pg_catalog.pg_namespace n,
      LATERAL pg_catalog.aclexplode(COALESCE(n.nspacl,pg_catalog.acldefault('n',n.nspowner))) a
      WHERE n.nspname='public' AND a.grantee=0 AND a.privilege_type='CREATE'""")
    require(q.fetchone()==(0,),'untrusted_public_schema')
    q.execute("""SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relname=ANY(%s) AND c.relkind='r' AND NOT c.relrowsecurity""",
      (['benchmark_vintages','benchmark_vintage_values','benchmark_vintage_selections'],))
    require(q.fetchone()==(3,),'vintage_relations_invalid')
    q.execute("""SELECT p.proname,p.prosecdef,p.proconfig FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='public' AND p.proname=ANY(%s) ORDER BY p.proname""",
      (['bv_immutable','bv_json_unique','bv_header','bv_value','bv_complete','bv_select'],))
    funcs=q.fetchall()
    require(len(funcs)==6 and all(not x[1] and x[2]==['search_path=pg_catalog, public, pg_temp'] for x in funcs),'vintage_function_path_invalid')
    q.execute("""SELECT current_database(),d.oid,c.oid FROM pg_catalog.pg_database d,
      pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
      WHERE d.datname=current_database() AND n.nspname='public' AND c.relname='benchmark_vintages'
      AND c.relkind='r' AND NOT c.relrowsecurity""")
    r=q.fetchone();require(r is not None,'vintage_schema_required')
    route,role=connection_identity(q)
    require(route['database']==r[0],'actual_database_mismatch')
    return {'version':2,'database':r[0],'database_oid':r[1],'header_oid':r[2],'route':route,'authenticated_role':role}

def inspect_target(connect, *, allowed_roles=()):
    c=_open(connect,True)
    try:
        with c.cursor() as q:
            result=target(q);role=result.pop('authenticated_role')
            result.update(planned_by=role,allowed_roles=sorted(set([role,*allowed_roles])))
            validate_expected_target(result)
            q.execute('SELECT rolname FROM pg_catalog.pg_roles WHERE rolname=ANY(%s)',(result['allowed_roles'],))
            require(sorted(x[0] for x in q.fetchall())==result['allowed_roles'],'approved_role_absent')
            return result
    finally:c.close()

def import_vintage(connect,operation_id,document,payload,document_sha,payload_sha,expected_target):
    v=identifier(operation_id);p=validate_candidate(document,payload,document_sha,payload_sha)
    validate_expected_target(expected_target)
    c=_open(connect);committing=False
    try:
        with c.cursor() as q:
            require_target(q,expected_target)
            q.execute('SELECT document,payload FROM public.benchmark_vintages WHERE vintage_id=%s',(v,));old=q.fetchone()
            if old:
                require(bytes(old[0])==document and bytes(old[1])==payload,'operation_content_conflict');return {'status':'verified_replay','vintage_id':v}
            q.execute('''INSERT INTO public.benchmark_vintages(vintage_id,period,document,payload,document_sha256,payload_sha256,grid,levels,source_start,source_end)
             VALUES(%s,%s,%s,%s,%s,%s,%s::date[],%s::numeric[],%s,%s)''',
             (v,p['period'],document,payload,document_sha,payload_sha,p['grid'],p['levels'],p['source_start'],p['source_end']))
            q.executemany("INSERT INTO public.benchmark_vintage_values VALUES(%s,%s,'SPY',%s)",[(v,d,n) for d,n in zip(p['grid'],p['levels'])])
            q.execute('SET CONSTRAINTS ALL IMMEDIATE')
        committing=True;c.commit();return {'status':'imported_not_selected','vintage_id':v}
    except BaseException:
        if committing:raise Uncertain('import_commit_uncertain_reconcile_without_retry') from None
        raise
    finally:c.close()

def validate_selection(operation_id,period,vintage_id,predecessor,approval_reference):
    for value in (operation_id,vintage_id):
        require(type(value) is str and identifier(value)==value,'selection_identity')
    require(predecessor is None or (type(predecessor) is str and identifier(predecessor)==predecessor and predecessor!=operation_id),'predecessor_identity')
    require(type(period) is str and re.fullmatch('[0-9]{4}-(0[1-9]|1[0-2])',period),'selection_period')
    require(type(approval_reference) is str and 0<len(approval_reference.strip())<=1000,'approval_reference_required')

def select_vintage(connect,operation_id,period,vintage_id,predecessor,approval_reference,expected_target):
    validate_selection(operation_id,period,vintage_id,predecessor,approval_reference)
    event,v=identifier(operation_id),identifier(vintage_id);pred=identifier(predecessor) if predecessor is not None else None
    require(type(approval_reference) is str and 0<len(approval_reference.strip())<=1000,'approval_reference_required')
    content=(period,v,pred,approval_reference)
    validate_expected_target(expected_target)
    c=_open(connect);committing=False
    try:
        with c.cursor() as q:
            require_target(q,expected_target)
            q.execute('SELECT period,vintage_id::text,predecessor::text,approval_reference FROM public.benchmark_vintage_selections WHERE event_id=%s',(event,));old=q.fetchone()
            if old:require(old==content,'operation_content_conflict');return {'status':'verified_replay','event_id':event}
            q.execute('INSERT INTO public.benchmark_vintage_selections(event_id,period,vintage_id,predecessor,approval_reference) VALUES(%s,%s,%s,%s,%s)',(event,*content))
        committing=True;c.commit();return {'status':'selected_not_published','event_id':event}
    except BaseException:
        if committing:raise Uncertain('selection_commit_uncertain_reconcile_without_retry') from None
        raise
    finally:c.close()

def read_selection(q,period,event_id,vintage_id):
    event,v=identifier(event_id),identifier(vintage_id)
    q.execute('''SELECT h.document,h.payload,h.document_sha256,h.payload_sha256,h.imported_at,
       s.selected_at,s.predecessor::text,s.approval_reference,
       h.import_xid=pg_current_xact_id_if_assigned(),s.selection_xid=pg_current_xact_id_if_assigned()
       FROM public.benchmark_vintage_selections s JOIN public.benchmark_vintages h USING(vintage_id,period)
       WHERE s.event_id=%s AND s.period=%s AND s.vintage_id=%s''',(event,period,v))
    r=q.fetchone();require(r is not None,'explicit_selection_not_found')
    require(not r[8] and not r[9],'committed_selection_required')
    document,payload=bytes(r[0]),bytes(r[1]);p=validate_candidate(document,payload,r[2],r[3])
    q.execute('SELECT asof::text,symbol,tr FROM public.benchmark_vintage_values WHERE vintage_id=%s ORDER BY asof',(v,))
    rows=[{'asof':d,'symbol':s,'tr':decimal_text(n)} for d,s,n in q.fetchall()]
    require(rows==[{'asof':d,'symbol':'SPY','tr':n} for d,n in zip(p['grid'],p['levels'])],'stored_grid_mismatch')
    return {'mode':MODE,'vintage_id':v,'selection_event_id':event,'period':period,
      'document_sha256':r[2],'payload_sha256':r[3],'source_start':p['source_start'].isoformat(),
      'source_end':p['source_end'].isoformat(),'import_observed_at':canonical_observation(r[4]),
      'selection_observed_at':canonical_observation(r[5]),'predecessor':r[6],'approval_reference':r[7],'rows':rows}

def reconcile_import(connect,operation_id,document,payload,document_sha,payload_sha,expected_target):
    identifier(operation_id);validate_expected_target(expected_target)
    validate_candidate(document,payload,document_sha,payload_sha);c=_open(connect,True)
    try:
        with c.cursor() as q:
            require_target(q,expected_target)
            q.execute('SELECT document,payload FROM public.benchmark_vintages WHERE vintage_id=%s',(identifier(operation_id),));r=q.fetchone()
            require(r is None or (bytes(r[0])==document and bytes(r[1])==payload),'operation_content_conflict')
            return {'status':'absent' if r is None else 'complete_committed_import'}
    finally:c.close()

def reconcile_selection(connect,operation_id,period,vintage_id,predecessor,approval_reference,expected_target):
    validate_selection(operation_id,period,vintage_id,predecessor,approval_reference);validate_expected_target(expected_target)
    c=_open(connect,True)
    try:
        with c.cursor() as q:
            require_target(q,expected_target)
            q.execute('SELECT period,vintage_id::text,predecessor::text,approval_reference FROM public.benchmark_vintage_selections WHERE event_id=%s',(identifier(operation_id),));r=q.fetchone()
            require(r is None or r==(period,identifier(vintage_id),identifier(predecessor) if predecessor else None,approval_reference),'operation_content_conflict')
            if r:read_selection(q,period,operation_id,vintage_id)
            return {'status':'absent' if r is None else 'complete_committed_selection'}
    finally:c.close()
