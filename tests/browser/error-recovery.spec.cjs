const {test,expect}=require('@playwright/test');
const {execFileSync}=require('node:child_process');
const {mkdtempSync,readFileSync,writeFileSync,rmSync}=require('node:fs');
const {tmpdir}=require('node:os');
const {join}=require('node:path');
let fixtures;
test.beforeAll(()=>{
  fixtures=mkdtempSync(join(tmpdir(),'compressor-recovery-'));
  for(const [name,codec] of [['small.mp4','libx264'],['av1.mp4','libaom-av1'],['av1.webm','libaom-av1'],['small.mov','libx264']]) {
    execFileSync('ffmpeg',['-v','error','-f','lavfi','-i','testsrc2=size=320x240:rate=30','-t','0.4','-c:v',codec,
      ...(codec==='libaom-av1'?['-cpu-used','8']:[]),join(fixtures,name)]);
  }
  for(const name of ['small.mp4','av1.mp4']){
    const input=readFileSync(join(fixtures,name)),padding=Buffer.alloc(1200000-input.length);
    padding.writeUInt32BE(padding.length);padding.write('free',4);
    writeFileSync(join(fixtures,'large-'+name),Buffer.concat([input,padding]));
  }
});
test.afterAll(()=>rmSync(fixtures,{recursive:true,force:true}));

for (const asset of ['encoder-client.js', 'processing-errors.js', 'util-esm/index.js']) {
  test(`missing required asset ${asset} shows an actionable startup error`, async ({page, request}) => {
    await page.route('**/' + asset, route => route.abort());
    const session = await (await request.post('/test/session')).json();
    await page.goto(session.path);
    await expect(page.locator('#startup-error')).toBeVisible();
    await expect(page.locator('#startup-error')).toContainText('Nothing has been uploaded');
    await expect(page.locator('#clips')).toBeDisabled();
    await expect(page.locator('#submit')).toBeDisabled();
  });
}

