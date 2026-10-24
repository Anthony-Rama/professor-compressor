const { test, expect } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const { mkdtempSync, rmSync, readFileSync, writeFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');

const operator = {username: 'admin', password: 'browser-test-only-operator-password'};
const auth = {Authorization: 'Basic ' + Buffer.from(operator.username + ':' + operator.password).toString('base64')};
let fixtures;
test.beforeAll(() => {
  fixtures = mkdtempSync(join(tmpdir(), 'compressor-observability-'));
  execFileSync('ffmpeg', ['-hide_banner','-loglevel','error','-f','lavfi','-i',
    'testsrc2=size=320x240:rate=25','-t','0.4','-c:v','libx264','-crf','35',join(fixtures,'small.mp4')]);
  const input=readFileSync(join(fixtures,'small.mp4'));
  const free=Buffer.alloc(1_200_000-input.length);
  free.writeUInt32BE(free.length);free.write('free',4);
  writeFileSync(join(fixtures,'oversized.mp4'),Buffer.concat([input,free]));
  writeFileSync(join(fixtures,'damaged.mp4'), Buffer.from('not-a-video'));
});
test.afterAll(() => rmSync(fixtures, {recursive:true, force:true}));

async function open(page, request) {
  const session = await (await request.post('/test/session')).json();
  const loaded=page.waitForResponse(r => r.url().endsWith('/brand/diagnostics.js'));
  await page.goto(session.path);
  await expect(page.locator('#submit')).toBeVisible();
  await loaded;
  return session;
}
async function data(request, reference) {
  return (await request.get('/ops/data?tests=1&reference='+reference,{headers:auth})).json();
}

test('session history correlates browser and server delivery without content', async ({page,request}) => {
  const reports=[];
  page.on('request', r => {if(r.url().endsWith('/events')) reports.push(r.postData());});
  const session=await open(page,request);
  await page.locator('#caption').fill('private-caption-not-for-logs');
  await page.locator('#clips').setInputFiles(join(fixtures,'small.mp4'));
  await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');
  await expect.poll(async () => (await data(request,session.reference)).sessions[0]?.status).toBe('delivered');
  await expect.poll(async () => (await data(request,session.reference)).events.some(e => e.event==='client_complete')).toBe(true);
  const result=await data(request,session.reference);
  expect(result.groups.error_code).toEqual([]);
  expect(result.timings.relay_receive_seconds.samples).toBe(1);
  expect(result.events.some(e=>e.event==='delivered'&&e.source==='server')).toBe(true);
  expect(result.events.some(e=>e.event==='file_ready'&&e.source==='browser')).toBe(true);
  expect(result.events.filter(e=>e.run_id).every(e=>/^[0-9a-f]{32}$/.test(e.run_id))).toBe(true);
  expect(JSON.stringify(result)).not.toContain('small.mp4');
  expect(JSON.stringify(reports)).not.toContain('private-caption-not-for-logs');
});

test('failed validation shows searchable reference and a specific error code', async ({page,request}) => {
  const session=await open(page,request);
  await page.locator('#clips').setInputFiles(join(fixtures,'damaged.mp4'));
  await page.locator('#submit').click();
  await expect(page.locator('#message')).toContainText(session.reference);
  await expect.poll(async () => (await data(request,session.reference)).events.some(e=>e.data.error_code==='INVALID_SIGNATURE')).toBe(true);
  const result=await data(request,session.reference);
  expect(result.sessions[0].status).toBe('client_failed');
  expect(JSON.stringify(result.events)).not.toContain('damaged.mp4');
});

test('synchronous telemetry errors and unavailable event transport cannot fail delivery', async ({page,request}) => {
  await page.addInitScript(() => {
    const original=window.fetch;
    window.fetch=(url, options)=> {
      if(String(url).endsWith('/telemetry')||String(url).endsWith('/events')) throw new TypeError('injected diagnostic transport failure');
      return original(url,options);
    };
  });
  await open(page,request);
  await page.locator('#clips').setInputFiles(join(fixtures,'small.mp4'));
  await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');
  await expect(page.locator('#message')).toHaveClass('message success');
});

test('optional diagnostics module can be unavailable without breaking compression', async ({page,request}) => {
  await page.route('**/brand/diagnostics.js', route=>route.abort());
  const session=await (await request.post('/test/session')).json();
  await page.goto(session.path);
  await page.locator('#clips').setInputFiles(join(fixtures,'oversized.mp4'));
  await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');
});

for(const failAll of [false,true])test(`null engine startup rejection without diagnostics, fail all: ${failAll}`,async({page,request})=>{
  await page.route('**/brand/diagnostics.js',route=>route.abort());
  await page.addInitScript(failAll=>{
    const send=Worker.prototype.postMessage;let loads=0;
    Worker.prototype.postMessage=function(message,...rest){
      if(message?.type==='LOAD'&&(++loads===1||failAll)){
        queueMicrotask(()=>this.onmessage?.({data:{id:message.id,type:'ERROR',data:null}}));return;
      }
      return send.call(this,message,...rest);
    };
  },failAll);
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const session=await(await request.post('/test/session')).json();await page.goto(session.path);
  await page.locator('#clips').setInputFiles(join(fixtures,'oversized.mp4'));await page.locator('#submit').click();
  if(failAll){
    await expect(page.locator('#phase-title')).toHaveText('Action needed');
    await expect(page.locator('#submit')).toBeEnabled();
    await expect(page.locator('#message')).toContainText('video engine stopped unexpectedly');
  }else await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');
  expect(errors).toEqual([]);
});

for(const width of [375,1440]) {
  test(`protected dashboard renders and filters test history at ${width}px`, async ({browser,request}) => {
    const response=await request.get('/ops/data');
    expect(response.status()).toBe(401);
    const context=await browser.newContext({httpCredentials:operator,viewport:{width,height:900}});
    try {
      const page=await context.newPage();
      const sample=await open(page,request);
      await page.locator('#clips').setInputFiles(join(fixtures,'small.mp4'));
      await page.locator('#submit').click();
      await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');
      await page.goto('http://127.0.0.1:18764/ops');
      await expect(page.locator('#status')).toContainText('Updated');
      await expect(page.locator('[data-metric="connected_servers"] .metric-value')).toHaveText('57');
      const publicView=await (await request.get('/ops/data',{headers:auth})).json();
      expect(publicView.totals.sessions).toBe(0);
      expect(publicView.runtime.connected_servers).toBe(57);
      await expect(page.locator('#notice')).toBeVisible();
      await page.locator('#tests').check();
      await page.getByRole('button',{name:'Refresh',exact:true}).click();
      await page.screenshot({path:`/tmp/professor-ops-${width}.png`,fullPage:true});
      await page.getByRole('tab',{name:'Session explorer',exact:true}).click();
      await expect(page.locator('#sessions')).toContainText('PC-');
      await page.getByRole('button',{name:sample.reference,exact:true}).click();
      await expect(page.locator('#events')).toContainText('delivered');
      await page.screenshot({path:`/tmp/professor-ops-sessions-${width}.png`,fullPage:true});
      for(const name of ['Activity & adoption','Reliability','Performance','System health','Overview']){
        await page.getByRole('tab',{name,exact:true}).click();
        expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),name).toBe(true);
      }
      const overflow=await page.evaluate(()=>Array.from(document.querySelectorAll('main,form,label,input,select,.tables,.scroll,table')).filter(el=>el.getBoundingClientRect().right>innerWidth).map(el=>({tag:el.tagName,id:el.id,width:el.getBoundingClientRect().width})));
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),JSON.stringify(overflow)).toBe(true);
    } finally { await context.close(); }
  });
}

