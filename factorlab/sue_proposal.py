"""Bounded proposal shards and a final, resumable, hash-verified manifest."""
import os
from .sue_plan import (MAX_BYTES, canonical, digest, seal, open_document, require,
                       validate_plan, proposal_shards, load)
from .sue_store import (safe_path, private_root, publish, read, Store)

MAX_TOTAL = 64 * 1024**3
RESERVE = 64 * 1024**2


def resource_bounds(plan):
    b=validate_plan(plan)
    # Every selected member has exactly one allowed attribution. No held acquisition.
    count=len(b['selected'])
    output=(count+2)*MAX_BYTES
    raw=b['limits']['attempts']*(b['limits']['response_bytes']+16384)
    require(output<=MAX_TOTAL,'aggregate_output_bound')
    return {'file_bytes':MAX_BYTES,'shards':count,'aggregate_bytes':output,
            'raw_campaign_bytes':raw,'free_reserve_bytes':RESERVE}


def check_capacity(root, plan):
    root=private_root(root);bounds=resource_bounds(plan)
    free=os.statvfs(root).f_bavail*os.statvfs(root).f_frsize
    require(free>=bounds['aggregate_bytes']+bounds['raw_campaign_bytes']+RESERVE,'capacity_precondition')
    return bounds


def verify_proposal(path):
    path=safe_path(path);doc=load(read(path));body=open_document(doc,'sue-proposal-manifest')
    parts=private_root(path.parent/(path.name+'.shards'))
    require(digest(read(parts/'intent.json'))==body['intent_sha256'],'proposal_intent_link')
    total=0;count=0;seen=set()
    for ref in body['shards']:
        require(ref['file']==digest(ref['security'].encode())+'.json' and ref['security'] not in seen,'proposal_shard_path')
        seen.add(ref['security']);raw=read(parts/ref['file']);total+=len(raw)
        require(len(raw)==ref['bytes'] and digest(raw)==ref['sha256'],'proposal_shard_hash')
        shard=open_document(load(raw),'sue-proposal-shard')
        require(shard['security']==ref['security'] and shard['plan_sha256']==body['plan_sha256'] and shard['observation']['sha256']==ref['observation_id'],'proposal_shard_link')
        require(len(shard['events'])==ref['events'],'proposal_event_count');count+=len(shard['events'])
    require(count==body['event_count'] and total==body['shard_bytes'] and total<=body['bounds']['aggregate_bytes'],'proposal_totals')
    complete=open_document(load(read(parts/'complete.json')),'sue-proposal-completion')
    require(complete['manifest_sha256']==digest(canonical(doc)) and complete['event_count']==count,'proposal_completion_link')
    return doc


def write_proposal(plan, observations, output, checkpoint=lambda _:None):
    validate_plan(plan);output=safe_path(output);private_root(output.parent)
    bounds=resource_bounds(plan)
    # This manifest never holds campaign rows: at most one bounded security shard.
    parts=output.parent/(output.name+'.shards')
    if not parts.exists():parts.mkdir(mode=0o700)
    private_root(parts)
    # Reusing Store's exact exclusive lock gives one local proposal writer.
    store=Store(output.parent,parts.name)
    with store.lock():
        # Descriptor identity is independent of raw byte equality.
        identities={s:observations[s]['lineage']['sha256'] for s in observations}
        intent=seal('sue-proposal-intent',{'plan_sha256':plan['sha256'],'observations':identities,'bounds':bounds})
        intent_raw=canonical(intent)
        if (parts/'intent.json').exists():require(read(parts/'intent.json')==intent_raw,'proposal_operation_conflict')
        else:publish(parts/'intent.json',intent_raw)
        refs=[];count=0;total=0;usable=0
        for doc in proposal_shards(plan,observations):
            raw=canonical(doc);b=doc['body'];filename=digest(b['security'].encode())+'.json'
            total+=len(raw);require(len(raw)<=MAX_BYTES and total<=bounds['aggregate_bytes'],'proposal_resource_bound')
            require(os.statvfs(parts).f_bavail*os.statvfs(parts).f_frsize>=len(raw)+RESERVE,'proposal_disk_bound')
            path=parts/filename
            if path.exists():require(read(path)==raw,'proposal_existing_shard_conflict')
            else:publish(path,raw)
            refs.append({'file':filename,'security':b['security'],'bytes':len(raw),'sha256':digest(raw),
                         'events':len(b['events']),'observation_id':b['observation']['sha256']})
            count+=len(b['events']);usable+=any(e['in_window'] and e['proposed']['sue'] is not None for e in b['events'])
            checkpoint('shard')
        require(len(refs)==bounds['shards'],'proposal_scope_count')
        manifest=seal('sue-proposal-manifest',{'plan_sha256':plan['sha256'],'coverage':plan['body']['coverage'],
            'intent_sha256':digest(intent_raw),'observations':identities,'shards':refs,'event_count':count,
            'shard_bytes':total,'bounds':bounds,'new_observation_usable':usable,'applicable':False,
            'semantics':'UNVERIFIED_PROVIDER_DATE_AND_PIT'})
        complete=seal('sue-proposal-completion',{'manifest_sha256':digest(canonical(manifest)),'event_count':count})
        checkpoint('before_completion')
        for path,doc in [(parts/'complete.json',complete),(output,manifest)]:
            raw=canonical(doc)
            if path.exists():require(read(path)==raw,'proposal_completed_conflict')
            else:publish(path,raw)
        return verify_proposal(output)
