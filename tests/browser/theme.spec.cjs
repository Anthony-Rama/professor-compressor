const { test, expect } = require("@playwright/test");
const { openSession } = require("./helpers.cjs");

// Page background of each palette, as computed by the browser.
const BACKGROUND = { dark: "rgb(14, 17, 32)", light: "rgb(245, 246, 251)" };

async function expectPalette(page, palette) {
  await expect(page.locator("body")).toHaveCSS("background-color", BACKGROUND[palette]);
}

const storedTheme = (page) => page.evaluate(() => localStorage.getItem("pc-theme"));
const themeAttribute = (page) => page.evaluate(() => document.documentElement.dataset.theme);
const themeButton = (page, name) => page.getByRole("button", { name: "Theme: " + name });
const themeMenu = (page) => page.getByRole("menu", { name: "Theme" });
async function chooseTheme(page, current, next) {
  await themeButton(page, current).click();
  await themeMenu(page).getByRole("menuitemradio", { name: next, exact: true }).click();
}

test("System follows the device setting on session and website pages", async ({
  page,
  request,
}) => {
  for (const scheme of ["dark", "light"]) {
    await page.emulateMedia({ colorScheme: scheme });
    await openSession(page, request);
    await expect(themeButton(page, "System")).toBeVisible();
    await expectPalette(page, scheme);
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Theme: System" })).toBeVisible();
    await expectPalette(page, scheme);
    expect(await themeAttribute(page)).toBeUndefined();
  }
});

test("the compressor header menu chooses each theme and the choice persists", async ({
  page,
  request,
}) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await openSession(page, request);
  const header = page.getByRole("banner");
  const button = header.getByRole("button", { name: "Theme: System" });
  await expect(button).toBeVisible();
  const session = await header.locator("#session-note").boundingBox();
  expect((await button.boundingBox()).x).toBeGreaterThanOrEqual(session.x + session.width);

  // An explicit Light choice wins over a dark device setting.
  await chooseTheme(page, "System", "Light");
  await expectPalette(page, "light");
  expect(await storedTheme(page)).toBe("light");
  await page.reload();
  await expect(page.locator("#clips")).toBeEnabled();
  expect(await themeAttribute(page)).toBe("light");
  await expect(themeButton(page, "Light")).toBeVisible();
  await expectPalette(page, "light");

  // Dark wins over a light device setting.
  await page.emulateMedia({ colorScheme: "light" });
  await chooseTheme(page, "Light", "Dark");
  await expect(themeButton(page, "Dark")).toBeFocused();
  await expectPalette(page, "dark");
  expect(await storedTheme(page)).toBe("dark");

  // The choice carries over to the website pages and back.
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Theme: Dark" })).toBeVisible();
  await expectPalette(page, "dark");
  await page.goto("/guide/compress-video-for-discord");
  await expect(page.getByRole("button", { name: "Theme: Dark" })).toBeVisible();
  await expectPalette(page, "dark");

  // System forgets the choice and keeps following the device as it changes.
  await openSession(page, request);
  await chooseTheme(page, "Dark", "System");
  expect(await storedTheme(page)).toBeNull();
  expect(await themeAttribute(page)).toBeUndefined();
  await expectPalette(page, "light");
  await page.emulateMedia({ colorScheme: "dark" });
  await expectPalette(page, "dark");
});

test("the header menu is keyboard operable and chooses each theme", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  const button = page.getByRole("button", { name: "Theme: System" });
  const menu = page.getByRole("menu", { name: "Theme" });
  await expect(button).toHaveAttribute("aria-haspopup", "menu");
  await expect(button).toHaveAttribute("aria-expanded", "false");
  await expect(menu).toBeHidden();

  // Enter opens the menu on the checked item; arrows move; Enter chooses.
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(menu).toBeVisible();
  await expect(button).toHaveAttribute("aria-expanded", "true");
  await expect(menu.getByRole("menuitemradio", { name: "System" })).toBeFocused();
  await expect(menu.getByRole("menuitemradio", { name: "System" })).toHaveAttribute(
    "aria-checked",
    "true",
  );
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await expect(menu.getByRole("menuitemradio", { name: "Dark" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(menu).toBeHidden();
  const dark = page.getByRole("button", { name: "Theme: Dark" });
  await expect(dark).toBeFocused();
  await expect(dark).toHaveAttribute("aria-expanded", "false");
  await expectPalette(page, "dark");
  expect(await storedTheme(page)).toBe("dark");

  // Escape closes without choosing and returns focus to the button.
  await page.keyboard.press("Space");
  await expect(menu.getByRole("menuitemradio", { name: "Dark" })).toBeFocused();
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await expect(dark).toBeFocused();
  await expectPalette(page, "dark");

  // A click outside closes it too; a click on an item chooses it.
  await dark.click();
  await expect(menu).toBeVisible();
  await page.locator("h1").click();
  await expect(menu).toBeHidden();
  await expect(dark).toBeFocused();
  await dark.click();
  await menu.getByRole("menuitemradio", { name: "Light" }).click();
  await expect(page.getByRole("button", { name: "Theme: Light" })).toBeFocused();
  expect(await storedTheme(page)).toBe("light");
  await page.emulateMedia({ colorScheme: "dark" });
  await expectPalette(page, "light");

  // System removes the saved choice and follows the device again.
  await page.getByRole("button", { name: "Theme: Light" }).click();
  await menu.getByRole("menuitemradio", { name: "System" }).click();
  expect(await storedTheme(page)).toBeNull();
  await expectPalette(page, "dark");
  await page.reload();
  await expect(page.getByRole("button", { name: "Theme: System" })).toBeVisible();
  await expectPalette(page, "dark");
});

test("the compressor header fits on a narrow phone", async ({ page, request }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  await openSession(page, request);
  const header = page.getByRole("banner");
  const button = header.getByRole("button", { name: "Theme: System" });
  await expect(button).toBeVisible();
  await expect(header.locator("#expires")).toBeVisible();
  const control = await button.boundingBox();
  const session = await header.locator("#session-note").boundingBox();
  expect(control.y + control.height).toBeLessThanOrEqual(session.y);
  await button.click();
  const menu = await themeMenu(page).boundingBox();
  expect(menu.x).toBeGreaterThanOrEqual(0);
  expect(menu.x + menu.width).toBeLessThanOrEqual(320);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await themeMenu(page).getByRole("menuitemradio", { name: "Light" }).click();
  await expectPalette(page, "light");
});

test("the header menu stays inside a 360px viewport", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto("/privacy");
  await page.getByRole("button", { name: "Theme: System" }).click();
  const box = await page.getByRole("menu", { name: "Theme" }).boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(360);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("blocked storage keeps pages working on the System theme", async ({ page, request }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new DOMException("Storage is blocked", "SecurityError");
      },
    });
  });
  await page.emulateMedia({ colorScheme: "dark" });
  await openSession(page, request);
  await expect(page.locator("#startup-error")).toBeHidden();
  expect(await page.evaluate(() => window.compressorStartup.pending())).toBe(false);
  await expect(themeButton(page, "System")).toBeVisible();
  await expectPalette(page, "dark");
  // The choice still applies for this page even though it cannot be saved.
  await chooseTheme(page, "System", "Light");
  await expectPalette(page, "light");
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Theme: System" })).toBeVisible();
  await expectPalette(page, "dark");
});
