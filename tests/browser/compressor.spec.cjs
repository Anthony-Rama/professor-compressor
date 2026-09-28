const { test, expect } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const { mkdtempSync, rmSync, readFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');

let fixtures;
test.beforeAll(() => {
  fixtures = mkdtempSync(join(tmpdir(), 'compressor-browser-'));
  const generate = (name, crf, duration) => execFileSync('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
    'testsrc2=size=1280x720:rate=30', '-t', String(duration),
    '-c:v', 'libx264', '-crf', String(crf), '-preset', 'ultrafast', join(fixtures, name),
  ]);
  generate('large.mp4', 0, 3);
  generate('small.mp4', 35, 0.5);
  generate('small.mov', 35, 0.5);
  execFileSync('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
    'testsrc2=size=360x640:rate=30', '-f', 'lavfi', '-i',
    'sine=frequency=440:sample_rate=48000', '-t', '2',
    '-vf', "select='not(mod(n,2))+not(mod(n,5))'", '-fps_mode', 'vfr',
    '-c:v', 'libx264', '-crf', '18', '-preset', 'ultrafast',
    '-c:a', 'pcm_s16le', join(fixtures, 'portrait-audio-vfr.mov'),
  ]);
  for (const [name, codec] of [
    ['sample.mkv', 'libx264'], ['sample.avi', 'mpeg4'],
    ['sample.webm', 'libvpx-vp9'], ['sample.wmv', 'wmv2'],
    ['sample.m2ts', 'mpeg2video'], ['sample.3gp', 'mpeg4'],
    ['sample.mpg', 'mpeg2video'], ['sample.ogv', 'libtheora'],
    ['sample.flv', 'flv'], ['sample.ts', 'mpeg2video'],
    ['hevc.mkv', 'libx265'], ['av1.mkv', 'libaom-av1'],
  ]) execFileSync('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
    'testsrc2=size=320x240:rate=25', '-t', '0.4', '-c:v', codec,
    ...(codec === 'libx265' ? ['-x265-params', 'log-level=error:pools=2'] : []),
    ...(codec === 'libaom-av1' ? ['-cpu-used', '8'] : []),
    join(fixtures, name),
  ]);
  execFileSync('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
    'sine=frequency=440', '-t', '0.4', '-c:a', 'libopus', join(fixtures, 'audio.webm'),
  ]);
});
test.afterAll(() => rmSync(fixtures, { recursive: true, force: true }));

async function openSession(page, request) {
  const response = await request.post('/test/session');
  await page.goto((await response.json()).path);
  await expect(page.locator('#submit')).toBeVisible();
}
async function deliver(page, files) {
  await page.locator('#clips').setInputFiles(files);
  await page.locator('#submit').click();
  await expect.poll(async () => {
    if (await page.locator('#message').isVisible()) return page.locator('#phase-title').textContent();
    return 'Processing';
  }, { timeout: 60000 }).toMatch(/Delivered to Discord|Action needed/);
  expect(await page.locator('#phase-title').textContent(), await page.locator('#message').textContent()).toBe('Delivered to Discord');
}

test('browser reports selection and start without filenames or media', async ({ page, request }) => {
  const reports = [];
  await page.route('**/upload/*/progress', async route => {
    reports.push(JSON.parse(route.request().postData()));
    await route.continue();
  });
  await openSession(page, request);
  await deliver(page, join(fixtures, 'small.mp4'));
  expect(reports).toContainEqual({event: 'selected', count: 1});
  expect(reports).toContainEqual({event: 'started'});
  expect(JSON.stringify(reports)).not.toContain('small.mp4');
});

test('page exit sends a best-effort leave report', async ({ page, request }) => {
  let resolveReport;
  const report = new Promise(resolve => { resolveReport = resolve; });
  await page.route('**/upload/*/progress', async route => {
    const body = JSON.parse(route.request().postData());
    if (body.event === 'page_left') resolveReport(body);
    await route.continue();
  });
  await openSession(page, request);
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  expect(await report).toEqual({event: 'page_left'});
});

