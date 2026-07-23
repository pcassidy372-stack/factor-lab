"""Postgres connection helper. DATABASE_URL injected by `railway run`."""
import os

import psycopg2
import psycopg2.extras


def conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set (run under `railway run`)")
    return psycopg2.connect(url)
