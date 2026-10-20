"""Option A research qualification. Explicit local evidence; no production resolver.

Hashes establish consistency, not truth, human approval, or trading authority.
Existing observations retain their original code/schema authority.
"""
from datetime import datetime, timezone, timedelta
from fractions import Fraction
from decimal import Decimal
from pathlib import Path
import re
from .sue_plan import (canonical, digest, load, exact_keys, text, day, number,
                       validate_plan, calculate, parse_response, require, Invalid, open_document)
from .sue_acquire import validate_observation
from .sue_store import read, write_json, private_root

VERSION = 'sue-observed-research-use-v2-exact-epoch'
USE = 'observed-revised-history-research'
MAX_FILES = 5000
MAX_TOTAL = 32 * 1024 * 1024


def utc(value):
    try:
        if isinstance(value, str):
            require(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value) is not None, 'sue_use_aware_time_required')
        d = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
        require(isinstance(d, datetime) and d.tzinfo is not None and d.utcoffset() is not None,
                'sue_use_aware_time_required')
        return d.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, AttributeError):
        raise Invalid('sue_use_aware_time_required') from None


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MIN_EPOCH = Fraction(-62135596800)
MAX_EPOCH = Fraction(253402300799999999, 1000000)


def exact_epoch(value):
    """Bounded exact receipt number, never binary float or context arithmetic."""
    require(not isinstance(value, str) or len(value)<=128, 'sue_use_epoch_bound')
    require(type(value) is not int or value.bit_length()<=256, 'sue_use_epoch_bound')
    n=number(value)
    parts=n.as_tuple()
    require(len(parts.digits)<=64 and abs(parts.exponent)<=64, 'sue_use_epoch_bound')
    result=Fraction(n)
    require(MIN_EPOCH<=result<=MAX_EPOCH,'sue_use_epoch_range')
    return result


def instant_epoch(value):
    """Aware ISO boundary to exact rational seconds; no total_seconds float."""
    delta=datetime.fromisoformat(utc(value))-EPOCH
    return Fraction((delta.days*86400+delta.seconds)*1000000+delta.microseconds,1000000)


