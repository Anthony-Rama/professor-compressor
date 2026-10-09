const {test, expect} = require('@playwright/test');
const {execFileSync} = require('node:child_process');
const {mkdtempSync, rmSync, readFileSync, writeFileSync} = require('node:fs');
const {tmpdir} = require('node:os');
const {join} = require('node:path');
let fixtures;

test.beforeAll(() => {
  fixtures = mkdtempSync(join(tmpdir(), 'adaptive-quality-'));
  for (const [name, source, seconds] of [
    ['detail.mp4', 'testsrc=size=1920x1080:rate=30', 2],
    ['motion.mp4', 'testsrc2=size=1920x1080:rate=60', 3],
    ['tight.mp4', 'testsrc2=size=1280x720:rate=30', 24],
    ['baseline.mp4', 'testsrc2=size=1280x720:rate=30', 12],
    ['portrait.mp4', 'testsrc=size=1080x1920:rate=24', 1],
    ['4k.mp4', 'testsrc=size=3840x2160:rate=30', 1],
  ]) {
    execFileSync('ffmpeg', ['-v','error','-f','lavfi','-i',source,'-t',String(seconds),
      '-c:v','libx264','-preset','ultrafast','-crf','0','-pix_fmt','yuv420p',join(fixtures,name)]);
    const bytes = readFileSync(join(fixtures,name));
    // Force encoding, not the fitting-MP4 shortcut, without changing frames.
    if (bytes.length < 8_000_001) writeFileSync(join(fixtures,name),
      Buffer.concat([bytes,Buffer.alloc(8_000_001-bytes.length)]));
  }
});
test.afterAll(() => rmSync(fixtures,{recursive:true,force:true}));

async function open(page,request,limit=1_100_000) {
  await page.addInitScript(() => {
    const send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function(body) {
      if (body instanceof FormData && body.has('clips')) {
        window.result = body.get('clips');
        window.diagnostic = JSON.parse(body.get('diagnostics'))[0];
      }
      return send.call(this,body);
    };
  });
  const response = await request.post('/test/session?limit='+limit);
  await page.goto((await response.json()).path);
}
async function encode(page,name) {
  const started = Date.now();
  await page.locator('#clips').setInputFiles(join(fixtures,name));
  await page.locator('#submit').click();
  await expect.poll(()=>page.locator('#phase-title').textContent(),{timeout:80000})
    .toMatch(/Delivered to Discord|Action needed/);
  expect(await page.locator('#phase-title').textContent(),await page.locator('#message').textContent())
    .toBe('Delivered to Discord');
  const bytes = Buffer.from(await page.evaluate(async () => {
    const data = new Uint8Array(await window.result.arrayBuffer());
    let binary = '';
    for (let i=0;i<data.length;i+=32768) binary += String.fromCharCode(...data.subarray(i,i+32768));
    return btoa(binary);
  }),'base64');
  const path = join(fixtures, 'result-'+name);
  writeFileSync(path,bytes);
  const metadata = JSON.parse(execFileSync('ffprobe',['-v','error','-show_streams','-show_format','-of','json',path]));
  execFileSync('ffmpeg',['-v','error','-xerror','-i',path,'-f','null','-']);
  const diagnostic = await page.evaluate(() => window.diagnostic);
  await test.info().attach('encoding-result',{contentType:'application/json',body:JSON.stringify({
    bytes:bytes.length,elapsedSeconds:(Date.now()-started)/1000,metadata,diagnostic,
  })});
  return {path,bytes,metadata,diagnostic};
}

test('screen detail stays 1080p and smaller quality output beats the old 720p cap', async ({page,request}) => {
  await open(page,request);
  const result = await encode(page,'detail.mp4');
  const video = result.metadata.streams.find(s=>s.codec_type==='video');
  expect([video.width,video.height]).toEqual([1920,1080]);
  expect(result.bytes.length).toBeLessThan(1_078_000);
  expect(result.diagnostic.video_kbps).toBeNull();
  const old = join(fixtures,'old-detail.mp4');
  execFileSync('ffmpeg',['-v','error','-i',join(fixtures,'detail.mp4'),'-vf','scale=1280:720',
    '-c:v','libx264','-preset','veryfast','-crf','18',old]);
  // ffmpeg prints metrics on stderr; retain them without console noise.
  const {spawnSync} = require('node:child_process');
  const metric = path => {
    const run = spawnSync('ffmpeg',['-v','info','-i',path,'-i',join(fixtures,'detail.mp4'),
      '-lavfi','[0:v]scale=1920:1080,setsar=1[a];[a][1:v]ssim','-f','null','-'],{encoding:'utf8'});
    expect(run.status).toBe(0);
    return Number(run.stderr.match(/All:([0-9.]+)/)[1]);
  };
  const adaptive = metric(result.path), baseline = metric(old);
  expect(adaptive).toBeGreaterThan(baseline);
  await test.info().attach('detail-comparison',{contentType:'application/json',body:JSON.stringify({adaptive,baseline})});
});

