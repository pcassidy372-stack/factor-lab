"""Plan exact-date SPY adjClose inputs, then explicitly apply a reviewed plan.

No credential lookup, connection, client construction or cache mutation at import.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys


def main(argv=None, *, connect=None, client_factory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan', help='Read calendar/overlaps and request a bounded vendor grid; no database writes')
    p.add_argument('--period', required=True, help='Completed monthly period, e.g. 2026-10 means September 2026')
    p.add_argument('--output', required=True, type=Path)
    for name in ('apply', 'reconcile'):
        p2 = sub.add_parser(name)
        p2.add_argument('--plan', required=True, type=Path)
    for command in sub.choices.values():
        command.add_argument('--connection-env', required=True, help='Explicit name of one DSN environment variable; no fallback')
    args = parser.parse_args(argv)
    # Only stdlib above this boundary. Invalid arguments do not even import the client.
    if not re.fullmatch('[A-Z][A-Z0-9_]*', args.connection_env):
        parser.error('invalid connection variable name')
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from factorlab import benchmark_loader as loader
    try:
        if args.command == 'plan':
            loader.month_bounds(args.period, datetime.now(timezone.utc).date())
            loader.require(not args.output.exists(), 'output_exists')
        else:
            plan = json.loads(args.plan.read_text())
            loader.check_plan(plan, datetime.now(timezone.utc).date())
        if connect is None:
            import psycopg2
            from psycopg2.extensions import parse_dsn
            # No db.conn(), public/private fallback, passfile or service resolution.
            cfg = parse_dsn(os.environ[args.connection_env])
            loader.require(set(cfg) <= {'host','port','dbname','user','password','sslmode','sslrootcert','sslcert','sslkey'}, 'unsupported_connection_option')
            loader.require(all(cfg.get(k) for k in ('host','port','dbname','user','password','sslmode')), 'explicit_connection_required')
            loader.require(',' not in cfg['host'] and ',' not in cfg['port'], 'single_endpoint_required')
            loader.require(not any(k.startswith('PG') for k in os.environ), 'inherited_libpq_settings_forbidden')
            connect = lambda: psycopg2.connect(**cfg, connect_timeout=5)
        if args.command == 'plan':
            target, grid, existing, _ = loader.read_state(connect, args.period)
            if client_factory is None:
                from factorlab.fmp_client import FMPClient
                client_factory = FMPClient
            values = loader.fetch_values(client_factory(), grid)
            plan = loader.make_plan(args.period, grid, [{'date':d,'adjClose':v} for d,v in values.items()],
                                    existing,target,datetime.now(timezone.utc).isoformat())
            with args.output.open('x') as f:
                json.dump(plan,f,sort_keys=True,indent=2); f.write('\n')
            print(json.dumps({'outcome':'planned','plan_sha256':plan['plan_sha256'],
                              'missing':len(plan['missing_keys']),'conflicts':len(plan['conflicting_overlaps'])}))
        else:
            result = (loader.apply_plan if args.command == 'apply' else loader.reconcile_plan)(connect,plan)
            print(json.dumps(result,sort_keys=True))
        return 0
    except loader.CommitUncertain:
        print('commit_outcome_unknown: reconcile the exact plan; do not retry apply',file=sys.stderr)
        return 3
    except loader.BenchmarkError as exc:
        print(str(exc),file=sys.stderr)  # loader emits only fixed classifications
        return 2
    except Exception:
        print('benchmark_operation_failed',file=sys.stderr)  # never emit URLs, credentials or vendor payloads
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
