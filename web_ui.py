def browser_compressor(
    max_clips: int,
    target_bytes: int,
    session_secret: str,
    expires_in_seconds: int,
) -> str:
    """Return the one-use page that encodes videos locally in the browser."""
    template = r'''
<img class="brand" src="/brand/professor-compressor.png"
  alt="Professor Compressor mascot">
<h1>Compress videos for Discord</h1>
<p class="intro">Choose up to __MAX_CLIPS__ videos. Finished videos are formatted
  for Discord's current free upload limit.</p>
<div class="stay-open">
  <span aria-hidden="true">⏱</span>
  <span><strong>Keep this page open and active until it finishes.</strong><br>
  This helps compression run at full speed.</span>
</div>
<details class="performance-help">
  <summary>Keep compression running quickly</summary>
  <p>For maximum speed, move this tab into a separate window and keep part of
    the window visible while compression runs.</p>
  <p>In Chrome, open <code>chrome://settings/performance</code>, add
    <code>professor-compressor.duckdns.org</code> under <strong>Always keep these
    sites active</strong>, and turn off <strong>Energy Saver</strong> while processing.</p>
</details>
<form id="upload">
  <input class="file-input" id="clips" name="clips" type="file"
    accept="video/*,.mkv,.avi,.ts,.m2ts" multiple required>
  <label class="file-picker" for="clips">
    <span class="picker-plus" aria-hidden="true">+</span>
    <span><strong>Choose videos</strong><small>Select up to __MAX_CLIPS__ files</small></span>
  </label>
  <p id="selection" class="selection" aria-live="polite">No videos selected</p>
  <div class="session-note">
    <span>Private, one-use session</span><span id="expires">Expires in --:--</span>
  </div>
  <div id="files" class="file-list" hidden></div>
  <div class="controls">
    <button id="submit" type="submit" disabled>Compress and send to Discord</button>
    <button id="cancel" class="secondary" type="button" hidden>Cancel</button>
  </div>
  <div id="work" class="work" hidden>
    <div id="background-warning" class="background-warning" hidden role="status">
      Compression may slow down or pause while this tab is hidden. Keep this
      window visible for full speed.
    </div>
    <div class="phase-panel">
      <div class="phase-track" aria-label="Processing steps">
        <div id="compress-step" class="phase-step">1. Compressing on this device</div>
        <div id="send-step" class="phase-step">2. Sending to Discord</div>
      </div>
      <h2 id="phase-title" class="phase-title">Preparing videos</h2>
      <p id="phase-copy" class="phase-copy">Nothing has been uploaded yet.</p>
      <progress id="overall-progress" max="100" value="0"></progress>
      <div class="run-meta">
        <span id="elapsed">Elapsed 0:00</span><span id="run-detail">Preparing files</span>
      </div>
    </div>
    <div id="message" class="message" hidden aria-live="assertive"></div>
  </div>
  <p class="privacy"><span aria-hidden="true">🔒</span> Originals are compressed on
    this device. Only finished files are sent through the relay to Discord.</p>
</form>

<script type="module">
import { FFmpeg } from "/assets/ffmpeg-esm/index.js";
import { fetchFile, toBlobURL } from "/assets/util-esm/index.js";

const MAX_CLIPS = __MAX_CLIPS__;
const TARGET_BYTES = __TARGET_BYTES__;
const SESSION_SECRET = "__SESSION_SECRET__";
const SESSION_EXPIRES_SECONDS = __EXPIRES_SECONDS__;
const OUTPUT_TARGET_RATIO = 0.97;
const SINGLE_CORE_BASE = "/assets/core-esm";
const MULTI_CORE_BASE = "/assets/core-mt-esm";

const form = document.getElementById("upload");
const clips = document.getElementById("clips");
const submit = document.getElementById("submit");
const cancelButton = document.getElementById("cancel");
const selection = document.getElementById("selection");
const filesElement = document.getElementById("files");
const work = document.getElementById("work");
const message = document.getElementById("message");
const overallProgress = document.getElementById("overall-progress");
const phaseTitle = document.getElementById("phase-title");
const phaseCopy = document.getElementById("phase-copy");
const compressStep = document.getElementById("compress-step");
const sendStep = document.getElementById("send-step");
const elapsedElement = document.getElementById("elapsed");
const runDetailElement = document.getElementById("run-detail");
const expiresElement = document.getElementById("expires");
const backgroundWarning = document.getElementById("background-warning");
const ORIGINAL_TITLE = document.title;

let ffmpeg = null;
let encoderMode = "single-threaded";
let selectedFiles = [];
let currentState = null;
let currentAttempt = 1;
let lastFfmpegMessage = "";
let startedAt = 0;
let timer = null;
let expiryTimer = null;
let uploadRequest = null;
let wakeLock = null;
let cancelled = false;
let running = false;
let tabProgress = "Preparing files";

function readableSize(bytes) {
  if (bytes < 1024 * 1024) return Math.max(1, Math.round(bytes / 1024)) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function readableTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "--:--";
  const rounded = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(rounded / 60);
  return minutes + ":" + String(rounded % 60).padStart(2, "0");
}

function safeStem(filename, index) {
  const withoutExtension = filename.replace(/\.[^/.]+$/, "");
  const cleaned = withoutExtension.replace(/[^a-zA-Z0-9._ -]/g, "_").trim();
  return cleaned || "clip-" + index;
}

function safeOriginalName(filename, index) {
  return safeStem(filename, index) + ".mp4";
}

function setPhase(phase, title, copy) {
  phaseTitle.textContent = title;
  phaseCopy.textContent = copy;
  compressStep.className = "phase-step";
  sendStep.className = "phase-step";
  if (phase === "compress") compressStep.classList.add("active");
  if (phase === "send") {
    compressStep.classList.add("done");
    sendStep.classList.add("active");
  }
  if (phase === "done") {
    compressStep.classList.add("done");
    sendStep.classList.add("done");
  }
}

function showMessage(text, kind = "error") {
  message.textContent = text;
  message.className = "message " + kind;
  message.hidden = false;
}

function hideMessage() {
  message.hidden = true;
  message.textContent = "";
}

function renderDocumentTitle() {
  if (!running) {
    document.title = ORIGINAL_TITLE;
    return;
  }
  const returnPrompt = document.hidden ? "Return to compressor · " : "";
  document.title = returnPrompt + tabProgress + " · Professor Compressor";
}

function setRunDetail(text) {
  runDetailElement.textContent = text;
  tabProgress = text;
  renderDocumentTitle();
}

async function requestWakeLock() {
  if (!running || document.hidden || !("wakeLock" in navigator)) return;
  if (wakeLock && !wakeLock.released) return;
  try {
    wakeLock = await navigator.wakeLock.request("screen");
    wakeLock.addEventListener("release", () => {
      wakeLock = null;
    });
  } catch (error) {
    console.debug("Screen wake lock was unavailable.", error);
  }
}

async function releaseWakeLock() {
  if (!wakeLock || wakeLock.released) {
    wakeLock = null;
    return;
  }
  try {
    await wakeLock.release();
  } catch (error) {
    console.debug("Screen wake lock release failed.", error);
  }
  wakeLock = null;
}

document.addEventListener("visibilitychange", () => {
  if (!running) return;
  if (document.hidden) {
    backgroundWarning.hidden = false;
    renderDocumentTitle();
    return;
  }
  renderDocumentTitle();
  void requestWakeLock();
});

function updateFile(state, status, percent = null) {
  state.status.textContent = status;
  if (percent !== null) state.progress.value = Math.max(0, Math.min(100, percent));
}

function updateFileSizes(state, finalBytes) {
  state.finalSize = finalBytes;
  const reduction = state.file.size > 0
    ? Math.max(0, (1 - finalBytes / state.file.size) * 100)
    : 0;
  state.meta.textContent = readableSize(state.file.size) + " original · " +
    readableSize(finalBytes) + " finished · " + reduction.toFixed(1) + "% smaller";
}

function renderSelectedFiles(files) {
  filesElement.replaceChildren();
  selectedFiles = files.map((file, index) => {
    const card = document.createElement("div");
    card.className = "file-card";
    const head = document.createElement("div");
    head.className = "file-head";
    const name = document.createElement("span");
    name.className = "file-name";
    name.textContent = file.name;
    const status = document.createElement("span");
    status.className = "file-status";
    status.textContent = "Ready";
    head.append(name, status);
    const progress = document.createElement("progress");
    progress.max = 100;
    progress.value = 0;
    const meta = document.createElement("div");
    meta.className = "file-meta";
    meta.textContent = readableSize(file.size) + " original";
    card.append(head, progress, meta);
    filesElement.append(card);
    return { file, index: index + 1, card, status, progress, meta, finalSize: 0 };
  });
  filesElement.hidden = files.length === 0;
}

function renderSelection() {
  const files = Array.from(clips.files);
  const validCount = files.length >= 1 && files.length <= MAX_CLIPS;
  submit.disabled = !validCount || running;
  selection.className = validCount || files.length === 0 ? "selection" : "selection error";
  if (files.length === 0) {
    selection.textContent = "No videos selected";
  } else if (files.length > MAX_CLIPS) {
    selection.textContent = "Choose no more than " + MAX_CLIPS + " videos.";
  } else {
    const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
    selection.textContent = files.length + (files.length === 1 ? " video" : " videos") +
      " selected · " + readableSize(totalBytes);
  }
  renderSelectedFiles(files);
}

clips.addEventListener("change", renderSelection);

function createEncoder() {
  const encoder = new FFmpeg();
  encoder.on("log", ({ message: line }) => {
    lastFfmpegMessage = line;
    console.debug("[ffmpeg] " + line);
  });
  encoder.on("progress", ({ progress: fraction }) => {
    if (!currentState || cancelled) return;
    const percent = Math.max(0, Math.min(100, Math.round(fraction * 100)));
    const adjustment = currentAttempt > 1 ? "Adjusting size" : "Compressing";
    updateFile(currentState, adjustment + " " + percent + "%", percent);
    const completed = selectedFiles.filter((state) => state.finalSize > 0).length;
    const overall = ((completed + fraction) / selectedFiles.length) * 80;
    overallProgress.value = Math.max(0, Math.min(80, overall));
    setRunDetail("Video " + currentState.index + " of " +
      selectedFiles.length + " · " + percent + "%");
  });
  return encoder;
}

async function loadEncoder() {
  if (cancelled) throw new DOMException("Cancelled", "AbortError");
  const supportsThreads = self.crossOriginIsolated &&
    typeof SharedArrayBuffer !== "undefined";
  if (supportsThreads) {
    ffmpeg = createEncoder();
    try {
      await ffmpeg.load({
        coreURL: await toBlobURL(MULTI_CORE_BASE + "/ffmpeg-core.js", "text/javascript"),
        wasmURL: await toBlobURL(MULTI_CORE_BASE + "/ffmpeg-core.wasm", "application/wasm"),
        workerURL: await toBlobURL(MULTI_CORE_BASE + "/ffmpeg-core.worker.js", "text/javascript")
      });
      if (cancelled) throw new DOMException("Cancelled", "AbortError");
      return "multithreaded";
    } catch (error) {
      if (cancelled) throw error;
      console.warn("Multithreaded encoder unavailable; using fallback.", error);
      ffmpeg.terminate();
    }
  }
  ffmpeg = createEncoder();
  await ffmpeg.load({
    coreURL: await toBlobURL(SINGLE_CORE_BASE + "/ffmpeg-core.js", "text/javascript"),
    wasmURL: await toBlobURL(SINGLE_CORE_BASE + "/ffmpeg-core.wasm", "application/wasm")
  });
  if (cancelled) throw new DOMException("Cancelled", "AbortError");
  return "single-threaded";
}

async function resetEncoder() {
  if (ffmpeg) ffmpeg.terminate();
  ffmpeg = null;
  encoderMode = await loadEncoder();
}

async function videoSignature(file) {
  const bytes = new Uint8Array(await file.slice(0, 512).arrayBuffer());
  const ascii = (start, length) => String.fromCharCode(...bytes.slice(start, start + length));
  if (bytes.length >= 16 && ascii(4, 4) === "ftyp") {
    const boxLength = Math.min(bytes.length, new DataView(bytes.buffer).getUint32(0));
    const allowedBrands = new Set([
      "avc1", "dash", "iso2", "iso3", "iso4", "iso5", "iso6", "isom",
      "M4V ", "mp41", "mp42", "MSNV", "qt  "
    ]);
    for (let offset = 8; offset + 4 <= boxLength; offset += 4) {
      if (allowedBrands.has(ascii(offset, 4))) return "mp4";
    }
    throw new Error(file.name + " uses an unsupported media-container signature. " +
      "Choose a standard MP4, MOV, WebM, MKV, AVI, MPEG, OGG, FLV, or TS video.");
  }
  if (bytes.length >= 4 && bytes[0] === 0x1a && bytes[1] === 0x45 &&
      bytes[2] === 0xdf && bytes[3] === 0xa3) return "webm/mkv";
  if (bytes.length >= 12 && ascii(0, 4) === "RIFF" && ascii(8, 4) === "AVI ") return "avi";
  if (bytes.length >= 4 && ascii(0, 4) === "OggS") return "ogg";
  if (bytes.length >= 3 && ascii(0, 3) === "FLV") return "flv";
  if (bytes.length >= 4 && bytes[0] === 0x00 && bytes[1] === 0x00 &&
      bytes[2] === 0x01 && [0xba, 0xb3].includes(bytes[3])) return "mpeg";
  if (bytes.length >= 377 && bytes[0] === 0x47 && bytes[188] === 0x47) return "ts";
  throw new Error(file.name + " does not have a recognized video signature. " +
    "Choose an MP4, MOV, WebM, MKV, AVI, MPEG, OGG, FLV, or TS video.");
}

function durationOf(file) {
  return new Promise((resolve, reject) => {
    const video = document.createElement("video");
    const url = URL.createObjectURL(file);
    video.preload = "metadata";
    video.onloadedmetadata = () => {
      const duration = video.duration;
      URL.revokeObjectURL(url);
      if (Number.isFinite(duration) && duration > 0) resolve(duration);
      else reject(new Error("Could not determine the duration of " + file.name +
        ". Try converting it to MP4 first."));
    };
    video.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("Your browser could not read " + file.name +
        ". Try converting it to MP4 first."));
    };
    video.src = url;
  });
}

async function removeVirtualFile(name) {
  if (!ffmpeg) return;
  try { await ffmpeg.deleteFile(name); } catch (_) { /* Optional file. */ }
}

async function compressOne(state) {
  const file = state.file;
  const format = await videoSignature(file);
  if (file.size <= TARGET_BYTES && format === "mp4") {
    updateFile(state, "Already fits", 100);
    updateFileSizes(state, file.size);
    return { blob: file, name: safeOriginalName(file.name, state.index), state };
  }
  const duration = await durationOf(file);
  const audioKbps = 96;
  const usableBits = TARGET_BYTES * 8 * OUTPUT_TARGET_RATIO;
  let videoKbps = Math.floor(usableBits / duration / 1000 - audioKbps);
  if (videoKbps < 100) {
    throw new Error(file.name + " is too long to fit at a usable quality. " +
      "Trim the video into shorter clips and try again.");
  }
  const inputName = "input-" + state.index + ".video";
  const outputName = "output-" + state.index + ".mp4";
  const scale = "fps=fps='min(source_fps,30)'," +
    "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease," +
    "scale=trunc(iw/2)*2:trunc(ih/2)*2";
  async function encode(bitrate) {
    lastFfmpegMessage = "";
    return ffmpeg.exec([
      "-i", inputName, "-map", "0:v:0", "-map", "0:a:0?",
      "-c:v", "libx264", "-threads", encoderMode === "multithreaded" ? "4" : "1",
      "-preset", "veryfast", "-b:v", bitrate + "k", "-maxrate", bitrate + "k",
      "-bufsize", (bitrate * 2) + "k", "-vf", scale, "-pix_fmt", "yuv420p",
      "-c:a", "aac", "-b:a", audioKbps + "k", "-movflags", "+faststart", outputName
    ]);
  }
  try {
    await ffmpeg.writeFile(inputName, await fetchFile(file));
    currentAttempt = 1;
    let exitCode = await encode(videoKbps);
    if (cancelled) throw new DOMException("Cancelled", "AbortError");
    if (exitCode !== 0) throw new Error("The encoder could not read " + file.name +
      ". The video may use an unsupported or damaged codec.");
    let output = await ffmpeg.readFile(outputName);
    if (output.byteLength > TARGET_BYTES) {
      const correction = TARGET_BYTES / output.byteLength;
      videoKbps = Math.max(100, Math.floor(
        videoKbps * correction * OUTPUT_TARGET_RATIO
      ));
      await removeVirtualFile(outputName);
      currentAttempt = 2;
      updateFile(state, "Adjusting final size", 0);
      exitCode = await encode(videoKbps);
      if (cancelled) throw new DOMException("Cancelled", "AbortError");
      if (exitCode !== 0) throw new Error("The final size adjustment failed for " +
        file.name + ". Try that video by itself.");
      output = await ffmpeg.readFile(outputName);
    }
    if (output.byteLength > TARGET_BYTES) {
      throw new Error(file.name + " could not be reduced below Discord's limit. " +
        "Trim it into a shorter clip and try again.");
    }
    const blob = new Blob([output], { type: "video/mp4" });
    updateFile(state, "Compressed", 100);
    updateFileSizes(state, blob.size);
    return { blob, name: safeStem(file.name, state.index) + "-compressed.mp4", state };
  } finally {
    await removeVirtualFile(inputName);
    await removeVirtualFile(outputName);
  }
}

async function compressWithRetry(state) {
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      currentState = state;
      updateFile(state, attempt === 1 ? "Starting" : "Retrying automatically", 0);
      return await compressOne(state);
    } catch (error) {
      if (cancelled || error.name === "AbortError") throw error;
      console.error("Compression attempt failed", error, lastFfmpegMessage);
      if (attempt === 2 || /too long|recognized video signature|Could not determine/.test(error.message)) {
        updateFile(state, "Needs attention", 0);
        throw error;
      }
      updateFile(state, "Retrying encoder", 0);
      phaseCopy.textContent = "The encoder stopped unexpectedly. Reloading it and retrying " +
        state.file.name + " once.";
      await resetEncoder();
    }
  }
}

function xhrUpload(results) {
  return new Promise((resolve, reject) => {
    const data = new FormData();
    for (const result of results) data.append("clips", result.blob, result.name);
    const request = new XMLHttpRequest();
    uploadRequest = request;
    request.open("POST", window.location.href);
    request.responseType = "json";
    request.setRequestHeader("X-Upload-Session", SESSION_SECRET);
    request.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      const fraction = event.loaded / event.total;
      overallProgress.value = 80 + fraction * 18;
      const resultBytes = results.reduce((sum, result) => sum + result.blob.size, 0);
      const sentBytes = fraction * resultBytes;
      let previousBytes = 0;
      for (const result of results) {
        const fileFraction = Math.max(0, Math.min(1,
          (sentBytes - previousBytes) / result.blob.size));
        if (fileFraction >= 1) updateFile(result.state, "Sent to relay", 100);
        else if (fileFraction > 0) updateFile(result.state,
          "Sending " + Math.round(fileFraction * 100) + "%", fileFraction * 100);
        else updateFile(result.state, "Waiting to send", 0);
        previousBytes += result.blob.size;
      }
      phaseCopy.textContent = "Uploading finished files through the secure relay: " +
        Math.round(fraction * 100) + "%";
      setRunDetail("Sending " + Math.round(fraction * 100) + "%");
    });
    request.upload.addEventListener("load", () => {
      overallProgress.value = 98;
      phaseCopy.textContent = "Finished files reached the relay. Waiting for Discord to accept them.";
      setRunDetail("Waiting for Discord");
      for (const result of results) updateFile(result.state, "Waiting for Discord", 100);
    });
    request.addEventListener("load", () => {
      const body = request.response || {};
      if (request.status >= 200 && request.status < 300 && body.ok) resolve(body);
      else reject(Object.assign(new Error(body.error || "The relay rejected the upload."), {
        status: request.status, code: body.code, retryAfter: body.retry_after
      }));
    });
    request.addEventListener("error", () => reject(Object.assign(
      new Error("The network connection was interrupted."), { status: 0 }
    )));
    request.addEventListener("abort", () => reject(new DOMException("Cancelled", "AbortError")));
    request.send(data);
  });
}

async function uploadWithRetry(results) {
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const response = await xhrUpload(results);
      uploadRequest = null;
      return response;
    } catch (error) {
      uploadRequest = null;
      if (cancelled || error.name === "AbortError") throw error;
      const retryable = error.status === 0 || error.status === 429 ||
        error.status === 500 || error.status === 503;
      if (!retryable || attempt === 3) throw error;
      const waitSeconds = Number(error.retryAfter) || Math.pow(2, attempt);
      for (let remaining = waitSeconds; remaining > 0; remaining -= 1) {
        if (cancelled) throw new DOMException("Cancelled", "AbortError");
        phaseCopy.textContent = "Upload interrupted. Retrying automatically in " +
          remaining + " second" + (remaining === 1 ? "" : "s") + ".";
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    }
  }
}

function friendlyError(error) {
  if (error.code === "session_expired" || error.code === "session_used") {
    return "This private session expired or was already used. Return to Discord and run /compress again.";
  }
  if (error.code === "discord_delivery_failed") return error.message;
  if (error.status === 0) {
    return "The upload could not reach the relay after three attempts. Check your connection, keep this page open, and try again.";
  }
  if (/memory|allocation|out of bounds/i.test(error.message || "")) {
    return "This browser ran out of memory. Close other tabs and try fewer or smaller videos.";
  }
  return error.message || "The operation failed. Try fewer videos or reload the page and run /compress again.";
}

function startClock() {
  clearInterval(timer);
  timer = setInterval(() => {
    elapsedElement.textContent = "Elapsed " + readableTime((performance.now() - startedAt) / 1000);
  }, 500);
}

function stopClock() {
  clearInterval(timer);
  timer = null;
}

function finishRun() {
  running = false;
  backgroundWarning.hidden = true;
  void releaseWakeLock();
  renderDocumentTitle();
  cancelButton.hidden = true;
  clips.disabled = false;
  submit.disabled = false;
  submit.textContent = "Try again";
  stopClock();
}

cancelButton.addEventListener("click", () => {
  if (!running) return;
  cancelled = true;
  if (uploadRequest) uploadRequest.abort();
  if (ffmpeg) {
    ffmpeg.terminate();
    ffmpeg = null;
  }
  for (const state of selectedFiles) {
    if (!state.finalSize) updateFile(state, "Cancelled", 0);
  }
  setPhase("compress", "Cancelled", "No additional files will be sent. You can start again on this page.");
  setRunDetail("Cancelled");
  showMessage("Cancelled. Your original videos were not changed.", "error");
  finishRun();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (selectedFiles.length < 1 || selectedFiles.length > MAX_CLIPS) {
    showMessage("Choose between 1 and " + MAX_CLIPS + " videos.");
    return;
  }
  cancelled = false;
  running = true;
  submit.disabled = true;
  clips.disabled = true;
  cancelButton.hidden = false;
  work.hidden = false;
  hideMessage();
  startedAt = performance.now();
  overallProgress.value = 0;
  backgroundWarning.hidden = true;
  setRunDetail("Checking files");
  await requestWakeLock();
  startClock();
  for (const state of selectedFiles) {
    state.finalSize = 0;
    state.meta.textContent = readableSize(state.file.size) + " original";
    updateFile(state, "Checking file", 0);
  }
  try {
    const formats = await Promise.all(selectedFiles.map((state) => videoSignature(state.file)));
    const needsEncoder = selectedFiles.some((state, index) =>
      state.file.size > TARGET_BYTES || formats[index] !== "mp4");
    setPhase("compress", "Compressing on your device",
      "Originals remain on this device. Nothing is sent until every video is ready.");
    if (needsEncoder && !ffmpeg) encoderMode = await loadEncoder();
    const results = [];
    for (const state of selectedFiles) {
      if (cancelled) throw new DOMException("Cancelled", "AbortError");
      results.push(await compressWithRetry(state));
    }
    currentState = null;
    overallProgress.value = 80;
    setPhase("send", "Sending finished files",
      "Compression is complete. Finished MP4 files are now being sent through the relay to Discord.");
    const response = await uploadWithRetry(results);
    for (const result of results) updateFile(result.state, "Delivered", 100);
    overallProgress.value = 100;
    setRunDetail("Complete");
    setPhase("done", "Delivered to Discord", response.message);
    showMessage("Compression complete. You can close this page and return to Discord.", "success");
    clips.disabled = true;
    submit.hidden = true;
    cancelButton.hidden = true;
    running = false;
    backgroundWarning.hidden = true;
    void releaseWakeLock();
    renderDocumentTitle();
    stopClock();
  } catch (error) {
    if (cancelled || error.name === "AbortError") return;
    console.error(error);
    setPhase("compress", "Action needed", "The process stopped before delivery completed.");
    setRunDetail("Stopped");
    showMessage(friendlyError(error));
    finishRun();
  }
});

const expiryStartedAt = Date.now();
function updateExpiry() {
  const elapsed = Math.floor((Date.now() - expiryStartedAt) / 1000);
  const remaining = Math.max(0, SESSION_EXPIRES_SECONDS - elapsed);
  expiresElement.textContent = remaining > 0
    ? "Expires in " + readableTime(remaining)
    : "Session expired";
  if (remaining === 0) clearInterval(expiryTimer);
}
updateExpiry();
expiryTimer = setInterval(updateExpiry, 1000);
</script>
'''
    return (
        template.replace("__MAX_CLIPS__", str(max_clips))
        .replace("__TARGET_BYTES__", str(target_bytes))
        .replace("__SESSION_SECRET__", session_secret)
        .replace("__EXPIRES_SECONDS__", str(expires_in_seconds))
    )