test('oversized video loads WebAssembly and compresses under the real CSP', async ({ page, request }) => {
  await openSession(page, request);
  await expect(page.locator('#feedback-actions')).toBeHidden();
  expect(await page.evaluate(() => crossOriginIsolated)).toBe(true);
  await deliver(page, join(fixtures, 'large.mp4'));
  await expect(page.locator('.file-meta')).toContainText('smaller');
  await expect(page.locator('#feedback-actions')).toBeVisible();
  await expect(page.locator('#feedback-link')).toHaveAttribute('href', /feedback%20\(success\)/);
  await expect(page.locator('.support-link[href^="mailto:"]')).toHaveAttribute('href', /^mailto:professorcompressor\.support@gmail\.com\?/);
  await expect(page.getByRole('link', {name: 'Join support server'})).toHaveAttribute('href', 'https://discord.com/invite/32RWwNWyEH');
});

test('single-thread fallback still encodes when multithread core is unavailable', async ({ page, request }) => {
  await page.route('**/assets/**/core-mt-esm/**', route => route.abort());
  await openSession(page, request);
  await deliver(page, join(fixtures, 'large.mp4'));
});

test('reloading a failed encoder keeps completed file progress intact', async ({ page, request }) => {
  await page.addInitScript(() => {
    const send = Worker.prototype.postMessage;
    window.injectedWorkerFailure = false;
    Worker.prototype.postMessage = function(message, ...rest) {
      if (!window.injectedWorkerFailure && message?.type === 'WRITE_FILE') {
        window.injectedWorkerFailure = true;
        queueMicrotask(() => this.onmessage?.({data: {
          id: message.id, type: 'ERROR', data: new Error('Injected worker write failure'),
        }}));
        return;
      }
      return send.call(this, message, ...rest);
    };
  });
  await openSession(page, request);
  await page.locator('#clips').setInputFiles([
    join(fixtures, 'small.mp4'), join(fixtures, 'large.mp4'),
  ]);
  await page.evaluate(() => {
    const firstStatus = document.querySelector('.file-status');
    window.firstFileStatuses = [firstStatus.textContent];
    new MutationObserver(() => window.firstFileStatuses.push(firstStatus.textContent))
      .observe(firstStatus, {childList:true, subtree:true, characterData:true});
  });
  await page.locator('#submit').click();
  await expect(page.locator('#phase-title')).toHaveText('Delivered to Discord', {timeout:60000});
  const {injected, statuses} = await page.evaluate(() => ({
    injected: window.injectedWorkerFailure, statuses: window.firstFileStatuses,
  }));
  expect(injected).toBe(true);
  const completedAt = statuses.indexOf('Already fits');
  expect(completedAt).toBeGreaterThanOrEqual(0);
  expect(statuses.slice(completedAt + 1).some(status => status.startsWith('Loading'))).toBe(false);
});

test('ten fitting MP4s deliver together without loading an encoder', async ({ page, request }) => {
  await page.route('**/assets/**/core*-esm/**', () => { throw new Error('Unexpected encoding'); });
  await openSession(page, request);
  await deliver(page, Array(10).fill(join(fixtures, 'small.mp4')));
  await expect(page.locator('#phase-copy')).toContainText('10 file(s)');
});

test('small QuickTime MOV is converted to an actual MP4', async ({ page, request }) => {
  await openSession(page, request);
  await deliver(page, join(fixtures, 'small.mov'));
});

test('portrait variable-frame-rate video retains playable video and audio', async ({ page, request }) => {
  await page.addInitScript(() => {
    const originalSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function(body) {
      if (body instanceof FormData && body.has('clips')) {
        window.preparedOutput = body.get('clips');
      }
      return originalSend.call(this, body);
    };
  });
  await openSession(page, request);
  await deliver(page, join(fixtures, 'portrait-audio-vfr.mov'));
  const bytes = Buffer.from(await page.evaluate(async () =>
    Array.from(new Uint8Array(await window.preparedOutput.arrayBuffer()))));
  expect(bytes.length).toBeLessThanOrEqual(1_100_000);
  const metadata = JSON.parse(execFileSync('ffprobe', [
    '-v', 'error', '-count_frames', '-show_streams', '-of', 'json', '-i', 'pipe:0',
  ], {input: bytes, maxBuffer: 2 * 1024 * 1024}));
  const video = metadata.streams.find(stream => stream.codec_type === 'video');
  const audio = metadata.streams.find(stream => stream.codec_type === 'audio');
  expect(video.codec_name).toBe('h264');
  expect(video.height).toBeGreaterThan(video.width);
  expect(Number(video.nb_read_frames)).toBeGreaterThan(1);
  expect(audio.codec_name).toBe('aac');
  execFileSync('ffmpeg', [
    '-v', 'error', '-xerror', '-i', 'pipe:0', '-f', 'null', '-',
  ], {input: bytes});
});

