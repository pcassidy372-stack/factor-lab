"""Actual isolated DB publication/reuse with synthetic exact-clock evidence."""
import pytest
from test_publication_postgres import database,query
from test_monthly_producer_postgres import seed,REVISION
from monthly_samples import monthly_sample
from sue_time_samples import timed_package
from sue_use_samples import CUTOFF,change
from factorlab.monthly_scheduler import run_monthly_period
from factorlab.sue_use_contract import Invalid
pytestmark=pytest.mark.postgres


def test_exact_receipt_cutoff_refuses_before_new_publication(database):
    seed(database);package=timed_package(payload=monthly_sample())
    with pytest.raises(Invalid,match='after_cutoff'):
        run_monthly_period(database.runtime('publisher'),'2020-09',REVISION,selection_event=database.selection_event,vintage_id=database.vintage_id,sue_evidence=package,research_cutoff='2020-09-02T12:00:00Z')
    assert query(database,'SELECT count(*) FROM fl_dataset_generations')==[(0,)]
    assert query(database,'SELECT count(*) FROM fl_monthly_attempts')==[(0,)]


def test_exact_boundary_before_early_reuse_and_matching_reuse(database,record_property):
    seed(database);package=timed_package(payload=monthly_sample())
    args=dict(selection_event=database.selection_event,vintage_id=database.vintage_id,sue_evidence=package)
    first=run_monthly_period(database.runtime('publisher'),'2020-09',REVISION,research_cutoff=CUTOFF,**args)
    def denied(*a,**kw):raise AssertionError('no connection/refresh/capture for ineligible reuse')
    with pytest.raises(Invalid,match='after_cutoff'):
        run_monthly_period(denied,'2020-09',REVISION,research_cutoff='2020-09-02T12:00:00Z',capture=denied,refresh=denied,**args)
    second=run_monthly_period(database.runtime('publisher'),'2020-09',REVISION,research_cutoff=CUTOFF,capture=denied,refresh=denied,**args)
    assert second['generation_id']==first['generation_id'] and second['status']=='reused'
    assert query(database,'SELECT count(*) FROM fl_dataset_generations')==[(1,)]
    record_property('exact_receipt_time','submicrosecond cutoff refused before connect; matching actual generation reused without refresh/capture')


def test_exact_issuer_left_edge_refuses_before_actual_publication(database):
    seed(database);package=timed_package('1599047999.9999996','1599048000',payload=monthly_sample())
    change(package,'issuer',lambda x:x.update(valid_from='2020-09-02T12:00:00Z'))
    with pytest.raises(Invalid,match='issuer_observation_window'):
        run_monthly_period(database.runtime('publisher'),'2020-09',REVISION,selection_event=database.selection_event,vintage_id=database.vintage_id,sue_evidence=package,research_cutoff=CUTOFF)
    assert query(database,'SELECT count(*) FROM fl_dataset_generations')==[(0,)]