for (const kind of ['syntax', 'evaluation', 'initialization']) {
  test(`${kind} startup failure displays recovery and keeps selection disabled`, async ({page, request}) => {
    await page.route('**/brand/encoder-client.js', async route => {
      if (kind === 'initialization') return route.continue();
      await route.fulfill({contentType: 'application/javascript', body: kind === 'syntax'
        ? 'export class EncoderClient { broken'
        : 'throw new Error("test module evaluation failed"); export class EncoderClient {}'});
    });
    if (kind === 'initialization') {
      await page.route('**/brand/processing-errors.js', route => route.fulfill({
        contentType: 'application/javascript',
        body: 'export function createProcessingFallback() { throw new Error("test initialization failed"); }'
      }));
    }
    const session = await (await request.post('/test/session')).json();
    await page.goto(session.path);
    await expect(page.locator('#startup-error')).toBeVisible();
    await expect(page.locator('#startup-error')).toBeFocused();
    await expect(page.locator('#clips')).toBeDisabled();
    await expect(page.locator('#submit')).toBeDisabled();
    expect(await page.locator('#startup-error').evaluate(node =>
      Boolean(node.compareDocumentPosition(document.querySelector('#picker-row')) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
  });
}

test('stalled startup times out and late imports cannot revive the picker', async ({page, request}) => {
  await page.addInitScript(() => {
    const original = window.setTimeout;
    window.setTimeout = (fn, delay, ...args) => original(fn, delay === 30000 ? 200 : delay, ...args);
  });
  let release, preparationRequests = 0;
  page.on('request', request => { if (request.url().endsWith('/brand/input-preservation.js')) preparationRequests++; });
  await page.route('**/brand/encoder-client.js', async route => {
    const response = await route.fetch();
    await new Promise(resolve => { release = () => route.fulfill({response}).then(resolve); });
  });
  const session = await (await request.post('/test/session')).json();
  await page.goto(session.path, {waitUntil: 'commit'});
  await expect(page.locator('#clips')).toBeDisabled();
  await expect(page.locator('#startup-error')).toBeVisible();
  await expect.poll(() => Boolean(release)).toBe(true);
  await release();
  await page.waitForLoadState('load');
  await expect(page.locator('#startup-error')).toBeVisible();
  await expect(page.locator('#clips')).toBeDisabled();
  await expect(page.locator('#submit')).toBeDisabled();
  expect(preparationRequests).toBe(0);
});

test('syntax error in the main inline module is caught by the independent guard', async ({page, request}) => {
  const session = await (await request.post('/test/session')).json();
  await page.route('**' + session.path, async route => {
    const response = await route.fetch();
    const body = (await response.text()).replace('async function initializeCompressor() {', 'async function initializeCompressor( {');
    await route.fulfill({response, body});
  });
  await page.goto(session.path);
  await expect(page.locator('#startup-error')).toBeVisible();
  await expect(page.locator('#clips')).toBeDisabled();
  await expect(page.locator('#submit')).toBeDisabled();
});

test('successful startup removes the watchdog and ignores later unrelated errors', async ({page, request}) => {
  await page.addInitScript(() => {
    const original = window.setTimeout;
    window.startupTimer = null;
    window.setTimeout = (fn, delay, ...args) => {
      if (delay === 30000 && !window.startupTimer) window.startupTimer = fn;
      return original(fn, delay, ...args);
    };
  });
  const session = await (await request.post('/test/session')).json();
  await page.goto(session.path);
  await expect(page.locator('#clips')).toBeEnabled();
  await page.evaluate(() => {
    window.startupTimer();
    window.dispatchEvent(new ErrorEvent('error', {message: 'unrelated later error'}));
  });
  await expect(page.locator('#startup-error')).toBeHidden();
  await expect(page.locator('#clips')).toBeEnabled();
});

test('required error handling classifies failures without optional diagnostics', async ({page, request}) => {
  await page.route('**/brand/diagnostics.js', route => route.abort());
  const session = await (await request.post('/test/session')).json();
  await page.goto(session.path);
  const codes = await page.evaluate(async () => {
    const {createProcessingFallback, errorCode} = await import('/brand/processing-errors.js');
    const processing = createProcessingFallback();
    const result = [];
    for (const value of ['RuntimeError: memory access out of bounds', 'out of memory', new DOMException('cancelled', 'AbortError')]) {
      try { await processing.step('encoding', 1, () => Promise.reject(value), 'ENCODE_FAILED'); }
      catch (error) { result.push([errorCode(error), error.diagnosticFileIndex]); }
    }
    return result;
  });
  expect(codes).toEqual([['WASM_TRAP', 1], ['BROWSER_MEMORY', 1], ['CANCELLED', 1]]);
  await page.locator('#clips').setInputFiles(join(fixtures, 'large-av1.mp4'));
  await page.locator('#submit').click();
  await expect(page.locator('#message')).toContainText('H.264 MP4 with AAC audio');
  await expect(page.getByRole('button', {name: 'Reselect large-av1.mp4', exact: true})).toBeVisible();
});

for (const outcome of ['unknown', 'rejected']) {
  test(`refreshed delivery status preserves ${outcome} recovery instructions`, async ({page, request}) => {
    const session = await (await request.post('/test/session?slow=1')).json();
    await page.goto(session.path);
    await page.locator('#clips').setInputFiles(join(fixtures, 'small.mp4'));
    await page.locator('#submit').click();
    await expect(page.locator('#page-heading')).toHaveText('Sending to Discord');
    const message = outcome === 'unknown'
      ? 'Delivery could not be confirmed. Check Discord before starting another session.'
      : 'The bot needs Attach Files. Ask an admin to restore access.';
    await page.route('**' + session.path + '/status', route => route.fulfill({status: 502,
      json: {code: 'discord_delivery_failed', delivery_outcome: outcome, error: message}}));
    await page.reload();
    await expect(page.locator('#delivery-status')).toContainText(message);
    await expect(page.locator('#clips')).toHaveCount(0);
    // Drain this test's deliberately slow fake delivery before the next test.
    await page.unroute('**' + session.path + '/status');
    await page.reload();
    await expect(page.locator('#delivery-status')).toContainText('Delivered to Discord', {timeout: 15000});
  });
}
async function open(page,request,metadata=null){
  await page.addInitScript(metadata=>{
    window.executions=[];
    const send=Worker.prototype.postMessage;
    Worker.prototype.postMessage=function(message,...rest){
      if(message?.type==='EXEC')window.executions.push(message.data.args);
      if(metadata&&message?.type==='READ_FILE'&&message.data.path.startsWith('probe-')){
        queueMicrotask(()=>this.onmessage?.({data:{id:message.id,type:message.type,data:JSON.stringify(metadata)}}));return;
      }
      return send.call(this,message,...rest);
    };
  },metadata);
  const session=await(await request.post('/test/session')).json();
  await page.goto(session.path);return session;
}
for (const diagnostics of [true, false]) {
  test(`activity warning survives backgrounding and clears on progress, diagnostics=${diagnostics}`, async ({page, request}) => {
    if (!diagnostics) await page.route('**/brand/diagnostics.js', route => route.abort());
    await page.addInitScript(() => {
      window.activityTimeOffset = 0;
      const now = performance.now.bind(performance);
      performance.now = () => now() + window.activityTimeOffset;
      const send = Worker.prototype.postMessage;
      Worker.prototype.postMessage = function(message, ...rest) {
        if (message?.type === 'EXEC') { window.heldEncoder = this; return; }
        return send.call(this, message, ...rest);
      };
    });
    await open(page, request);
    await expect(page.locator('#stay-open')).toContainText('keep the screen unlocked');
    await page.locator('#clips').setInputFiles(join(fixtures, 'large-small.mp4'));
    await page.locator('#submit').click();
    await expect.poll(() => page.evaluate(() => Boolean(window.heldEncoder))).toBe(true);
    await expect(page.locator('#stall-warning')).toBeHidden();
    await page.evaluate(() => {
      window.testHidden = true;
      Object.defineProperty(document, 'hidden', {configurable: true, get: () => window.testHidden});
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await expect(page.locator('#background-warning')).toBeVisible();
    await page.evaluate(() => {
      window.activityTimeOffset += 121000;
      window.testHidden = false;
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await expect(page.locator('#background-warning')).toContainText('was in the background');
    await expect(page.locator('#stall-warning')).toBeVisible();
    await expect(page.locator('#stall-warning')).toContainText('not a confirmed failure');
    await page.evaluate(() => window.heldEncoder.onmessage({data: {type: 'PROGRESS', data: {progress: 0, time: 0}}}));
    await expect(page.locator('#stall-warning')).toBeVisible();
    await page.evaluate(() => window.heldEncoder.onmessage({data: {type: 'PROGRESS', data: {progress: 0.2, time: 200000}}}));
    await expect(page.locator('#stall-warning')).toBeHidden();
    await expect(page.locator('#background-warning')).toBeHidden();
    await page.evaluate(() => { window.activityTimeOffset += 121000; });
    await expect(page.locator('#stall-warning')).toBeVisible();
    // Identical/invalid/regressing events cannot disguise inactivity.
    await page.evaluate(() => {
      for (const data of [{progress: 0.2, time: 200000}, {progress: 0.1, time: 100000}, {progress: NaN, time: NaN}])
        window.heldEncoder.onmessage({data: {type: 'PROGRESS', data}});
    });
    await expect(page.locator('#stall-warning')).toBeVisible();
    // Once the percentage reaches 100%, over-reported fractions cannot count
    // as further progress unless processed media time actually advances.
    await page.evaluate(() => window.heldEncoder.onmessage({data: {type: 'PROGRESS', data: {progress: 1, time: 200000}}}));
    await expect(page.locator('#stall-warning')).toBeHidden();
    await page.evaluate(() => { window.activityTimeOffset += 121000; });
    await expect(page.locator('#stall-warning')).toBeVisible();
    await page.evaluate(() => {
      for (const progress of [1.1, 2, Infinity, -1, NaN])
        window.heldEncoder.onmessage({data: {type: 'PROGRESS', data: {progress, time: 200000}}});
    });
    await expect(page.locator('#stall-warning')).toBeVisible();
    await page.evaluate(() => window.heldEncoder.onmessage({data: {type: 'PROGRESS', data: {progress: 1.1, time: 300000}}}));
    await expect(page.locator('#stall-warning')).toBeHidden();
    await page.locator('#cancel').click();
    await expect(page.locator('#stall-warning')).toBeHidden();
    await expect(page.locator('#background-warning')).toBeHidden();
    await expect(page.locator('#page-heading')).toHaveText('Compress videos for Discord');
  });
}
for(const file of ['large-av1.mp4','av1.webm'])test(`AV1 ${file} stops without encoding and preserves other clips and caption`,async({page,request})=>{
  await open(page,request);
  await page.locator('#caption').fill('Keep my caption');
  await page.locator('#clips').setInputFiles([join(fixtures,file),join(fixtures,'small.mp4')]);
  await page.locator('#submit').click();
  await expect(page.locator('#message')).toContainText('H.264 MP4 with AAC audio');
  await expect(page.locator('#message')).toContainText('other selected clips');
  expect(await page.evaluate(()=>window.executions.length)).toBe(0);
  await expect(page.locator('#caption')).toHaveValue('Keep my caption');
  await expect(page.locator('.file-card')).toHaveCount(2);
  await expect(page.getByRole('button',{name:'Reselect '+file,exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Remove '+file,exact:true}).click();
  await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');
});
test('fitting AV1 MP4 remains unchanged without decoder use',async({page,request})=>{
  await open(page,request);await page.locator('#clips').setInputFiles(join(fixtures,'av1.mp4'));
  await page.locator('#submit').click();await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');
  expect(await page.evaluate(()=>window.executions.length)).toBe(0);
});
for(const audio of [true,false])test(`impossible duration budget stops before conversion, audio=${audio}`,async({page,request})=>{
  const streams=[{codec_type:'video',codec_name:'h264',width:320,height:240,avg_frame_rate:'30/1'}];
  if(audio)streams.push({codec_type:'audio',codec_name:'pcm_s16le'});
  await open(page,request,{format:{duration:'5305'},streams});
  // MP4 must encode; MOV with incompatible audio must convert.
  await page.locator('#clips').setInputFiles(join(fixtures,audio?'small.mov':'large-small.mp4'));
  await page.locator('#submit').click();await expect(page.locator('#message')).toContainText('too long to fit');
  expect(await page.evaluate(()=>window.executions.length)).toBe(0);
});
test('long compatible MOV can still remux when output fits',async({page,request})=>{
  await open(page,request,{format:{duration:'5305'},streams:[{codec_type:'video',codec_name:'h264',width:320,height:240,avg_frame_rate:'30/1'}]});
  await page.locator('#clips').setInputFiles(join(fixtures,'small.mov'));await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');
  expect(await page.evaluate(()=>window.executions.every(args=>args.includes('copy')))).toBe(true);
});
test('upload connection reset retries prepared output without re-encoding',async({page,request})=>{
  const session=await open(page,request);let posts=0,encoded;
  await page.route('**'+session.path,async route=>{
    if(route.request().method()!=='POST')return route.continue();
    posts++;
    if(posts===1){encoded=await page.evaluate(()=>window.executions.length);return route.fulfill({status:503,json:{code:'upload_connection_reset',error:'Interrupted',retry_after:1}});}
    return route.continue();
  });
  await page.locator('#clips').setInputFiles(join(fixtures,'large-small.mp4'));await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord',{timeout:20000});
  expect(posts).toBe(2);expect(encoded).toBeGreaterThan(0);
  expect(await page.evaluate(()=>window.executions.length)).toBe(encoded);
});
test('exhausted reset retries preserve downloadable output and manual recovery',async({page,request})=>{
  const session=await open(page,request);let posts=0;
  await page.route('**'+session.path,route=>{
    if(route.request().method()!=='POST')return route.continue();posts++;
    return route.fulfill({status:503,json:{code:'upload_connection_reset',error:'Interrupted',retry_after:1}});
  });
  await page.locator('#clips').setInputFiles(join(fixtures,'small.mp4'));await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivery needs attention',{timeout:20000});
  expect(posts).toBe(3);
  await expect(page.locator('#message')).toContainText('do not need to be compressed again');
  await expect(page.locator('#downloads a')).toHaveCount(1);
  await expect(page.locator('#downloads a')).toHaveAttribute('href',/^blob:/);
  await page.unroute('**'+session.path);await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');
});
test('lost upload response checks receipt without duplicating delivery',async({page,request})=>{
  const session=await open(page,request);let posts=0;
  await page.route('**'+session.path,async route=>{
    if(route.request().method()!=='POST')return route.continue();posts++;
    await route.fetch();await route.abort('connectionreset');
  });
  await page.locator('#clips').setInputFiles(join(fixtures,'small.mp4'));await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord',{timeout:20000});
  expect(posts).toBe(1);
});
test('AV1 unavailable acceleration signal is classified without retaining raw logs',async({page,request})=>{
  await open(page,request);
  const signal=await page.evaluate(async()=>{
    const module=await import('/brand/diagnostics.js');
    return module.engineSignal("[av1 @ 0x123] Your platform doesn't suppport hardware accelerated AV1 decoding.");
  });expect(signal).toBe('unsupported_codec');
});

for(const audio of [true,false])test(`budget reserves audio only when present, audio=${audio}`,async({page,request})=>{
  const streams=[{codec_type:'video',codec_name:'h264',width:320,height:240,avg_frame_rate:'30/1'}];
  if(audio)streams.push({codec_type:'audio',codec_name:'aac'});
  await open(page,request,{format:{duration:'70'},streams});
  await page.locator('#clips').setInputFiles(join(fixtures,'large-small.mp4'));await page.locator('#submit').click();
  if(audio){await expect(page.locator('#message')).toContainText('too long to fit');expect(await page.evaluate(()=>window.executions.length)).toBe(0);}
  else{await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');expect(await page.evaluate(()=>window.executions.length)).toBe(1);}
});
test('repeated submit events cannot send the same batch twice',async({page,request})=>{
  const session=await open(page,request);let posts=0;
  await page.route('**'+session.path,async route=>{
    if(route.request().method()==='POST'){posts++;await new Promise(resolve=>setTimeout(resolve,100));}
    return route.continue();
  });
  await page.locator('#clips').setInputFiles(join(fixtures,'small.mp4'));
  await expect(page.locator('#submit')).toBeEnabled();
  await page.evaluate(()=>{document.querySelector('#upload').requestSubmit();document.querySelector('#upload').requestSubmit();});
  await expect(page.locator('#page-heading')).toHaveText('Delivered to Discord');expect(posts).toBe(1);
});
test('expired delivery retry retains local downloads without resending',async({page,request})=>{
  const session=await open(page,request);let posts=0;
  await page.route('**'+session.path,route=>{
    if(route.request().method()!=='POST')return route.continue();posts++;
    return route.fulfill({status:503,json:{code:'upload_connection_reset',error:'Interrupted',retry_after:1}});
  });
  await page.locator('#clips').setInputFiles(join(fixtures,'small.mp4'));await page.locator('#submit').click();
  await expect(page.locator('#page-heading')).toHaveText('Delivery needs attention',{timeout:20000});
  await page.route('**'+session.path+'/status',route=>route.fulfill({status:410,json:{code:'session_expired',error:'Expired'}}));
  await page.locator('#submit').click();await expect(page.locator('#message')).toContainText('session expired');
  await expect(page.locator('#downloads a')).toHaveCount(1);await expect(page.locator('#submit')).toBeHidden();expect(posts).toBe(3);
});