test('cancelling during the session check does not start the video engine', async ({ page, request }) => {
  let releaseCheck;
  let markArrived;
  const arrived = new Promise(resolve => { markArrived = resolve; });
  let coreRequests = 0;
  page.on('request', req => {
    if (/\/core(?:-mt)?-esm\//.test(new URL(req.url()).pathname)) coreRequests++;
  });
  await page.route('**/heartbeat', async route => {
    const release = new Promise(resolve => { releaseCheck = resolve; });
    markArrived();
    await release;
    await route.continue();
  });
  await openSession(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures, 'large.mp4'));
  await page.locator('#submit').click();
  await arrived;
  await page.locator('#cancel').click();
  releaseCheck();
  await expect(page.locator('#submit')).toBeEnabled();
  await expect(page.locator('#message')).toContainText('Cancelled');
  expect(coreRequests).toBe(0);
});

for (const name of ['sample.mkv', 'sample.avi', 'sample.webm', 'sample.wmv',
  'sample.m2ts', 'sample.3gp', 'sample.mpg', 'sample.ogv', 'sample.flv', 'sample.ts',
  'hevc.mkv']) {
  test(`converts ${name} using encoder metadata rather than browser codecs`, async ({ page, request }) => {
    await openSession(page, request);
    await deliver(page, join(fixtures, name));
  });
}

test('unsupported AV1 fails without uploading and allows a supported replacement', async ({ page, request }) => {
  let uploads = 0;
  page.on('request', req => {
    if (req.method() === 'POST' && /\/upload\/[^/]+$/.test(new URL(req.url()).pathname)) uploads++;
  });
  await openSession(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures, 'av1.mkv'));
  await page.locator('#submit').click();
  await expect(page.locator('#message')).toContainText('unsupported or damaged codec', {timeout:30000});
  await expect(page.locator('#feedback-actions')).toBeVisible();
  await expect(page.locator('#feedback-link')).toHaveAttribute('href', /feedback%20\(failure\)/);
  expect(uploads).toBe(0);
  await deliver(page, join(fixtures, 'small.mp4'));
  await expect(page.locator('#feedback-link')).toHaveAttribute('href', /feedback%20\(success\)/);
});

async function dropFiles(page, count) {
  const transfer = await page.evaluateHandle(({ bytes, count }) => {
    const dt = new DataTransfer();
    for (let i = 0; i < count; i++) dt.items.add(new File([new Uint8Array(bytes)], `clip-${i}.mp4`, {type:'video/mp4'}));
    return dt;
  }, {bytes:Array.from(readFileSync(join(fixtures, 'small.mp4'))), count});
  await page.locator('#drop-zone').dispatchEvent('drop', {dataTransfer:transfer});
  await transfer.dispose();
}

test('dropped videos populate the input and deliver together', async ({ page, request }) => {
  await openSession(page, request);
  await dropFiles(page, 2);
  await expect(page.locator('.file-card')).toHaveCount(2);
  await page.locator('#submit').click();
  await expect(page.locator('#phase-title')).toHaveText('Delivered to Discord');
  await dropFiles(page, 1);
  await expect(page.locator('.file-card')).toHaveCount(2);
});

test('too many dropped files preserves the existing selection', async ({ page, request }) => {
  await openSession(page, request);
  await dropFiles(page, 1);
  await dropFiles(page, 11);
  await expect(page.locator('#message')).toContainText('no more than 10');
  await expect(page.locator('#message')).toBeVisible();
  await expect(page.locator('.file-card')).toHaveCount(1);
});

test('audio-only and damaged containers fail clearly and allow another selection', async ({ page, request }) => {
  await openSession(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures, 'audio.webm'));
  await page.locator('#submit').click();
  await expect(page.locator('#message')).toContainText('No video stream', { timeout: 30000 });
  await page.locator('#clips').setInputFiles({name:'broken.mkv', mimeType:'video/x-matroska',
    buffer:Buffer.from([0x1a, 0x45, 0xdf, 0xa3, 0, 0, 0, 0])});
  await page.locator('#submit').click();
  await expect(page.locator('#message')).toContainText('Could not determine video metadata', { timeout: 30000 });
  await deliver(page, join(fixtures, 'small.mp4'));
});

