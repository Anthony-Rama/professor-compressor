"""Read-only operator diagnostics. No credential in a URL or browser storage."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import deque

from aiohttp import BasicAuth, web

from .observability import EventStore

HTML = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Professor Compressor · Operations</title>
<style>
:root{color-scheme:dark;font:15px system-ui,sans-serif;background:#0d1422;color:#edf2fa}
*{box-sizing:border-box}body{margin:0}main{max-width:1200px;margin:auto;padding:28px 20px}
h1{font-size:26px;margin:0 0 8px}h2{font-size:19px;margin-top:28px}p{color:#b3bfd1;line-height:1.5}
form,.summary{display:flex;gap:12px;flex-wrap:wrap;align-items:end;margin:22px 0}
label{display:grid;gap:6px}label.check{display:flex;align-items:center;align-self:center}
input,select,button{font:inherit;min-height:42px;padding:8px 12px;border:1px solid #45566e;border-radius:8px;background:#172437;color:inherit;max-width:100%}
button{background:#5865f2}a{color:#b4baff}code{overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}
.stat{background:#172437;border:1px solid #2d405a;border-radius:10px;padding:14px;min-width:140px}
.stat strong{display:block;font-size:25px}.stat span{color:#aab8cc}.tables{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:22px}
.scroll{overflow:auto;max-width:100%}.tables>section{min-width:0}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:10px 7px;border-bottom:1px solid #2c3b50;vertical-align:top}th{color:#aab8cc}td{overflow-wrap:anywhere}
.warning{color:#ffd69b}.ok{color:#9ce8d2}[hidden]{display:none!important}summary{padding:8px 0;cursor:pointer}
</style><main>
<h1>Professor Compressor operations</h1>
<p>Server-confirmed delivery and browser-reported processing. Test servers excluded by default. Times are UTC.</p>
<form id="filters">
<label>Window<select id="days"><option value="1">24 hours</option><option value="7" selected>7 days</option><option value="30">30 days</option></select></label>
<label>Diagnostic reference<input id="reference" placeholder="PC-…" maxlength="64"></label>
<label>Filter<select id="dimension"><option value="">All</option><option value="error_code">Error code</option><option value="stage">Stage</option><option value="input_format">Format</option><option value="browser">Browser</option><option value="revision">Revision</option></select></label>
<label>Value<input id="value" maxlength="64" placeholder="e.g. METADATA_INVALID"></label>
<label class="check"><input id="tests" type="checkbox"> Include tests</label>
<button type="submit">Refresh</button><button type="button" id="export">Export this view</button>
</form>
<p id="status" role="status">Loading…</p><div id="summary" class="summary"></div>
<div class="tables" id="groups"></div><h2>Stage times</h2><p>Median and p95 from up to 5,000 recent samples per measurement. Encoding is per file; other times are per run or delivery. Compare like inputs.</p><div id="timings"></div>
<h2>Comparable encoding groups</h2><div id="performance"></div>
<h2>Activity and adoption</h2><p id="adoption"></p><p>Daily activity below covers the selected time window and test setting, independent of the reference or category filter.</p><div id="activity"></div>
<h2>Recent sessions</h2><p>Up to 100 matching sessions. Open a reference to inspect its events. Expired and unresolved sessions are not counted as confirmed compression failures.</p><div id="sessions" class="scroll"></div>
<h2>Session timeline</h2><p>Latest 500 events, shown in arrival order. Browser reports are untrusted and best-effort; their absence does not prove inactivity. A failure may be followed by a successful retry. Server timestamps reflect arrival order, not exact browser execution order.</p><div id="events"></div>
<p>History is limited by retention, row and disk caps. Unknown outcomes cannot be reconstructed after a crash or lost browser reports.</p>
</main><script>
const el = id => document.getElementById(id);
let snapshot = null;
let loadSequence = 0;
function cell(value) { const td=document.createElement('td');td.textContent=value ?? '—';return td; }
function table(target, columns, rows) {
  target.replaceChildren(); target.classList.add('scroll'); const t=document.createElement('table'),head=document.createElement('tr');
  for(const [label] of columns){const th=document.createElement('th');th.textContent=label;head.append(th);}t.append(head);
  for(const row of rows){const tr=document.createElement('tr');for(const [,key] of columns) tr.append(cell(row[key]));t.append(tr);}target.append(t);
}
const when = value => new Date(value*1000).toISOString().replace('T',' ').slice(0,19)+' UTC';
const seconds = value => value === undefined ? '—' : value < 1 ? Math.round(value*1000)+'ms' : Number(value).toFixed(2)+'s';
async function load() {
  const sequence = ++loadSequence;
  snapshot = null;
  const query = new URLSearchParams({days:el('days').value,tests:el('tests').checked?'1':'0',reference:el('reference').value,dimension:el('dimension').value,value:el('value').value});
  el('status').textContent='Loading…';
  try {
    const response=await fetch('/ops/data?'+query,{cache:'no-store'});
    if(!response.ok) throw new Error('Could not load diagnostics ('+response.status+').');
    const result=await response.json();
    if(sequence!==loadSequence)return;
    snapshot=result; const d=snapshot,s=d.storage;
    el('status').textContent=s.ready?'Updated '+new Date().toISOString()+' · queued '+s.queued+' · dropped '+s.dropped+' · storage errors '+s.errors+' · retention '+s.retention_days+' days':'Diagnostic storage is not ready. Check operator logs/configuration.';
    el('status').className=s.errors||s.dropped||!s.ready?'warning':'ok';
    el('summary').replaceChildren();
    for(const [label,value] of Object.entries(d.totals||{})){const box=document.createElement('div');box.className='stat';const n=document.createElement('strong'),l=document.createElement('span');n.textContent=value===null?'—':label==='delivery_percent'?value+'%':value;l.textContent=({delivery_percent:'Delivered / started',users:'Users with sessions',servers:'Servers with sessions',recovered:'Recovered sessions'})[label]||label;box.append(n,l);el('summary').append(box);}
    el('groups').replaceChildren();
    const groups={Status:d.statuses||[],...(d.groups||{})};
    for(const [name,rows] of Object.entries(groups)){const section=document.createElement('section'),h=document.createElement('h2'),content=document.createElement('div');h.textContent=({stage:'Stages reached',error_code:'Error codes',input_format:'Input formats',browser:'Browsers',revision:'Releases',failure_stage:'Failures by stage'})[name]||name;section.append(h,content);el('groups').append(section);table(content,[[h.textContent,name==='Status'?'status':'value'],['Sessions',name==='Status'?'count':'sessions']],rows);}
    table(el('timings'),[['Measurement','name'],['Samples','samples'],['Median','p50'],['p95','p95']],Object.entries(d.timings||{}).map(([name,v])=>({name,...v,p50:seconds(v.p50),p95:seconds(v.p95)})));
    table(el('performance'),[['Format','format'],['Duration','duration_band'],['Output height','height'],['Files','files'],['Mean encode','mean_encoding'],['Media seconds / encode second','speed']],(d.performance||[]).map(r=>({...r,mean_encoding:seconds(r.mean_encoding),speed:r.speed===null?'—':r.speed.toFixed(2)})));
    const a=d.adoption||{};el('adoption').textContent='Servers with a delivered session: '+(a.activated_servers||0)+' · Servers with at least two delivered sessions: '+(a.repeat_servers||0)+'. Counts cover this window, not lifetime retention.';
    table(el('activity'),[['Day (UTC)','day'],['Event','event'],['Count','count']],d.activity||[]);
    const rows=(d.sessions||[]).map(r=>({...r,created:when(r.created)}));
    table(el('sessions'),[['Reference','reference'],['Created','created'],['Status','status'],['Files delivered','files'],['Failure reports','failures'],['Revision','revision'],['Test','is_test']],rows);
    for(const [index,row] of rows.entries()){const td=el('sessions').querySelectorAll('tr')[index+1].children[0],button=document.createElement('button');button.textContent=row.reference;button.type='button';button.addEventListener('click',()=>{el('reference').value=row.reference;void load();});td.replaceChildren(button);}
    el('events').replaceChildren();
    for(const e of d.events||[]){const item=document.createElement('details'),summary=document.createElement('summary'),data=document.createElement('pre');summary.textContent=when(e.at)+' · '+e.event+' · '+e.source+' · file '+e.file_index+' · run '+(e.run_id.slice(0,8)||'none');data.textContent=JSON.stringify(e.data,null,2);item.append(summary,data);el('events').append(item);}
  } catch(error){if(sequence!==loadSequence)return;el('status').textContent=error.message;el('status').className='warning';}
}
el('filters').addEventListener('submit',event=>{event.preventDefault();void load();});
el('export').addEventListener('click',()=>{if(!snapshot)return;const url=URL.createObjectURL(new Blob([JSON.stringify(snapshot,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='professor-diagnostics.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
void load();
</script></html>"""


