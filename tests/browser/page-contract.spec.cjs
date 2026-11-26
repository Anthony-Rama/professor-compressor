const { test, expect } = require('@playwright/test');

test('the private compressor page loads its dependencies without putting session credentials in their URLs', async ({ page, request }) => {
  const session = await request.post('/test/session');
  const { path } = await session.json();
  const assets = [];
  page.on('request', req => {
    const url = new URL(req.url());
    if (url.pathname.startsWith('/brand/') || url.pathname.startsWith('/assets/')) assets.push(url);
  });
  const response = await page.goto(path);
  expect(response.headers()['cache-control']).toBe('no-store');
  expect(response.headers()['referrer-policy']).toBe('no-referrer');
  await expect(page.locator('#clips')).toBeEnabled();
  await expect(page.locator('#startup-error')).toBeHidden();
  expect(assets.some(url => url.pathname.endsWith('/encoder-client.js'))).toBe(true);
  expect(assets.every(url => !url.href.includes(path.split('/').pop()))).toBe(true);
  const controller = await request.get('/brand/compressor.js');
  expect(controller.ok()).toBe(true);
  const config = JSON.parse(await page.locator('#compressor-config').textContent());
  expect(typeof config.SESSION_SECRET).toBe('string');
  expect(config.SESSION_SECRET.length).toBeGreaterThan(0);
  expect(assets.every(url => !url.href.includes(encodeURIComponent(config.SESSION_SECRET)))).toBe(true);
  expect((await controller.text()).includes(config.SESSION_SECRET)).toBe(false);
});

test('an unavailable compressor asset leaves the picker disabled with recovery instructions', async ({ page, request }) => {
  await page.route('**/brand/compressor.js', route => route.abort());
  const { path } = await (await request.post('/test/session')).json();
  await page.goto(path);
  await expect(page.locator('#startup-error')).toContainText('reload');
  await expect(page.locator('#clips')).toBeDisabled();
  await expect(page.locator('#submit')).toBeDisabled();
});