test('drops cannot replace files while compression is running', async ({ page, request }) => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/assets/**/core-mt-esm/ffmpeg-core.js', async route => {
    await gate;
    await route.continue();
  });
  await openSession(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures, 'large.mp4'));
  await page.locator('#submit').click();
  await expect(page.locator('#clips')).toBeDisabled();
  await expect(page.locator('#phase-title')).toHaveText('Loading video engine');
  await expect(page.locator('#run-detail')).toHaveText('Starting video engine');
  await expect(page.locator('.file-status')).toHaveText('Loading video engine');
  await dropFiles(page, 2);
  await expect(page.locator('.file-card')).toHaveCount(1);
  await expect(page.locator('.file-name')).toHaveText('large.mp4');
  release();
  await expect(page.locator('#phase-title')).toHaveText('Delivered to Discord', { timeout: 60000 });
});

test('folder drops are rejected and outside drops do not navigate away', async ({ page, request }) => {
  await openSession(page, request);
  const url = page.url();
  expect(await page.evaluate(() => {
    const event = new Event('drop', {bubbles:true, cancelable:true});
    Object.defineProperty(event, 'dataTransfer', {value:{items:[{
      webkitGetAsEntry: () => ({isDirectory:true}),
    }], files:[]}});
    return document.getElementById('drop-zone').dispatchEvent(event);
  })).toBe(false);
  await expect(page.locator('#message')).toBeVisible();
  await expect(page.locator('#message')).toContainText('not folders');
  expect(await page.evaluate(() => document.body.dispatchEvent(
    new DragEvent('drop', {bubbles:true, cancelable:true, dataTransfer:new DataTransfer()})
  ))).toBe(false);
  expect(page.url()).toBe(url);
});

test('invalid input can be replaced and retried in the same session', async ({ page, request }) => {
  await openSession(page, request);
  await page.locator('#clips').setInputFiles({name:'bad.mp4', mimeType:'video/mp4', buffer:Buffer.from('not video')});
  await page.locator('#submit').click();
  await expect(page.locator('#phase-title')).toHaveText('Action needed');
  await deliver(page, join(fixtures, 'small.mp4'));
});

test('cancel during engine loading settles before another attempt', async ({ page, request }) => {
  await page.route('**/assets/**/core-mt-esm/ffmpeg-core.js', async route => {
    await new Promise(resolve => setTimeout(resolve, 500));
    await route.continue();
  });
  await openSession(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures, 'large.mp4'));
  await page.locator('#submit').click();
  await page.locator('#cancel').click();
  await expect(page.locator('#submit')).toBeEnabled({timeout:30000});
  await deliver(page, join(fixtures, 'small.mp4'));
});

test('consumed links cannot be reopened', async ({ page, request }) => {
  await openSession(page, request);
  await page.reload();
  await expect(page.getByRole('heading')).toHaveText('Upload link unavailable');
});

test('busy relay is retried without recompression', async ({ page, request }) => {
  await openSession(page, request);
  let attempts = 0;
  await page.route('**/upload/*', async route => {
    if (route.request().method() === 'POST' && attempts++ === 0) {
      await route.fulfill({status:503, contentType:'application/json',
        body:JSON.stringify({error:'Relay busy', retry_after:1})});
    } else await route.continue();
  });
  await deliver(page, join(fixtures, 'small.mp4'));
  expect(attempts).toBe(2);
});

test('delivery rejection shows an error and prevents duplicate submission', async ({ page, request }) => {
  await openSession(page, request);
  await page.route('**/upload/*', async route => {
    if (route.request().method() === 'POST') await route.fulfill({
      status:502, contentType:'application/json',
      body:JSON.stringify({error:'Discord rejected the files', code:'discord_delivery_failed'}),
    });
    else await route.continue();
  });
  await page.locator('#clips').setInputFiles(join(fixtures, 'small.mp4'));
  await page.locator('#submit').click();
  await expect(page.locator('#phase-title')).toHaveText('Action needed');
  await expect(page.locator('#submit')).toBeHidden();
});

