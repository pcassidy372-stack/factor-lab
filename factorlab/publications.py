"""Immutable, date-scoped publication store with durable attempts.

No connection is opened on import and no environment credential is consulted.
Supply a factory returning a NEW, idle psycopg2 connection for each invocation.
The producer must freeze/validate its actual input snapshot; this layer does not
reclassify reconstructed vendor history as point-in-time evidence.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime
from typing import Callable, Iterator, Mapping
from uuid import UUID, uuid4

from .publication_contract import (CORE_FACTORS, FACTOR_KEYS, UNIVERSE_KEYS,
    PublicationError, day, instant, normalize_spec, prepare_rows, require)

LOCK_NAMESPACE = 214011


class PublicationStore:
    def __init__(self, connect: Callable):
        require(callable(connect), "explicit_connection_factory_required")
        self.connect = connect

    def _open(self, *, readonly=False):
        from psycopg2.extensions import STATUS_READY
        from psycopg2.extras import register_uuid
        cx = self.connect()
        try:
            require(not cx.closed and cx.status == STATUS_READY,
                    "connection_must_be_new_and_idle")
            register_uuid(conn_or_curs=cx)
            cx.set_session(readonly=readonly, autocommit=False,
                           isolation_level="REPEATABLE READ" if readonly else "READ COMMITTED")
            return cx
        except BaseException:
            cx.close()
            raise

    @staticmethod
    def _dicts(cur):
        keys = [column.name for column in cur.description]
        return [dict(zip(keys, row)) for row in cur.fetchall()]

    @staticmethod
    def _failed(cx, attempt_id):
        """Best-effort evidence only; never retry the producer or uncertain commit."""
        if cx.closed or attempt_id is None:
            return
        try:
            cx.rollback()
            with cx.cursor() as cur:
                cur.execute("SELECT 1 FROM fl_publication_outcomes WHERE attempt_id=%s", (attempt_id,))
                if cur.fetchone() is None:
                    cur.execute("""INSERT INTO fl_publication_outcomes
                        (attempt_id,status,error_code) VALUES (%s,'failed','build_failed')""", (attempt_id,))
            cx.commit()
        except Exception:
            # Lost/uncertain connections leave a durable open attempt for the next
            # lock owner to reconcile. Do not overwrite the original exception.
            if not cx.closed:
                cx.rollback()

    @contextmanager
    def build(self, spec: Mapping) -> Iterator["BuildAttempt"]:
        s = normalize_spec(spec)
        cx, attempt_id = self._open(), None
        try:
            with cx.cursor() as cur:
                # Session scope is intentional: attempts and data commit separately.
                # Closing this dedicated connection releases the lock after a crash.
                cur.execute("SELECT pg_try_advisory_lock(%s,%s)",
                            (LOCK_NAMESPACE, int(s["asof"].replace("-", ""))))
                require(cur.fetchone()[0], "publication_scope_busy")
            cx.commit()
            with cx.cursor() as cur:
                cur.execute("""INSERT INTO fl_publication_requests (spec) VALUES (%s::jsonb)
                               ON CONFLICT DO NOTHING RETURNING request_id""", (json.dumps(s),))
                row = cur.fetchone()
                if row:
                    request_id = row[0]
                else:
                    cur.execute("""SELECT request_id FROM fl_publication_requests
                        WHERE request_id=encode(sha256(convert_to(%s::jsonb::text,'UTF8')),'hex')""",
                        (json.dumps(s),))
                    request_id = cur.fetchone()[0]
                # Acquiring the exclusive scope lock proves no cooperating producer
                # still owns these open attempts; preserve each as interrupted.
                cur.execute("""INSERT INTO fl_publication_outcomes (attempt_id,status,error_code)
                    SELECT a.attempt_id,'interrupted','interrupted'
                    FROM fl_publication_attempts a JOIN fl_publication_requests r USING(request_id)
                    LEFT JOIN fl_publication_outcomes o USING(attempt_id)
                    WHERE r.asof=%s AND o.attempt_id IS NULL""", (s["asof"],))
                attempt_id = uuid4()
                cur.execute("INSERT INTO fl_publication_attempts (attempt_id,request_id) VALUES (%s,%s)",
                            (attempt_id, request_id))
                cur.execute("SELECT generation_id FROM fl_dataset_generations WHERE request_id=%s", (request_id,))
                existing = cur.fetchone()
            cx.commit()
            attempt = BuildAttempt(self, cx, attempt_id, request_id, s,
                                   existing[0] if existing else None)
            yield attempt
            require(attempt.finished, "attempt_exited_without_publication")
        except BaseException:
            self._failed(cx, attempt_id)
            raise
        finally:
            cx.close()

    def publish(self, spec: Mapping, producer: Callable) -> dict:
        """Run an explicitly supplied producer, validate, commit, then confirm.

        Even identical retries validate/compare the supplied output. A caller
        cannot quietly reuse an identity with changed rows. Producer/network
        operations are never automatically retried after an uncertain result.
        """
        with self.build(spec) as attempt:
            universe, factors = producer(attempt.spec)
            generation_id = attempt.write(universe, factors)
            return self.confirm(generation_id)

    def confirm(self, generation_id: UUID | str) -> dict:
        """Fresh-transaction observation of an already committed complete dataset.

        Its server timestamp is a conservative known-complete boundary, NOT the
        pre-commit seal time or a claim about the source's historical availability.
        A later retry preserves the first receipt and both content hashes.
        """
        cx = self._open()
        try:
            with cx.cursor() as cur:
                cur.execute("""INSERT INTO fl_publication_receipts (generation_id)
                               VALUES (%s) ON CONFLICT DO NOTHING""", (str(generation_id),))
            cx.commit()
        except BaseException:
            cx.rollback()
            raise
        finally:
            cx.close()
        return self.load_id(generation_id)

    def load_id(self, generation_id: UUID | str) -> dict:
        cx = self._open(readonly=True)
        try:
            with cx.cursor() as cur:
                cur.execute("SELECT * FROM fl_published_datasets WHERE generation_id=%s", (str(generation_id),))
                found = self._dicts(cur)
                require(len(found) == 1, "publication_not_confirmed")
                result = found[0]
                for name, table, keys in (("universe", "fl_universe_generations", UNIVERSE_KEYS),
                                          ("factors", "fl_factor_generations", FACTOR_KEYS)):
                    # Tables and columns are module constants, never user SQL.
                    order = "security_id, factor_id" if name == "factors" else "security_id"
                    cur.execute("SELECT " + ",".join(keys) + " FROM " + table
                                + " WHERE generation_id=%s ORDER BY " + order, (str(generation_id),))
                    result[name] = self._dicts(cur)
                # Immutable storage plus a repeatable-read snapshot prevents a mix
                # of two generations. Revalidate schema/date/missingness on reads.
                prepare_rows(result["spec"], result["universe"], result["factors"])
                return result
        finally:
            cx.close()

    def select(self, asof: date | str, cutoff: datetime | str) -> dict:
        d, t = day(asof), instant(cutoff)
        cx = self._open(readonly=True)
        try:
            with cx.cursor() as cur:
                cur.execute("""SELECT p.generation_id FROM fl_published_datasets p
                    JOIN fl_dataset_generations g USING(generation_id)
                    WHERE p.asof=%s AND p.available_at<=%s
                      AND p.spec->'factor_contract'=%s::jsonb
                    ORDER BY g.sealed_at DESC, g.generation_id DESC LIMIT 1""",
                    (d, t, json.dumps(CORE_FACTORS)))
                row = cur.fetchone()
        finally:
            cx.close()
        require(row is not None, "no_exact_date_publication_before_cutoff")
        # The selected ID and its bytes are immutable; a later publication cannot
        # change this selection while the full payload is read in a fresh session.
        return self.load_id(row[0])

    def readiness(self, asof: date | str, cutoff: datetime | str) -> dict:
        try:
            p = self.select(asof, cutoff)
        except PublicationError as exc:
            return {"status": "unavailable", "reason": str(exc), "asof": day(asof).isoformat(),
                    "trading_authority": False}
        coverage = {}
        for fid in CORE_FACTORS:
            panel = [f for f in p["factors"] if f["factor_id"] == fid]
            missing = {}
            for f in panel:
                if f["missing_reason"] is not None:
                    missing[f["missing_reason"]] = missing.get(f["missing_reason"], 0) + 1
            coverage[fid] = {"rows": len(panel),
                "nonnull": sum(f["z_sector_size"] is not None for f in panel), "missing": missing}
        return {"status": "ready", "asof": p["asof"], "generation_id": p["generation_id"],
                "request_id": p["request_id"], "available_at": p["available_at"],
                "universe_sha256": p["universe_sha256"], "factors_sha256": p["factors_sha256"],
                "factor_coverage": coverage, "trading_authority": False,
                "source_completeness_certified": False}


class BuildAttempt:
    def __init__(self, store, cx, attempt_id, request_id, spec, existing):
        self.store, self.cx = store, cx
        self.attempt_id, self.request_id = attempt_id, request_id
        self.spec, self.existing, self.finished = spec, existing, False

    def write(self, universe, factors) -> UUID:
        from psycopg2.extras import execute_values
        require(not self.finished, "attempt_already_finished")
        urows, frows = prepare_rows(self.spec, universe, factors)
        with self.cx.cursor() as cur:
            if self.existing is not None:
                current = []
                for table, keys in (("fl_universe_generations", UNIVERSE_KEYS),
                                    ("fl_factor_generations", FACTOR_KEYS)):
                    cur.execute("SELECT " + ",".join(keys) + " FROM " + table + " WHERE generation_id=%s",
                                (self.existing,))
                    current.append(self.store._dicts(cur))
                require(prepare_rows(self.spec, *current) == (urows, frows), "retry_payload_conflict")
                generation_id, status = self.existing, "reused"
            else:
                generation_id, status = self.attempt_id, "published"
                cur.execute("INSERT INTO fl_dataset_generations (generation_id) VALUES (%s)", (generation_id,))
                for table, keys, data in (("fl_universe_generations", UNIVERSE_KEYS, urows),
                                          ("fl_factor_generations", FACTOR_KEYS, frows)):
                    execute_values(cur, "INSERT INTO " + table + " (generation_id," + ",".join(keys)
                                   + ") VALUES %s", [(generation_id,) + tuple(r[k] for k in keys)
                                                      for r in data], page_size=2000)
            cur.execute("""INSERT INTO fl_publication_outcomes (attempt_id,status,generation_id)
                           VALUES (%s,%s,%s)""", (self.attempt_id, status, generation_id))
        # Completeness constraint fires here, not just in a Python assertion.
        self.cx.commit()
        self.finished = True
        return generation_id
