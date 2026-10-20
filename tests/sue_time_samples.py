"""New SYNTHETIC sealed clock documents; never edit an original observation."""
from decimal import Decimal, localcontext
from sue_use_samples import evidence,change
from monthly_samples import monthly_sample
from factorlab.sue_plan import seal,digest,canonical,load


def timed_package(start='1599048000',finish='1599048000.0000004',payload=None):
    package=evidence(monthly_sample(1) if payload is None else payload)
    change(package,'policy',lambda p:p.update(available_at='2020-09-02T11:00:00Z'))
    count=len(load(package['manifest'].encode())['subjects'])
    for index in range(count):
        change(package,'events',lambda p:p.update(validated_at='2020-09-02T11:00:00Z'),index)
    def clock(o):
        line=o['lineage']['body'];binding=line['campaign']['body']
        binding.update(started=Decimal('1599047900'),deadline=Decimal('1599048500'))
        line['campaign']=seal('sue-campaign',binding);line['campaign_sha256']=digest(canonical(line['campaign']))
        i=line['intent'];i.update(started=Decimal(start),campaign_sha256=line['campaign_sha256'])
        line['intent_sha256']=digest(canonical(i))
        r=line['receipt']['body']
        with localcontext() as ctx:
            ctx.prec=100
            elapsed=Decimal(finish)-Decimal(start)
        r.update(finished=Decimal(finish),elapsed=elapsed,charged=elapsed,intent_sha256=line['intent_sha256'])
        line['receipt']=seal('sue-attempt-receipt',r);line['receipt_sha256']=digest(canonical(line['receipt']))
        status=line['status_receipt']['body'];status.update(at=Decimal(start),intent_sha256=line['intent_sha256'])
        line['status_receipt']=seal('sue-http-status',status);line['status_sha256']=digest(canonical(line['status_receipt']))
        line['observation_interval']={'started':Decimal(start),'finished':Decimal(finish)}
        o['lineage']=seal('sue-observation',line)
    for index in range(count):change(package,'observation',clock,index)
    return package
