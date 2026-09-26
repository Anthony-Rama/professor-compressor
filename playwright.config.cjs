const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests/browser',
  workers: 1,
  timeout: 90000,
  use: {
    baseURL: 'http://127.0.0.1:18764',
    launchOptions: process.env.CHROME_EXECUTABLE
      ? { executablePath: process.env.CHROME_EXECUTABLE } : {},
  },
  webServer: {
    command: `${process.env.TEST_PYTHON || 'python3'} tests/browser_server.py`,
    url: 'http://127.0.0.1:18764/healthz',
    reuseExistingServer: false,
  },
});