test('queued events keep their original run when a new attempt starts', async ({page,request}) => {
  await open(page,request);
  const result=await page.evaluate(async () => {
    const {createDiagnostics}=await import('/brand/diagnostics.js');
    const original=window.fetch, batches=[];
    window.fetch=(_url,options)=>{batches.push(JSON.parse(options.body));return Promise.resolve(new Response('{}'));};
    try {
      const reporter=createDiagnostics('test','PC-test');
      const first=reporter.begin();
      for(let i=0;i<45;i++)reporter.emit('stage_started',1,{stage:'encoding'});
      const second=reporter.begin();
      reporter.emit('stage_started',2,{stage:'metadata'});
      reporter.flush();reporter.flush();reporter.flush();
      return {first,second,batches};
    } finally {window.fetch=original;}
  });
  expect(result.batches.flatMap(b=>b.events.filter(e=>e.file_index===1).map(()=>b.run_id)))
    .toEqual(Array(45).fill(result.first));
  expect(result.batches.find(b=>b.events.some(e=>e.file_index===2)).run_id).toBe(result.second);
});

test('cancelled diagnostic step stays cancellation rather than encoder failure', async ({page,request}) => {
  await open(page,request);
  const result=await page.evaluate(async () => {
    const {createDiagnostics}=await import('/brand/diagnostics.js');
    const original=window.fetch, events=[];
    window.fetch=(_url,options)=>{events.push(...JSON.parse(options.body).events);return Promise.resolve(new Response('{}'));};
    try {
      const reporter=createDiagnostics('test','PC-test');reporter.begin();
      try {await reporter.step('engine',0,async()=>{throw new DOMException('cancelled','AbortError');},'ENGINE_LOAD_FAILED');}
      catch (_) {}
      reporter.flush();return events;
    } finally {window.fetch=original;}
  });
  expect(result.find(e=>e.event==='stage_finished').data).toMatchObject({error_code:'CANCELLED',outcome:'cancelled'});
});

