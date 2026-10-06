"""Import the actual scheduler; replace every external I/O dependency explicitly."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("psycopg2", reason="Scheduler import requires installed PostgreSQL driver")
from factorlab.job_health import JobFailure


@pytest.fixture
def scheduler():
    path = Path(__file__).resolve().parents[1] / "scripts/incremental.py"
    spec = importlib.util.spec_from_file_location("factorlab_incremental_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScopeDB:
    def __init__(self, members=None):
        self.members, self.closed = members or [], False
    def safe(self, fn):
        return fn(self)
    def execute(self, *args):
        pass
    def fetchall(self):
        return self.members
    def close(self):
        self.closed = True


def test_main_propagates_daily_failure_to_exit(scheduler, monkeypatch):
    db, records = ScopeDB(), []
    monkeypatch.setattr(scheduler, "RDB", lambda: db)
    monkeypatch.setattr(scheduler, "claim", lambda *a: True)
    monkeypatch.setattr(scheduler, "finish", lambda *a: records.append(a))
    monkeypatch.setattr(scheduler, "job_daily", lambda _: {"prices": {"error": 1}})
    assert scheduler.main(["--force", "daily"]) == 1
    assert records[0][3] == "error" and db.closed


def test_main_success_exit(scheduler, monkeypatch):
    db, records = ScopeDB(), []
    monkeypatch.setattr(scheduler, "RDB", lambda: db)
    monkeypatch.setattr(scheduler, "claim", lambda *a: True)
    monkeypatch.setattr(scheduler, "finish", lambda *a: records.append(a))
    monkeypatch.setattr(scheduler, "job_weekly", lambda _: {"statements": {"unchanged": 4}})
    assert scheduler.main(["--force", "weekly"]) == 0
    assert records[0][3] == "ok" and db.closed


def test_main_persistence_error_is_redacted(scheduler, monkeypatch, capsys):
    db = ScopeDB()
    monkeypatch.setattr(scheduler, "RDB", lambda: db)
    def fail(*a):
        raise RuntimeError("password=SECRET")
    monkeypatch.setattr(scheduler, "claim", fail)
    assert scheduler.main(["--force", "weekly"]) == 1
    assert "SECRET" not in capsys.readouterr().out and db.closed


def test_no_due_job_opens_no_database(scheduler, monkeypatch):
    monkeypatch.setattr(scheduler, "NOW", datetime(2026, 9, 1, 1, tzinfo=timezone.utc))
    def fail():
        raise AssertionError("database constructed without due job")
    monkeypatch.setattr(scheduler, "RDB", fail)
    assert scheduler.main([]) == 0


def test_weekly_statement_errors_fail_overall_job(scheduler, monkeypatch):
    monkeypatch.setattr(scheduler, "FMPClient", lambda **k: object())
    monkeypatch.setattr(scheduler, "RDB", ScopeDB)
    def fail(*a):
        raise ValueError("apikey=SECRET")
    monkeypatch.setattr(scheduler, "ingest_security", fail)
    stages = []
    monkeypatch.setattr(scheduler, "checked_script", lambda *a, **k: stages.append(a[1]))
    with pytest.raises(JobFailure) as error:
        scheduler.job_weekly(ScopeDB([(1, "SYNTH", "123")]))
    assert error.value.detail["statements"]["error"] == 1
    assert "SECRET" not in str(error.value.detail)
    assert stages == ["scripts/estimates_snapshot.py"]


def test_estimates_failure_retains_statement_counts(scheduler, monkeypatch):
    monkeypatch.setattr(scheduler, "FMPClient", lambda **k: object())
    monkeypatch.setattr(scheduler, "RDB", ScopeDB)
    monkeypatch.setattr(scheduler, "ingest_security", lambda *a: {"unchanged": 1})
    def fail(*a, **k):
        raise JobFailure("child_failed", {"failed_stage": "scripts/estimates_snapshot.py", "returncode": 1})
    monkeypatch.setattr(scheduler, "checked_script", fail)
    with pytest.raises(JobFailure) as error:
        scheduler.job_weekly(ScopeDB([(1, "SYNTH", "123")]))
    assert error.value.detail["statements"]["unchanged"] == 1
    assert error.value.detail["failed_stage"] == "scripts/estimates_snapshot.py"


def test_empty_weekly_scope_fails(scheduler):
    with pytest.raises(JobFailure, match="empty_weekly_scope"):
        scheduler.job_weekly(ScopeDB())


def test_monthly_uses_new_coordinator_not_legacy_rebuild(scheduler, monkeypatch):
    calls = []
    monkeypatch.setenv("FACTORLAB_CODE_SHA", "a" * 40)
    monkeypatch.setenv("FACTORLAB_BENCHMARK_SELECTION", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("FACTORLAB_BENCHMARK_VINTAGE", "22222222-2222-2222-2222-222222222222")
    def run(connect, period, revision, **kwargs):
        calls.append((period, revision, kwargs))
        return {"status": "published"}
    monkeypatch.setattr(scheduler, "run_monthly_period", run)
    assert scheduler.job_monthly(ScopeDB(), "2026-09") == {"status": "published"}
    assert calls[0][:2] == ("2026-09", "a" * 40)
    assert calls[0][2]["selection_event"] == "11111111-1111-1111-1111-111111111111"
    assert calls[0][2]["vintage_id"] == "22222222-2222-2222-2222-222222222222"
    assert "run_factor_chain" not in vars(scheduler)
    assert "universe_build.py" not in Path(scheduler.__file__).read_text()


def test_daily_calendar_failure_stops_before_writes(scheduler, monkeypatch):
    class FailedClient:
        def get(self, *a, **k):
            raise ValueError("apikey=SECRET")
    monkeypatch.setattr(scheduler, "FMPClient", lambda **k: FailedClient())
    monkeypatch.setattr(scheduler, "active_secs", lambda db: [])
    class NoWrites:
        def safe(self, *a):
            raise AssertionError("calendar failure must stop before writes")
    with pytest.raises(JobFailure, match="source_failed") as error:
        scheduler.job_daily(NoWrites())
    assert error.value.detail["source_errors"] == 2
    assert "SECRET" not in str(error.value.detail)


def test_monthly_main_bypasses_old_claim_without_rewriting_it(scheduler, monkeypatch, capsys):
    db=ScopeDB()
    monkeypatch.setattr(scheduler,'RDB',lambda:db)
    def forbidden(*a): raise AssertionError('legacy monthly claim/finish invoked')
    monkeypatch.setattr(scheduler,'claim',forbidden)
    monkeypatch.setattr(scheduler,'finish',forbidden)
    monkeypatch.setattr(scheduler,'job_monthly',lambda db,key:{'status':'published','trading_authority':False})
    assert scheduler.main(['--force','monthly'])==0
    assert 'published' in capsys.readouterr().out and db.closed


def test_monthly_failure_stops_without_legacy_finish_or_false_success(scheduler, monkeypatch, capsys):
    db=ScopeDB();monkeypatch.setattr(scheduler,'RDB',lambda:db)
    def fail(*a): raise RuntimeError('secret-password')
    monkeypatch.setattr(scheduler,'job_monthly',fail)
    monkeypatch.setattr(scheduler,'finish',lambda *a:pytest.fail('legacy row must stay untouched'))
    assert scheduler.main(['--force','monthly'])==1
    out=capsys.readouterr().out
    assert 'secret-password' not in out and db.closed
