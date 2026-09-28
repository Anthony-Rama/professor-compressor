import json

from .assets import ASSET_PREFIX


def _json_script_value(value: object) -> str:
    """Serialize data without allowing it to terminate an inline script."""
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def browser_compressor(
    max_clips: int,
    target_bytes: int,
    max_batch_bytes: int,
    session_secret: str,
    expires_in_seconds: int,
    *,
    reopened: bool = False,
) -> str:
    """Return the browser-bound page that encodes videos locally."""
    template = r"""
<img class="brand" src="/brand/professor-compressor.png"
  alt="Professor Compressor mascot">
<h1>Compress videos for Discord</h1>
<p class="intro">Choose up to __MAX_CLIPS__ videos. Finished videos are formatted
  for Discord's current upload limit.</p>
__RECOVERY_NOTICE__
<div class="stay-open">
  <span aria-hidden="true">⏱</span>
  <span><strong>Keep this page open and active until it finishes.</strong><br>
  This helps compression run at full speed.</span>
</div>
<details class="performance-help">
  <summary>Keep compression running quickly</summary>
  <p>For maximum speed, move this tab into a separate window and keep part of
    the window visible while compression runs.</p>
  <p>In Chrome, open <code>chrome://settings/performance</code>, add this site
    under <strong>Always keep these sites active</strong>, and turn off
    <strong>Energy Saver</strong> while processing.</p>
</details>
<form id="upload">
  <input class="file-input" id="clips" name="clips" type="file"
    accept="video/*,.mp4,.mov,.m4v,.webm,.mkv,.avi,.mpeg,.mpg,.ogv,.ogg,.flv,.ts,.mts,.m2ts,.3gp,.3g2,.wmv,.asf" multiple required>
  <label class="file-picker" id="drop-zone" for="clips">
    <span class="picker-plus" aria-hidden="true">+</span>
    <span><strong>Choose videos or drag them here</strong><small>Select up to __MAX_CLIPS__ files; a new selection replaces the previous one</small></span>
  </label>
  <p id="selection" class="selection" aria-live="polite">No videos selected</p>
  <div class="session-note">
    <span>Private session, bound to this browser</span><span id="expires">Expires in --:--</span>
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
  </div>
  <div id="message" class="message" hidden aria-live="assertive"></div>
  <div id="downloads" class="feedback-actions" hidden aria-label="Save prepared videos"></div>
  <div id="feedback-actions" class="feedback-actions" hidden>
    <a id="feedback-link" class="feedback-link"
      href="mailto:professorcompressor.support@gmail.com?subject=Professor%20Compressor%20feedback">Share feedback</a>
    <a class="support-link"
      href="mailto:professorcompressor.support@gmail.com?subject=Professor%20Compressor%20support">Email support</a>
    <a class="support-link" href="https://discord.com/invite/32RWwNWyEH">Join support server</a>
    <p>Optional: email links open your email app; the server link opens Discord.
      Please don't share private videos or session links.</p>
  </div>
  <details class="performance-help format-help">
    <summary>Supported video formats</summary>
    <p>MP4, MOV, M4V, WebM, MKV, AVI, MPEG, OGV, FLV, TS, MTS, M2TS,
      3GP, 3G2, WMV, and ASF. Support also depends on the codec inside the file.
      AV1 conversion is not supported by this encoder. Damaged, encrypted,
      or unsupported videos cannot be converted.</p>
  </details>
  <p class="privacy"><span aria-hidden="true">🔒</span> Compression runs on
    this device. Only finished files are sent through the relay to Discord.
    MP4s that already fit may be sent unchanged.<br>
    <a href="/privacy" target="_blank" rel="noopener">Privacy Policy</a>
    <span aria-hidden="true"> · </span>
    <a href="/terms" target="_blank" rel="noopener">Terms of Service</a></p>
</form>

<script type="module">
import { FFmpeg } from "__ASSET_PREFIX__/ffmpeg-esm/index.js";
import { fetchFile } from "__ASSET_PREFIX__/util-esm/index.js";

const MAX_CLIPS = __MAX_CLIPS__;
const TARGET_BYTES = __TARGET_BYTES__;
const MAX_BATCH_BYTES = __MAX_BATCH_BYTES__;
const SESSION_SECRET = __SESSION_SECRET__;
const SESSION_EXPIRES_SECONDS = __EXPIRES_SECONDS__;
const OUTPUT_TARGET_RATIO = 0.97;
const SINGLE_CORE_BASE = "__ASSET_PREFIX__/core-esm";
const MULTI_CORE_BASE = "__ASSET_PREFIX__/core-mt-esm";

const form = document.getElementById("upload");
const clips = document.getElementById("clips");
const submit = document.getElementById("submit");
const cancelButton = document.getElementById("cancel");
const selection = document.getElementById("selection");
const filesElement = document.getElementById("files");
const work = document.getElementById("work");
const message = document.getElementById("message");
const feedbackActions = document.getElementById("feedback-actions");
const feedbackLink = document.getElementById("feedback-link");
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
let runStage = "Browser compression";
let sessionDeadline = Date.now() + SESSION_EXPIRES_SECONDS * 1000;
let heartbeatTimer = null;
let preparedResults = null;
let recoveryURLs = [];
let pauseRequest = Promise.resolve();
let deliveryComplete = false;
let pageIsLeaving = false;
let progressQueue = Promise.resolve();

function reportProgress(event, count) {
  const body = count === undefined ? {event} : {event, count};
  // Observability must never hold up local compression or expose file details.
  const send = () => fetch(window.location.pathname + "/progress", {
    method: "POST",
    headers: {"X-Upload-Session": SESSION_SECRET, "Content-Type": "application/json"},
    body: JSON.stringify(body),
    cache: "no-store",
    keepalive: true,
    signal: event === "page_left" ? undefined : AbortSignal.timeout(5000)
  }).catch(() => {});
  if (event === "page_left") {
    void send();
  } else {
    progressQueue = progressQueue.then(() => pageIsLeaving ? undefined : send());
  }
}

window.addEventListener("pagehide", () => {
  pageIsLeaving = true;
  if (!deliveryComplete && !sessionExpired()) reportProgress("page_left");
});
window.addEventListener("pageshow", () => { pageIsLeaving = false; });

function sessionExpired() { return Date.now() >= sessionDeadline; }

function clearPreparedFiles() {
  preparedResults = null;
  for (const url of recoveryURLs) URL.revokeObjectURL(url);
  recoveryURLs = [];
  document.getElementById("downloads").replaceChildren();
  document.getElementById("downloads").hidden = true;
}

function showPreparedFiles() {
  const downloads = document.getElementById("downloads");
  if (!preparedResults?.length || recoveryURLs.length) return;
  const note = document.createElement("p");
  note.textContent = "Your prepared files are still on this device. Save them before closing this page.";
  downloads.append(note);
  for (const result of preparedResults) {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(result.blob);
    recoveryURLs.push(link.href);
    link.download = result.name;
    link.className = "support-link";
    link.textContent = "Save video " + result.state.index;
    downloads.append(link);
  }
  downloads.hidden = false;
}

async function sessionRequest(action = "status") {
  let response;
  try {
    response = await fetch(window.location.pathname + "/" + action, {
      method: action === "status" ? "GET" : "POST",
      headers: {"X-Upload-Session": SESSION_SECRET},
      cache: "no-store", signal: AbortSignal.timeout(10000)
    });
  } catch (_) {
    throw Object.assign(new Error("Could not confirm delivery status. Keep this page open and try again; check Discord before starting a new session."), {status: 0});
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(new Error(body.error || "Session check failed."), {status: response.status, code: body.code});
  if (!body.ok && !body.ready && !body.pending) throw Object.assign(new Error("The relay returned an unreadable status."), {status: 0});
  if (body.ready) sessionDeadline = Date.now() + body.expires_in * 1000;
  return body;
}

function stopHeartbeat() {
  clearInterval(heartbeatTimer);
  heartbeatTimer = null;
}

async function keepSessionAlive() {
  try {
    await sessionRequest("heartbeat");
    updateExpiry();
  } catch (error) {
    if (error.status === 410 || error.status === 403) sessionDeadline = 0;
    updateExpiry();
  }
}

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

function outputTargetBytes() {
  const batchTargetBytes = Math.floor(
    MAX_BATCH_BYTES / Math.max(1, selectedFiles.length)
  );
  return Math.min(TARGET_BYTES, batchTargetBytes);
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

function showFeedback(outcome) {
  feedbackLink.href = "mailto:professorcompressor.support@gmail.com?subject=" +
    encodeURIComponent("Professor Compressor feedback (" + outcome + ")");
  feedbackActions.hidden = false;
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
    const acquired = await navigator.wakeLock.request("screen");
    if (!running) { await acquired.release(); return; }
    wakeLock = acquired;
    acquired.addEventListener("release", () => {
      if (wakeLock === acquired) wakeLock = null;
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
  clearPreparedFiles();
  const files = Array.from(clips.files);
  const validCount = files.length >= 1 && files.length <= MAX_CLIPS;
  submit.disabled = !validCount || running || sessionExpired();
  submit.textContent = "Compress and send to Discord";
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
  if (validCount) reportProgress("selected", files.length);
}

clips.addEventListener("change", renderSelection);

const dropZone = document.getElementById("drop-zone");
let dragDepth = 0;
function clearDrag() {
  dragDepth = 0;
  dropZone.classList.remove("drag-over");
}
// Prevent dropped files from navigating away from this single-use session.
document.addEventListener("dragover", (event) => event.preventDefault());
document.addEventListener("drop", (event) => {
  event.preventDefault();
  clearDrag();
});
dropZone.addEventListener("dragenter", (event) => {
  event.preventDefault();
  if (running || clips.disabled) return;
  dragDepth += 1;
  dropZone.classList.add("drag-over");
});
dropZone.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) clearDrag();
});
dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect =
    running || clips.disabled ? "none" : "copy";
});
dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  clearDrag();
  if (running || clips.disabled || !event.dataTransfer) return;
  const items = Array.from(event.dataTransfer.items || []);
  if (items.some((item) => item.webkitGetAsEntry?.()?.isDirectory)) {
    showMessage("Drop video files, not folders.", "error");
    return;
  }
  const files = Array.from(event.dataTransfer.files);
  if (!files.length) return;
  if (files.length > MAX_CLIPS) {
    showMessage("Choose no more than " + MAX_CLIPS + " videos.", "error");
    return;
  }
  clips.files = event.dataTransfer.files;
  hideMessage();
  renderSelection();
});

function createEncoder() {
  const encoder = new FFmpeg();
  encoder.on("log", ({ message: line }) => {
    lastFfmpegMessage = line;
  });
  let lastProgressState = null;
  let lastProgressAttempt = 0;
  let lastProgressPercent = -1;
  encoder.on("progress", ({ progress: fraction }) => {
    if (!currentState || cancelled) return;
    const percent = Math.max(0, Math.min(100, Math.round(fraction * 100)));
    if (currentState === lastProgressState && currentAttempt === lastProgressAttempt &&
        percent === lastProgressPercent) return;
    lastProgressState = currentState;
    lastProgressAttempt = currentAttempt;
    lastProgressPercent = percent;
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

async function startEncoder(config) {
  const encoder = ffmpeg;
  let timer;
  try {
    await Promise.race([
      encoder.load(config),
      new Promise((_, reject) => {
        timer = setTimeout(() => {
          const error = new Error("The video engine took too long to start. Check your connection and try again.");
          error.code = "encoder_start_timeout";
          reject(error);
        }, 300000);
      })
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function loadEncoder() {
  if (cancelled) throw new DOMException("Cancelled", "AbortError");
  for (const state of selectedFiles) {
    if (!state.finalSize) updateFile(state, "Loading video engine", 0);
  }
  setRunDetail("Loading video engine");
  const supportsThreads = self.crossOriginIsolated &&
    typeof SharedArrayBuffer !== "undefined";
  if (supportsThreads) {
    ffmpeg = createEncoder();
    try {
      setRunDetail("Starting video engine");
      await startEncoder({
        coreURL: MULTI_CORE_BASE + "/ffmpeg-core.js",
        wasmURL: MULTI_CORE_BASE + "/ffmpeg-core.wasm",
        workerURL: MULTI_CORE_BASE + "/ffmpeg-core.worker.js"
      });
      if (cancelled) throw new DOMException("Cancelled", "AbortError");
      return "multithreaded";
    } catch (error) {
      if (cancelled) throw error;
      console.warn("Multithreaded encoder unavailable; using fallback.", error);
      ffmpeg.terminate();
      if (error.code === "encoder_start_timeout") {
        ffmpeg = null;
        throw error;
      }
    }
  }
  ffmpeg = createEncoder();
  try {
    for (const state of selectedFiles) {
      if (!state.finalSize) updateFile(state, "Loading compatibility engine", 0);
    }
    setRunDetail("Loading compatibility engine");
    await startEncoder({
      coreURL: SINGLE_CORE_BASE + "/ffmpeg-core.js",
      wasmURL: SINGLE_CORE_BASE + "/ffmpeg-core.wasm"
    });
  } catch (error) {
    if (ffmpeg) ffmpeg.terminate();
    ffmpeg = null;
    throw error;
  }
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
    const majorBrand = ascii(8, 4);
    if (majorBrand === "qt  ") return "mov";
    if (/^3g/.test(majorBrand)) return "3gp";
    const boxLength = Math.min(bytes.length, new DataView(bytes.buffer).getUint32(0));
    const allowedBrands = new Set([
      "avc1", "dash", "iso2", "iso3", "iso4", "iso5", "iso6", "isom",
      "M4V ", "mp41", "mp42", "MSNV"
    ]);
    for (let offset = 8; offset + 4 <= boxLength; offset += 4) {
      if (allowedBrands.has(ascii(offset, 4))) return "mp4";
    }
    throw new Error(file.name + " uses an unsupported media-container signature. " +
      "Choose a supported video format listed below the upload controls.");
  }
  if (bytes.length >= 4 && bytes[0] === 0x1a && bytes[1] === 0x45 &&
      bytes[2] === 0xdf && bytes[3] === 0xa3) return "webm/mkv";
  if (bytes.length >= 12 && ascii(0, 4) === "RIFF" && ascii(8, 4) === "AVI ") return "avi";
  if (bytes.length >= 4 && ascii(0, 4) === "OggS") return "ogg";
  if (bytes.length >= 3 && ascii(0, 3) === "FLV") return "flv";
  const asfHeader = [0x30,0x26,0xb2,0x75,0x8e,0x66,0xcf,0x11,0xa6,0xd9,0x00,0xaa,0x00,0x62,0xce,0x6c];
  if (asfHeader.every((value, index) => bytes[index] === value)) return "asf/wmv";
  if (bytes.length >= 4 && bytes[0] === 0x00 && bytes[1] === 0x00 &&
      bytes[2] === 0x01 && [0xba, 0xb3].includes(bytes[3])) return "mpeg";
  if (bytes.length >= 377 && bytes[0] === 0x47 && bytes[188] === 0x47) return "ts";
  if (bytes.length >= 389 && bytes[4] === 0x47 && bytes[196] === 0x47 && bytes[388] === 0x47) return "m2ts";
  throw new Error(file.name + " does not have a recognized video signature. " +
    "Choose a supported video format listed below the upload controls.");
}

async function removeVirtualFile(name) {
  if (!ffmpeg) return;
  try { await ffmpeg.deleteFile(name); } catch (_) { /* Optional file. */ }
}

async function compressOne(state) {
  const file = state.file;
  const format = await videoSignature(file);
  const effectiveTargetBytes = outputTargetBytes();
  if (file.size <= effectiveTargetBytes && format === "mp4") {
    updateFile(state, "Already fits", 100);
    updateFileSizes(state, file.size);
    return { blob: file, name: safeOriginalName(file.name, state.index), state };
  }
  // Probe using the same decoder as conversion rather than HTML video support.
  // Browsers cannot read metadata for many otherwise decodable MKV/AVI files.
  const inputName = "input-" + state.index + ".video";
  const outputName = "output-" + state.index + ".mp4";
  const probeName = "probe-" + state.index + ".json";
  try {
    await ffmpeg.writeFile(inputName, await fetchFile(file));
    updateFile(state, "Reading video metadata", 0);
    await ffmpeg.ffprobe([
      "-v", "error", "-show_error", "-show_entries", "format=duration:stream=codec_type,duration",
      "-of", "json", inputName, "-o", probeName
    ], 30000);
    if (cancelled) throw new DOMException("Cancelled", "AbortError");
    // The pinned wasm core returns -1 even for successful probes; validate its
    // structured output instead of relying on that exit status.
    let metadata;
    try {
      metadata = JSON.parse(await ffmpeg.readFile(probeName, "utf8"));
      if (metadata.error || !Array.isArray(metadata.streams)) throw new Error("Invalid metadata");
    } catch (error) {
      if (cancelled) throw new DOMException("Cancelled", "AbortError");
      throw new Error("Could not determine video metadata for " + file.name +
        ". The file may be damaged or unsupported by this encoder.");
    }
    const videoStream = metadata.streams.find((stream) => stream.codec_type === "video");
    if (!videoStream) throw new Error("No video stream found in " + file.name + ". Choose a video, not an audio-only file.");
    const duration = [metadata.format?.duration, videoStream.duration]
      .map(Number).find((value) => Number.isFinite(value) && value > 0);
    if (!duration) throw new Error(
      "Could not determine the duration of " + file.name + ". Re-export the clip with a finite duration.");
    const audioKbps = 96;
    const usableBits = effectiveTargetBytes * 8 * OUTPUT_TARGET_RATIO;
    let videoKbps = Math.floor(usableBits / duration / 1000 - audioKbps);
    if (videoKbps < 100) {
      throw new Error(file.name + " is too long to fit at a usable quality. " +
        "Trim the video into shorter clips and try again.");
    }
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
    currentAttempt = 1;
    let exitCode = await encode(videoKbps);
    if (cancelled) throw new DOMException("Cancelled", "AbortError");
    if (exitCode !== 0) throw new Error("The encoder could not read " + file.name +
      ". The video may use an unsupported or damaged codec.");
    let output = await ffmpeg.readFile(outputName);
    if (output.byteLength > effectiveTargetBytes) {
      const correction = effectiveTargetBytes / output.byteLength;
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
    if (output.byteLength > effectiveTargetBytes) {
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
    await removeVirtualFile(probeName);
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
      if (attempt === 2 || /too long|recognized video signature|Could not determine|No video stream/.test(error.message)) {
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
    request.timeout = 600000;
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
      if (request.status >= 200 && request.status < 300 && (body.ok || body.pending)) resolve(body);
      else reject(Object.assign(new Error(body.error || "The relay rejected the upload."), {
        status: request.status, code: body.code, retryAfter: body.retry_after
      }));
    });
    request.addEventListener("error", () => reject(Object.assign(
      new Error("The network connection was interrupted."), { status: 0 }
    )));
    request.addEventListener("abort", () => reject(new DOMException("Cancelled", "AbortError")));
    request.addEventListener("timeout", () => reject(Object.assign(
      new Error("Sending timed out. Check the Discord channel before starting another session."),
      { status: 504 }
    )));
    request.send(data);
  });
}

async function uploadWithRetry(results) {
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      // Reconcile an earlier upload before sending bytes again.
      let response = await sessionRequest();
      if (response.ready) response = await xhrUpload(results);
      uploadRequest = null;
      const deadline = performance.now() + 600000;
      while (response.pending) {
        setRunDetail("Waiting for Discord");
        phaseCopy.textContent = "The relay is processing this batch. You do not need to send it again.";
        if (performance.now() > deadline) throw Object.assign(new Error("Delivery is still pending. Check its status again without recompressing."), {status: 0});
        await new Promise(resolve => setTimeout(resolve, 2000));
        response = await sessionRequest();
      }
      if (!response.ok) throw Object.assign(new Error("The upload was interrupted before it was queued."), {status: 0});
      return response;
    } catch (error) {
      uploadRequest = null;
      if (cancelled || error.name === "AbortError") throw error;
      const retryable = error.status === 0 || error.status === 408 || error.status === 429 ||
        error.status === 500 || error.status === 503 || error.status === 504 ||
        (error.status === 502 && error.code !== "discord_delivery_failed");
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

async function reportBrowserFailure(stage) {
  try {
    await fetch(window.location.pathname.replace(/\/$/, "") + "/failure", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Upload-Session": SESSION_SECRET
      },
      body: JSON.stringify({ stage }),
      cache: "no-store",
      credentials: "same-origin",
      keepalive: true
    });
  } catch (reportError) {
    console.debug("The failure notification could not reach the relay.", reportError);
  }
}

function friendlyError(error) {
  if (error.code === "session_expired" || error.code === "session_used") {
    return "This session expired or the service restarted. Check Discord before creating a new session. You can save any prepared files below.";
  }
  if (error.code === "discord_delivery_failed") return error.message;
  if (error.status === 0) {
    return "Delivery could not be confirmed. Check your connection and Discord, then use Check delivery / retry. Prepared files will not be recompressed.";
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
  stopHeartbeat();
  pauseRequest = sessionRequest("pause").catch(() => {});
  backgroundWarning.hidden = true;
  void releaseWakeLock();
  renderDocumentTitle();
  cancelButton.hidden = true;
  clips.disabled = preparedResults !== null;
  submit.disabled = sessionExpired() && !preparedResults;
  submit.textContent = preparedResults ? "Check delivery / retry" : "Try again";
  showPreparedFiles();
  stopClock();
}

cancelButton.addEventListener("click", () => {
  if (!running) return;
  cancelled = true;
  reportProgress("cancelled");
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
  cancelButton.disabled = true;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (running) return;
  if (sessionExpired() && !preparedResults) {
    updateExpiry();
    showMessage("This session expired. Run /compress for a new link before processing videos.");
    return;
  }
  if (selectedFiles.length < 1 || selectedFiles.length > MAX_CLIPS) {
    showMessage("Choose between 1 and " + MAX_CLIPS + " videos.");
    return;
  }
  reportProgress("started");
  cancelled = false;
  running = true;
  submit.disabled = true;
  clips.disabled = true;
  cancelButton.hidden = Boolean(preparedResults);
  cancelButton.disabled = false;
  work.hidden = false;
  hideMessage();
  feedbackActions.hidden = true;
  startedAt = performance.now();
  runStage = preparedResults ? "Relay upload or Discord delivery" : "Browser compression";
  overallProgress.value = 0;
  backgroundWarning.hidden = true;
  setRunDetail("Checking files");
  void requestWakeLock();
  startClock();
  if (!preparedResults) {
    for (const state of selectedFiles) {
      state.finalSize = 0;
      state.meta.textContent = readableSize(state.file.size) + " original";
      updateFile(state, "Checking file", 0);
    }
  }
  try {
    await pauseRequest;
    if (cancelled) throw new DOMException("Cancelled", "AbortError");
    if (!preparedResults) {
      const session = await sessionRequest("heartbeat");
      if (cancelled) throw new DOMException("Cancelled", "AbortError");
      if (!session.ready) throw new Error("This session already has a delivery in progress.");
      stopHeartbeat();
      heartbeatTimer = setInterval(() => { void keepSessionAlive(); }, 60000);
      const formats = await Promise.all(selectedFiles.map((state) => videoSignature(state.file)));
      if (cancelled) throw new DOMException("Cancelled", "AbortError");
      const effectiveTargetBytes = outputTargetBytes();
      const needsEncoder = selectedFiles.some((state, index) =>
        state.file.size > effectiveTargetBytes || formats[index] !== "mp4");
      if (needsEncoder && !ffmpeg) {
        setPhase("compress", "Loading video engine",
          "The browser is preparing its video tools. A first or uncached visit may take a few minutes on a slow connection; your video stays on this device.");
        encoderMode = await loadEncoder();
      }
      setPhase("compress", "Compressing on your device",
        "Compression runs locally. Nothing is sent until every video is ready.");
      const results = [];
      for (const state of selectedFiles) {
        if (cancelled) throw new DOMException("Cancelled", "AbortError");
        results.push(await compressWithRetry(state));
      }
      preparedResults = results;
    }
    stopHeartbeat();
    currentState = null;
    overallProgress.value = 80;
    cancelButton.hidden = true;
    runStage = "Relay upload or Discord delivery";
    setPhase("send", "Sending finished files",
      "Compression is complete. Finished MP4 files are now being sent through the relay to Discord.");
    const response = await uploadWithRetry(preparedResults);
    for (const result of preparedResults) updateFile(result.state, "Delivered", 100);
    overallProgress.value = 100;
    setRunDetail("Complete");
    setPhase("done", "Delivered to Discord", response.message);
    showMessage("Compression complete. You can close this page and return to Discord.", "success");
    deliveryComplete = true;
    showFeedback("success");
    clips.disabled = true;
    submit.hidden = true;
    cancelButton.hidden = true;
    running = false;
    clearPreparedFiles();
    backgroundWarning.hidden = true;
    void releaseWakeLock();
    renderDocumentTitle();
    stopClock();
  } catch (error) {
    if (cancelled || error.name === "AbortError") { finishRun(); return; }
    console.error(error);
    void reportBrowserFailure(runStage);
    setPhase("compress", "Action needed", "The process stopped before delivery completed.");
    setRunDetail("Stopped");
    showMessage(friendlyError(error));
    showFeedback("failure");
    finishRun();
    if ([403, 409, 410].includes(error.status) || error.code === "discord_delivery_failed") {
      submit.hidden = true;
      clips.disabled = true;
    }
  }
});

function updateExpiry() {
  const remaining = Math.max(0, Math.ceil((sessionDeadline - Date.now()) / 1000));
  expiresElement.textContent = remaining > 0
    ? "Expires in " + readableTime(remaining)
    : "Session expired";
  if (remaining === 0) {
    if (!running) {
      // Prepared files may already be queued; checking their receipt is safe.
      submit.disabled = !preparedResults;
    } else if (runStage === "Browser compression") {
      cancelled = true;
      if (ffmpeg) { ffmpeg.terminate(); ffmpeg = null; }
      showMessage("This session expired before encoding finished. Run /compress for a new link.");
    }
  }
}
updateExpiry();
expiryTimer = setInterval(updateExpiry, 1000);
</script>
"""
    return (
        template.replace("__ASSET_PREFIX__", ASSET_PREFIX)
        .replace("__MAX_CLIPS__", _json_script_value(max_clips))
        .replace("__TARGET_BYTES__", _json_script_value(target_bytes))
        .replace("__MAX_BATCH_BYTES__", _json_script_value(max_batch_bytes))
        .replace("__SESSION_SECRET__", _json_script_value(session_secret))
        .replace("__EXPIRES_SECONDS__", _json_script_value(expires_in_seconds))
        .replace(
            "__RECOVERY_NOTICE__",
            '<div class="message" role="status"><strong>Page refreshed.</strong> '
            "Videos selected in the previous page are no longer available here. "
            "Choose them again to restart local processing.</div>"
            if reopened
            else "",
        )
    )


