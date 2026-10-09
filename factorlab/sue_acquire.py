"""Bounded SUE-only HTTPS acquisition, durable budgets and observation lineage."""
import os
import re
import time
from collections.abc import Mapping
from urllib.parse import quote
from .sue_plan import (ENDPOINT, Invalid, canonical, calculate, digest, parse_response,
                       require, text, validate_plan, seal, open_document, day, load, exact_keys, number)
from .sue_store import Store, publish
from .sue_worker import run_owned, WorkerFailure

SUCCESS = ('successful-nonempty', 'validated-empty', 'incomplete-history')


def terminal(status):
    return status is not None and status != 200 and status != 429 and not 500 <= status <= 599


class HTTPS:
    def __init__(self):
        self.last_worker = None

    def close(self):
        pass  # Every request's worker/session is already closed and reaped.

    @staticmethod
    def _read(symbol, key, timeout, bound, emit):
        import requests
        session = requests.Session(); session.trust_env = False
        try:
            with session.get(ENDPOINT, params={'symbol': symbol, 'limit': 120, 'apikey': key},
                             timeout=(min(5, timeout), min(15, timeout)), verify=True,
                             allow_redirects=False, stream=True) as response:
                status = response.status_code; emit(status)
                if terminal(status): return status, b''  # Never drain unauthorized/refused bodies.
                chunks = []; count = 0; quota_tail = b''
                for b in response.iter_content(16384):
                    count += len(b); require(count <= bound, 'response_bound'); chunks.append(b)
                    quota_probe = quota_tail + b.lower(); quota_tail = quota_probe[-4:]
                    if status == 429 and b'quota' in quota_probe:
                        return status, b'quota'  # Only a fixed policy classification crosses the pipe.
                return status, b''.join(chunks)
        finally:
            session.close()

    def get(self, symbol, key, timeout, bound, on_status=lambda _: None):
        try:
            result, self.last_worker = run_owned(self._read, (symbol, key, timeout, bound), timeout, on_status)
            return result
        except WorkerFailure as exc:
            self.last_worker = exc.receipt
            raise


class TransportFailure(Exception):
    pass


def campaign(plan, binding, operation):
    exact_keys(binding, ('plan_sha256','operation','approval','started','deadline','seconds','code_contract','code_identity','schema'))
    require(binding['plan_sha256'] == plan['sha256'] and binding['operation'] == operation, 'operation_conflict')
    text(operation); text(binding['approval'])
    start, end = number(binding['started']), number(binding['deadline'])
    require(end-start == binding['seconds'] == plan['body']['limits']['seconds'], 'campaign_timing')
    require(binding['code_identity'] == plan['body']['code_identity'] and binding['code_contract'] == plan['body']['contract'] and binding['schema'] == plan['version'], 'campaign_code')


