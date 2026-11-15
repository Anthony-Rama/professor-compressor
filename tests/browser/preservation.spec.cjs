const { test, expect } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const { mkdtempSync, rmSync, readFileSync, writeFileSync } = require('node:fs');
const { createHash } = require('node:crypto');
const { tmpdir } = require('node:os');
const { join } = require('node:path');

let fixtures, original;
test.beforeAll(() => {
  fixtures = mkdtempSync(join(tmpdir(), 'compressor-preservation-'));
  execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
    'testsrc2=size=320x240:rate=25', '-t', '0.4', '-c:v', 'libx264',
    '-preset', 'ultrafast', join(fixtures, 'clip.mp4')]);
  original = readFileSync(join(fixtures, 'clip.mp4'));
  for (const name of ['other.mp4', 'unreadable.mp4']) writeFileSync(join(fixtures, name), original);
});
test.afterAll(() => rmSync(fixtures, {recursive:true, force:true}));

async function open(page, request) {
  const result = await (await request.post('/test/session')).json();
  await page.goto(result.path);
  await expect(page.locator('#submit')).toBeVisible();
  return result;
}
async function ready(page) {
  await expect(page.locator('#submit')).toBeEnabled();
  await expect(page.locator('.file-status')).toHaveText(['Ready']);
}
async function storedFiles(page) {
  return page.evaluate(async () => {
    const root = await (await navigator.storage.getDirectory()).getDirectoryHandle('professor-compressor-inputs-v1');
    let files = 0;
    for await (const handle of root.values()) {
      if (handle.kind === 'directory') for await (const file of handle.values()) {
        if (file.kind === 'file') files++;
      }
    }
    return files;
  });
}

test('a prepared local copy survives the original disk file being replaced', async ({page, request}) => {
  const source = join(fixtures, 'changing.mp4');
  writeFileSync(source, original);
  await page.addInitScript(() => {
    const send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function(body) {
      if (body instanceof FormData && body.has('clips')) {
        window.sentPreservedDigest = body.get('clips').arrayBuffer().then(bytes => crypto.subtle.digest('SHA-256', bytes))
          .then(hash => Array.from(new Uint8Array(hash), n => n.toString(16).padStart(2, '0')).join(''));
      }
      return send.call(this, body);
    };
  });
  await open(page, request);
  await page.locator('#clips').setInputFiles(source);
  await ready(page);
  writeFileSync(source, Buffer.from('GG replaced its export with other bytes'));
  await page.locator('#submit').click();
  await expect(page.locator('#phase-title')).toHaveText('Delivered to Discord');
  const digest = await page.evaluate(() => window.sentPreservedDigest);
  expect(digest).toBe(createHash('sha256').update(original).digest('hex'));
  await expect.poll(() => storedFiles(page)).toBe(0);
});

