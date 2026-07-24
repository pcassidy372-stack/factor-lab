"""Reconnecting autocommit DB wrapper for long ingestion runs.

Railway's TCP proxy drops connections occasionally (observed session 4).
Work units must be idempotent; on OperationalError the unit is replayed
on a fresh connection. Autocommit + per-unit idempotence = the database
is its own checkpoint.
"""
import time

import psycopg2

from factorlab.db import conn


class RDB:
    def __init__(self):
        self._cx = None

    def _cur(self):
        if self._cx is None or self._cx.closed:
            self._cx = conn()
            self._cx.autocommit = True
        return self._cx.cursor()

    def safe(self, fn, tries=3):
        """Run fn(cur); reconnect and replay on connection loss."""
        for i in range(tries):
            try:
                cur = self._cur()
                out = fn(cur)
                cur.close()
                return out
            except psycopg2.OperationalError:
                self._cx = None
                if i == tries - 1:
                    raise
                time.sleep(2 * (i + 1))

    def close(self):
        if self._cx is not None and not self._cx.closed:
            self._cx.close()