test('worker strings and immutable errors retain safe categories and file context', async ({page,request}) => {
  await open(page,request);
  const result=await page.evaluate(async () => {
    const {createDiagnostics}=await import('/brand/diagnostics.js');
    const original=window.fetch, events=[], results=[];
    window.fetch=(_url,options)=>{events.push(...JSON.parse(options.body).events);return Promise.resolve(new Response('{}'));};
    try {
      const values=['RuntimeError: memory access out of bounds private-marker', 'private-marker encoder failure',
        null, 42, Object.freeze(new Error('allocation failed private-marker')),
        Object.freeze(Object.assign(new Error('private-marker'),{diagnosticCode:'ENCODE_FAILED'})),
        new DOMException('private-marker','AbortError')];
      for (const value of values) {
        const reporter=createDiagnostics('test','PC-test');reporter.begin();
        try {await reporter.step('encoding',3,async()=>{throw value;},'ENCODE_FAILED');}
        catch(error){results.push({code:reporter.code(error),file:error.diagnosticFileIndex});reporter.fail(error);}
      }
      return {results,events};
    } finally {window.fetch=original;}
  });
  expect(result.results).toEqual(['WASM_TRAP','ENCODE_FAILED','ENCODE_FAILED','ENCODE_FAILED',
    'BROWSER_MEMORY','ENCODE_FAILED','CANCELLED'].map(code=>({code,file:3})));
  expect(result.events.filter(e=>e.event==='client_failed')).toHaveLength(7);
  expect(result.events.filter(e=>e.event!=='stage_started'||e.data.stage!=='validation').every(e=>e.file_index===3)).toBe(true);
  expect(JSON.stringify(result.events)).not.toContain('private-marker');
});

test('real worker string rejection reports encoding failure without uploading media', async ({page,request}) => {
  await page.addInitScript(()=>{
    const send=Worker.prototype.postMessage;
    Worker.prototype.postMessage=function(message,...rest){
      if(message?.type==='EXEC'){
        queueMicrotask(()=>this.onmessage?.({data:{id:message.id,type:'ERROR',
          data:'RuntimeError: memory access out of bounds private-worker-marker'}}));
        return;
      }
      return send.call(this,message,...rest);
    };
  });
  const session=await open(page,request);
  await page.locator('#clips').setInputFiles(join(fixtures,'oversized.mp4'));
  await page.locator('#submit').click();
  await expect(page.locator('#phase-title')).toHaveText('Action needed');
  // The compact /failure alert can arrive before the richer /events batch.
  await expect.poll(async()=> (await data(request,session.reference)).events.some(e=>e.event==='client_failed'&&e.file_index===1)).toBe(true);
  const result=await data(request,session.reference);
  expect(result.events.find(e=>e.event==='client_failed')).toMatchObject({file_index:1,data:{stage:'encoding',error_code:'WASM_TRAP',engine_signal:'wasm_bounds'}});
  expect(result.events.some(e=>e.event==='delivered'||e.event==='file_ready')).toBe(false);
  expect(JSON.stringify(result.events)).not.toContain('private-worker-marker');
});