test('unreadable sources explain the problem above the picker and can be reselected', async ({page, request}) => {
  await page.setViewportSize({width:375, height:812});
  await page.addInitScript(() => {
    const slice = File.prototype.slice;
    window.rejectLocalSource = true;
    File.prototype.slice = function(...args) {
      if (window.rejectLocalSource && this.name === 'unreadable.mp4') return {
        arrayBuffer: async () => { throw new DOMException('PRIVATE_SOURCE_PATH', 'NotReadableError'); }
      };
      return slice.apply(this, args);
    };
  });
  await open(page, request);
  await page.locator('#caption').fill('Keep my caption');
  await page.locator('#clips').setInputFiles([join(fixtures, 'clip.mp4'), join(fixtures, 'unreadable.mp4')]);
  await expect(page.locator('.file-status')).toHaveText(['Ready', 'Reselect file']);
  await expect(page.locator('#submit')).toBeDisabled();
  await expect(page.locator('#message')).toContainText('could not be read');
  await expect(page.locator('#message')).not.toContainText('PRIVATE_SOURCE_PATH');
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  expect(await page.evaluate(() => Boolean(document.getElementById('message')
    .compareDocumentPosition(document.getElementById('picker-row')) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
  await page.evaluate(() => { window.rejectLocalSource = false; });
  const picker = page.waitForEvent('filechooser');
  await page.getByRole('button', {name:'Reselect unreadable.mp4', exact:true}).click();
  await (await picker).setFiles(join(fixtures, 'unreadable.mp4'));
  await expect(page.locator('#submit')).toBeEnabled();
  await expect(page.locator('.file-card')).toHaveCount(2);
  await expect(page.locator('#caption')).toHaveValue('Keep my caption');
  await page.locator('#submit').click();
  await expect(page.locator('#phase-title')).toHaveText('Delivered to Discord');
});

test('local copies read in bounded chunks and hung reads fail instead of waiting forever', async ({page, request}) => {
  const large = join(fixtures, 'chunked.mp4');
  writeFileSync(large, Buffer.concat([original, Buffer.alloc(3 * 1024**2)]));
  await page.addInitScript(() => {
    const read = Blob.prototype.arrayBuffer;
    window.largestPreparationRead = 0;
    Blob.prototype.arrayBuffer = function(...args) {
      window.largestPreparationRead = Math.max(window.largestPreparationRead, this.size);
      return read.apply(this, args);
    };
  });
  await open(page, request);
  await page.locator('#clips').setInputFiles(large);
  await ready(page);
  expect(await page.evaluate(() => window.largestPreparationRead)).toBeLessThanOrEqual(1024**2);
  await page.locator('#clear-selection').click();
  await page.clock.install();
  await page.evaluate(() => {
    const slice = File.prototype.slice;
    File.prototype.slice = function(...args) {
      const blob = slice.apply(this, args);
      blob.arrayBuffer = () => { window.hungReadStarted = true; return new Promise(() => {}); };
      return blob;
    };
  });
  await page.locator('#clips').setInputFiles(join(fixtures, 'clip.mp4'));
  await expect.poll(() => page.evaluate(() => window.hungReadStarted)).toBe(true);
  await page.clock.fastForward(35000);
  await expect(page.locator('#message')).toContainText('Preparing this clip took too long');
  await expect(page.locator('#submit')).toBeDisabled();
});

test('storage quota failures are explicit and clearing allows retry without RAM fallback', async ({page, request}) => {
  await page.addInitScript(() => {
    const create = FileSystemFileHandle.prototype.createWritable;
    window.failLocalStorage = true;
    FileSystemFileHandle.prototype.createWritable = function(...args) {
      if (window.failLocalStorage) throw new DOMException('PRIVATE_DISK_ERROR', 'QuotaExceededError');
      return create.apply(this, args);
    };
  });
  await open(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures, 'clip.mp4'));
  await expect(page.locator('#message')).toContainText('Not enough browser storage');
  await expect(page.locator('#message')).not.toContainText('PRIVATE_DISK_ERROR');
  await expect(page.locator('#submit')).toBeDisabled();
  await expect.poll(() => storedFiles(page)).toBe(0);
  await page.locator('#clear-selection').click();
  await page.evaluate(() => { window.failLocalStorage = false; });
  await page.locator('#clips').setInputFiles(join(fixtures, 'clip.mp4'));
  await ready(page);
});

test('removing a preparing file cancels it without late updates to a newer selection', async ({page, request}) => {
  await page.addInitScript(() => {
    const slice = File.prototype.slice;
    File.prototype.slice = function(...args) {
      const blob = slice.apply(this, args);
      if (this.name === 'clip.mp4') {
        const read = blob.arrayBuffer.bind(blob);
        blob.arrayBuffer = async () => {
          await new Promise(resolve => { window.releaseLocalRead = resolve; });
          return read();
        };
      }
      return blob;
    };
  });
  await open(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures, 'clip.mp4'));
  await expect.poll(() => page.evaluate(() => typeof window.releaseLocalRead)).toBe('function');
  await expect(page.locator('#submit')).toHaveText('Preparing videos…');
  await expect(page.locator('#submit')).toBeDisabled();
  await page.locator('#clear-selection').click();
  await page.locator('#clips').setInputFiles(join(fixtures, 'other.mp4'));
  await ready(page);
  await page.evaluate(() => window.releaseLocalRead());
  await expect.poll(() => storedFiles(page)).toBe(1);
  await expect(page.locator('.file-name')).toHaveText('other.mp4');
  await expect(page.locator('#message')).toBeHidden();
  await page.locator('#clear-selection').click();
  await expect.poll(() => storedFiles(page)).toBe(0);
});

test('preparation enforces file and batch limits without allocating oversized buffers', async ({page, request}) => {
  await open(page, request);
  const result = await page.evaluate(async () => {
    const {InputPreserver, INPUT_FILE_LIMIT, INPUT_BATCH_LIMIT} = await import('/brand/input-preservation.js');
    const store = new InputPreserver();
    let perFile, perBatch;
    try { store.create({size:INPUT_FILE_LIMIT + 1}); } catch (error) { perFile = error.diagnosticCode; }
    // Reserve space with blocked storage initialization; never create a huge Blob.
    const originalReady = store.ready;
    let release;
    store.ready = new Promise(resolve => { release = resolve; });
    const a = store.create({size:INPUT_FILE_LIMIT});
    const b = store.create({size:INPUT_FILE_LIMIT});
    const c = store.create({size:INPUT_FILE_LIMIT});
    const d = store.create({size:INPUT_FILE_LIMIT});
    try { store.create({size:1}); } catch (error) { perBatch = error.diagnosticCode; }
    const reserved = store.reservedBytes;
    const removed = [a, b, c, d].map(task => task.dispose());
    release(await originalReady);
    await Promise.all(removed);
    store.ready = originalReady;
    const afterRemoval = store.reservedBytes;
    await store.close();
    return {perFile, perBatch, reserved, limit:INPUT_BATCH_LIMIT, afterRemoval};
  });
  expect(result).toEqual({perFile:'INPUT_TOO_LARGE', perBatch:'INPUT_TOO_LARGE', reserved:4*1024**3, limit:4*1024**3, afterRemoval:0});
});

test('low storage rejects additions without disturbing ready clips or captions', async ({page, request}) => {
  await page.addInitScript(() => {
    navigator.storage.estimate = async () => ({quota: window.lowStorage ? 128*1024**2 : 16*1024**3, usage:0});
  });
  await open(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures, 'clip.mp4'));
  await ready(page);
  await page.locator('textarea').fill('Keep my caption');
  await page.evaluate(() => { window.lowStorage = true; });
  await page.locator('#clips').setInputFiles(join(fixtures, 'other.mp4'));
  await expect(page.locator('#message')).toContainText('Existing clips were kept');
  await expect(page.locator('.file-name')).toHaveText('clip.mp4');
  await expect(page.locator('textarea')).toHaveValue('Keep my caption');
  await expect(page.locator('#submit')).toBeEnabled();
  await expect.poll(() => storedFiles(page)).toBe(1);
});

