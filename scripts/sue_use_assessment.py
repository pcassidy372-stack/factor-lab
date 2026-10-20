"""Credential-free Option A assessment of an explicitly supplied local package."""
import argparse
import re
from factorlab.sue_use_contract import package_from_files, assess, reference, utc
from factorlab.sue_plan import Invalid
from factorlab.sue_store import write_json


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',required=True);p.add_argument('--manifest',required=True)
    p.add_argument('--cutoff',required=True);p.add_argument('--output',required=True)
    a=p.parse_args(argv)
    try:
        reference(a.manifest);utc(a.cutoff)
        result=assess(package_from_files(a.root,a.manifest),a.cutoff)
        write_json(a.output,result)
        print('Option A research assessment saved; no production or data apply');return 0
    except Invalid as error:
        code=str(error)
        if re.fullmatch(r'[a-z][a-z0-9_]{0,80}',code) is None:code='invalid_evidence'
        print('SUE use assessment refused: '+code);return 2
    except (OSError,ValueError,TypeError,KeyError):
        print('SUE use assessment refused; inspect explicit evidence and policy');return 2

if __name__=='__main__':raise SystemExit(main())
