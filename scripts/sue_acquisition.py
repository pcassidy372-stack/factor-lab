#!/usr/bin/env python3
"""Explicit, private SUE planning/acquisition. There is deliberately no DB apply."""
import argparse
from factorlab.sue_plan import Invalid, load, make_plan, propose, validate_plan
from factorlab.sue_store import read, write_json, private_root, safe_path
from factorlab.sue_acquire import acquire, reconcile
from factorlab.sue_proposal import write_proposal


def main(argv=None, *, transport_factory=None, credential=None, clock=None, sleep=None, checkpoint=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('plan')
    p.add_argument('--scope', required=True); p.add_argument('--selected', required=True)
    p.add_argument('--limits', required=True); p.add_argument('--output', required=True)
    for name in ('acquire', 'reconcile', 'propose'):
        p = commands.add_parser(name)
        p.add_argument('--plan', required=True); p.add_argument('--root', required=True)
        p.add_argument('--operation', required=True)
        if name == 'acquire':
            p.add_argument('--approval', required=True); p.add_argument('--credential-env', required=True)
            p.add_argument('--retry-unknown')
        else: p.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'plan':
            result = make_plan(load(read(args.scope)), load(read(args.selected)), load(read(args.limits)))
            write_json(args.output, result)
        else:
            plan = load(read(args.plan)); validate_plan(plan); private_root(args.root)
            if args.command == 'acquire':
                options = {k: v for k, v in dict(transport_factory=transport_factory, credential=credential,
                           clock=clock, sleep=sleep, checkpoint=checkpoint).items() if v is not None}
                result = acquire(plan, args.root, args.operation, args.approval, args.credential_env,
                                 retry_unknown=args.retry_unknown, **options)
                if not result['complete']:
                    print('SUE campaign incomplete; inspect private outcomes'); return 2
            else:
                summary, observations = reconcile(plan, args.root, args.operation)
                if args.command == 'propose':
                    result = write_proposal(plan, observations, args.output, checkpoint=checkpoint or (lambda _:None))
                else:
                    result = summary
                    write_json(args.output, result)
        print('SUE private result saved; no database apply'); return 0
    except (Invalid, OSError, ValueError, TypeError, KeyError):
        print('SUE operation refused; inspect validated private inputs and outcomes'); return 2


if __name__ == '__main__':
    raise SystemExit(main())