test('storage estimates handle reservations, missing data, rejection and timeouts', async ({page, request}) => {
  await open(page, request);
  const result = await page.evaluate(async () => {
    const {InputPreserver} = await import('/brand/input-preservation.js');
    const store = new InputPreserver();
    await store.ready;
    store.entries.set('pending', {size:1024**3});
    navigator.storage.estimate = async () => ({quota:2*1024**3, usage:0});
    let code;
    try { await store.checkAvailable(1024**3); } catch (error) { code = error.diagnosticCode; }
    for (const estimate of [async () => ({}), async () => ({quota:NaN, usage:0}), async () => {throw Error('private');}]) {
      navigator.storage.estimate = estimate;
      await store.checkAvailable(1);
    }
    store.entries.clear();
    await store.close();
    return code;
  });
  expect(result).toBe('INPUT_STORAGE_FULL');
  await page.clock.install();
  const pending = page.evaluate(async () => {
    const {InputPreserver} = await import('/brand/input-preservation.js');
    const store = new InputPreserver();
    navigator.storage.estimate = () => new Promise(() => {});
    window.estimateStarted = true;
    await store.checkAvailable(1);
    await store.close();
  });
  await expect.poll(() => page.evaluate(() => window.estimateStarted)).toBe(true);
  await page.clock.fastForward(3100);
  await pending;
});