def validate_observation(plan, symbol, observation):
    exact_keys(observation, ('raw','lineage'))
    lineage = open_document(observation['lineage'], 'sue-observation')
    exact_keys(lineage, ('campaign','campaign_sha256','intent','intent_sha256','receipt','receipt_sha256','status_receipt','status_sha256','payload_sha256','symbol','observation_interval'))
    b = open_document(lineage['campaign'], 'sue-campaign')
    campaign(plan, b, b['operation'])
    require(digest(canonical(lineage['campaign'])) == lineage['campaign_sha256'], 'campaign_link')
    i = lineage['intent']; r = open_document(lineage['receipt'], 'sue-attempt-receipt')
    exact_keys(i, ('symbol','endpoint','limit','plan_sha256','operation','campaign_sha256','started','attempt','budget_before','reserved'))
    exact_keys(r, ('state','error','stop','status','finished','elapsed','charged','worker','validation','payload_sha256','intent_sha256','replayable'))
    require(type(i['attempt']) is int and 1 <= i['attempt'] <= plan['body']['limits']['attempts'] and number(i['budget_before']) >= 0 and number(i['reserved']) > 0, 'intent_shape')
    require(type(r['stop']) is bool and type(r['replayable']) is bool and type(r['status']) is int, 'receipt_shape')
    require(digest(canonical(i)) == lineage['intent_sha256'] == r['intent_sha256'], 'intent_link')
    require(digest(canonical(lineage['receipt'])) == lineage['receipt_sha256'], 'receipt_link')
    require(i['symbol'] == lineage['symbol'] == symbol and i['plan_sha256'] == plan['sha256'] and i['operation'] == b['operation'] and i['campaign_sha256'] == lineage['campaign_sha256'], 'observation_request_link')
    require(i['endpoint'] == ENDPOINT and i['limit'] == 120 and r['state'] in SUCCESS and r['status'] == 200 and not r['stop'] and r['error'] is None and r['replayable'], 'observation_status')
    interval = lineage['observation_interval']; exact_keys(interval, ('started','finished'))
    require(interval == {'started':i['started'],'finished':r['finished']} and number(b['started']) <= number(i['started']) <= number(r['finished']) <= number(b['deadline']), 'observation_timing')
    require(number(r['elapsed']) >= 0 and number(r['charged']) >= number(r['elapsed']), 'elapsed_timing')
    status = open_document(lineage['status_receipt'], 'sue-http-status')
    exact_keys(status, ('status','at','intent_sha256'))
    require(digest(canonical(lineage['status_receipt'])) == lineage['status_sha256'] and status['intent_sha256'] == lineage['intent_sha256'] and status['status'] == 200 and number(i['started']) <= number(status['at']) <= number(r['finished']), 'status_link')
    raw = observation['raw']; require(isinstance(raw,bytes) and digest(raw) == lineage['payload_sha256'] == r['payload_sha256'], 'payload_link')
    rows, duplicate = parse_response(raw, symbol)
    require(isinstance(r['validation'], dict), 'validation_link')
    exact_keys(r['validation'], ('events','identical_duplicates','post_asof_attributed','window_usable_events','semantics','history_complete'))
    require(r['validation']['semantics'] == 'UNVERIFIED_PROVIDER_DATE_AND_PIT' and r['validation']['history_complete'] is False, 'validation_semantics')
    require( r['validation']['events'] == len(rows) and r['validation']['identical_duplicates'] == duplicate, 'validation_link')
    return raw, lineage


class Observations(Mapping):
    """Descriptors only; hash-verify one payload on each access, never load a campaign."""
    def __init__(self, store, plan, binding, entries):
        self.store, self.plan, self.binding, self.entries = store, plan, binding, entries
    def __iter__(self): return iter(self.entries)
    def __len__(self): return len(self.entries)
    def __getitem__(self, symbol):
        stem, intent, receipt = self.entries[symbol]
        receipt_doc = self.store.get(stem+'.receipt'); status_doc = self.store.get(stem+'.status')
        lineage = seal('sue-observation', {'campaign':self.binding,'campaign_sha256':digest(canonical(self.binding)),
            'intent':intent,'intent_sha256':digest(canonical(intent)), 'receipt':receipt_doc,'receipt_sha256':digest(canonical(receipt_doc)),
            'status_receipt':status_doc,'status_sha256':digest(canonical(status_doc)), 'payload_sha256':receipt['payload_sha256'],
            'symbol':symbol,'observation_interval':{'started':intent['started'],'finished':receipt['finished']}})
        observation = {'raw':self.store.raw(stem+'.body'),'lineage':lineage}
        validate_observation(self.plan,symbol,observation)
        return observation


def reconcile(plan, root, operation):
    validate_plan(plan); s = Store(root, operation); binding_doc = s.get('binding.json')
    binding = open_document(binding_doc, 'sue-campaign'); campaign(plan,binding,operation)
    results = []; completed = {}; charged = 0
    requests = {r['symbol'] for r in plan['body']['requests']}
    for stem, intent, receipt in s.attempts():
        require(intent['plan_sha256'] == plan['sha256'] and intent['symbol'] in requests and intent['operation'] == operation and intent['campaign_sha256'] == digest(canonical(binding_doc)), 'operation_conflict')
        require(number(intent['budget_before']) == number(charged), 'budget_chain')
        charged += number(receipt['charged'] if receipt else intent['reserved'])
        status = s.get(stem+'.status')['body']['status'] if stem+'.status' in s.names() else None
        state = receipt['state'] if receipt else ('terminal-status-unknown-body' if terminal(status) else 'interrupted-unknown-request')
        results.append({'attempt':stem,'symbol':intent['symbol'],'state':state,'status':status})
        if receipt and receipt['state'] in SUCCESS:
            require(intent['symbol'] not in completed, 'duplicate_completed_observation')
            completed[intent['symbol']] = (stem,intent,receipt)
    observations = Observations(s,plan,binding_doc,completed)
    # Validate complete lineage without retaining raw campaign bytes.
    for symbol in observations: observations[symbol]
    return {'operation':operation,'plan_sha256':plan['sha256'],'results':results,'charged_seconds':str(charged),
            'held':[{'security':m['id'],'state':'identity-held'} for m in plan['body']['scope']['members'] if m['eligible'] and m['held']],
            'complete':len(completed)==len(requests)}, observations