test('an older dashboard response cannot overwrite a newer refresh', async ({browser}) => {
  const context=await browser.newContext({httpCredentials:operator});
  try {
    const page=await context.newPage();
    let first;
    const snapshot=count=>({storage:{ready:true,queued:0,dropped:0,errors:0,retention_days:30},totals:{sessions:count}});
    await page.route('**/ops/data?**',async route=>{
      if(!first){first=route;return;}
      await route.fulfill({json:snapshot(222)});
    });
    await page.goto('http://127.0.0.1:18764/ops');
    await expect.poll(()=>Boolean(first)).toBe(true);
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await expect(page.locator('#summary [data-metric="sessions"] strong')).toHaveText('222');
    const oldResponse=page.waitForResponse(r=>r.url().includes('/ops/data?'));
    await first.fulfill({json:snapshot(111)});
    await oldResponse;
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    await expect(page.locator('#summary [data-metric="sessions"] strong')).toHaveText('222');
  } finally {await context.close();}
});

test('engine OOM outranks a misleading wrapper and failed files appear in the private dashboard',async({browser,request})=>{
  const context=await browser.newContext({httpCredentials:operator,viewport:{width:375,height:900}});
  try {
    const page=await context.newPage();
    await page.addInitScript(()=>{
      const send=Worker.prototype.postMessage;
      Worker.prototype.postMessage=function(message,...rest){
        if(message?.type==='EXEC'){
          queueMicrotask(()=>{
            this.onmessage?.({data:{type:'LOG',data:{type:'stderr',message:'https://private.invalid/private.mp4?secret=marker worker sent an error! Aborted(OOM)'}}});
            this.onmessage?.({data:{id:message.id,type:'ERROR',data:"TypeError: Cannot read properties of undefined (reading 'startsWith')"}});
          });
          return;
        }
        return send.call(this,message,...rest);
      };
    });
    const session=await open(page,request);
    await page.locator('#clips').setInputFiles(join(fixtures,'oversized.mp4'));
    await page.locator('#submit').click();
    await expect(page.locator('#phase-title')).toHaveText('Action needed');
    await expect.poll(async()=> (await data(request,session.reference)).events.some(e=>e.event==='client_failed'&&e.file_index===1&&e.data.engine_signal==='oom')).toBe(true);
    await expect.poll(async()=> (await data(request,session.reference)).events.filter(e=>e.event==='attempt_started').length).toBe(2);
    const result=await data(request,session.reference);
    const failure=result.events.find(e=>e.event==='client_failed');
    expect(failure.data).toMatchObject({error_code:'BROWSER_MEMORY',engine_signal:'oom',signal_source:'engine_log',exception_kind:'TypeError',decoder_mode:'auto',encoder_threads:4,attempt:2});
    expect(result.events.filter(e=>e.event==='attempt_started').map(e=>e.data.attempt)).toEqual([1,2]);
    expect(result.events.find(e=>e.event==='retry').data).toMatchObject({retry_reason:'same_settings',attempt:2,failed_attempt:1});
    const metadata=result.events.find(e=>e.data.pixel_format);
    expect(metadata.data).toMatchObject({pixel_format:'yuv420p',bit_depth:8});
    expect(JSON.stringify(result)).not.toMatch(/private\.invalid|private\.mp4|secret=marker|startsWith|oversized\.mp4/);
    const filtered=await(await request.get('/ops/data?tests=1&dimension=engine_signal&value=oom',{headers:auth})).json();
    expect(filtered.sessions.some(s=>s.reference===session.reference)).toBe(true);
    await page.goto('/ops');
    await page.locator('#tests').check();await page.locator('#reference').fill(session.reference);
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await page.getByRole('tab',{name:'Session explorer',exact:true}).click();
    await expect(page.locator('#session-files')).toContainText('320 × 240');
    await expect(page.locator('#session-attempts')).toContainText('Out of memory');
    await expect(page.locator('#session-attempts')).toContainText('TypeError');
    await expect(page.locator('#session-attempts')).toContainText('1 → 2');
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    expect(await page.locator('#session-attempts').evaluate(element=>element.scrollWidth>element.clientWidth)).toBe(true);
    await page.screenshot({path:'/tmp/compressor-evidence-mobile.png',fullPage:true});
    await page.locator('#session-attempts').screenshot({path:'/tmp/compressor-evidence-attempts.png'});
  } finally {await context.close();}
});

