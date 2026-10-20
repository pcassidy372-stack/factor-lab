"""Explicit benchmark import/select plans. No provider, migration or publication.

Plans contain private raw inputs: store with mode0600 and do not share. Run plan
with an explicit connection env name, inspect its exact target/content, then use
apply or fresh reconcile. No retries or auto-discovery.
"""
import argparse
import os
from pathlib import Path
import sys

def bounded(path,limit):
    with path.open('rb') as f:
        value=f.read(limit+1)
    if len(value)>limit:raise ValueError('input_size_limit')
    return value

def validated_plan(raw):
    """Reject the complete serialized operation BEFORE credential lookup/connect."""
    import base64
    from factorlab import benchmark_vintages as v
    plan=v.decode(raw,8000000)
    v.require(type(plan) is dict,'plan_shape')
    kind=plan.get('kind')
    common={'kind','operation_id','expected_target','plan_sha256'}
    fields={'import':{'document','payload','document_sha','payload_sha'},
            'select':{'period','vintage_id','predecessor','approval_reference'}}
    v.require(type(kind) is str and kind in fields,'unknown_operation')
    v.require(set(plan)==common|fields[kind],'plan_shape')
    supplied=plan.pop('plan_sha256')
    v.require(type(supplied) is str and v.digest(v.canonical(plan))==supplied,'plan_hash_mismatch')
    v.require(type(plan['operation_id']) is str and v.identifier(plan['operation_id'])==plan['operation_id'],'operation_identity')
    v.validate_expected_target(plan['expected_target'])
    if kind=='import':
        for field,limit in (('document',262144),('payload',5242880)):
            value=plan[field]
            v.require(type(value) is str and len(value)<=4*((limit+2)//3),'encoded_size')
            decoded=base64.b64decode(value,validate=True)
            v.require(base64.b64encode(decoded).decode()==value,'canonical_encoding')
            plan[field]=decoded
        v.validate_candidate(plan['document'],plan['payload'],plan['document_sha'],plan['payload_sha'])
    else:
        v.validate_selection(plan['operation_id'],plan['period'],plan['vintage_id'],plan['predecessor'],plan['approval_reference'])
    return plan

def main(argv=None, *, connect=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--connection-env',required=True)
    subs=p.add_subparsers(dest='command',required=True)
    imp=subs.add_parser('import-plan');imp.add_argument('--candidate',type=Path,required=True);imp.add_argument('--payload',type=Path,required=True)
    imp.add_argument('--document-sha256',required=True);imp.add_argument('--payload-sha256',required=True)
    sel=subs.add_parser('select-plan');sel.add_argument('--period',required=True);sel.add_argument('--vintage',required=True);sel.add_argument('--approval',required=True)
    pred=sel.add_mutually_exclusive_group(required=True);pred.add_argument('--predecessor');pred.add_argument('--empty-chain',action='store_true')
    for sub in (imp,sel):
        sub.add_argument('--operation-id',required=True);sub.add_argument('--output',type=Path,required=True)
        sub.add_argument('--permit-role',action='append',default=[],help='Explicit additional authenticated apply/reconcile role on this same target')
    for cmd in ('apply','reconcile'):subs.add_parser(cmd).add_argument('--plan',type=Path,required=True)
    a=p.parse_args(argv)
    import re
    if not re.fullmatch('[A-Z][A-Z0-9_]*',a.connection_env):p.error('invalid connection name')
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from factorlab import benchmark_vintages as v
    import base64,json
    try:
        if a.command=='import-plan':
            v.require(v.identifier(a.operation_id)==a.operation_id,'operation_identity')
            doc=bounded(a.candidate,262144);raw=bounded(a.payload,5242880)
            v.validate_candidate(doc,raw,a.document_sha256,a.payload_sha256)
            plan={'kind':'import','operation_id':a.operation_id,'document':base64.b64encode(doc).decode(),'payload':base64.b64encode(raw).decode(),'document_sha':a.document_sha256,'payload_sha':a.payload_sha256}
        elif a.command=='select-plan':
            v.validate_selection(a.operation_id,a.period,a.vintage,a.predecessor,a.approval)
            plan={'kind':'select','operation_id':a.operation_id,'period':a.period,'vintage_id':a.vintage,'predecessor':a.predecessor,'approval_reference':a.approval}
        else:
            plan=validated_plan(bounded(a.plan,8000000))
        if connect is None:
            import psycopg2
            from psycopg2.extensions import parse_dsn
            cfg=parse_dsn(os.environ[a.connection_env])
            v.require(set(cfg)<={'host','port','dbname','user','password','sslmode','sslrootcert','sslcert','sslkey'} and all(cfg.get(k) for k in ['host','port','dbname','user','password','sslmode']),'explicit_target_required')
            v.require(',' not in cfg['host'] and ',' not in cfg['port'] and not any(k.startswith('PG') for k in os.environ),'inherited_or_multihost_forbidden')
            connect=lambda:psycopg2.connect(**cfg,connect_timeout=5)
        if a.command.endswith('-plan'):
            plan['expected_target']=v.inspect_target(connect,allowed_roles=a.permit_role);plan['plan_sha256']=v.digest(v.canonical(plan))
            fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as f:f.write(v.canonical(plan))
            print('planned_not_applied');return 0
        kind=plan.pop('kind')
        if kind=='import':
            fn=v.import_vintage if a.command=='apply' else v.reconcile_import
        else:
            v.require(kind=='select','unknown_operation');fn=v.select_vintage if a.command=='apply' else v.reconcile_selection
        print(json.dumps(fn(connect,**plan)));return 0
    except v.Uncertain:print('commit_uncertain_reconcile_exact_plan',file=sys.stderr);return 3
    except Exception:print('benchmark_vintage_operation_failed',file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