def stamp(value):
    """First representable microsecond NOT EARLIER than exact completion."""
    micros=exact_epoch(value)*1000000
    upward=-(-micros.numerator//micros.denominator)
    return (EPOCH+timedelta(microseconds=upward)).isoformat()


def at(value):
    return datetime.fromisoformat(utc(value))


def later(a, b):
    return utc(max(at(a), at(b)))


def reference(ref):
    require(isinstance(ref,str) and len(ref)==64 and all(c in '0123456789abcdef' for c in ref), 'sue_use_reference')
    return ref


def normalized_rows(rows):
    out=[];seen=set()
    require(isinstance(rows,list) and len(rows)<=120000, 'sue_use_rows_bound')
    for r in rows:
        exact_keys(r, ('security_id','report_date','eps_actual','eps_est','sue'))
        sid=r['security_id'];require(type(sid) is int and sid>0,'sue_use_security')
        d=r['report_date'].isoformat() if hasattr(r['report_date'],'isoformat') else r['report_date'];day(d)
        require((sid,d) not in seen,'sue_use_duplicate_row');seen.add((sid,d))
        n={'security_id':sid,'report_date':d}
        for k in ('eps_actual','eps_est','sue'):
            v=r[k]
            if v is None:n[k]=None
            else:
                value=number(str(v))
                require(len(value.as_tuple().digits)<=4096 and abs(value.as_tuple().exponent)<=4096,'sue_use_numeric_identity_bound')
                fixed=format(value,'f')
                n[k]=(fixed.rstrip('0').rstrip('.') if '.' in fixed else fixed)
                if number(n[k])==0:n[k]='0'
        out.append(n)
    return sorted(out,key=lambda r:(r['security_id'],r['report_date']))


def package_from_files(root, manifest_hash):
    """Explicit bounded content-addressed files; never follow a path from a document."""
    root=private_root(root);reference(manifest_hash)
    manifest=read(root/manifest_hash);require(digest(manifest)==manifest_hash,'sue_use_manifest_hash')
    doc=load(manifest);exact_keys(doc,('version','policy','plan','subjects'))
    refs={doc['policy'],doc['plan']}
    require(isinstance(doc['subjects'],list) and len(doc['subjects'])<=1000,'sue_use_subject_bound')
    for s in doc['subjects']:
        exact_keys(s,('security_id','symbol','observation','issuer','events'));refs.update(s[k] for k in ('observation','issuer','events'))
    files={};total=len(manifest)
    # Referenced observation payload and evidence source files are also explicit.
    pending=list(refs)
    while pending:
        ref=reference(pending.pop())
        if ref in files:continue
        raw=read(root/ref);require(digest(raw)==ref,'sue_use_file_hash');total+=len(raw)
        require(total<=MAX_TOTAL and len(files)<MAX_FILES,'sue_use_package_bound')
        files[ref]=raw.decode('utf-8')
        obj=load(raw)
        if isinstance(obj,dict):
            for key in ('raw_ref','source_ref'):
                if key in obj:pending.append(obj[key])
    return {'manifest':manifest.decode('utf-8'),'files':files}


def assess(package, cutoff):
    """Revalidate every consumed byte/link. No qualified=true shortcut or DB lookup."""
    cutoff=utc(cutoff);exact_keys(package,('manifest','files'))
    require(isinstance(package['manifest'],str) and isinstance(package['files'],dict),'sue_use_package_shape')
    require(len(package['files'])<=MAX_FILES and sum(len(x.encode()) for x in package['files'].values() if isinstance(x,str))+len(package['manifest'].encode())<=MAX_TOTAL,'sue_use_package_bound')
    def get(ref):
        reference(ref);raw=package['files'].get(ref)
        require(isinstance(raw,str) and digest(raw.encode())==ref,'sue_use_evidence_missing_or_tampered')
        return load(raw.encode())
    def source(ref):
        obj=get(ref);exact_keys(obj,('kind','reference','record'))
        require(obj['kind']=='external-evidence-record' and isinstance(obj['record'],str) and 0<len(obj['record'])<=10000,'sue_use_evidence_record')
        text(obj['reference'])
    m=load(package['manifest'].encode());exact_keys(m,('version','policy','plan','subjects'))
    require(m['version']==VERSION,'sue_use_version')
    policy=get(m['policy']);exact_keys(policy,('use','version','research_approval','available_at','source_ref'))
    require(policy['use']==USE,'sue_use_original_consensus_unsupported');require(policy['version']==VERSION,'sue_use_policy_version')
    text(policy['research_approval']);source(policy['source_ref']);available=utc(policy['available_at'])
    plan=get(m['plan']);b=validate_plan(plan)
    require(isinstance(m['subjects'],list) and 0<len(m['subjects'])<=1000,'sue_use_subject_bound')
    members={x['id']:x for x in b['scope']['members']};seen=set();history=[];observations=[];outcomes=[]
    for s in m['subjects']:
        exact_keys(s,('security_id','symbol','observation','issuer','events'))
        sid=s['security_id'];require(type(sid) is int and sid>0 and sid not in seen,'sue_use_security');seen.add(sid)
        member=members.get(str(sid));require(member is not None and not member['held'],'sue_use_identity_held')
        req=next((r for r in b['requests'] if r['symbol']==s['symbol']),None)
        require(req is not None and len(req['attributions'])==1 and req['attributions'][0]['id']==str(sid),'sue_use_current_holder_attribution')
        o=get(s['observation']);exact_keys(o,('raw_ref','lineage'))
        raw_text=package['files'].get(o['raw_ref']);get(o['raw_ref'])
        # Bound numeric clock inputs before the unchanged receipt validator's arithmetic.
        clock_line=open_document(o['lineage'],'sue-observation')
        interval=clock_line['observation_interval'];exact_keys(interval,('started','finished'))
        for value in interval.values():exact_epoch(value)
        campaign_clock=open_document(clock_line['campaign'],'sue-campaign')
        for value in (campaign_clock['started'],campaign_clock['deadline'],clock_line['intent']['started'],
                      open_document(clock_line['receipt'],'sue-attempt-receipt')['finished'],
                      open_document(clock_line['status_receipt'],'sue-http-status')['at']):exact_epoch(value)
        raw,line=validate_observation(plan,s['symbol'],{'raw':raw_text.encode(),'lineage':o['lineage']})
        started=exact_epoch(line['observation_interval']['started'])
        finished=exact_epoch(line['observation_interval']['finished'])
        require(started<=finished,'sue_use_observation_time_order')
        require(finished<=instant_epoch(cutoff),'sue_use_after_cutoff')
        available=later(available,stamp(line['observation_interval']['finished']))
        issuer=get(s['issuer']);exact_keys(issuer,('security_id','symbol','current_holder_security_id','continuity','valid_from','valid_through','source_ref'))
        source(issuer['source_ref'])
        require(type(issuer['security_id']) is int and type(issuer['current_holder_security_id']) is int and issuer['security_id']==sid==issuer['current_holder_security_id'] and issuer['symbol']==s['symbol'],'sue_use_current_issuer_mismatch')
        require(issuer['continuity'] in ('same_issuer','evidenced_continuous_rename'),'sue_use_issuer_continuity')
        require(instant_epoch(issuer['valid_from'])<=started<=finished<=instant_epoch(issuer['valid_through']),'sue_use_issuer_observation_window')
        ev=get(s['events']);exact_keys(ev,('security_id','validated_at','source_ref','records'))
        source(ev['source_ref']);require(type(ev['security_id']) is int and ev['security_id']==sid,'sue_use_event_security');available=later(available,ev['validated_at'])
        rows,_=parse_response(raw,s['symbol']);w=req['attributions'][0]
        attributed=[r for r in rows if w['start']<=r['date']<=w['end'] and r['date']<=b['scope']['asof']]
        require(isinstance(ev['records'],list),'sue_use_event_records');records={}
        for r in ev['records']:
            exact_keys(r,('date','date_meaning','event_link','accounting','currency','share_basis'))
            day(r['date']);require(r['date'] not in records,'sue_use_duplicate_event_evidence')
            require(r['date_meaning']=='evidenced_release_date' and r['event_link']=='resolved','sue_use_date_link_unresolved')
            for k in ('accounting','currency','share_basis'):text(r[k])
            records[r['date']]=r
        require(set(records)=={r['date'] for r in attributed},'sue_use_dependency_evidence_incomplete')
        # No mixed observations within a security; all used differences require one explicit compatible basis.
        require(len({(r['accounting'],r['currency'],r['share_basis']) for r in records.values()})<=1,'sue_use_incompatible_basis')
        values,_=calculate(attributed,b['coverage']['window_start'],b['scope']['asof'])
        for r in values:
            history.append(dict(security_id=sid,report_date=r['date'],eps_actual=r['actual'],eps_est=r['estimate'],sue=r['sue']))
        observations.append(o['lineage']['sha256']);outcomes.append({'security_id':sid,'state':line['receipt']['body']['state'],'reasons':[r['reason'] for r in values]})
    require(seen=={int(x) for x in b['selected']},'sue_use_subject_selection')
    require(at(available)<=at(cutoff),'sue_use_after_cutoff')
    history=normalized_rows(history);rows=[r for r in history if b['coverage']['window_start']<=r['report_date']<=b['scope']['asof']]
    return {'version':VERSION,'use':USE,'policy':{**policy,'available_at':utc(policy['available_at'])},'scope_ids':sorted(int(x) for x in members),
            'period':b['scope']['period'],'asof':b['scope']['asof'],'rows':rows,'history':history,
            'observation_ids':observations,'evidence_identity':digest(canonical(package)),
            'not_before':available,'outcomes':outcomes,'historical_pre_event_qualified':False,'trading_authority':False}


def require_snapshot(package, cutoff, rows, history, scope_ids, asof):
    a=assess(package,cutoff)
    require(a['asof']==str(asof) and a['scope_ids']==sorted(scope_ids),'sue_use_snapshot_scope')
    require(a['rows']==normalized_rows(rows),'sue_use_snapshot_rows')
    require(a['history']==normalized_rows(history),'sue_use_snapshot_dependencies')
    return a


def production_resolver():
    raise Invalid('sue_use_production_resolution_unavailable')
