from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import copy
import json
import re
import subprocess

import pytest

from factorlab.job_health import (JobFailure, checked_script, due_jobs,
                                  execute_job, require_complete, safe_error)
from factorlab.weekly_fundamentals import (INSERT_SQL, curate, curated_hash,
                                          ingest_security)


@pytest.fixture
def statements():
    common = {"date": "2026-06-30", "cik": "000123", "period": "Q2"}
    return {
        "income_q": [dict(common, acceptedDate="2026-08-10 15:00:00",
                          filingDate="2026-08-10", reportedCurrency="USD",
                          revenue=100, grossProfit=40, operatingIncome=20,
                          netIncome=10, weightedAverageShsOutDil=5)],
        "balance_q": [dict(common, totalAssets=200, totalDebt=50,
                           cashAndShortTermInvestments=25, totalStockholdersEquity=100)],
        "cashflow_q": [dict(common, operatingCashFlow=15, capitalExpenditure=-3)],
    }


class Client:
    def __init__(self, payload):
        self.payload = payload

    def get(self, name, **kwargs):
        assert kwargs["limit"] == 2
        return copy.deepcopy(self.payload[name])


class RecordingDB:
    """SQL-shape double; NOT proof that SQL works in PostgreSQL."""
    def __init__(self, previous=None):
        self.previous, self.calls, self.result = previous, [], None

    def atomic(self, fn):
        return fn(self)

    def execute(self, query, args):
        self.calls.append((query, args))
        if query == INSERT_SQL:
            assert set(re.findall(r"%\((\w+)\)s", query)) == set(args)
            self.result = (args["vintage_id"],)
        elif "SELECT vintage_id" in query:
            self.result = self.previous
        else:
            self.result = None

    def fetchone(self):
        return self.result


def test_missing_hash_parameter_and_currency_regression(statements):
    db = RecordingDB()
    result = ingest_security(Client(statements), db, 1, "SYNTH", "123")
    params = db.calls[-1][1]
    expected = curated_hash(curate(*(statements[n][0] for n in
                                    ("income_q", "balance_q", "cashflow_q"))))
    assert params["source_hash"] == expected and params["currency"] == "USD"
    assert result["new_vintages"] == 1 and params["vintage_id"] == 1


def test_identical_replay_does_not_insert(statements):
    h = curated_hash(curate(*(statements[n][0] for n in
                             ("income_q", "balance_q", "cashflow_q"))))
    db = RecordingDB((3, h))
    result = ingest_security(Client(statements), db, 1, "SYNTH", "123")
    assert result["unchanged"] == 1
    assert not any(q == INSERT_SQL for q, _ in db.calls)


def test_changed_values_append_next_vintage(statements):
    db = RecordingDB((3, "old_hash"))
    result = ingest_security(Client(statements), db, 1, "SYNTH", "123")
    assert result["live_restatements"] == 1
    assert db.calls[-1][1]["vintage_id"] == 4
    assert all(not q.lstrip().startswith(("UPDATE", "DELETE")) for q, _ in db.calls)


@pytest.mark.parametrize("accepted, expected, timing", [
    (None, "missing", False), ("2026-07-10", "missing", False),
    ("2026-07-11", "release", True), ("2026-07-25", "release", True),
    ("2026-07-26", "filing", True), ("2027-02-01", "delinquent", True),
])
def test_r8_classification_preserved(statements, accepted, expected, timing):
    statements["income_q"][0]["acceptedDate"] = accepted
    db = RecordingDB()
    ingest_security(Client(statements), db, 1, "SYNTH", "123")
    params = db.calls[-1][1]
    assert (params["lag_class"], params["timing_pit"]) == (expected, timing)


def test_zero_values_are_preserved():
    assert curate({}, {}, {"operatingCashFlow": 0,
                          "netCashProvidedByOperatingActivities": 99})["cfo"] == 0


def test_hash_mapping_order_independent():
    assert curated_hash({"a": 1, "b": 2}) == curated_hash({"b": 2, "a": 1})


@pytest.mark.parametrize("broken", [None, {}, [None], [{"date": "not-a-date"}]])
def test_malformed_response_fails(statements, broken):
    statements["income_q"] = broken
    with pytest.raises(ValueError):
        ingest_security(Client(statements), RecordingDB(), 1, "SYNTH", "123")


def test_identity_conflict_fails_before_write(statements):
    statements["balance_q"][0]["cik"] = "999"
    db = RecordingDB()
    with pytest.raises(ValueError, match="identity_conflict"):
        ingest_security(Client(statements), db, 1, "SYNTH", "123")
    assert not db.calls


def test_incomplete_statement_does_not_insert_partial_vintage(statements):
    statements["balance_q"] = []
    db = RecordingDB()
    result = ingest_security(Client(statements), db, 1, "SYNTH", "123")
    assert result["incomplete"] == 1 and not db.calls


def test_nodata_not_reported_as_insert():
    db = RecordingDB()
    result = ingest_security(Client({n: [] for n in
                                     ("income_q", "balance_q", "cashflow_q")}),
                             db, 1, "SYNTH", "123")
    assert result["nodata"] == 1 and result["new_vintages"] == 0 and not db.calls