test('lost success response is recovered without another upload', async ({page, request}) => {
  await page.addInitScript(() => {
    const listen = XMLHttpRequest.prototype.addEventListener;
    window.lostSuccess = false;
    XMLHttpRequest.prototype.addEventListener = function(type, callback, ...args) {
      if (type !== 'load') return listen.call(this,type,callback,...args);
      return listen.call(this,type,function(event) {
        if (!window.lostSuccess && this.responseType === 'json' && this.status === 200 && this.response?.ok) {
          window.lostSuccess = true;
          this.dispatchEvent(new ProgressEvent('error'));
          return;
        }
        return callback.call(this,event);
      },...args);
    };
  });
  await openSession(page, request);
  let uploads = 0;
  page.on('request', r => {if(r.method()==='POST' && /\/upload\/[^/]+$/.test(new URL(r.url()).pathname)) uploads++;});
  await deliver(page, join(fixtures,'small.mp4'));
  expect(await page.evaluate(()=>window.lostSuccess)).toBe(true);
  expect(uploads).toBe(1);
});

test('slow delivery is polled rather than reported failed or resent', async ({page, request}) => {
  const session = await (await request.post('/test/session?slow=1')).json();
  await page.goto(session.path);
  let uploads=0;
  let pending=0;
  page.on('request', r=> {if(r.method()==='POST' && new URL(r.url()).pathname===session.path) uploads++;});
  page.on('response', r=> {if(r.status()===202) pending++;});
  await deliver(page,join(fixtures,'small.mp4'));
  expect(uploads).toBe(1);
  expect(pending).toBeGreaterThan(0);
});

test('expired idle session cannot start encoding', async ({page, request}) => {
  await page.clock.install();
  await openSession(page, request);
  await page.locator('#clips').setInputFiles(join(fixtures,'large.mp4'));
  await page.clock.fastForward(31*60*1000);
  await expect(page.locator('#expires')).toHaveText('Session expired');
  await expect(page.locator('#submit')).toBeDisabled();
  await page.locator('#clips').setInputFiles(join(fixtures,'small.mp4'));
  await expect(page.locator('#submit')).toBeDisabled();
});

test('manual relay retry reuses prepared files and offers local recovery', async ({page, request}) => {
  await page.addInitScript(() => {
    window.encoderWrites=0;
    const send=Worker.prototype.postMessage;
    Worker.prototype.postMessage=function(message,...rest) {
      if(message?.type==='WRITE_FILE') window.encoderWrites++;
      return send.call(this,message,...rest);
    };
  });
  await openSession(page,request);
  let uploads=0;
  await page.route('**/upload/*',async route=> {
    if(route.request().method()==='POST' && uploads++<3) {
      await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Busy',retry_after:0.01})});
    } else await route.continue();
  });
  await page.locator('#clips').setInputFiles(join(fixtures,'large.mp4'));
  await page.locator('#submit').click();
  await expect(page.locator('#phase-title')).toHaveText('Action needed',{timeout:60000});
  await expect(page.locator('#downloads a')).toHaveCount(1);
  await expect(page.locator('#clips')).toBeDisabled();
  const writes=await page.evaluate(()=>window.encoderWrites);
  expect(writes).toBeGreaterThan(0);
  await page.getByRole('button',{name:'Check delivery / retry'}).click();
  await expect(page.locator('#phase-title')).toHaveText('Delivered to Discord',{timeout:10000});
  expect(await page.evaluate(()=>window.encoderWrites)).toBe(writes);
  await expect(page.locator('#downloads')).toBeHidden();
});

test('narrow screens contain a ten-file selection', async ({ page, request }) => {
  await page.setViewportSize({width:375, height:812});
  await openSession(page, request);
  await page.locator('#clips').setInputFiles(Array(10).fill(join(fixtures, 'small.mp4')));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

for (const width of [375, 1440]) {
  test(`format help does not overlap controls or messages at ${width}px`, async ({ page, request }) => {
    await page.setViewportSize({width, height:1000});
    await openSession(page, request);
    const help = page.locator('.format-help');
    const gapAfter = async selector => {
      const above = await page.locator(selector).boundingBox();
      const below = await help.boundingBox();
      expect(below.y - (above.y + above.height)).toBeGreaterThanOrEqual(15);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    };
    await gapAfter('#submit');
    await help.locator('summary').click();
    await gapAfter('#submit');
    await deliver(page, join(fixtures, 'small.mp4'));
    await gapAfter('#message');
    await gapAfter('#feedback-actions');
    await help.locator('summary').click();
    await gapAfter('#feedback-actions');
    await page.screenshot({path: test.info().outputPath(`layout-${width}.png`), fullPage:true});
  });
}
