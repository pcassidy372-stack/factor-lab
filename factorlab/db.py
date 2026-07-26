"""Postgres helper. Inside Railway the private URL is fastest and free;
from the laptop it doesn't resolve. Try private with a short timeout,
fall back to the public proxy."""
import os

import psycopg2


def conn():
    prv = os.environ.get("DATABASE_URL")
    pub = os.environ.get("DATABASE_PUBLIC_URL")
    if prv and ".railway.internal" in prv:
        try:
            return psycopg2.connect(prv, connect_timeout=2)
        except psycopg2.OperationalError:
            pass
    url = pub or prv
    if not url:
        raise RuntimeError("no DATABASE_PUBLIC_URL / DATABASE_URL (run under `railway run`)")
    return psycopg2.connect(url)