test('roomy budget retains native 1080p60 motion rather than capping at 720p30', async ({page,request}) => {
  await open(page,request,8_000_000);
  const result = await encode(page,'motion.mp4');
  const video = result.metadata.streams.find(s=>s.codec_type==='video');
  expect([video.width,video.height,video.avg_frame_rate]).toEqual([1920,1080,'60/1']);
  expect(result.bytes.length).toBeLessThanOrEqual(7_840_000);
  expect(Number(result.metadata.format.duration)).toBeCloseTo(3,1);
});

test('tight budget automatically lowers resolution without asking to trim', async ({page,request}) => {
  await open(page,request);
  const result = await encode(page,'tight.mp4');
  const video = result.metadata.streams.find(s=>s.codec_type==='video');
  expect(video.height).toBeLessThan(720);
  expect(video.avg_frame_rate).toBe('30/1');
  expect(result.bytes.length).toBeLessThanOrEqual(1_078_000);
  expect(Number(result.metadata.format.duration)).toBeCloseTo(24,1);
});

test('busy video keeps the 720p30 baseline when its bitrate remains feasible', async ({page,request}) => {
  await open(page,request);
  const result = await encode(page,'baseline.mp4');
  const video = result.metadata.streams.find(s=>s.codec_type==='video');
  expect([video.width,video.height,video.avg_frame_rate]).toEqual([1280,720,'30/1']);
  expect(result.diagnostic.video_kbps).toBeGreaterThan(600);
  expect(result.bytes.length).toBeLessThanOrEqual(1_078_000);
  expect(Number(result.metadata.format.duration)).toBeCloseTo(12,1);
});

for (const [name,width,height,fps] of [['portrait.mp4',1080,1920,'24/1'],['4k.mp4',1920,1080,'30/1']]) {
  test(`adaptive encoding handles ${name} within the resolution ceiling without increasing frame rate`, async ({page,request}) => {
    await open(page,request,8_000_000);
    const result = await encode(page,name);
    const video = result.metadata.streams.find(s=>s.codec_type==='video');
    expect([video.width,video.height,video.avg_frame_rate]).toEqual([width,height,fps]);
    expect(result.bytes.length).toBeLessThanOrEqual(7_840_000);
  });
}

test('cancelling a quality preview stops all encoding and sends no media', async ({page,request}) => {
  await page.addInitScript(() => {
    const post = Worker.prototype.postMessage;
    Worker.prototype.postMessage = function(message,...rest) {
      if (message?.type==='EXEC' && message.data.args.includes('sample-1.mp4')) {
        window.previewHeld = true;
        return;
      }
      return post.call(this,message,...rest);
    };
  });
  await open(page,request);
  await page.locator('#clips').setInputFiles(join(fixtures,'detail.mp4'));
  await page.locator('#submit').click();
  await expect.poll(()=>page.evaluate(()=>window.previewHeld),{timeout:30000}).toBe(true);
  await page.locator('#cancel').click();
  await expect(page.locator('#message')).toContainText('Cancelled');
  expect(await page.evaluate(()=>window.result)).toBeUndefined();
  await expect(page.locator('#submit')).toBeEnabled();
});

test('failed high-resolution preview retries with a conservative profile', async ({page,request}) => {
  await page.addInitScript(() => {
    const post = Worker.prototype.postMessage;
    Worker.prototype.postMessage = function(message,...rest) {
      if (!window.previewFailure && message?.type==='EXEC' && message.data.args.includes('sample-1.mp4')) {
        window.previewFailure = true;
        queueMicrotask(()=>this.onmessage({data:{id:message.id,type:'ERROR',data:new Error('Simulated encoder memory failure')}}));
        return;
      }
      return post.call(this,message,...rest);
    };
  });
  await open(page,request);
  const result = await encode(page,'detail.mp4');
  expect(await page.evaluate(()=>window.previewFailure)).toBe(true);
  const video = result.metadata.streams.find(s=>s.codec_type==='video');
  expect([video.width,video.height]).toEqual([1280,720]);
  expect(result.bytes.length).toBeLessThanOrEqual(1_078_000);
});
