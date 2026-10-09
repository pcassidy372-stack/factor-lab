"""Synthetic arithmetic and isolated real writer-function bodies; no DB/vendor."""
import ast
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
import threading
import pytest
from factorlab import tr_precision as tr

D = Decimal
ROOT = Path(__file__).parents[1]

@pytest.mark.parametrize('value', ['0.0000004','1e-50','1e-300','100.123456789'])
def test_positive_persistence_and_idempotency(value):
    result = tr.persist(value)
    assert result > 0 and result == D(value)
    assert tr.persist(result) == tr.persist(str(result)) == result
    assert float(result) > 0

@pytest.mark.parametrize('args,expected', [((10,11,1,0),'1.1'),((10,5,2,0),'1'),((10,9,1,1),'1')])
def test_economic_formula(args,expected):
    assert tr.gross_return(*args) == D(expected)

@pytest.mark.parametrize('bad', [True,False,None,'bad','NaN','Infinity','-Infinity',0,-1])
def test_invalid_or_zero_seed_rejected(bad):
    with pytest.raises(tr.TRInputError):
        tr.advance(bad,1,seed_date='2026-01-01',price_date='2026-01-01',next_date='2026-01-02')

@pytest.mark.parametrize('args', [(0,1,1,0),(10,1,0,0),(10,1,1,-1),(10,'NaN',1,0),(10,1,True,0),(10,1,1,None)])
def test_invalid_economic_inputs(args):
    with pytest.raises(tr.TRInputError):tr.gross_return(*args)

@pytest.mark.parametrize('value', ['1e-400','1e400'])
def test_consumer_range_failure_never_clamped(value):
    with pytest.raises(tr.TRInputError,match='consumer_range'):tr.persist(value)

def test_terminal_loss_is_not_positive_continuation():
    with pytest.raises(tr.TerminalTreatmentRequired):tr.gross_return(10,0,1,0)
    assert tr.gross_return(10,0,1,10)==1 # cash consideration is not discarded

@pytest.mark.parametrize('seed,price,nxt', [('2026-01-01','2026-01-02','2026-01-03'),('2026-01-02','2026-01-02','2026-01-02'),('2026-01-02','2026-01-02','2026-01-01'),('20260101','2026-01-01','2026-01-02')])
def test_date_alignment(seed,price,nxt):
    with pytest.raises(tr.TRInputError):tr.advance(100,1,seed_date=seed,price_date=price,next_date=nxt)

def test_persisted_restart_matches_uninterrupted_and_ambient_context():
    level=restart=tr.persist(100)
    for i in range(70):
        a=date(2026,1,1)+timedelta(days=i);b=a+timedelta(days=1)
        gross=tr.gross_return(13,7,1,'0.03')
        level=tr.advance(level,gross,seed_date=a,price_date=a,next_date=b)
        with localcontext() as c:
            c.prec=6
            restart=tr.advance(D(str(tr.persist(restart))),gross,seed_date=a,price_date=a,next_date=b)
        assert level==restart
    assert D(0)<level<D('0.000001')

def test_direct_gross_avoids_minus_one_cancellation():
    gross=tr.gross_return('1e30',1)
    assert float(gross)-1 == -1.0
    assert tr.advance(100,gross,seed_date='2026-01-01',price_date='2026-01-01',next_date='2026-01-02')==D('1e-28')

def test_oracle_selection_explicit_and_outside_grid_refused():
    prices={'2026-01-01':100,'2026-01-02':400}
    assert tr.build_levels(prices,{}, {})[-1][1]==400
    assert tr.build_levels(prices,{}, {},oracle_gross={'2026-01-02':D('1.1')})[-1][1]==110
    with pytest.raises(tr.TRInputError):tr.build_levels(prices,{}, {},oracle_gross={'2026-01-03':1})
    with pytest.raises(tr.TRInputError):tr.build_levels(prices,{}, {},oracle_gross={'2026-01-02':0})


def writer_functions(filename, names, extras):
    """Compile only named function definitions, not module imports/startup."""
    source=ast.parse((ROOT/'scripts'/filename).read_text())
    selected=[n for n in ast.walk(source) if isinstance(n,ast.FunctionDef) and n.name in names]
    assert len(selected)==len(names)
    ns={name:getattr(tr,name) for name in ['build_levels','gross_series','number','gross_return','advance','persist','TRInputError']}
    ns.update(extras)
    exec(compile(ast.Module(body=selected,type_ignores=[]),filename,'exec'),ns)
    return ns

