// Shared helpers for the browser specs: test sessions and ffmpeg-generated fixtures.
const { expect } = require("@playwright/test");
const { execFileSync } = require("node:child_process");
const { readFileSync, writeFileSync } = require("node:fs");

// Create a test session, open its compressor page, and wait until startup enables the picker.
async function openSession(page, request) {
  const session = await (await request.post("/test/session")).json();
  await page.goto(session.path);
  await expect(page.locator("#clips")).toBeEnabled();
  return session;
}

// Run ffmpeg with errors-only logging.
function ffmpeg(...args) {
  execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", ...args]);
}

// Encode a lavfi test pattern. `options` follow the video codec, before the output path.
function testPatternClip(
  output,
  { size = "320x240", rate = 25, duration = 0.4, codec = "libx264", options = [] } = {},
) {
  ffmpeg(
    "-f",
    "lavfi",
    "-i",
    `testsrc2=size=${size}:rate=${rate}`,
    "-t",
    String(duration),
    "-c:v",
    codec,
    ...options,
    output,
  );
}

// Copy an MP4 with a trailing `free` box so the result is exactly `bytes` long and still valid.
function padWithFreeBox(input, output, bytes) {
  const source = readFileSync(input);
  const padding = Buffer.alloc(bytes - source.length);
  padding.writeUInt32BE(padding.length);
  padding.write("free", 4);
  writeFileSync(output, Buffer.concat([source, padding]));
}

module.exports = { openSession, ffmpeg, testPatternClip, padWithFreeBox };
