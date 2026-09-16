"""Synthetic raw inputs, deliberately unrelated to production securities."""
import calendar
from datetime import date, datetime, timedelta, timezone

from factorlab.monthly_producer import CORE_FACTORS, PRODUCER_VERSION, FIELDS


def monthly_sample(n=100):
    grid = []
    for index in range(13):
        year, month0 = divmod(2019 * 12 + 7 + index, 12)
        d = date(year, month0 + 1, calendar.monthrange(year, month0 + 1)[1])
        while d.weekday() > 4:
            d -= timedelta(days=1)
        grid.append(d.isoformat())
    asof = date.fromisoformat(grid[-1])
    days, d = [], asof
    while len(days) < 63:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d -= timedelta(days=1)
    payload = dict(producer_version=PRODUCER_VERSION, asof=asof.isoformat(), month_grid=grid,
        observed_at='2020-09-02T14:00:00+00:00', database_snapshot='synthetic',
        security_ids=list(range(1,n+1)), expected_universe=n,
        prices=[], caps=[], profiles=[], symbols=[], adr_history=[], fundamentals=[], returns=[], surprises=[],
        benchmarks=[dict(asof=d, symbol='SPY', tr=100+j) for j,d in enumerate(grid)],
        registry=[dict(factor_id=k,**v,params={'excl_financials': k=='gp_a'},frozen=True)
                  for k,v in CORE_FACTORS.items()])
    quarters = [date(2019,3,31),date(2019,6,30),date(2019,9,30),
                date(2019,12,31),date(2020,3,31),date(2020,6,30)]
    for sec in payload['security_ids']:
        payload['prices'] += [dict(security_id=sec,d=d,close=10+sec/100,volume=1e6) for d in sorted(days)]
        payload['caps'].append(dict(security_id=sec,asof=grid[-1],mktcap=400e6+sec*1e6))
        payload['profiles'].append(dict(security_id=sec,asof='2020-09-02',
            sector='Financial Services' if sec%5==0 else ('Technology' if sec%2 else 'Industrials'),is_adr=False))
        payload['symbols'].append(dict(security_id=sec,symbol='S'+str(sec),valid_from='2010-01-01',valid_to=None))
        for j,pe in enumerate(quarters):
            r=dict(security_id=sec,fiscal_period_end=pe.isoformat(),vintage_id=1,
                accepted_date=(datetime.combine(pe,datetime.min.time(),timezone.utc)+timedelta(days=35,hours=12)).isoformat(),
                observed_at='2020-09-02T13:00:00+00:00',timing_pit=True,value_pit=False,
                source_hash=str(sec)+'-'+str(j),mapping_version='synthetic',currency='USD')
            r.update({k: float(100+j+sec) for k in FIELDS})
            r.update(gross_profit=float(100+sec*(j+1)),total_assets=float(1000+2*sec),shares_dil=float(1000+j*sec))
            payload['fundamentals'].append(r)
        for j,d in enumerate(grid):
            payload['returns'].append(dict(security_id=sec,d=d,
                tr=(100+sec)*(1+.004+(sec%7)*.001)**j*(1+.00001*sec*j*j),
                method_version='tr-v2-window-priority'))
        payload['surprises'].append(dict(security_id=sec,report_date='2020-08-01',eps_actual=1,eps_est=.9,sue=(sec-30)/10))
    return payload