test('clear during a pending storage estimate cannot restore removed files', async ({page, request}) => {
  await open(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures, 'clip.mp4'));
  await ready(page);
  await page.evaluate(() => {
    navigator.storage.estimate = () => new Promise(resolve => {window.finishEstimate = resolve;});
  });
  await page.locator('#clips').setInputFiles(join(fixtures, 'other.mp4'));
  await expect.poll(() => page.evaluate(() => !!window.finishEstimate)).toBe(true);
  await page.locator('#clear-selection').click();
  await page.evaluate(() => window.finishEstimate({quota:16*1024**3,usage:0}));
  await expect(page.locator('.file-card')).toHaveCount(0);
  await expect(page.locator('#submit')).toBeDisabled();
  await expect.poll(() => storedFiles(page)).toBe(0);
});

test('batch and per-file preflight reject additions without creating failed cards', async ({page, request}) => {
  await open(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures, 'clip.mp4'));
  await ready(page);
  await page.evaluate(() => {
    const size = Object.getOwnPropertyDescriptor(Blob.prototype, 'size').get;
    Object.defineProperty(File.prototype, 'size', {configurable:true, get() {
      if (this.name.startsWith('large-')) return 1024**3;
      if (this.name === 'huge.mp4') return 1024**3+1;
      return size.call(this);
    }});
    const transfer = new DataTransfer();
    for (let index = 0; index < 5; index++) transfer.items.add(new File(['x'], 'large-' + index + '.mp4'));
    document.dispatchEvent(new DragEvent('drop', {dataTransfer:transfer, bubbles:true, cancelable:true}));
  });
  await expect(page.locator('#message')).toContainText('above the 4 GiB batch limit');
  await expect(page.locator('.file-status')).toHaveText(['Ready']);
  await page.evaluate(() => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(['x'], 'huge.mp4'));
    document.dispatchEvent(new DragEvent('drop', {dataTransfer:transfer, bubbles:true, cancelable:true}));
  });
  await expect(page.locator('#message')).toContainText('Each video must be 1 GiB or smaller');
  await expect(page.locator('.file-name')).toHaveText('clip.mp4');
  await expect(page.locator('#submit')).toBeEnabled();
  await expect.poll(() => storedFiles(page)).toBe(1);
});

test('rapid selections are admitted sequentially without losing an earlier addition', async ({page, request}) => {
  await open(page, request);
  await page.evaluate(() => {
    for (const name of ['first.mp4','second.mp4']) {
      const transfer = new DataTransfer();
      transfer.items.add(new File(['clip'], name));
      document.dispatchEvent(new DragEvent('drop', {dataTransfer:transfer, bubbles:true, cancelable:true}));
    }
  });
  await expect(page.locator('.file-name')).toHaveText(['first.mp4','second.mp4']);
  await expect(page.locator('.file-status')).toHaveText(['Ready','Ready']);
  await expect(page.locator('#selection')).toContainText('/ 4 GiB');
  await expect(page.locator('#submit')).toBeEnabled();
});

test('another tab cannot delete active copies, but abandoned anonymous copies are cleaned', async ({page, context, request}) => {
  await open(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures, 'clip.mp4'));
  await ready(page);
  await page.evaluate(async () => {
    const root = await (await navigator.storage.getDirectory()).getDirectoryHandle('professor-compressor-inputs-v1');
    const abandoned = await root.getDirectoryHandle('session-00000000-0000-4000-8000-000000000000', {create:true});
    const writer = await (await abandoned.getFileHandle('old', {create:true})).createWritable();
    await writer.write('abandoned'); await writer.close();
  });
  const second = await context.newPage();
  await open(second, request);
  await expect.poll(() => storedFiles(page)).toBe(1);
  await page.locator('#submit').click();
  await expect(page.locator('#phase-title')).toHaveText('Delivered to Discord');
  await second.close();
});