class Recorder:
    def __init__(self, seed=None, price=None):self.seed=seed;self.price=price;self.statements=[];self.rows=[]
    def safe(self,fn):return fn(self)
    def execute(self,q,args=()):self.statements.append(q)
    def fetchone(self):return (self.price,) if 'SELECT close' in self.statements[-1] else self.seed
    def fetchall(self):return []

def insert_record(cur,q,rows,**kw):cur.rows.append((q,list(rows)))

@pytest.mark.parametrize('script,fn', [('prices_backfill.py','process_security'),('prices_repair.py','repair_one')])
@pytest.mark.parametrize('oracle', [False,True])
def test_backfill_and_repair_function_body_persists_precise_levels(script,fn,oracle):
    prices={'2026-01-01':(None,None,None,1e12,100),'2026-01-02':(None,None,None,1,100),'2026-01-03':(None,None,None,4 if oracle else 2,100)}
    ora={'2026-01-01':100,'2026-01-02':100,'2026-01-03':110} if oracle else {}
    fetch=lambda *a:(prices,ora,{}, {},0,0) if fn=='process_security' else (prices,ora,{}, {},{})
    ns=writer_functions(script,['rets_from',fn],{'fetch_series':fetch,'fetch_windowed':fetch,'execute_values':insert_record,'TRV':'tr-v1-close-div-split' if fn=='process_security' else 'tr-v2-window-priority'})
    rec=Recorder()
    if fn=='process_security':ns[fn](rec,None,1,[],None,None)
    else:ns[fn](None,rec,1,[],None,None)
    rows=next(rows for q,rows in rec.rows if 'INSERT INTO tr_index_d' in q)
    assert rows[1][2]==D('1e-10')
    assert rows[2][2]==D('1.1e-10' if oracle else '2e-10')
    assert all(isinstance(x[2],D) for x in rows)
    assert {x[3] for x in rows}=={ns['TRV']}

@pytest.mark.parametrize('seed_date,seed,fail', [('2026-01-01','0.0000004',False),('2025-12-31','0.0000004',True),('2026-01-01','0',True)])
def test_actual_daily_extension_body(seed_date,seed,fail):
    ns=writer_functions('incremental.py',['one'],{'TODAY':'2026-01-02','date':date,'timedelta':timedelta,'execute_values':insert_record,'lock':threading.Lock(),'counts':defaultdict(int)})
    class SyntheticClient:
        def get(self,*a,**kw):return [{'date':'2026-01-02','adjClose':11,'volume':100}]
    rec=Recorder((date.fromisoformat(seed_date),D(seed)),D(10))
    if fail:
        with pytest.raises(tr.TRInputError):ns['one'](SyntheticClient(),rec,1,'SYNTHETIC','2026-01-01')
        assert rec.rows==[]
    else:
        assert ns['one'](SyntheticClient(),rec,1,'SYNTHETIC','2026-01-01')=='ok'
        row=next(rows for q,rows in rec.rows if 'INSERT INTO tr_index_d' in q)[0]
        assert row[2]==D('0.00000044') and row[3]=='tr-v2-window-priority'

@pytest.mark.parametrize('script,fn', [('prices_backfill.py','process_security'),('prices_repair.py','repair_one')])
@pytest.mark.parametrize('close,oracle_close,split,expected', [
    (300,100,False,'300'), # abs(return) == 2: no override
    (400,250,False,'400'), # discrepancy == 1.5: no override
    (400,100,False,'100'), # both strict thresholds exceeded
    (400,100,True,'400'),  # explicit split blocks override
])
def test_writer_oracle_strict_boundaries(script,fn,close,oracle_close,split,expected):
    prices={'2026-01-01':(None,None,None,100,100),'2026-01-02':(None,None,None,close,100)}
    ora={'2026-01-01':100,'2026-01-02':oracle_close}
    splits={'2026-01-02':1} if split else {}
    fetch=lambda *a:(prices,ora,{},splits,0,0) if fn=='process_security' else (prices,ora,{},splits,{})
    ns=writer_functions(script,['rets_from',fn],{'fetch_series':fetch,'fetch_windowed':fetch,'execute_values':insert_record,'TRV':'synthetic-method'})
    rec=Recorder()
    if fn=='process_security':ns[fn](rec,None,1,[],None,None)
    else:ns[fn](None,rec,1,[],None,None)
    rows=next(rows for q,rows in rec.rows if 'INSERT INTO tr_index_d' in q)
    assert rows[-1][2]==D(expected)
