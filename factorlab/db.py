"""Postgres connection helper.

`railway run` executes locally, so the private DATABASE_URL
(*.railway.internal) is unreachable from this machine - prefer
DATABASE_PUBLIC_URL, fall back to DATABASE_URL only when routable.
"""
import os

import psycopg2


def conn():
    pub = os.environ.get("DATABASE_PUBLIC_URL")
    prv = os.environ.get("DATABASE_URL")
    url = pub or prv
    if not pub and prv and ".railway.internal" in prv:
        raise RuntimeError("only the internal DATABASE_URL is set - add the "
                           "DATABASE_PUBLIC_URL reference on superb-truth and Apply")
    if not url:
        raise RuntimeError("no DATABASE_PUBLIC_URL / DATABASE_URL (run under `railway run`)")
    return psycopg2.connect(url)
