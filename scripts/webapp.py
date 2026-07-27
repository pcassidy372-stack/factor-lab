"""Phase 5: read-only dashboard for factor_lab. Second Railway service
(always-on) beside the cron dispatcher. Optional guard: set DASH_TOKEN and
append ?token=... to the URL."""
import json
import os
import sys
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path

from flask import Flask, abort, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from factorlab.db import conn

app = Flask(__name__)
SET_A = {"mom_12_1": 1, "gp_a": 1, "sue": 1, "net_issuance": -1}
DEVVAL_END = "2023-07-31"


def guard():
    tok = os.environ.get("DASH_TOKEN")
    if tok and request.args.get("token") != tok:
        abort(403)


def q(sql, params=()):
    cx = conn()
    cur = cx.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    cx.close()
    return rows


@app.route("/api/summary")
def summary():
    guard()
    out = {}
    out["counts"] = {t: q("SELECT count(*) FROM %s" % t)[0][0] for t in
                     ("securities", "prices_raw_d", "fundamentals_q", "surprises",
                      "factor_values")}
    out["universe"] = [[str(a), n] for a, n in q(
        """SELECT asof, count(*) FROM universe_snapshots WHERE in_universe
           GROUP BY 1 ORDER BY 1""")]
    out["jobs"] = [[j, k, s, str(r)] for j, k, s, r in q(
        "SELECT job, period_key, status, ran_at FROM job_log ORDER BY ran_at DESC LIMIT 8")]
    out["recon"] = [[c or "no-oracle", n] for c, n in q(
        """SELECT CASE WHEN match_pct IS NULL THEN NULL
           WHEN match_pct >= 99 THEN '99+' WHEN match_pct >= 95 THEN '95-99'
           ELSE '<95' END, count(*) FROM price_recon GROUP BY 1 ORDER BY 1""")]
    return jsonify(out)


@app.route("/api/factors")
def factors():
    guard()
    ls = defaultdict(list)
    for f, a, v in q("SELECT factor_id, asof, ls_ret FROM factor_ls ORDER BY asof"):
        ls[f].append([str(a), float(v)])
    table = []
    for f, ann, t, ic_t in q("""
        WITH s AS (SELECT factor_id, avg(ls_ret) m, stddev_samp(ls_ret) sd, count(*) n
                   FROM factor_ls GROUP BY 1),
             i AS (SELECT factor_id, avg(ic) m, stddev_samp(ic) sd, count(*) n
                   FROM factor_ic GROUP BY 1)
        SELECT s.factor_id, 12*s.m, s.m/(s.sd/sqrt(s.n)), i.m/(i.sd/sqrt(i.n))
        FROM s JOIN i USING (factor_id) ORDER BY 2 DESC"""):
        table.append([f, round(100 * float(ann), 2), round(float(t), 2), round(float(ic_t), 2)])
    return jsonify({"ls": ls, "table": table})


@app.route("/api/composite")
def composite():
    guard()
    out = {}
    for cid, in q("SELECT DISTINCT composite_id FROM composites_ls"):
        lvl, series = 1.0, []
        for a, v in q("SELECT asof, ls_ret FROM composites_ls WHERE composite_id=%s ORDER BY asof",
                      (cid,)):
            lvl *= (1 + float(v))
            series.append([str(a), round(lvl, 4)])
        out[cid] = series
    return jsonify({"curves": out, "boundary": DEVVAL_END})


