"""Visibly synthetic retained packages, using the real mocked acquisition lifecycle."""
import tempfile
from pathlib import Path
from copy import deepcopy
from factorlab import sue_plan as p
from factorlab.sue_acquire import acquire,reconcile
from factorlab.sue_use_contract import VERSION,USE,assess
CUTOFF='2099-01-01T00:00:00+00:00'


def evidence(payload, operation='synthetic-use', *, last_checked=None, omit_first_prior=False):
    rows=deepcopy(payload['surprises']);ids=payload['security_ids'];asof=payload['asof']
    year,month=map(int,asof[:7].split('-'));period=f'{year+(month==12):04d}-{month%12+1:02d}'
    members=[{'id':str(i),'eligible':True,'held':False,'marker':'absent','windows':[{'symbol':'SYNTH_'+str(i),'start':'2010-01-01','end':asof}],'before':[],'before_complete':True} for i in ids]
    scope={'period':period,'asof':asof,'scope_identity':'synthetic-full-scope','before_identity':'synthetic-before','semantics':'UNVERIFIED_PROVIDER_DATE_AND_PIT','members':members}
    plan=p.make_plan(scope,[str(i) for i in ids],dict(logical=len(ids),attempts=len(ids),retries=0,seconds=600,spacing='0.15',response_bytes=p.MAX_BYTES))
    histories={}
    for i in ids:
        target=next((r for r in rows if r['security_id']==i),None)
        histories['SYNTH_'+str(i)]=([] if target is None else [{'date':d,'epsActual':v,'epsEstimated':'0','symbol':'SYNTH_'+str(i)} for d,v in [('2019-09-01','-1'),('2019-12-01','1'),('2020-01-01','-1'),('2020-03-01','1'),(str(target['report_date']),str(target['sue']) if target['sue'] is not None else None)]])
    for history in histories.values():
        if omit_first_prior and history:history.pop(0)
        if last_checked is not None:
            for event in history:event['lastUpdated']=last_checked
    class Clock:
        t=1599048000.
        def __call__(self):return self.t
        def sleep(self,n):self.t+=n
    class Fake:
        def get(self,symbol,*a):return 200,p.canonical(histories[symbol])
        def close(self):pass
    c=Clock();files={}
    def put(obj):
        raw=p.canonical(obj);h=p.digest(raw);files[h]=raw.decode();return h
    source=put({'kind':'external-evidence-record','reference':'synthetic-reviewed-record','record':'SYNTHETIC ONLY: explicit independent issuer/date/basis and research-review fixture.'})
    policy=put({'version':VERSION,'use':USE,'research_approval':'SYNTHETIC-OPTION-A','available_at':'2020-09-02T13:00:00+00:00','source_ref':source})
    subjects=[]
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);root.chmod(0o700)
        acquire(plan,root,operation,'synthetic-acquisition-only','SYNTHETIC_KEY',credential=lambda _:'synthetic-not-a-key',transport_factory=Fake,clock=c,sleep=c.sleep)
        _,obs=reconcile(plan,root,operation)
        for i in ids:
            symbol='SYNTH_'+str(i);o=obs[symbol];rawref=put(p.load(o['raw']))
            observation=put({'raw_ref':rawref,'lineage':o['lineage']})
            issuer=put({'security_id':i,'symbol':symbol,'current_holder_security_id':i,'continuity':'same_issuer','valid_from':'2010-01-01T00:00:00+00:00','valid_through':'2099-01-01T00:00:00+00:00','source_ref':source})
            events=put({'security_id':i,'validated_at':'2020-09-02T13:00:00+00:00','source_ref':source,'records':[{'date':r['date'],'date_meaning':'evidenced_release_date','event_link':'resolved','accounting':'synthetic-matched','currency':'SYNTH_CCY','share_basis':'synthetic-common-share-basis'} for r in histories[symbol]]})
            subjects.append({'security_id':i,'symbol':symbol,'observation':observation,'issuer':issuer,'events':events})
    return {'manifest':p.canonical({'version':VERSION,'policy':policy,'plan':put(plan),'subjects':subjects}).decode(),'files':files}


def attach(payload):
    from factorlab.monthly_producer import PRODUCER_VERSION
    pack=evidence(payload);a=assess(pack,CUTOFF)
    payload.update(producer_version=PRODUCER_VERSION,sue_evidence=pack,research_cutoff=CUTOFF,sue_history=a['history'],surprises=a['rows'])
    return pack


def publish_files(root,pack):
    root.chmod(0o700)
    from factorlab.sue_store import publish
    for h,raw in pack['files'].items():publish(root/h,raw.encode())
    ref=p.digest(pack['manifest'].encode());publish(root/ref,pack['manifest'].encode());return ref


def change(pack,which,fn,index=0):
    """Reseal only NEW synthetic evidence; original production artifacts never read."""
    m=p.load(pack['manifest'].encode());ref=m[which] if which in ('policy','plan') else m['subjects'][index][which]
    obj=p.load(pack['files'][ref].encode());fn(obj);raw=p.canonical(obj);new=p.digest(raw);pack['files'][new]=raw.decode()
    if which in ('policy','plan'):m[which]=new
    else:m['subjects'][index][which]=new
    pack['manifest']=p.canonical(m).decode()
