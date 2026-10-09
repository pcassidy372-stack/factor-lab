"""Synthetic candidate in documented private-candidate format; never real prices."""
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
from factorlab import benchmark_vintages as v

def candidate(grid,period,levels=None):
    rows=[{'date':d,'symbol':'SPY','adjClose':str((levels or list(range(100,113)))[i])} for i,d in enumerate(grid)]
    raw=v.canonical(rows);now=datetime.now(timezone.utc).isoformat()
    doc={'status':'UNAPPLIED_UNAPPROVED_CANDIDATE','period':period,'symbol':'SPY','field':'adjClose',
      'rows':[{'date':r['date'],'adjClose':r['adjClose']} for r in rows],
      'provenance':{'response_sha256':v.digest(raw),'request_endpoint':v.ENDPOINT,'request_scope':{'symbol':'SPY','from':grid[0],'to':grid[-1]},
      'retrieval_start':now,'retrieval_end':now,'input_hashes':{'synthetic':'0'*64},'code_commit':v.VALIDATOR_COMMIT,'code_tree':v.VALIDATOR_TREE,'source_contract':v.SOURCE_CONTRACT,
      'validator_sha256':v.digest(Path(v.__file__).with_name('benchmark_loader.py').read_bytes()),'grid_sha256':v.digest(v.canonical(grid))}}
    return v.canonical(doc),raw

def install(connect,grid,period,levels=None,predecessor=None):
    doc,raw=candidate(grid,period,levels);vid,eid=str(uuid4()),str(uuid4());target=v.inspect_target(connect,allowed_roles=list(connect.roles.values()))
    v.import_vintage(connect.runtime('importer'),vid,doc,raw,v.digest(doc),v.digest(raw),target)
    v.select_vintage(connect.runtime('selector'),eid,period,vid,predecessor,'SYNTHETIC test approval',target)
    connect.selection_event=eid;connect.vintage_id=vid
    return eid,vid
