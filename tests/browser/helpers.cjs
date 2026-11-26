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

// Run ffmpeg with errors-only logging; `timeout` (ms) bounds slow encodes.
function runFfmpeg(args, { timeout } = {}) {
  execFileSync(
    "ffmpeg",
    ["-hide_banner", "-loglevel", "error", ...args],
    timeout ? { timeout } : undefined,
  );
}

function ffmpeg(...args) {
  runFfmpeg(args);
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

// Write `source` followed by a `free` box of `size` bytes; the MP4 stays valid.
function writeWithFreeBox(source, output, size) {
  const padding = Buffer.alloc(size);
  padding.writeUInt32BE(padding.length);
  padding.write("free", 4);
  writeFileSync(output, Buffer.concat([source, padding]));
}

// Copy an MP4 with a trailing `free` box so the result is exactly `bytes` long.
function padWithFreeBox(input, output, bytes) {
  const source = readFileSync(input);
  writeWithFreeBox(source, output, bytes - source.length);
}

// Pad an MP4 in place to at least `minimum` bytes, always adding a box.
function padToAtLeast(file, minimum) {
  const source = readFileSync(file);
  writeWithFreeBox(source, file, Math.max(8, minimum - source.length));
}

module.exports = {
  openSession,
  ffmpeg,
  runFfmpeg,
  testPatternClip,
  padWithFreeBox,
  padToAtLeast,
};