test('unavailable storage or preservation module does not silently use original references', async ({page, request}) => {
  await page.route('**/brand/input-preservation.js', route => route.abort());
  await open(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures, 'clip.mp4'));
  await expect(page.locator('#message')).toContainText('Local file preparation could not start');
  await expect(page.locator('#submit')).toBeDisabled();
  await expect(page.locator('.file-card')).toHaveCount(0);
});

test('denied browser storage reports an actionable preparation error', async ({page, request}) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator.storage, 'getDirectory', {value:async () => {
      throw new DOMException('PRIVATE_STORAGE_DETAIL', 'SecurityError');
    }});
  });
  await open(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures, 'clip.mp4'));
  await expect(page.locator('#message')).toContainText('browser could not save a temporary local copy');
  await expect(page.locator('#message')).not.toContainText('PRIVATE_STORAGE_DETAIL');
  await expect(page.locator('#submit')).toBeDisabled();
  await page.locator('#clear-selection').click();
  await expect(page.locator('.file-card')).toHaveCount(0);
});

test('idle expiration removes local copies and disables further preparation', async ({page, request}) => {
  await open(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures, 'clip.mp4'));
  await ready(page);
  await page.clock.install();
  await page.clock.fastForward(4 * 60 * 60 * 1000);
  await expect(page.locator('#clips')).toBeDisabled();
  await expect(page.locator('#submit')).toBeDisabled();
  await expect.poll(() => storedFiles(page)).toBe(0);
});

