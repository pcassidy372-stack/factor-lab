"""Regression runs cannot access inherited application credentials or live HTTP."""
import os
import pytest
import requests


@pytest.fixture(autouse=True)
def isolate_external_inputs(monkeypatch):
    for key in list(os.environ):
        if key.startswith("PG") or key in (
            "DATABASE_URL", "DATABASE_PUBLIC_URL", "FACTOR_DATABASE_URL",
            "MOMENTUM_DATABASE_URL", "FMP_API_KEY",
        ):
            monkeypatch.delenv(key, raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("Live provider HTTP is prohibited in tests")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