test('engine evidence resets between attempts and cannot turn a later network error into OOM',async({page,request})=>{
  await open(page,request);
  const result=await page.evaluate(async()=>{
    const {createDiagnostics,commandSettings,mediaDetails,engineSignal}=await import('/brand/diagnostics.js');
    const reporter=createDiagnostics('test','PC-test',false);reporter.begin();reporter.attempt(1,1);
    const args=['-threads','1','-i','private-file.mp4','-c:v','libx264','-threads','4','-preset','veryfast','-b:v','2000k','output.mp4'];
    reporter.configure(args,'multithreaded','617df09cbdea3add');
    let first;
    try {await reporter.step('encoding',1,async()=>{reporter.log('Aborted(OOM) private');throw 'TypeError: private';},'ENCODE_FAILED');}
    catch(error){first={code:reporter.code(error),facts:reporter.failureContext(error)};}
    reporter.attempt(2,1);
    let second;
    try {await reporter.step('encoding',2,async()=>{throw 'generic private error';},'ENCODE_FAILED');}
    catch(error){second={code:reporter.code(error),facts:reporter.failureContext(error)};}
    reporter.mark('upload');const network=reporter.code({status:0});
    const media=mediaDetails({profile:'private caption',pix_fmt:'p010le',color_transfer:'smpte2084',level:153,avg_frame_rate:'30/1',r_frame_rate:'60/1',side_data_list:[{rotation:-90}]},{channels:2,sample_rate:'48000'});
    return {first,second,network,settings:commandSettings(args),media,
      metadataSignal:engineSignal('    title           : out of memory'),
      filenameSignal:engineSignal("Input #0 from 'OOM.mp4'"),
      http:reporter.failureContext({status:503})};
  });
  expect(result.first).toMatchObject({code:'BROWSER_MEMORY',facts:{engine_signal:'oom',decoder_mode:'fixed',decoder_threads:1,encoder_threads:4}});
  expect(result.second.code).toBe('ENCODE_FAILED');expect(result.second.facts).not.toHaveProperty('engine_signal');
  expect(result.network).toBe('NETWORK');
  expect(result.metadataSignal).toBe('');expect(result.filenameSignal).toBe('');
  expect(result.http.http_status).toBe(503);
  expect(result.settings).toMatchObject({decoder_threads:1,encoder_threads:4,video_kbps:2000});
  expect(result.media).toMatchObject({video_profile:'other',pixel_format:'p010le',bit_depth:10,color_transfer:'smpte2084',rotation_quarter_turns:3,frame_rates_differ:true,audio_channels:2,audio_sample_rate:48000});
  expect(JSON.stringify(result)).not.toMatch(/private|output\.mp4/);
});

