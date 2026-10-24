const { test, expect } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const { mkdtempSync, rmSync, writeFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');

const operator = {username: 'admin', password: 'browser-test-only-operator-password'};
const auth = {Authorization: 'Basic ' + Buffer.from(operator.username + ':' + operator.password).toString('base64')};
let fixtures;
test.beforeAll(() => {
  fixtures = mkdtempSync(join(tmpdir(), 'compressor-observability-'));
  execFileSync('ffmpeg', ['-hide_banner','-loglevel','error','-f','lavfi','-i',
    'testsrc2=size=320x240:rate=25','-t','0.4','-c:v','libx264','-crf','35',join(fixtures,'small.mp4')]);
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
  await page.locator('#clips').setInputFiles(join(fixtures,'small.mp4'));
  await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');
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
