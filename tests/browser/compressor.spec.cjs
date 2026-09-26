const { test, expect } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const { mkdtempSync, rmSync } = require('node:fs');
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
  await expect(page.locator('#phase-title')).toHaveText('Delivered to Discord', { timeout: 60000 });
}

test('oversized video loads WebAssembly and compresses under the real CSP', async ({ page, request }) => {
  await openSession(page, request);
  expect(await page.evaluate(() => crossOriginIsolated)).toBe(true);
  await deliver(page, join(fixtures, 'large.mp4'));
  await expect(page.locator('.file-meta')).toContainText('smaller');
});

test('single-thread fallback still encodes when multithread core is unavailable', async ({ page, request }) => {
  await page.route('**/assets/core-mt-esm/**', route => route.abort());
  await openSession(page, request);
  await deliver(page, join(fixtures, 'large.mp4'));
});

test('ten fitting MP4s deliver together without loading an encoder', async ({ page, request }) => {
  await page.route('**/assets/core*-esm/**', () => { throw new Error('Unexpected encoding'); });
  await openSession(page, request);
  await deliver(page, Array(10).fill(join(fixtures, 'small.mp4')));
  await expect(page.locator('#phase-copy')).toContainText('10 file(s)');
});

test('small QuickTime MOV is converted to an actual MP4', async ({ page, request }) => {
  await openSession(page, request);
  await deliver(page, join(fixtures, 'small.mov'));
});

test('invalid input can be replaced and retried in the same session', async ({ page, request }) => {
  await openSession(page, request);
  await page.locator('#clips').setInputFiles({name:'bad.mp4', mimeType:'video/mp4', buffer:Buffer.from('not video')});
  await page.locator('#submit').click();
  await expect(page.locator('#phase-title')).toHaveText('Action needed');
  await deliver(page, join(fixtures, 'small.mp4'));
});

test('cancel during engine loading settles before another attempt', async ({ page, request }) => {
  await page.route('**/assets/core-mt-esm/ffmpeg-core.js', async route => {
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

test('narrow screens contain a ten-file selection', async ({ page, request }) => {
  await page.setViewportSize({width:375, height:812});
  await openSession(page, request);
  await page.locator('#clips').setInputFiles(Array(10).fill(join(fixtures, 'small.mp4')));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