test('progress checkpoints are throttled and expanded event batches stay below the request limit',async({page,request})=>{
  await open(page,request);
  const result=await page.evaluate(async()=>{
    const {createDiagnostics}=await import('/brand/diagnostics.js');
    const originalFetch=window.fetch,originalNow=performance.now.bind(performance),batches=[];
    let now=100;
    window.fetch=(_url,options)=>{batches.push(options.body);return Promise.resolve(new Response('{}'));};
    Object.defineProperty(performance,'now',{configurable:true,value:()=>now});
    try {
      const reporter=createDiagnostics('test','PC-test');reporter.begin();reporter.attempt(1,1);
      await reporter.step('encoding',1,async()=>{
        for(let i=0;i<100;i++){now+=100;reporter.progress(i/100,i*100000);}
        now+=15000;reporter.progress(0.9,9000000);return 0;
      },'ENCODE_FAILED');
      for(let i=0;i<40;i++)reporter.emit('stage_started',1,{stage:'encoding',ignored:'x'.repeat(1500)});
      for(let i=0;i<20;i++)reporter.flush();
      const events=batches.flatMap(body=>JSON.parse(body).events);
      return {sizes:batches.map(body=>new TextEncoder().encode(body).length),events};
    } finally {window.fetch=originalFetch;Object.defineProperty(performance,'now',{configurable:true,value:originalNow});}
  });
  expect(result.events.filter(e=>e.event==='processing_progress')).toHaveLength(1);
  expect(result.events.find(e=>e.event==='processing_progress').data).toMatchObject({last_media_seconds:9,last_progress_percent:90});
  expect(result.sizes.every(size=>size<16384)).toBe(true);
  expect(result.events.every((e,index)=>e.data.client_sequence===index+1)).toBe(true);
  expect(result.events.filter(e=>e.data.ignored)).toHaveLength(40);
});

test('stronger exception evidence overrides earlier codec warnings and retries clear progress',async({page,request})=>{
  await open(page,request);
  const result=await page.evaluate(async()=>{
    const {createDiagnostics,mediaDetails}=await import('/brand/diagnostics.js');
    const reporter=createDiagnostics('test','PC-test',false);reporter.begin();reporter.attempt(1,1);
    let failure;
    try {await reporter.step('encoding',1,async()=>{
      reporter.progress(0.5,5000000);reporter.log('unsupported codec');
      throw 'RuntimeError: Aborted(OOM)';
    },'ENCODE_FAILED');}catch(error){failure={code:reporter.code(error),...reporter.failureContext(error)};}
    reporter.attempt(1,2);
    const next=reporter.failureContext(new Error('new failure'));
    reporter.log('RuntimeError: memory access out of bounds');reporter.log('invalid data found when processing input');
    return {failure,next,trap:reporter.failureContext(new Error('wrapper')),
      missing:mediaDetails({level:null},{channels:null,sample_rate:''})};
  });
  expect(result.failure).toMatchObject({code:'BROWSER_MEMORY',engine_signal:'oom',signal_source:'exception',last_media_seconds:5});
  expect(result.next).not.toHaveProperty('last_media_seconds');
  expect(result.next).not.toHaveProperty('progress_idle_seconds');
  expect(result.trap.engine_signal).toBe('wasm_bounds');
  for(const field of ['video_level','audio_channels','audio_sample_rate'])expect(result.missing).not.toHaveProperty(field);
});

test('file summaries keep runs separate and prefer newer browser facts and relay receipts',async({browser})=>{
  const context=await browser.newContext({httpCredentials:operator});
  try {
    const page=await context.newPage();
    const event=(run,event,source,data)=>({run_id:run,file_index:1,event,source,data});
    await page.route('**/ops/data?**',route=>route.fulfill({json:{storage:{ready:true},events:[
      event('aaaaaaaa','file_ready','browser',{client_sequence:20,input_width:1280,input_height:720,output_bytes:2000000}),
      event('aaaaaaaa','file_received','server',{output_bytes:1900000}),
      event('aaaaaaaa','stage_finished','browser',{client_sequence:10,input_width:640,input_height:360,output_bytes:999}),
      event('bbbbbbbb','stage_finished','browser',{client_sequence:1,input_width:1920,input_height:1080})
    ]}}));
    await page.goto('/ops');await expect(page.locator('#status')).toContainText('Updated');
    await page.getByRole('tab',{name:'Session explorer',exact:true}).click();
    const rows=page.locator('#session-files tbody tr');await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText('1280 × 720');await expect(rows.nth(0)).toContainText('1.8 MiB');
    await expect(rows.nth(0)).not.toContainText('640 × 360');
    await expect(rows.nth(1)).toContainText('1920 × 1080');
  } finally {await context.close();}
});