def register_dashboard(
    application: web.Application, store: EventStore, key: str, ip_address
) -> None:
    failures: dict[str, deque] = {}
    global_failures: deque[float] = deque()
    slots = asyncio.Semaphore(2)
    headers = {
        "Cache-Control": "no-store",
        "X-Robots-Tag": "noindex, nofollow, noarchive",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }

    def authenticate(request: web.Request) -> web.Response | None:
        if not key:
            return web.Response(status=404, headers=headers)
        # A stranger must not be able to lock out an operator who knows the key.
        try:
            auth = BasicAuth.decode(
                request.headers.get("Authorization", ""), encoding="utf-8"
            )
            valid = auth.login == "admin" and secrets.compare_digest(
                auth.password.encode(), key.encode()
            )
        except (ValueError, UnicodeError):
            valid = False
        if valid:
            return None
        now = time.monotonic()
        while global_failures and now - global_failures[0] > 60:
            global_failures.popleft()
        if len(global_failures) >= 500:
            return web.Response(status=429, headers={**headers, "Retry-After": "60"})
        # Bound unauthenticated bookkeeping, including requests with spoofed IPs.
        for ip in list(failures):
            if not failures[ip] or now - failures[ip][-1] > 60:
                failures.pop(ip, None)
        ip = ip_address(request)
        attempts = failures.get(ip, deque())
        while attempts and now - attempts[0] > 60:
            attempts.popleft()
        if len(attempts) >= 10:
            return web.Response(status=429, headers={**headers, "Retry-After": "60"})
        if not valid:
            global_failures.append(now)
            if len(failures) < 2000 or ip in failures:
                failures.setdefault(ip, attempts).append(now)
            return web.Response(
                status=401,
                headers={
                    **headers,
                    "WWW-Authenticate": 'Basic realm="Professor Compressor operations", charset="UTF-8"',
                },
            )
        return None

    async def dashboard(request: web.Request) -> web.Response:
        failure = authenticate(request)
        if failure is not None:
            return failure
        return web.Response(text=HTML, content_type="text/html", headers=headers)

    async def data(request: web.Request) -> web.Response:
        failure = authenticate(request)
        if failure is not None:
            return failure
        try:
            days = int(request.query.get("days", "7"))
        except ValueError:
            return web.json_response(
                {"error": "Invalid days"}, status=400, headers=headers
            )
        if slots.locked():
            return web.json_response(
                {"error": "Dashboard busy"}, status=429, headers=headers
            )
        async with slots:
            try:
                result = await asyncio.to_thread(
                    store.query,
                    days=days,
                    include_tests=request.query.get("tests") == "1",
                    reference=request.query.get("reference", ""),
                    dimension=request.query.get("dimension", ""),
                    value=request.query.get("value", ""),
                )
            except Exception:
                return web.json_response(
                    {"error": "Diagnostic storage unavailable"},
                    status=503,
                    headers=headers,
                )
        return web.json_response(result, headers=headers)

    application.router.add_get("/ops", dashboard)
    application.router.add_get("/ops/data", data)