@app.route("/api/portfolio")
def portfolio():
    guard()
    asof = q("SELECT max(asof) FROM factor_values")[0][0]
    rows = q("""SELECT fv.security_id, fv.factor_id, fv.z_sector_size FROM factor_values fv
                WHERE fv.asof=%s AND fv.factor_id = ANY(%s)""", (asof, list(SET_A)))
    z = defaultdict(dict)
    for sec, fid, v in rows:
        z[sec][fid] = float(v) * SET_A[fid]
    scored = [(sum(d.values()) / len(d), sec) for sec, d in z.items() if len(d) >= 3]
    scored.sort(reverse=True)
    k = max(1, len(scored) // 10)
    names = {}
    for label, grp in (("long", scored[:k][:25]), ("short", scored[-k:][-25:])):
        ids = [sec for _, sec in grp]
        meta = {s: (sym, sect) for s, sym, sect in q(
            """SELECT sm.security_id, sm.symbol, COALESCE(p.sector, '?')
               FROM symbol_map sm LEFT JOIN LATERAL (
                 SELECT sector FROM profile_snapshots ps
                 WHERE ps.security_id = sm.security_id ORDER BY asof DESC LIMIT 1) p ON true
               WHERE sm.valid_to IS NULL AND sm.security_id = ANY(%s)""", (ids,))}
        names[label] = [[meta.get(sec, ("?", "?"))[0], meta.get(sec, ("?", "?"))[1],
                         round(cz, 2)] for cz, sec in grp]
    return jsonify({"asof": str(asof), "deciles_n": k, "portfolio": names})


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>factor_lab</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
body{background:#0d1117;color:#c9d1d9;font:14px/1.5 -apple-system,Segoe UI,sans-serif;margin:0;padding:24px}
h1{font-size:20px;color:#e6edf3} h2{font-size:15px;color:#8b949e;margin:28px 0 8px;text-transform:uppercase;letter-spacing:.08em}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px}
.big{font-size:22px;color:#e6edf3}.lbl{font-size:11px;color:#8b949e}
table{border-collapse:collapse;width:100%}td,th{padding:5px 10px;border-bottom:1px solid #21262d;text-align:right;font-variant-numeric:tabular-nums}
th{color:#8b949e;font-size:11px;text-transform:uppercase}td:first-child,th:first-child{text-align:left}
.pos{color:#3fb950}.neg{color:#f85149}.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}
canvas{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px}
</style></head><body>
<h1>factor_lab <span class="lbl">— live paper track</span></h1>
<div class="grid" id="stats"></div>
<h2>composite equity curves (registered | holdout)</h2><canvas id="cmp" height="90"></canvas>
<h2>universe</h2><canvas id="uni" height="70"></canvas>
<h2>factor board (full-sample LS)</h2><div class="card"><table id="ftab"></table></div>
<h2>current cmpA_ew paper portfolio <span class="lbl" id="pasof"></span></h2>
<div class="two"><div class="card"><table id="long"></table></div>
<div class="card"><table id="short"></table></div></div>
<script>
const T = new URLSearchParams(location.search).get('token');
const api = p => fetch('/api/'+p+(T?'?token='+T:'')).then(r=>r.json());
const C = {plugins:{legend:{labels:{color:'#8b949e'}}},scales:{x:{ticks:{color:'#484f58',maxTicksLimit:14}},y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}}};
api('summary').then(d=>{
  const s=document.getElementById('stats');
  const items=[['securities',d.counts.securities],['price rows',d.counts.prices_raw_d.toLocaleString()],
   ['statement rows',d.counts.fundamentals_q.toLocaleString()],['factor cells',d.counts.factor_values.toLocaleString()],
   ['last job',d.jobs.length? d.jobs[0][0]+' '+d.jobs[0][2] : 'none']];
  s.innerHTML=items.map(i=>`<div class="card"><div class="big">${i[1]}</div><div class="lbl">${i[0]}</div></div>`).join('');
  new Chart(uni,{type:'line',data:{labels:d.universe.map(x=>x[0]),datasets:[{label:'in-universe',
   data:d.universe.map(x=>x[1]),borderColor:'#58a6ff',pointRadius:0,borderWidth:1.5}]},options:C});
});
api('composite').then(d=>{
  const colors={cmpA_ew:'#3fb950',cmpA_icw:'#2ea043',cmpB_ew:'#f85149',cmpB_icw:'#da3633'};
  const ds=Object.entries(d.curves).map(([k,v])=>({label:k,data:v.map(x=>x[1]),
   borderColor:colors[k]||'#8b949e',pointRadius:0,borderWidth:k=='cmpA_ew'?2.5:1}));
  const labels=Object.values(d.curves)[0].map(x=>x[0]);
  const opts=JSON.parse(JSON.stringify(C));
  new Chart(cmp,{type:'line',data:{labels,datasets:ds},options:opts});
});
api('factors').then(d=>{
  ftab.innerHTML='<tr><th>factor</th><th>LS ann %</th><th>t</th><th>IC t</th></tr>'+
   d.table.map(r=>`<tr><td>${r[0]}</td><td class="${r[1]>=0?'pos':'neg'}">${r[1]>0?'+':''}${r[1]}</td><td>${r[2]}</td><td>${r[3]}</td></tr>`).join('');
});
api('portfolio').then(d=>{
  pasof.textContent='asof '+d.asof+' · decile n='+d.deciles_n;
  const row=r=>`<tr><td>${r[0]}</td><td class="lbl">${r[1]}</td><td>${r[2]}</td></tr>`;
  long.innerHTML='<tr><th>LONG</th><th>sector</th><th>z</th></tr>'+d.portfolio.long.map(row).join('');
  short.innerHTML='<tr><th>SHORT</th><th>sector</th><th>z</th></tr>'+d.portfolio.short.map(row).join('');
});
</script></body></html>"""


@app.route("/")
def index():
    guard()
    return PAGE


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
