"""Scheduler outcome checks. A completed process is not a certified dataset."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable


class JobFailure(RuntimeError):
    """A stable error code with a structured, secret-free partial summary."""

    def __init__(self, code: str, detail: dict | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


def safe_error(exc: Exception) -> dict:
    """Never persist exception messages, SQL, request URLs, or child output."""
    result = {"error_type": type(exc).__name__}
    state = getattr(exc, "pgcode", None)
    if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state):
        result["sqlstate"] = state
    if isinstance(exc, JobFailure):
        result["error_code"] = exc.code
    return result


def checked_script(root: Path, script: str, timeout: int,
                   *, env: dict | None = None, runner: Callable | None = None):
    """Check each fixed-path child; failure output may contain secrets."""
    try:
        result = (runner or subprocess.run)(
            [sys.executable, "-u", str(root / script)], cwd=str(root),
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise JobFailure("child_timeout", {"failed_stage": script}) from exc
    except OSError as exc:
        raise JobFailure("child_start_failed", {"failed_stage": script}) from exc
    if result.returncode != 0:
        raise JobFailure("child_failed", {"failed_stage": script,
                                          "returncode": result.returncode})
    return result


def require_complete(job: str, detail: dict) -> None:
    """Actual errors prevent `ok`; nodata is distinct and does not certify coverage."""
    counts = detail.get("statements" if job == "weekly" else "prices", {})
    if counts.get("error", 0) or counts.get("incomplete", 0):
        raise JobFailure("incomplete_" + job, detail)
    if detail.get("source_errors", 0):
        raise JobFailure("source_failed", detail)
    if detail.get("golden_gate") == "FAIL":
        raise JobFailure("golden_gate_failed", detail)


def due_jobs(now: datetime, force: str | None = None) -> list[tuple[str, str]]:
    """Force selects ONE job, but does not bypass its durable claim."""
    keys = {"daily": now.date().isoformat(), "weekly": now.strftime("%G-W%V"),
            "monthly": now.strftime("%Y-%m")}
    if force is not None:
        if force not in keys:
            raise ValueError("unknown job")
        return [(force, keys[force])]
    due = []
    if now.weekday() < 5 and now.hour >= 23:
        due.append(("daily", keys["daily"]))
    if now.weekday() == 5 and now.hour >= 12:
        due.append(("weekly", keys["weekly"]))
    if now.day >= 2 and now.hour >= 13:
        due.append(("monthly", keys["monthly"]))
    return due


def execute_job(db, job: str, key: str, fn: Callable, *, claim: Callable,
                finish: Callable, emit: Callable = print) -> bool:
    if not claim(db, job, key):
        emit(f"{job} {key} already claimed")
        return True  # skipped; NOT a new successful refresh or certification
    emit(f"running {job} {key}")
    try:
        detail = fn(db)
        require_complete(job, detail)
    except Exception as exc:
        detail = dict(exc.detail) if isinstance(exc, JobFailure) else {}
        detail.update(safe_error(exc))
        finish(db, job, key, "error", detail)
        emit(json.dumps({"level": "error", "job": job, "period_key": key,
                         "detail": detail}, sort_keys=True))
        return False
    finish(db, job, key, "ok", detail)
    emit(json.dumps({"level": "info", "job": job, "period_key": key,
                     "status": "ok", "detail": detail}, sort_keys=True))
    return True