def test_duplicate_conflict_rejected(statements):
    statements["income_q"].append(dict(statements["income_q"][0], netIncome=999))
    with pytest.raises(ValueError, match="duplicate_statement_conflict"):
        ingest_security(Client(statements), RecordingDB(), 1, "SYNTH", "123")


def test_uncertain_commit_does_not_return_insert_success(statements):
    class FailingDB(RecordingDB):
        def atomic(self, fn):
            fn(self)
            raise RuntimeError("synthetic commit failure")
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        ingest_security(Client(statements), FailingDB(), 1, "SYNTH", "123")


@pytest.mark.parametrize("job,detail", [
    ("weekly", {"statements": {"error": 3173, "ok": 340}}),
    ("weekly", {"statements": {"error": 1, "new_vintages": 5}}),
    ("weekly", {"statements": {"incomplete": 1}}),
    ("daily", {"prices": {"error": 1, "ok": 5000}}),
    ("daily", {"source_errors": 1}),
    ("monthly", {"source_errors": 1}),
    ("monthly", {"golden_gate": "FAIL"}),
])
def test_failed_work_never_reports_ok(job, detail):
    records, output = [], []
    assert not execute_job(None, job, "synthetic", lambda _: detail,
                           claim=lambda *a: True,
                           finish=lambda *a: records.append(a), emit=output.append)
    assert records[0][3] == "error"
    assert json.loads(output[-1])["level"] == "error"


@pytest.mark.parametrize("counts", [{"unchanged": 4}, {"nodata": 1},
                                     {"new_vintages": 1, "live_restatements": 1}])
def test_typed_nonfailed_outcomes_are_not_rewritten(counts):
    detail = {"statements": counts}
    require_complete("weekly", detail)
    assert detail == {"statements": counts}


def test_full_summary_not_truncated_before_gate_result():
    output = []
    detail = {"universe": "x" * 500, "factor_chain": "OK", "golden_gate": "PASS"}
    assert execute_job(None, "monthly", "synthetic", lambda _: detail,
                       claim=lambda *a: True, finish=lambda *a: None, emit=output.append)
    assert json.loads(output[-1])["detail"]["factor_chain"] == "OK"


@pytest.mark.parametrize("rc", [1, 2, -9])
def test_child_failure_is_checked_and_redacted(rc):
    def run(*args, **kwargs):
        assert kwargs["cwd"] == "/repo" and "shell" not in kwargs
        return SimpleNamespace(returncode=rc, stdout="apikey=SECRET", stderr="PRIVATE")
    with pytest.raises(JobFailure) as error:
        checked_script(Path("/repo"), "scripts/golden_gate.py", 5, runner=run)
    assert error.value.detail["returncode"] == rc
    assert "SECRET" not in repr(error.value.detail) and "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("exc,code", [
    (subprocess.TimeoutExpired("private", 5, output="SECRET"), "child_timeout"),
    (OSError("SECRET"), "child_start_failed"),
])
def test_child_exceptions_are_typed(exc, code):
    def run(*a, **k):
        raise exc
    with pytest.raises(JobFailure, match=code):
        checked_script(Path("/repo"), "scripts/factor_eval.py", 5, runner=run)


def test_child_success():
    result = SimpleNamespace(returncode=0)
    assert checked_script(Path("/repo"), "scripts/factor_eval.py", 5,
                          runner=lambda *a, **k: result) is result


def test_raw_exception_not_persisted():
    records, output = [], []
    def fail(_):
        raise ValueError("postgresql://user:SECRET@host/db")
    assert not execute_job(None, "weekly", "test", fail, claim=lambda *a: True,
                           finish=lambda *a: records.append(a), emit=output.append)
    assert records[0][-1] == {"error_type": "ValueError"}
    assert "SECRET" not in repr(records) + repr(output)


def test_safe_sqlstate_retained():
    exc = RuntimeError("SECRET")
    exc.pgcode = "23505"
    assert safe_error(exc) == {"error_type": "RuntimeError", "sqlstate": "23505"}
    exc.pgcode = "private"
    assert "sqlstate" not in safe_error(exc)


@pytest.mark.parametrize("job", ["daily", "weekly", "monthly"])
def test_force_selects_one_job(job):
    now = datetime(2026, 9, 5, 23, tzinfo=timezone.utc)
    assert [j for j, _ in due_jobs(now, job)] == [job]


def test_schedule_preserved_and_invalid_force_rejected():
    now = datetime(2026, 9, 5, 13, tzinfo=timezone.utc)
    assert due_jobs(now) == [("weekly", "2026-W36"), ("monthly", "2026-09")]
    assert due_jobs(now.replace(hour=1)) == []
    with pytest.raises(ValueError):
        due_jobs(now, "reset")


def test_claimed_job_not_reexecuted_or_recertified():
    calls = []
    assert execute_job(None, "monthly", "2026-09", lambda _: calls.append("ran"),
                       claim=lambda *a: False, finish=lambda *a: calls.append("finished"),
                       emit=calls.append)
    assert calls == ["monthly 2026-09 already claimed"]
