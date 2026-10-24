// Private operator UI. Render untrusted event values as text, never HTML.
const el = id => document.getElementById(id);
let snapshot = null, loadSequence = 0, loading = false;
const titles = {
  overview: ['Overview', 'A clear view of your bot, its users, and every delivery.'],
  activity: ['Activity & adoption', 'Understand who uses the bot and how adoption changes.'],
  reliability: ['Reliability', 'Find recurring failures and distinguish recovery from abandonment.'],
  performance: ['Performance', 'Understand encoding speed, delivery time, and file-size savings.'],
  sessions: ['Session explorer', 'Follow an individual session from its private link to delivery.'],
  system: ['System health', 'Live process, Discord, queue, and diagnostic storage health.']
};
const number = value => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : '—';
const seconds = value => typeof value !== 'number' ? '—' : value < 1 ? Math.round(value * 1000) + ' ms' : value.toFixed(1) + ' s';
const bytes = value => typeof value !== 'number' ? '—' : value < 1048576 ? (value/1024).toFixed(1)+' KiB' : (value/1048576).toFixed(1)+' MiB';
const percent = value => typeof value === 'number' ? value.toFixed(1)+'%' : '—';
const when = value => typeof value === 'number' ? new Date(value*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'}) : '—';
const age = value => typeof value !== 'number' ? '—' : value >= 86400 ? (value/86400).toFixed(1)+' days' : value >= 3600 ? (value/3600).toFixed(1)+' hours' : Math.floor(value/60)+' min';
const revision = value => value==='development'?'Development':value?.slice(0,8)||'—';
const labels = {
  created:'Created', opened:'Link opened', selected:'Files selected', processing:'Processing', uploading:'Uploading', queued:'Queued', delivering:'Sending to Discord', delivered:'Delivered',
  upload_failed:'Upload failed', delivery_failed:'Delivery failed', cancelled:'Cancelled', expired:'Expired before start', replaced:'Replaced', unknown:'Unknown outcome',
  engine_seconds:'Engine loading · per run', encoding_seconds:'Video encoding · per file', relay_receive_seconds:'Relay upload · per batch', queue_seconds:'Delivery queue · per batch', discord_delivery_seconds:'Discord delivery · per batch', total_seconds:'End to end · per run',
  installed:'Bot installed', removed:'Bot removed', stage_started:'Stage started', stage_finished:'Stage finished', file_ready:'File ready', file_received:'File received', retry:'Automatic retry', client_complete:'Browser finished', client_failed:'Browser failure reported', upload_started:'Upload started', delivery_started:'Discord delivery started', received:'Relay received batch', started:'Compress pressed', refreshed:'Page refreshed', page_left:'Page left', interrupted:'Process interrupted',
  unchanged:'Sent unchanged', remuxed:'Stream-copied', converted:'Converted', compressed:'Compressed', 'single-threaded':'Single-threaded', multithreaded:'Multithreaded', unused:'Engine not needed', desktop:'Desktop', mobile:'Mobile'
};
const label = value => labels[value] || value || 'Not reported';
function showPanel(name, focus = false) {
  if (!titles[name]) return;
  for (const button of document.querySelectorAll('[data-tab]')) {
    const selected = button.dataset.tab === name;
    button.setAttribute('aria-selected', String(selected));button.tabIndex = selected ? 0 : -1;
    if (selected && focus) button.focus();
    el('panel-'+button.dataset.tab).hidden = !selected;
  }
  el('page-title').textContent = titles[name][0];el('page-description').textContent = titles[name][1];
}
function empty(target, title, description = '') {
  target.replaceChildren();const box=document.createElement('div'),strong=document.createElement('strong'),p=document.createElement('p');
  box.className='empty';strong.textContent=title;p.textContent=description;box.append(strong,p);target.append(box);
}
function table(target, columns, rows, title = 'No measurements yet', description = 'New activity will appear here as it is recorded.') {
  target.replaceChildren();target.classList.add('scroll');
  if (!rows.length) {empty(target,title,description);return;}
  const t=document.createElement('table'),head=document.createElement('thead'),header=document.createElement('tr'),body=document.createElement('tbody');
  for (const [name] of columns) {const th=document.createElement('th');th.scope='col';th.textContent=name;header.append(th);}head.append(header);t.append(head,body);
  for (const row of rows) {const tr=document.createElement('tr');for (const [,key,render] of columns) {const td=document.createElement('td');const value=render ? render(row[key],row) : row[key];if(value instanceof Node)td.append(value);else td.textContent=value ?? '—';tr.append(td);}body.append(tr);}target.append(t);
}
function badge(value) {
  const node=document.createElement('span');node.className='badge';
  if(value==='delivered')node.classList.add('good');else if(['client_failed','upload_failed','delivery_failed'].includes(value))node.classList.add('danger');else if(['unknown','cancelled'].includes(value))node.classList.add('warning');
  node.textContent=label(value);return node;
}
function cards(target, items, live = false) {
  target.replaceChildren();
  for (const [name,value,detail='',tone='',key=''] of items) {
    const card=document.createElement('div'),title=document.createElement('div'),amount=document.createElement(live?'div':'strong'),sub=document.createElement('div');
    card.className=live?'live-stat':'stat';if(key)card.dataset.metric=key;
    title.className='metric-label';title.textContent=name;amount.className='metric-value '+tone;amount.textContent=value;sub.className='metric-detail';sub.textContent=detail;
    if(live)card.append(amount,title,sub);else card.append(title,amount,sub);target.append(card);
  }
}
function outcomes(target, items) {
  target.replaceChildren();
  for (const [name,value,detail='',tone=''] of items) {const box=document.createElement('div'),n=document.createElement('div'),l=document.createElement('div'),p=document.createElement('div');n.className='metric-value '+tone;n.textContent=value;l.className='metric-label';l.textContent=name;p.className='metric-detail';p.textContent=detail;box.append(n,l,p);target.append(box);}
}
function group(target, rows, kind = 'Sessions') {
  table(target, [['Category','value',label],[kind,'sessions',number]],rows||[],'Nothing recorded in this view','Try another window or include development tests.');
}
function chart(rows) {
  const target=el('chart');target.replaceChildren();target.style.gridTemplateColumns='minmax(0, 1fr)';
  const relevant=rows.filter(row=>['created','delivered'].includes(row.event));
  if(!relevant.length){empty(target,'No session activity yet','Your first recorded sessions and deliveries will appear here.');return;}
  const now=new Date(),start=new Date(now.getTime()-Number(el('days').value)*86400000),dates=[];
  start.setUTCHours(0,0,0,0);
  while(start<=now&&dates.length<31){dates.push(start.toISOString().slice(0,10));start.setUTCDate(start.getUTCDate()+1);}
  const max=Math.max(1,...relevant.map(row=>row.count));
  target.style.gridTemplateColumns=`repeat(${dates.length}, minmax(22px, 1fr))`;
  for (const day of dates) {const column=document.createElement('div'),bars=document.createElement('div'),caption=document.createElement('span');column.className='chart-day';bars.className='bars';
    const counts={};for(const event of ['created','delivered']){const count=relevant.find(row=>row.day===day&&row.event===event)?.count||0;counts[event]=count;const bar=document.createElement('div');bar.className='bar'+(event==='delivered'?' delivery':'');bar.style.height=(count/max*125)+'px';bar.setAttribute('aria-hidden','true');bars.append(bar);}
    column.setAttribute('role','img');column.setAttribute('aria-label',`${day}: ${counts.created} created, ${counts.delivered} delivered`);column.title=column.getAttribute('aria-label');caption.className='day-label';caption.textContent=day.slice(5);column.append(bars,caption);target.append(column);}
}
function render(d) {
  const t=d.totals||{},s=d.storage||{},r=d.runtime||{},c=r.counters||{},a=d.adoption||{},f=d.files||{},timings=d.timings||{},retries=d.retries||{},groups=d.groups||{};
  cards(el('live'),[
    ['Current servers',number(r.connected_servers),r.server_count_current?'Discord connected · current count':r.connected_servers==null?'Waiting for Discord connection':'Last known count · Discord disconnected','', 'connected_servers'],
    ['Bot status',r.ready?'Operational':r.draining?'Draining':r.discord_ready?'Starting':'Disconnected',r.discord_ready?'Gateway latency '+seconds(typeof r.gateway_latency_ms==='number'?r.gateway_latency_ms/1000:undefined):'Waiting for Discord readiness',r.ready?'good':'warning'],
    ['Active sessions',number(r.active_sessions),'Uploads '+number(r.active_uploads)+' · queue '+number(r.queue_depth)+' / '+number(r.queue_capacity)],
    ['Process uptime',age(c.uptime_seconds),'Release '+revision(r.revision)]
  ],true);
  cards(el('summary'),[
    ['Sessions created',number(t.sessions),'Distinct sessions in this view','', 'sessions'],
    ['Delivered sessions',number(t.delivered),'Confirmed by Discord delivery','good'],
    ['Videos delivered',number(t.files),'Individual files, across delivered batches'],
    ['Delivered / started',percent(t.delivery_percent),'Includes pending starts; not a final success rate'],
    ['Users with sessions',number(t.users),'Distinct pseudonymous users'],
    ['Servers with sessions',number(t.servers),'Usage in this view, not total installations'],
    ['Needs attention',number(t.needs_attention),'Latest state reports a processing or delivery failure',t.needs_attention?'danger':''],
    ['Recovered sessions',number(t.recovered),'A reported failure followed by successful delivery']
  ]);
  outcomes(el('outcomes'),[
    ['In progress',number(t.in_progress),'Processing, upload, queue or delivery'],['Needs attention',number(t.needs_attention),'Reported failures',t.needs_attention?'danger':''],['Cancelled',number(t.cancelled),'User cancelled',t.cancelled?'warning':''],['Unknown outcome',number(t.unknown),'Final outcome not confirmed',t.unknown?'warning':'']
  ]);
  el('funnel').replaceChildren();
  for(const [key,name] of [['sessions','Session created'],['opened','Link opened'],['selected','Videos selected'],['started','Compress pressed'],['delivered','Delivered to Discord']]){
    const row=document.createElement('div'),title=document.createElement('div'),left=document.createElement('span'),right=document.createElement('span'),track=document.createElement('div'),fill=document.createElement('div');row.className='funnel-row';title.className='funnel-title';left.textContent=name;right.textContent=number(t[key]);track.className='track';fill.className='track-fill';fill.style.width=(t.sessions?Math.min(100,(t[key]||0)/t.sessions*100):0)+'%';title.append(left,right);track.append(fill);row.append(title,track);el('funnel').append(row);
  }
  chart(d.activity||[]);
  let joins=0,leaves=0;for(const row of d.activity||[]){if(row.event==='installed')joins+=row.count;if(row.event==='removed')leaves+=row.count;}
  cards(el('adoption'),[
    ['Servers with delivery',number(a.activated_servers),'At least one delivered session'],['Repeat servers',number(a.repeat_servers),'At least two delivered sessions in this view'],['Bot installs',number(joins),'Recorded in the selected window; ignores category/reference filters'],['Bot removals',number(leaves),'Recorded in the selected window; not a failure reason']
  ]);
  cards(el('reliability-summary'),[
    ['Needs attention',number(t.needs_attention),'Latest state, rather than every failed attempt',t.needs_attention?'danger':''],['Unknown outcomes',number(t.unknown),'Not counted as confirmed failures'],['Manual runs',number(retries.runs),'Compress starts with a recorded run ID'],['Automatic retries',number(retries.automatic_retries),'Recorded retry attempts; reports may be incomplete']
  ]);
  for(const field of ['stage','error_code','failure_stage','input_format','browser','device','engine','path','revision'])group(el('group-'+field),groups[field]);
  table(el('statuses'),[['Outcome','status',badge],['Sessions','count',number]],d.statuses||[],'No outcomes yet');
  cards(el('performance-summary'),[
    ['Median encode',seconds(timings.encoding_seconds?.p50),'Per file · includes failed encoding attempts'],['p95 encode',seconds(timings.encoding_seconds?.p95),'95% of sampled file encodes were at or below this time'],['Median end to end',seconds(timings.total_seconds?.p50),'Browser-reported · per run'],['Median Discord delivery',seconds(timings.discord_delivery_seconds?.p50),'Server-measured · per delivered batch']
  ]);
  table(el('timings'),[['Measurement','name',label],['Samples','samples',number],['Median','p50',seconds],['p95','p95',seconds]],Object.entries(timings).map(([name,value])=>({name,...value})),'No stage timings yet','Timing samples appear after processing or delivery reports arrive.');
  outcomes(el('file-summary'),[
    ['Measured delivered files',number(f.measured_files),'Coverage may be smaller than all delivered files'],['Reported input size',bytes(f.input_bytes),'Input size supplied by the browser'],['Finished output size',bytes(f.output_bytes),'Measured by the relay'],['Size reduction',percent(f.reduction_percent),'Across files with both measurements']
  ]);
  table(el('performance'),[['Format','format',label],['Clip length','duration_band'],['Output height','height',value=>value?number(value)+'p':'Not reported'],['Files','files',number],['Mean encode','mean_encoding',seconds],['× real time','speed',value=>typeof value==='number'?value.toFixed(2)+'×':'—']],d.performance||[]);
  table(el('activity'),[['UTC day','day'],['Event','event',label],['Count','count',number]],d.activity||[],'No activity recorded yet','History begins when tracking was enabled; older Discord logs are not imported.');
  table(el('sessions'),[
    ['Reference','reference',(value)=>{const button=document.createElement('button');button.className='reference-button';button.textContent=value;button.type='button';button.addEventListener('click',()=>{el('reference').value=value;showPanel('sessions');void load();});return button;}],
    ['Created','created',when],['Outcome','status',badge],['Files','files',number],['Failure reports','failures',number],['Release','revision',revision],['Test','is_test',value=>value?'Yes':'No']
  ],d.sessions||[],'No matching sessions','Change the window or filters. Enable Include tests to see development-server activity.');
  el('events').replaceChildren();const history=d.events||[];
  const received=new Map();for(const event of history)if(event.event==='file_received'&&event.source==='server')received.set(event.file_index,event);
  const dimensions=(width,height,fps)=>width&&height?width+' × '+height+(typeof fps==='number'?' · '+fps.toFixed(1)+' fps':''):'Not reported';
  table(el('session-files'),[['File','file'],['Format','format'],['Path','path',label],['Input','input',bytes],['Output','output',bytes],['Clip length','duration',seconds],['Encode','encode',seconds],['Input video','input_video'],['Output video','output_video'],['Source codecs','codecs']],
    [...received.values()].sort((a,b)=>a.file_index-b.file_index).map(e=>{
      const metadata=[...history].reverse().find(row=>row.file_index===e.file_index&&row.run_id===e.run_id&&row.data.video_codec)?.data;
      return {file:'File '+e.file_index,format:(e.data.input_format||'unknown').toUpperCase()+' → MP4',path:e.data.path,input:e.data.input_bytes,output:e.data.output_bytes,duration:e.data.duration_seconds,encode:e.data.encoding_seconds,input_video:dimensions(e.data.input_width,e.data.input_height,e.data.input_fps),output_video:dimensions(e.data.output_width,e.data.output_height,e.data.output_fps),codecs:metadata?(metadata.video_codec||'Unknown')+' / '+(metadata.audio_codec||'Not reported'):'Not reported'};
    }),
    'No received-file diagnostics','Select a session that reached the relay. Earlier failures can be inspected in the timeline.');
  el('timeline-heading').textContent=el('reference').value?el('reference').value+' · '+history.length+' most recent events · arrival order':'Select a session to inspect its history.';
  if(!history.length)empty(el('events'),'No session selected','Choose a reference above. A timeline contains up to 500 retained events.');
  for(const event of history){const item=document.createElement('details'),summary=document.createElement('summary'),time=document.createElement('span'),name=document.createElement('span'),meta=document.createElement('span'),body=document.createElement('pre');item.className='timeline-event';time.className='event-time';time.textContent=when(event.at);name.textContent=label(event.event);meta.className='event-detail';meta.textContent=(event.source==='server'?'Server':'Browser')+' · '+(event.file_index?'File '+event.file_index:'Batch')+' · run '+(event.run_id?.slice(0,8)||'—');body.textContent=JSON.stringify(event.data,null,2);summary.append(time,name,meta);item.append(summary,body);el('events').append(item);}
  cards(el('system-summary'),[
    ['Delivery workers',number(r.delivery_workers)+' / '+number(r.worker_capacity),'Workers currently running'],['Buffered video data',bytes(r.buffer_bytes),'Limit '+bytes(r.buffer_limit_bytes)],['Diagnostic queue',number(s.queued),'Waiting for background storage'],['Storage errors / drops',number(s.errors)+' / '+number(s.dropped),'Since this process started',(s.errors||s.dropped)?'warning':'good']
  ]);
  table(el('system'),[['Signal','name'],['Current value','value']], [
    {name:'Discord connection',value:r.discord_ready?'Connected':'Disconnected'},{name:'Command sync',value:r.commands_synced?'Complete':'Pending'},
    {name:'Unavailable servers',value:number(r.unavailable_servers)},{name:'Delivery queue',value:number(r.queue_depth)+' / '+number(r.queue_capacity)},
    {name:'Diagnostic storage',value:s.ready?'Ready':'Unavailable'},{name:'Retention',value:number(s.retention_days)+' days'},
    {name:'Event row limit',value:number(s.max_events)},{name:'Release',value:r.revision||'—'}
  ]);
  const counterLabels={uptime_seconds:'Process uptime',sessions_created:'Sessions created',sessions_opened:'Sessions opened',batches_queued:'Batches queued',files_queued:'Files queued',bytes_queued:'Bytes queued',deliveries_succeeded:'Delivered batches',files_delivered:'Files delivered',bytes_delivered:'Bytes delivered',deliveries_failed:'Failed batches',rate_limited_requests:'Rate-limited requests'};
  table(el('counters'),[['Counter','name'],['Since process start','value']],Object.entries(c).map(([name,value])=>({name:counterLabels[name]||name,value:name==='uptime_seconds'?age(value):name.startsWith('bytes_')?bytes(value):number(value)})));
  const hidden=d.collection?.hidden_test_sessions||0;el('notice').hidden=!(s.ready&&t.sessions===0);el('show-tests').hidden=!hidden;
  el('notice-title').textContent=!el('tests').checked&&hidden?'No production sessions in this view yet':'No matching sessions in this view';
  el('notice-text').textContent=hidden?number(hidden)+' development session(s) are hidden. Include tests to inspect them; production metrics stay separate by default.':'Tracking is active. New sessions will appear here automatically. Try a wider window if filters are applied.';
  el('history-note').textContent='History '+(d.collection?.since?'available since '+when(d.collection.since):'begins with the first tracked session')+' · up to '+number(s.retention_days)+' days · bounded by storage limits.';
  el('status').textContent=s.ready?'Updated '+new Date((d.generated_at||Date.now()/1000)*1000).toLocaleTimeString()+' · '+(el('tests').checked?'Tests included':'Tests excluded'):'Diagnostic storage unavailable · live process snapshot is still shown.';
  el('status').className=s.errors||s.dropped||!s.ready?'warning':'good';
}
async function load() {
  const sequence=++loadSequence;loading=true;snapshot=null;el('export').disabled=true;el('status').textContent='Loading…';
  const query=new URLSearchParams({days:el('days').value,tests:el('tests').checked?'1':'0',reference:el('reference').value,dimension:el('dimension').value,value:el('value').value});
  try{const response=await fetch('/ops/data?'+query,{cache:'no-store',signal:AbortSignal.timeout(15000)});if(!response.ok)throw new Error(response.status===401?'Sign in again to view diagnostics.':'Could not load diagnostics ('+response.status+').');const result=await response.json();if(sequence!==loadSequence)return;render(result);snapshot=result;el('export').disabled=false;}
  catch(error){if(sequence!==loadSequence)return;el('status').textContent=error.name==='TimeoutError'?'Refresh timed out. Try again.':error.message;el('status').className='warning';}
  finally{if(sequence===loadSequence)loading=false;}
}
el('filters').addEventListener('submit',event=>{event.preventDefault();void load();});
el('reset').addEventListener('click',()=>{el('reference').value='';el('dimension').value='';el('value').value='';void load();});
el('show-tests').addEventListener('click',()=>{el('tests').checked=true;void load();});
el('export').addEventListener('click',()=>{if(!snapshot)return;const url=URL.createObjectURL(new Blob([JSON.stringify(snapshot,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='professor-diagnostics.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
const tabs=[...document.querySelectorAll('[data-tab]')];
for(const [index,tab] of tabs.entries()){tab.addEventListener('click',()=>showPanel(tab.dataset.tab));tab.addEventListener('keydown',event=>{const direction=['ArrowRight','ArrowDown'].includes(event.key)?1:['ArrowLeft','ArrowUp'].includes(event.key)?-1:0;let next=direction?(index+direction+tabs.length)%tabs.length:event.key==='Home'?0:event.key==='End'?tabs.length-1:-1;if(next>=0){event.preventDefault();showPanel(tabs[next].dataset.tab,true);}});}
for(const button of document.querySelectorAll('[data-open]'))button.addEventListener('click',()=>showPanel(button.dataset.open));
setInterval(()=>{if(el('auto-refresh').checked&&document.visibilityState==='visible'&&!loading)void load();},30000);
void load();
