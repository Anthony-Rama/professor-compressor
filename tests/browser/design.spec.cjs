const { test, expect } = require("@playwright/test");
const { mkdtempSync, rmSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const { openSession, testPatternClip } = require("./helpers.cjs");

let fixtures;
test.beforeAll(() => {
  fixtures = mkdtempSync(join(tmpdir(), "compressor-design-"));
  testPatternClip(join(fixtures, "small.mp4"));
});
test.afterAll(() => rmSync(fixtures, { recursive: true, force: true }));

// Background and text colours of the two palettes, as computed by the browser.
const THEMES = {
  dark: { background: "rgb(14, 17, 32)", text: "rgb(238, 240, 250)" },
  light: { background: "rgb(245, 246, 251)", text: "rgb(21, 25, 51)" },
};

async function expectTheme(page, scheme) {
  await expect(page.locator("body")).toHaveCSS("background-color", THEMES[scheme].background);
  await expect(page.locator("body")).toHaveCSS("color", THEMES[scheme].text);
  expect(
    await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue("color-scheme").trim(),
    ),
  ).toBe(scheme);
}

async function expectNoHorizontalOverflow(page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

// Deliver one clip, then reload so the same browser sees the delivery-status page.
async function openDeliveredStatus(page, request) {
  await openSession(page, request);
  await page.locator("#clips").setInputFiles(join(fixtures, "small.mp4"));
  await page.locator("#submit").click();
  await expect(page.locator("#page-heading")).toHaveText("Delivered to Discord", {
    timeout: 60000,
  });
  await page.reload();
  await expect(page.locator("#delivery-status")).toContainText("Delivered to Discord");
}

for (const scheme of ["dark", "light"]) {
  test(`${scheme} colour scheme applies its design tokens on every page type`, async ({
    page,
    request,
  }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await openSession(page, request);
    await expectTheme(page, scheme);
    const clear = page.locator("#clear-selection");
    await expect(clear).toBeDisabled();
    await expect(clear).toBeHidden();
    await page.locator("#clips").setInputFiles(join(fixtures, "small.mp4"));
    await expect(clear).toBeVisible();
    for (const property of ["color", "background-color", "border-top-color"]) {
      const cancelColor = await page
        .locator("#cancel")
        .evaluate((button, name) => getComputedStyle(button).getPropertyValue(name), property);
      await expect(clear).toHaveCSS(property, cancelColor);
    }
    const remove = page.locator(".remove-file");
    const neutralColor = await remove.evaluate((button) => getComputedStyle(button).color);
    const dangerColor = await clear.evaluate((button) => getComputedStyle(button).color);
    expect(neutralColor).not.toBe(dangerColor);
    await remove.hover();
    await expect(remove).toHaveCSS("color", dangerColor);
    await page.locator("#page-heading").hover();
    await expect(remove).toHaveCSS("color", neutralColor);
    for (const path of ["/", "/guide/compress-video-for-discord", "/privacy", "/terms"]) {
      await page.goto(path);
      await expectTheme(page, scheme);
    }
    await page.goto("/upload/not-a-real-token");
    await expectTheme(page, scheme);
    await openDeliveredStatus(page, request);
    await expectTheme(page, scheme);
  });
}

for (const width of [360, 1280]) {
  test(`compressor, home and status pages do not scroll sideways at ${width}px`, async ({
    page,
    request,
  }) => {
    await page.setViewportSize({ width, height: 800 });
    await openSession(page, request);
    await expectNoHorizontalOverflow(page);
    await page.locator("#clips").setInputFiles({
      name: "A-very-long-video-name-that-should-wrap-or-truncate-inside-its-card.mp4",
      mimeType: "video/mp4",
      buffer: Buffer.from("test"),
    });
    await expect(page.locator(".file-card")).toHaveCount(1);
    await expectNoHorizontalOverflow(page);
    for (const path of ["/", "/guide/compress-video-for-discord", "/privacy"]) {
      await page.goto(path);
      await expectNoHorizontalOverflow(page);
    }
    await page.goto("/upload/not-a-real-token");
    await expectNoHorizontalOverflow(page);
    await openDeliveredStatus(page, request);
    await expectNoHorizontalOverflow(page);
  });
}

test("home page lists Professor Compressor on Top.gg with the other directories", async ({
  page,
}) => {
  await page.goto("/");
  const listings = page.locator("#listings");
  for (const [name, href] of [
    ["dsc.sh", "https://dsc.sh/bot/1550752400271351839"],
    ["Discord Bot List", "https://discordbotlist.com/bots/professor-compressor"],
    ["discord.bots.gg", "https://discord.bots.gg/bots/1550752400271351839"],
    ["Top.gg", "https://top.gg/bot/1550752400271351839"],
  ]) {
    await expect(listings.getByRole("link", { name, exact: true })).toHaveAttribute("href", href);
  }
});

test("the Ready tip only shows when videos are selected", async ({ page, request }) => {
  await openSession(page, request);
  const tip = page.locator("#preparation-help");
  await expect(tip).toBeHidden();
  await page.locator("#clips").setInputFiles(join(fixtures, "small.mp4"));
  await expect(tip).toBeVisible();
  await page.getByRole("button", { name: "Clear selected videos" }).click();
  await expect(tip).toBeHidden();
});

test("the shared footer stays available on the compressor and completed delivery", async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 360, height: 740 });
  async function expectSharedNavigation() {
    const header = page.getByRole("banner");
    await expect(header.getByRole("button", { name: "Theme: System" })).toBeVisible();
    const footer = page.getByRole("contentinfo");
    const links = footer.getByRole("link");
    await expect(links).toHaveText([
      "Website",
      "Privacy Policy",
      "Terms of Service",
      "Third-party notices",
      "Contact Support",
      "Support Discord",
    ]);
    await expect(footer.locator(".theme-menu, .theme-switch")).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
  }
  await openSession(page, request);
  await expectSharedNavigation();
  await openDeliveredStatus(page, request);
  await expectSharedNavigation();
  await expect(page.locator(".status-support")).toBeHidden();
});

test("the action bar explains why sending is unavailable", async ({ page, request }) => {
  await page.addInitScript(() => {
    const slice = File.prototype.slice;
    File.prototype.slice = function (...args) {
      const blob = slice.apply(this, args);
      if (this.name === "slow.mp4") {
        const read = blob.arrayBuffer.bind(blob);
        blob.arrayBuffer = async () => {
          await new Promise((resolve) => {
            window.releaseLocalRead = resolve;
          });
          return read();
        };
      }
      return blob;
    };
  });
  await openSession(page, request);
  await expect(page.locator("#submit")).toHaveText("Add videos to continue");
  await expect(page.locator("#submit")).toBeDisabled();
  await page.locator("#clips").setInputFiles({
    name: "slow.mp4",
    mimeType: "video/mp4",
    buffer: Buffer.from("test"),
  });
  await expect(page.locator("#submit-help")).toHaveText("Waiting for 1 clip to finish preparing.");
  await expect.poll(() => page.evaluate(() => typeof window.releaseLocalRead)).toBe("function");
  await page.evaluate(() => window.releaseLocalRead());
  await expect(page.locator("#submit")).toHaveText("Compress & send 1 video");
  await expect(page.locator("#submit")).toBeEnabled();
  await expect(page.locator("#submit-help")).toHaveText("");
});
