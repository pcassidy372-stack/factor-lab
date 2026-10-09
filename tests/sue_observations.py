"""Synthetic v2 observation fixtures using the shipped acquisition/reconcile path."""
import tempfile
from pathlib import Path
from factorlab.sue_acquire import acquire,reconcile
from factorlab import sue_plan as p


def preview(plan, payloads):
    class Transport:
        def get(self,symbol,*args):return 200,payloads[symbol]
        def close(self):pass
    class Clock:
        t=1900000000.
        def __call__(self):return self.t
        def sleep(self,n):self.t+=n
    c=Clock()
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);root.chmod(0o700)
        acquire(plan,root,'synthetic-preview','synthetic-review','SYNTH_KEY',credential=lambda _:'synthetic-key-only',transport_factory=Transport,clock=c,sleep=c.sleep)
        _,observations=reconcile(plan,root,'synthetic-preview')
        shards=[s['body'] for s in p.proposal_shards(plan,observations)]
        return {'body':{'events':[e for s in shards for e in s['events']], 'gaps':[s['gap'] for s in shards],
                        'coverage':plan['body']['coverage'],'new_observation_usable':sum(any(e['in_window'] and e['proposed']['sue'] is not None for e in s['events']) for s in shards)}}