def browser_delivery_status(session_secret: str) -> str:
    """Show a status-only view while the relay owns the delivery."""
    return (
        '<img class="brand" src="/brand/professor-compressor.png" '
        'alt="Professor Compressor mascot">'
        "<h1>Checking Discord delivery</h1>"
        '<p class="intro">Your browser may have refreshed while finished files '
        "were being sent. The relay is checking the existing delivery. "
        "Do not start another upload yet.</p>"
        '<p id="delivery-status" class="message" role="status">Checking status…</p>'
        '<p id="reselect" hidden>That upload did not complete. Videos held by '
        "the previous page cannot be restored. "
        '<a href="" id="return-to-compressor">Return to compressor</a> '
        "and select them again.</p>"
        '<p class="privacy"><a href="/privacy">Privacy Policy</a></p>'
        "<script>\n"
        f"const SESSION_SECRET = {_json_script_value(session_secret)};\n"
        'const statusLine = document.getElementById("delivery-status");\n'
        'const reselect = document.getElementById("reselect");\n'
        "async function checkDelivery() {\n"
        "  try {\n"
        '    const response = await fetch(location.pathname + "/status", {\n'
        '      headers: {"X-Upload-Session": SESSION_SECRET},\n'
        '      cache: "no-store", signal: AbortSignal.timeout(10000)\n'
        "    });\n"
        "    const body = await response.json();\n"
        "    if (body.pending) {\n"
        '      statusLine.textContent = "Delivery is still in progress. Check Discord before starting a new session.";\n'
        "      setTimeout(checkDelivery, 2000);\n"
        "    } else if (body.ok) {\n"
        '      statusLine.textContent = "Delivered to Discord. You can return to your channel.";\n'
        '      statusLine.classList.add("success");\n'
        "    } else if (body.ready) {\n"
        '      statusLine.textContent = "The upload stopped before Discord delivery.";\n'
        "      reselect.hidden = false;\n"
        '    } else if (body.code === "discord_delivery_failed") {\n'
        '      statusLine.textContent = "Discord rejected this delivery. Run /compress again or contact support if it keeps happening.";\n'
        '      statusLine.classList.add("error");\n'
        "    } else {\n"
        '      statusLine.textContent = "Delivery could not be confirmed. Check Discord before running /compress again.";\n'
        "    }\n"
        "  } catch (_) {\n"
        '    statusLine.textContent = "Connection lost while checking delivery. Retrying safely…";\n'
        "    setTimeout(checkDelivery, 3000);\n"
        "  }\n"
        "}\n"
        "void checkDelivery();\n"
        "</script>"
    )
