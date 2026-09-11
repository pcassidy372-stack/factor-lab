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


def test_factor_chain_stops_at_first_failed_child(scheduler, monkeypatch):
    calls = []
    def run(root, script, timeout):
        calls.append(script)
        if script == "scripts/factor_eval.py":
            raise JobFailure("child_failed")
    monkeypatch.setattr(scheduler, "checked_script", run)
    with pytest.raises(JobFailure):
        scheduler.run_factor_chain()
    assert calls == ["scripts/factor_compute_v2.py", "scripts/factor_eval.py"]


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
