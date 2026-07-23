"""Numbered-migration runner. Each migration applies in one transaction and
is recorded in schema_migrations. Re-running is a no-op."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn
from factorlab.migrations import MIGRATIONS


def main():
    cx = conn()
    cx.autocommit = False
    cur = cx.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS schema_migrations
                   (id INT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    cx.commit()
    cur.execute("SELECT id FROM schema_migrations ORDER BY id")
    done = {r[0] for r in cur.fetchall()}
    for mid in sorted(MIGRATIONS):
        if mid in done:
            print("migration %03d: already applied" % mid)
            continue
        print("migration %03d: applying..." % mid)
        cur.execute(MIGRATIONS[mid])
        cur.execute("INSERT INTO schema_migrations (id) VALUES (%s)", (mid,))
        cx.commit()
        print("migration %03d: OK" % mid)
    cur.execute("""SELECT table_name FROM information_schema.tables
                   WHERE table_schema='public' ORDER BY table_name""")
    print("tables now:", [r[0] for r in cur.fetchall()])
    cx.close()


if __name__ == "__main__":
    main()
