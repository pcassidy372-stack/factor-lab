"""Real snapshot/publication guards with synthetic retained evidence, no provider."""
import copy
import pytest
from test_publication_postgres import database,query
from test_monthly_producer_postgres import seed,capture_month,run_monthly_period,REVISION
from sue_use_samples import CUTOFF,change,publish_files
from factorlab.sue_use_contract import package_from_files,assess,Invalid
from factorlab.monthly_producer import FrozenMonthlyInputs,prepare_publication,fingerprint
from factorlab.publication_contract import PublicationError
pytestmark=pytest.mark.postgres


def test_actual_package_files_capture_publication_and_matching_reuse(database,tmp_path,record_property):
    seed(database);ref=publish_files(tmp_path,database.sue_evidence)
    database.sue_evidence=package_from_files(tmp_path,ref)
    frozen=capture_month(database,'2020-09');spec,produce=prepare_publication(frozen,REVISION)
    assert spec['input_fingerprints']['surprises']==fingerprint(assess(database.sue_evidence,CUTOFF))
    first=run_monthly_period(database,'2020-09',REVISION)
    def forbidden(*a,**kw):raise AssertionError('matching reuse must not capture/refresh')
    assert run_monthly_period(database,'2020-09',REVISION,capture=forbidden,refresh=forbidden)['generation_id']==first['generation_id']
    assert query(database,'SELECT count(*) FROM fl_dataset_generations')==[(1,)]
    record_property('sue_use_evidence','synthetic files verified; actual collector/producer/publisher; matching reuse')

@pytest.mark.parametrize('mutation',['row','prior'])
def test_snapshot_mismatch_refuses_actual_rows_and_dependencies(database,mutation):
    seed(database)
    d='2020-08-01' if mutation=='row' else '2019-09-01'
    query(database,'UPDATE surprises SET eps_actual=999 WHERE security_id=1 AND report_date=%s',(d,))
    with pytest.raises(Invalid,match='snapshot_rows|snapshot_dependencies'):capture_month(database,'2020-09')
    assert query(database,'SELECT count(*) FROM fl_dataset_generations')==[(0,)]

@pytest.mark.parametrize('kind',['policy','observation'])
def test_same_numbers_changed_identity_refused_before_early_confirm(database,kind):
    seed(database);run_monthly_period(database,'2020-09',REVISION)
    old=copy.deepcopy(database.sue_evidence)
    if kind=='policy':change(database.sue_evidence,'policy',lambda p:p.update(research_approval='OTHER-SYNTHETIC-REVIEW'))
    else:
        from sue_use_samples import evidence
        from monthly_samples import monthly_sample
        database.sue_evidence=evidence(monthly_sample(),'another-observation')
    assert assess(old,CUTOFF)['rows']==assess(database.sue_evidence,CUTOFF)['rows']
    def forbidden(*a,**kw):raise AssertionError('early refusal must precede refresh/capture')
    with pytest.raises(PublicationError,match='existing_generation_sue_identity_mismatch'):
        run_monthly_period(database,'2020-09',REVISION,refresh=forbidden,capture=forbidden)
    assert query(database,'SELECT count(*) FROM fl_publication_receipts')==[(1,)]


def test_legacy_sue_group_refused_with_matching_benchmark(database):
    from factorlab.monthly_producer import replay_monthly,BENCHMARK_PRODUCER_VERSION
    from factorlab.publications import PublicationStore
    seed(database);p=capture_month(database,'2020-09').unpack();p['producer_version']=BENCHMARK_PRODUCER_VERSION
    old=replay_monthly(FrozenMonthlyInputs.freeze(p),REVISION)
    PublicationStore(database).publish(old['spec'],lambda _:(old['universe'],old['factors']))
    with pytest.raises(PublicationError,match='existing_generation_sue_identity_mismatch'):run_monthly_period(database,'2020-09',REVISION)
    assert query(database,'SELECT count(*) FROM fl_dataset_generations')==[(1,)]


def test_legacy_replay_numeric_and_non_sue_groups_preserved(database):
    from factorlab.monthly_producer import replay_monthly,BENCHMARK_PRODUCER_VERSION
    seed(database);frozen=capture_month(database,'2020-09');spec,produce=prepare_publication(frozen,REVISION);u,f=produce(spec)
    p=frozen.unpack();p['producer_version']=BENCHMARK_PRODUCER_VERSION
    old=replay_monthly(FrozenMonthlyInputs.freeze(p),REVISION)
    assert (u,f)==(old['universe'],old['factors'])
    for k in spec['input_fingerprints']:
        if k!='surprises':assert spec['input_fingerprints'][k]==old['spec']['input_fingerprints'][k]
    assert old['spec']['input_fingerprints']['surprises']==fingerprint(p['surprises'])


def test_concurrent_history_update_keeps_snapshot_coherent(database):
    from factorlab.monthly_snapshot import capture_month as real_capture
    seed(database)
    class Cursor:
        def __init__(self,c):self.c=c
        def __getattr__(self,n):return getattr(self.c,n)
        def __enter__(self):self.c.__enter__();return self
        def __exit__(self,*a):return self.c.__exit__(*a)
        def execute(self,sql,args=()):
            self.c.execute(sql,args)
            if 'CROSS JOIN LATERAL' in sql:query(database,"UPDATE surprises SET eps_actual=999 WHERE security_id=1 AND report_date='2019-09-01'")
    class Conn:
        def __init__(self):self.cx=database.runtime('reader')()
        def __getattr__(self,n):return getattr(self.cx,n)
        def cursor(self):return Cursor(self.cx.cursor())
    p=real_capture(Conn,'2020-09',selection_event=database.selection_event,vintage_id=database.vintage_id,sue_evidence=database.sue_evidence,research_cutoff=CUTOFF)
    assert p.unpack()['sue_history'][0]['eps_actual']=='-1'
    with pytest.raises(Invalid,match='snapshot_dependencies'):capture_month(database,'2020-09')