def acquire(plan, root, operation, approval, credential_env, *, retry_unknown=None,
            credential=None, transport_factory=HTTPS, clock=time.time, sleep=time.sleep,
            monotonic=time.monotonic, checkpoint=lambda _: None):
    from .sue_proposal import check_capacity
    b=validate_plan(plan); text(operation);text(approval);text(credential_env)
    require(credential_env.isidentifier(),'invalid_credential_name')
    if retry_unknown is not None:text(retry_unknown)
    check_capacity(root,plan)
    s=Store(root,operation,create=True)
    with s.lock():
        now=clock()
        if 'binding.json' in s.names():
            binding_doc=s.get('binding.json'); binding=open_document(binding_doc,'sue-campaign')
            campaign(plan,binding,operation);require(binding['approval']==approval,'operation_conflict')
        else:
            binding={'plan_sha256':plan['sha256'],'operation':operation,'approval':approval,'started':now,
                     'deadline':now+b['limits']['seconds'],'seconds':b['limits']['seconds'],
                     'code_contract':b['contract'],'code_identity':b['code_identity'],'schema':plan['version']}
            binding_doc=seal('sue-campaign',binding);s.put('binding.json',binding_doc)
        attempts=s.attempts(); summary,completed=reconcile(plan,root,operation)
        require(not any(terminal(x['status']) or x['state']=='terminal-status-unknown-body' for x in summary['results']), 'campaign_stopped')
        require(not any(r and r['stop'] for _,_,r in attempts),'campaign_stopped')
        if summary['complete']:return summary
        if attempts and (attempts[-1][2] is None or attempts[-1][2]['state']=='interrupted-unknown-request'):
            require(retry_unknown is not None,'uncertain_request_explicit_retry_required')
            stem=attempts[-1][0]
            if stem+'.retry-authorization' not in s.names():s.put(stem+'.retry-authorization',{'reference':retry_unknown,'at':clock()})
        require(len(attempts)<b['limits']['attempts'],'campaign_budget')
        latest=max([float(binding['started'])]+[float((r or i).get('finished',i['started'])) for _,i,r in attempts])
        require(clock()>=latest,'wall_clock_rollback')
        charged=number(summary['charged_seconds']); start_mono=monotonic(); start_charge=float(charged)
        def remaining():
            return min(float(binding['deadline'])-clock(), b['limits']['seconds']-start_charge-(monotonic()-start_mono), b['limits']['seconds']-float(charged))
        require(remaining()>0 and len(attempts)<b['limits']['attempts'],'campaign_budget')
        try:key=(credential or os.environ.get)(credential_env)
        except Exception:raise Invalid('credential_unavailable') from None
        require(isinstance(key,str) and len(key)>=8 and '\n' not in key and '\r' not in key,'credential_unavailable')
        try:transport=transport_factory()
        except Exception:raise Invalid('transport_initialization_failed') from None
        try:
            for req in b['requests']:
                symbol=req['symbol']
                if symbol in completed:continue
                used=sum(i['symbol']==symbol for _,i,_ in attempts)
                while used<=b['limits']['retries']:
                    require(len(attempts)<b['limits']['attempts'],'attempt_budget')
                    previous=attempts[-1][1]['started'] if attempts else None
                    wait=max(0,float(b['limits']['spacing'])-(clock()-float(previous))) if previous is not None else 0
                    if used:wait=max(wait,min(2**used,8))
                    require(wait<remaining(),'time_budget');sleep(wait)
                    allowance=min(20,remaining()-wait);require(allowance>0,'time_budget')
                    stem='a%06d'%(len(attempts)+1)
                    intent={'symbol':symbol,'endpoint':ENDPOINT,'limit':120,'plan_sha256':plan['sha256'],
                        'operation':operation,'campaign_sha256':digest(canonical(binding_doc)),
                        'started':clock(),'attempt':len(attempts)+1,'budget_before':charged,'reserved':allowance+wait}
                    s.put(stem+'.intent',intent); checkpoint('intent')
                    status=None;raw=None;state='request-failure';error=None;stop=False;payload=None;validation=None;worker=None
                    request_start=monotonic()
                    def known_status(value):
                        nonlocal status
                        require(status is None or status==value,'status_changed');status=value
                        if stem+'.status' not in s.names():
                            s.put(stem+'.status',seal('sue-http-status',{'status':value,'at':clock(),'intent_sha256':digest(canonical(intent))}))
                            checkpoint('status')
                    try:
                        if isinstance(transport,HTTPS):status,raw=transport.get(symbol,key,allowance,b['limits']['response_bytes'],known_status)
                        else:
                            status,raw=transport.get(symbol,key,allowance,b['limits']['response_bytes']);known_status(status)
                        worker=getattr(transport,'last_worker',None)
                        require(type(status) is int and isinstance(raw,bytes) and len(raw)<=b['limits']['response_bytes'],'transport_shape')
                        if terminal(status):error='authorization_denied' if status in (401,403) else 'http_refused';stop=True
                        else:
                            require(remaining()>=0,'time_budget')
                            inspected=raw
                            try:inspected+=b'\n'+canonical(load(raw))
                            except Invalid:pass
                            if key.encode() in inspected or quote(key,safe='').encode() in inspected or re.search(rb'(?:password|authorization|api[_-]?key|access[_-]?token)\s*[\"\']?\s*[:=]|https?://[^/\s]+@',inspected,re.I):raise Invalid('secret_echo_nonreplayable')
                            if status==429:error='rate_or_quota_limit';stop=b'quota' in raw.lower()
                            elif 500<=status<=599:error='provider_5xx'
                            else:
                                publish(s.path/(stem+'.body'),raw);payload=digest(raw);checkpoint('body')
                                rows,duplicates=parse_response(raw,symbol);calculated=[];future=0
                                for w in req['attributions']:
                                    attributed=[r for r in rows if day(w['start'])<=day(r['date'])<=day(w['end'])]
                                    values,later=calculate(attributed,b['coverage']['window_start'],b['scope']['asof']);calculated.extend(values);future+=later
                                validation={'events':len(rows),'identical_duplicates':duplicates,'post_asof_attributed':future,
                                    'window_usable_events':sum(r['in_window'] and r['sue'] is not None for r in calculated),
                                    'semantics':'UNVERIFIED_PROVIDER_DATE_AND_PIT','history_complete':False}
                                state='validated-empty' if not rows else ('successful-nonempty' if validation['window_usable_events'] else 'incomplete-history')
                                require(remaining()>=0,'time_budget')
                    except WorkerFailure as exc:
                        worker=exc.receipt;status=exc.status
                        if terminal(status):error='authorization_denied' if status in (401,403) else 'http_refused';stop=True
                        elif exc.code in ('worker_deadline_unknown','worker_exit_unknown'):
                            state='interrupted-unknown-request';error=exc.code
                        elif exc.code=='response_bound':state='schema-failure';error=exc.code;stop=True
                        else:error='transport_failure'
                    except Invalid as exc:
                        state='interrupted-unknown-request' if str(exc)=='time_budget' else 'schema-failure';error=str(exc);stop=state=='schema-failure'
                    except OSError:raise
                    except Exception:error='transport_failure'
                    elapsed=max(0,monotonic()-request_start);cost=max(elapsed,clock()-float(intent['started']))+wait
                    receipt={'state':state,'error':error,'stop':stop,'status':status,'finished':clock(),'elapsed':elapsed,'charged':cost,
                        'worker':worker,'validation':validation,'payload_sha256':payload,'intent_sha256':digest(canonical(intent)),'replayable':payload is not None}
                    s.put(stem+'.receipt',seal('sue-attempt-receipt',receipt));checkpoint('receipt')
                    attempts.append((stem,intent,receipt));charged+=number(str(cost));used+=1
                    if stop or state=='interrupted-unknown-request':return reconcile(plan,root,operation)[0]
                    if payload:break
            return reconcile(plan,root,operation)[0]
        finally:transport.close()