test('a later saved-copy read failure is classified as file access, not bad media', async ({page, request}) => {
  await open(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures, 'clip.mp4'));
  await ready(page);
  await page.evaluate(() => {
    window.originalFileSlice = File.prototype.slice;
    File.prototype.slice = () => ({arrayBuffer:async () => {
      throw new DOMException('PRIVATE_SOURCE_LOCATION', 'NotReadableError');
    }});
  });
  await page.locator('#submit').click();
  await expect(page.locator('#message')).toContainText('This clip could not be read');
  await expect(page.locator('#message')).not.toContainText('PRIVATE_SOURCE_LOCATION');
  expect(await page.evaluate(() => Boolean(document.querySelector('.controls')
    .compareDocumentPosition(document.getElementById('message')) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
  await expect(page.getByRole('button', {name:'Reselect clip.mp4', exact:true})).toBeVisible();
  await expect(page.locator('#submit')).toBeDisabled();
  await page.evaluate(() => { File.prototype.slice = window.originalFileSlice; });
  const picker = page.waitForEvent('filechooser');
  await page.getByRole('button', {name:'Reselect clip.mp4', exact:true}).click();
  await (await picker).setFiles(join(fixtures, 'clip.mp4'));
  await ready(page);
  await page.locator('#submit').click();
  await expect(page.locator('#phase-title')).toHaveText('Delivered to Discord');
});

test('a stalled preservation module times out visibly and late loading cannot create a store', async ({page, request}) => {
  let held;
  await page.clock.install();
  await page.route('**/brand/input-preservation.js', route => { held = route; });
  await open(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures, 'clip.mp4'));
  await expect(page.locator('#submit')).toHaveText('Checking storage…');
  await page.clock.fastForward(31000);
  await expect(page.locator('#message')).toContainText('could not start in time');
  await expect(page.locator('#message')).toContainText('Reload');
  await expect(page.locator('#submit')).toBeDisabled();
  await held.continue();
  await expect(page.locator('.file-card')).toHaveCount(0);
  expect(await page.evaluate(async () => {
    const root = await navigator.storage.getDirectory();
    try { await root.getDirectoryHandle('professor-compressor-inputs-v1'); return true; }
    catch (error) { if(error.name === 'NotFoundError') return false; throw error; }
  })).toBe(false);
});

test('completed copies are not counted twice, but pending copies reserve space', async ({page, request}) => {
  await open(page, request);
  const result = await page.evaluate(async () => {
    const {InputPreserver} = await import('/brand/input-preservation.js');
    const store = new InputPreserver(); await store.ready;
    const entry = {size:2*1024**3, committed:true};
    store.entries.set('simulated', entry);
    navigator.storage.estimate = async () => ({quota:4*1024**3, usage:2*1024**3});
    await store.checkAvailable(100*1024**2);
    entry.committed = false;
    let blocked;
    try { await store.checkAvailable(100*1024**2); } catch (error) { blocked=error.diagnosticCode; }
    store.entries.clear(); await store.close();
    return blocked;
  });
  expect(result).toBe('INPUT_STORAGE_FULL');
});

test('an already-removed abandoned directory does not block a new store', async ({page, request}) => {
  await open(page, request);
  const result = await page.evaluate(async () => {
    const {InputPreserver} = await import('/brand/input-preservation.js');
    const root = await (await navigator.storage.getDirectory()).getDirectoryHandle('professor-compressor-inputs-v1',{create:true});
    const name='session-00000000-0000-4000-8000-000000000000';
    await root.getDirectoryHandle(name,{create:true});
    const remove=FileSystemDirectoryHandle.prototype.removeEntry;
    FileSystemDirectoryHandle.prototype.removeEntry=async function(entry,options) {
      if(entry===name) {await remove.call(this,entry,options);throw new DOMException('Already removed','NotFoundError');}
      return remove.call(this,entry,options);
    };
    const store=new InputPreserver();
    try {await store.ready; const task=store.create(new File(['clip'],'test.mp4'));return (await task.promise).size;}
    finally {FileSystemDirectoryHandle.prototype.removeEntry=remove;await store.close();}
  });
  expect(result).toBe(4);
});

test('a late writable after timeout is aborted and its reservation can be reclaimed', async ({page, request}) => {
  await open(page, request); await page.clock.install();
  await page.evaluate(async () => {
    const {InputPreserver}=await import('/brand/input-preservation.js');
    const store=new InputPreserver(); await store.ready; window.lateStore=store;
    const create=FileSystemFileHandle.prototype.createWritable;
    FileSystemFileHandle.prototype.createWritable=function(...args) {
      return new Promise(resolve => {window.releaseWriter=async()=>resolve(await create.apply(this,args));});
    };
    const task=store.create(new File(['clip'],'test.mp4'));
    window.lateResult=task.promise.catch(error=>error.diagnosticCode);
    window.restoreWritable=()=>{FileSystemFileHandle.prototype.createWritable=create;};
  });
  await expect.poll(()=>page.evaluate(()=>!!window.releaseWriter)).toBe(true);
  await page.clock.fastForward(31000);
  expect(await page.evaluate(()=>window.lateResult)).toBe('INPUT_PREPARATION_TIMEOUT');
  await page.evaluate(async()=>{await window.releaseWriter();window.restoreWritable();});
  await expect.poll(()=>page.evaluate(()=>window.lateStore.reservedBytes)).toBe(0);
  expect(await page.evaluate(async()=>{
    const task=window.lateStore.create(new File(['next'],'next.mp4'));
    const saved=await task.promise;await window.lateStore.close();return saved.size;
  })).toBe(4);
});

test('transient deletion failure retains accounting and a later dispose retries cleanup', async ({page, request}) => {
  await open(page,request);
  const result=await page.evaluate(async()=>{
    const {InputPreserver}=await import('/brand/input-preservation.js');
    const store=new InputPreserver();await store.ready;
    const task=store.create(new File(['clip'],'test.mp4'));await task.promise;
    const remove=FileSystemDirectoryHandle.prototype.removeEntry;let fail=true;
    FileSystemDirectoryHandle.prototype.removeEntry=function(...args){
      if(this.name===store.sessionName&&fail){fail=false;return Promise.reject(new DOMException('Busy','NoModificationAllowedError'));}
      return remove.apply(this,args);
    };
    try{await task.dispose();const held=store.reservedBytes;await task.dispose();return {held,after:store.reservedBytes};}
    finally{FileSystemDirectoryHandle.prototype.removeEntry=remove;await store.close();}
  });
  expect(result).toEqual({held:4,after:0});
});

test('preparation failures are searchable but do not mark compression started or failed', async ({page,request})=>{
  const session=await open(page,request);
  await page.locator('#caption').fill('PRIVATE_CAPTION');
  await page.evaluate(()=>{File.prototype.slice=()=>({arrayBuffer:async()=>{throw new DOMException('PRIVATE_PATH','NotReadableError');}});});
  await page.locator('#clips').setInputFiles({name:'PRIVATE_FILENAME.mp4',mimeType:'video/mp4',buffer:original});
  await expect(page.locator('#message')).toContainText('could not be read');
  const headers={Authorization:'Basic '+Buffer.from('admin:browser-test-only-operator-password').toString('base64')};
  const data=async()=> (await request.get('/ops/data?tests=1&reference='+session.reference,{headers})).json();
  await expect.poll(async()=> (await data()).events.some(e=>e.data.stage==='preparation'&&e.data.error_code==='FILE_READ_FAILED')).toBe(true);
  const result=await data();
  expect(result.totals.started).toBe(0);
  expect(result.sessions[0].failures).toBe(0);
  expect(result.sessions[0].status).not.toBe('client_failed');
  expect(JSON.stringify(result)).not.toContain('PRIVATE_');
  await page.evaluate(()=>{const fetch=window.fetch;window.fetch=(url,options)=>{if(String(url).endsWith('/events'))throw Error('transport');return fetch(url,options);};});
  await page.locator('#clear-selection').click();
  await expect(page.locator('.file-card')).toHaveCount(0);
});

for (const missing of ['storage', 'locks']) {
  test('missing '+missing+' support fails explicitly without uploading', async ({page,request})=>{
    await page.addInitScript(kind=>{
      if(kind==='storage') Object.defineProperty(navigator.storage,'getDirectory',{value:undefined});
      else Object.defineProperty(navigator,'locks',{value:undefined});
    },missing);
    const uploads=[];page.on('request',r=>{if(r.method()==='POST'&&(r.headers()['content-type']||'').startsWith('multipart/form-data'))uploads.push(r.url());});
    await open(page,request);
    await page.locator('#clips').setInputFiles(join(fixtures,'clip.mp4'));
    await expect(page.locator('#message')).toContainText('updated Chrome or Edge');
    await expect(page.locator('#submit')).toBeDisabled();
    expect(uploads).toHaveLength(0);
  });
}

test('hung storage initialization times out and late resolution leaves no owned directory', async ({page,request})=>{
  await page.clock.install();
  await page.addInitScript(()=>{
    const get=navigator.storage.getDirectory.bind(navigator.storage);
    navigator.storage.getDirectory=()=>new Promise(resolve=>{
      window.releaseDirectory=async()=>resolve(await get());
    });
    window.originalGetDirectory=get;
  });
  await open(page,request);
  await page.locator('#clips').setInputFiles(join(fixtures,'clip.mp4'));
  await expect(page.locator('#submit')).toHaveText('Preparing videos…');
  await page.clock.fastForward(31000);
  await expect(page.locator('#message')).toContainText('took too long');
  await page.evaluate(()=>window.releaseDirectory());
  await expect.poll(()=>page.evaluate(async()=>{
    const root=await (await window.originalGetDirectory()).getDirectoryHandle('professor-compressor-inputs-v1');
    let count=0;for await(const _ of root.values())count++;return count;
  })).toBe(0);
  await expect(page.locator('#submit')).toBeDisabled();
});

test('late file-handle creation is cleaned after its timeout', async ({page,request})=>{
  await open(page,request);await page.clock.install();
  await page.evaluate(async()=>{
    const {InputPreserver}=await import('/brand/input-preservation.js');
    const store=new InputPreserver();await store.ready;window.handleStore=store;
    const get=FileSystemDirectoryHandle.prototype.getFileHandle;
    FileSystemDirectoryHandle.prototype.getFileHandle=function(...args){
      if(this.name!==store.sessionName)return get.apply(this,args);
      return new Promise(resolve=>{window.releaseHandle=async()=>resolve(await get.apply(this,args));});
    };
    window.handleResult=store.create(new File(['clip'],'test.mp4')).promise.catch(e=>e.diagnosticCode);
    window.restoreHandle=()=>{FileSystemDirectoryHandle.prototype.getFileHandle=get;};
  });
  await expect.poll(()=>page.evaluate(()=>!!window.releaseHandle)).toBe(true);
  await page.clock.fastForward(31000);
  expect(await page.evaluate(()=>window.handleResult)).toBe('INPUT_PREPARATION_TIMEOUT');
  await page.evaluate(async()=>{await window.releaseHandle();window.restoreHandle();});
  await expect.poll(()=>page.evaluate(async()=>{
    let count=0;for await(const _ of window.handleStore.directory.values())count++;return count;
  })).toBe(0);
  await page.evaluate(()=>window.handleStore.close());
});

test('reselecting an oversized replacement keeps the previous selection and caption', async ({page,request})=>{
  await page.addInitScript(()=>{
    const slice=File.prototype.slice,size=Object.getOwnPropertyDescriptor(Blob.prototype,'size').get;
    File.prototype.slice=function(...args){if(this.name==='unreadable.mp4')return{arrayBuffer:async()=>{throw new DOMException('private','NotReadableError');}};return slice.apply(this,args);};
    Object.defineProperty(File.prototype,'size',{get(){return this.name==='huge.mp4'?1024**3+1:size.call(this);}});
  });
  await open(page,request);
  await page.locator('#caption').fill('Keep caption');
  await page.locator('#clips').setInputFiles([join(fixtures,'clip.mp4'),join(fixtures,'unreadable.mp4')]);
  await expect(page.locator('.file-status')).toHaveText(['Ready','Reselect file']);
  const picker=page.waitForEvent('filechooser');
  await page.getByRole('button',{name:'Reselect unreadable.mp4',exact:true}).click();
  await(await picker).setFiles({name:'huge.mp4',mimeType:'video/mp4',buffer:original});
  await expect(page.locator('#message')).toContainText('replacement exceeds');
  await expect(page.locator('.file-name')).toHaveText(['clip.mp4','unreadable.mp4']);
  await expect(page.locator('#caption')).toHaveValue('Keep caption');
});

test('preparation telemetry transport errors cannot block a readable file', async ({page,request})=>{
  await page.addInitScript(()=>{
    const fetch=window.fetch;window.fetch=(url,options)=>{
      if(String(url).endsWith('/events'))throw Error('optional telemetry unavailable');
      return fetch(url,options);
    };
  });
  await open(page,request);
  await page.locator('#clips').setInputFiles(join(fixtures,'clip.mp4'));
  await ready(page);
  await page.locator('#submit').click();
  await expect(page.locator('#phase-title')).toHaveText('Delivered to Discord');
});

test('ten-file preparation batches diagnostics into bounded content-free requests', async ({page,request})=>{
  const bodies=[];
  page.on('request',r=>{
    if(r.url().endsWith('/events')) {
      const body=JSON.parse(r.postData());
      if(body.events.every(e=>e.data.stage==='preparation'))bodies.push(body);
    }
  });
  await open(page,request);
  await page.locator('#clips').setInputFiles(Array.from({length:10},(_,i)=>({name:'PRIVATE_CLIP_'+i+'.mp4',mimeType:'video/mp4',buffer:original})));
  await expect(page.locator('#submit')).toBeEnabled();
  await expect.poll(()=>bodies.reduce((count,b)=>count+b.events.length,0)).toBe(22);
  expect(bodies).toHaveLength(2);
  for(const body of bodies) {
    expect(body.events.length).toBeLessThanOrEqual(20);
    expect(Buffer.byteLength(JSON.stringify(body))).toBeLessThan(16384);
    expect(JSON.stringify(body)).not.toContain('PRIVATE_CLIP');
  }
});
