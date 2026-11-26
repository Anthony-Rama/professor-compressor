async function initializeCompressor() {
const {
  MAX_CLIPS,
  TARGET_BYTES,
  DISCORD_LIMIT_BYTES,
  MAX_BATCH_BYTES,
  SESSION_SECRET,
  DIAGNOSTIC_REFERENCE,
  DIAGNOSTIC_EVENTS_ENABLED,
  EXPIRES_SECONDS: SESSION_EXPIRES_SECONDS,
  ASSET_PREFIX
} = JSON.parse(document.getElementById("compressor-config").textContent);
const [{ EncoderClient }, { fetchFile }, { createProcessingFallback },
  { createRelayClient, createPreparedDownloadView }, { createFileSelection }] = await Promise.all([
  import("/brand/encoder-client.js"),
  import(ASSET_PREFIX + "/util-esm/index.js"),
  import("/brand/processing-errors.js"),
  import("/brand/relay-client.js"),
  import("/brand/file-selection.js")
]);
// Imports cannot be cancelled. A late response must not revive a timed-out page.
if (!window.compressorStartup.pending()) return;

let fallbackDiagnosticRun = "";
const processing = createProcessingFallback();
const diagnostic = {
  begin: () => {
    try { const bytes = new Uint8Array(16); crypto.getRandomValues(bytes);
      fallbackDiagnosticRun = Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
    } catch (_) { fallbackDiagnosticRun = ""; }
    return fallbackDiagnosticRun;
  }, run: () => fallbackDiagnosticRun, emit: () => {}, flush: () => {}, fail: () => {},
  context: () => ({}), code: error => error?.diagnosticCode || "UNKNOWN", mark: () => {},
  configure: () => {}, media: () => ({}), attempt: () => {}, log: () => {},
  progress: () => {}, failureContext: () => ({}),
  step: async (_stage, _file, action) => action(),
  ...processing
};
// Loading diagnostics is optional and must not block the compressor's startup.
void import("/brand/diagnostics.js").then(module => {
  const reporter = module.createDiagnostics(SESSION_SECRET, DIAGNOSTIC_REFERENCE, DIAGNOSTIC_EVENTS_ENABLED);
  reporter.resume(diagnostic.run());
  // Telemetry may decorate processing, but cannot replace its error semantics.
  Object.assign(diagnostic, reporter, {
    code: processing.code,
    step: (stage, file, action, failureCode) => processing.step(stage, file,
      () => reporter.step(stage, file, action, failureCode), failureCode),
    log: line => { processing.log(line); reporter.log(line); },
    attempt: (...args) => { processing.attempt(...args); reporter.attempt(...args); },
    mark: (...args) => { processing.mark(...args); reporter.mark(...args); }
  });
}).catch(() => {});
function diagnosticError(message, code) {
  const error = new Error(message); error.diagnosticCode = code; return error;
}
function processingError(value) {
  // The worker can reject with a primitive even when diagnostics cannot load.
  return value && typeof value === "object" ? value :
    new Error(typeof value === "string" ? value : "The video engine stopped unexpectedly.");
}
const OUTPUT_TARGET_RATIO = 0.97;
const SINGLE_CORE_BASE = ASSET_PREFIX + "/core-esm";
const MULTI_CORE_BASE = ASSET_PREFIX + "/core-mt-esm";
const DIAGNOSTIC_ASSET_BUILD = ASSET_PREFIX.split("/").pop();

const form = document.getElementById("upload");
const clips = document.getElementById("clips");
const clearSelectionButton = document.getElementById("clear-selection");
const caption = document.getElementById("caption");
const submit = document.getElementById("submit");
const cancelButton = document.getElementById("cancel");
const selection = document.getElementById("selection");
const sessionNote = document.getElementById("session-note");
const stayOpen = document.getElementById("stay-open");
const performanceHelp = document.getElementById("performance-help");
const formatHelp = document.getElementById("format-help");
const pageHeading = document.getElementById("page-heading");
const pageIntro = document.getElementById("page-intro");
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
const stallWarning = document.getElementById("stall-warning");
const ORIGINAL_TITLE = document.title;

// User-facing activity warnings must work even when optional telemetry is unavailable.
let encodingActivity = null;
function updateActivityWarning() {
  stallWarning.hidden = !running || !encodingActivity ||
    performance.now() - encodingActivity.lastProgress < 120000;
}
function noteEncodingProgress(fraction, time) {
  if (!encodingActivity) return;
  const boundedFraction = Number.isFinite(fraction) && fraction >= 0 ? Math.min(1, fraction) : null;
  const mediaTime = Number.isFinite(time) && time >= 0 ? time : null;
  const advanced = (mediaTime !== null && mediaTime > encodingActivity.mediaTime) ||
    (boundedFraction !== null && boundedFraction > encodingActivity.fraction);
  if (!advanced) return;
  if (mediaTime !== null) encodingActivity.mediaTime = Math.max(encodingActivity.mediaTime, mediaTime);
  if (boundedFraction !== null) encodingActivity.fraction = Math.max(encodingActivity.fraction, boundedFraction);
  encodingActivity.lastProgress = performance.now();
  updateActivityWarning();
  if (!document.hidden) backgroundWarning.hidden = true;
}
async function executeWithActivity(stage, index, action) {
  encodingActivity = {lastProgress: performance.now(), mediaTime: 0, fraction: 0};
  if (document.hidden) {
    backgroundWarning.textContent = "Compression may slow down or pause while this tab is hidden. Keep this browser in the foreground and your screen unlocked.";
    backgroundWarning.hidden = false;
  }
  updateActivityWarning();
  try {
    return await diagnostic.step(stage, index, action, "ENCODE_FAILED");
  } finally {
    encodingActivity = null;
    stallWarning.hidden = true;
    backgroundWarning.hidden = true;
  }
}

let ffmpeg = null;
let encoderMode = "single-threaded";
let batchMetrics = {};
let batchStarted = 0;
let hiddenStarted = null;
let hiddenMs = 0;
function hiddenSeconds() {
  return (hiddenMs + (hiddenStarted === null ? 0 : performance.now() - hiddenStarted)) / 1000;
}
function reportMetrics(outcome, error = null) {
  try {
  const errorCode = error ? diagnostic.code(error) : "";
  const category = !error ? "none" : error.name === "AbortError" ? "cancelled" :
    error.status === 0 ? "network" : error.status ? "http" :
      errorCode === "BROWSER_MEMORY" ? "memory" :
      /timeout/i.test(error.code || "") ? "timeout" : "processing";
  const hidden = hiddenSeconds();
  hiddenMs = hidden * 1000;
  hiddenStarted = null;
  void fetch(window.location.pathname + "/telemetry", {
    method: "POST", headers: {"Content-Type": "application/json", "X-Upload-Session": SESSION_SECRET,
      "X-Diagnostic-Run": diagnostic.run()},
    body: JSON.stringify({outcome, category, stage: preparedResults ? "delivery" : "processing",
      ...batchMetrics, ...diagnostic.context(), ...(error ? {error_code: diagnostic.code(error)} : {}),
      encoding_attempts: fileSelection.files.reduce((sum, state) => sum + (state.encodingAttempts || 0), 0),
      total_seconds: (performance.now() - batchStarted) / 1000,
      hidden_seconds: hidden}),
    keepalive: true, signal: AbortSignal.timeout(5000)
  }).catch(() => {});
  } catch (_) { /* Optional reporting cannot interrupt delivery or cleanup. */ }
}
let currentState = null;
let currentAttempt = 1;
let lastFfmpegMessage = "";
let startedAt = 0;
let timer = null;
let expiryTimer = null;
let wakeLock = null;
let cancelled = false;
let running = false;
let tabProgress = "Preparing files";
let runStage = "Browser compression";
let heartbeatTimer = null;
let preparedResults = null;
let pauseRequest = Promise.resolve();
let deliveryComplete = false;
let pageIsLeaving = false;
let progressQueue = Promise.resolve();

function reportProgress(event, count) {
  const body = count === undefined ? {event} : {event, count};
  if (diagnostic.run()) body.run_id = diagnostic.run();
  // Observability must never hold up local compression or expose file details.
  const send = () => { try { return fetch(window.location.pathname + "/progress", {
    method: "POST",
    headers: {"X-Upload-Session": SESSION_SECRET, "Content-Type": "application/json"},
    body: JSON.stringify(body),
    cache: "no-store",
    keepalive: true,
    signal: event === "page_left" ? undefined : AbortSignal.timeout(5000)
  }).catch(() => {}); } catch (_) { return Promise.resolve(); } };
  if (event === "page_left") {
    void send();
  } else {
    progressQueue = progressQueue.then(() => pageIsLeaving ? undefined : send());
  }
}

window.addEventListener("pagehide", () => {
  fileSelection.flushPreparationReports();
  diagnostic.flush();
  pageIsLeaving = true;
  if (!deliveryComplete && !sessionExpired()) reportProgress("page_left");
});
window.addEventListener("pageshow", () => { pageIsLeaving = false; });

const relay = createRelayClient({
  sessionSecret: SESSION_SECRET, expiresInSeconds: SESSION_EXPIRES_SECONDS,
  diagnostic, getBatchMetrics: () => batchMetrics, hiddenSeconds,
  getCaption: () => caption.value, isCancelled: () => cancelled,
  overallProgress, phaseCopy, updateFile, setRunDetail
});
const preparedDownloads = createPreparedDownloadView();

function sessionExpired() { return Date.now() >= relay.expiresAt(); }

function clearPreparedFiles() {
  preparedResults = null;
  preparedDownloads.clear();
}

function stopHeartbeat() {
  clearInterval(heartbeatTimer);
  heartbeatTimer = null;
}

async function keepSessionAlive() {
  try {
    await relay.sessionRequest("heartbeat");
    updateExpiry();
  } catch (error) {
    if (error.status === 410 || error.status === 403) relay.expire();
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
    MAX_BATCH_BYTES / Math.max(1, fileSelection.files.length)
  );
  return Math.min(TARGET_BYTES, batchTargetBytes);
}

function unchangedLimitBytes() {
  const batchTargetBytes = Math.floor(
    MAX_BATCH_BYTES / Math.max(1, fileSelection.files.length)
  );
  return Math.min(DISCORD_LIMIT_BYTES, batchTargetBytes);
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
  document.querySelector(".controls").after(message);
  message.textContent = text + (kind === "error" && DIAGNOSTIC_REFERENCE ?
    " Error reference: " + DIAGNOSTIC_REFERENCE : "");
  message.className = "message " + kind;
  message.hidden = false;
}

function showSelectionError(text) {
  showMessage(text);
  document.getElementById("picker-row").before(message);
  message.focus();
}

function hideMessage() {
  message.hidden = true;
  message.textContent = "";
}

function showFeedback(outcome) {
  document.getElementById("review-prompt").hidden = outcome !== "success";
  document.getElementById("contact-help").hidden = outcome === "success";
  document.getElementById("processing-privacy").hidden = outcome === "success";
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
  if (document.hidden && hiddenStarted === null) hiddenStarted = performance.now();
  if (!document.hidden && hiddenStarted !== null) {
    const away = performance.now() - hiddenStarted;
    hiddenMs += away;
    hiddenStarted = null;
    if (encodingActivity && away >= 15000) {
      backgroundWarning.textContent = "This page was in the background, which can slow or pause compression. Keep this browser visible and your screen unlocked. Waiting for the next progress update; nothing has been uploaded yet.";
      backgroundWarning.hidden = false;
    } else backgroundWarning.hidden = true;
  }
  if (document.hidden) {
    backgroundWarning.textContent = "Compression may slow down or pause while this tab is hidden. Keep this browser in the foreground and your screen unlocked.";
    backgroundWarning.hidden = !encodingActivity;
    renderDocumentTitle();
    return;
  }
  updateActivityWarning();
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

const fileSelection = createFileSelection({
  sessionSecret: SESSION_SECRET, maxClips: MAX_CLIPS,
  diagnosticEventsEnabled: DIAGNOSTIC_EVENTS_ENABLED, diagnostic, diagnosticError,
  clips, clearSelectionButton, submit, selection, filesElement,
  isRunning: () => running, sessionExpired, readableSize, updateFile,
  reportProgress, clearPreparedFiles, showSelectionError, hideMessage
});

function createEncoder() {
  const encoder = new EncoderClient(new URL(ASSET_PREFIX + "/ffmpeg-esm/worker.js", location.origin));
  encoder.on("log", ({ message: line }) => {
    lastFfmpegMessage = line;
    diagnostic.log(line);
  });
  let lastProgressState = null;
  let lastProgressAttempt = 0;
  let lastProgressPercent = -1;
  encoder.on("progress", ({ progress: fraction, time }) => {
    if (!currentState || cancelled) return;
    diagnostic.progress(fraction, time);
    noteEncodingProgress(fraction, time);
    const percent = Math.max(0, Math.min(100, Math.round(fraction * 100)));
    if (currentState === lastProgressState && currentAttempt === lastProgressAttempt &&
        percent === lastProgressPercent) return;
    lastProgressState = currentState;
    lastProgressAttempt = currentAttempt;
    lastProgressPercent = percent;
    const adjustment = currentAttempt > 1 ? "Adjusting size" : "Compressing";
    updateFile(currentState, adjustment + " " + percent + "%", percent);
    const completed = fileSelection.files.filter((state) => state.finalSize > 0).length;
    const overall = ((completed + fraction) / fileSelection.files.length) * 80;
    overallProgress.value = Math.max(0, Math.min(80, overall));
    setRunDetail("Video " + currentState.index + " of " +
      fileSelection.files.length + " · " + percent + "%");
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
  const start = performance.now();
  batchMetrics.stage = "engine";
  try {
    const mode = await diagnostic.step("engine", 0, () => loadEncoderCore(), "ENGINE_LOAD_FAILED");
    batchMetrics.engine = mode;
    return mode;
  }
  finally { batchMetrics.engine_seconds = (batchMetrics.engine_seconds || 0) + (performance.now() - start) / 1000; }
}

async function loadEncoderCore() {
  if (cancelled) throw new DOMException("Cancelled", "AbortError");
  for (const state of fileSelection.files) {
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
      if (error?.code === "encoder_start_timeout") {
        ffmpeg = null;
        throw error;
      }
    }
  }
  ffmpeg = createEncoder();
  try {
    for (const state of fileSelection.files) {
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

async function videoSignature(file, index) {
  let bytes;
  try { bytes = new Uint8Array(await file.slice(0, 512).arrayBuffer()); }
  catch (_) {
    const error = diagnosticError("This clip could not be read. Reselect it from a saved folder and try again.", "FILE_READ_FAILED");
    error.diagnosticFileIndex = index;
    throw error;
  }
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
    throw diagnosticError(file.name + " uses an unsupported media-container signature. " +
      "Choose a supported video format listed below the upload controls.", "UNSUPPORTED_CONTAINER");
  }
  if (bytes.length >= 4 && bytes[0] === 0x1a && bytes[1] === 0x45 &&
      bytes[2] === 0xdf && bytes[3] === 0xa3) {
    const header = ascii(0, bytes.length).toLowerCase();
    if (header.includes("matroska")) return "mkv";
    if (header.includes("webm")) return "webm";
    return "webm/mkv";
  }
  if (bytes.length >= 12 && ascii(0, 4) === "RIFF" && ascii(8, 4) === "AVI ") return "avi";
  if (bytes.length >= 4 && ascii(0, 4) === "OggS") return "ogg";
  if (bytes.length >= 3 && ascii(0, 3) === "FLV") return "flv";
  const asfHeader = [0x30,0x26,0xb2,0x75,0x8e,0x66,0xcf,0x11,0xa6,0xd9,0x00,0xaa,0x00,0x62,0xce,0x6c];
  if (asfHeader.every((value, index) => bytes[index] === value)) return "asf/wmv";
  if (bytes.length >= 4 && bytes[0] === 0x00 && bytes[1] === 0x00 &&
      bytes[2] === 0x01 && [0xba, 0xb3].includes(bytes[3])) return "mpeg";
  if (bytes.length >= 377 && bytes[0] === 0x47 && bytes[188] === 0x47) return "ts";
  if (bytes.length >= 389 && bytes[4] === 0x47 && bytes[196] === 0x47 && bytes[388] === 0x47) return "m2ts";
  throw diagnosticError(file.name + " does not have a recognized video signature. " +
    "Choose a supported video format listed below the upload controls.", "INVALID_SIGNATURE");
}

async function removeVirtualFile(name) {
  if (!ffmpeg) return;
  try { await ffmpeg.deleteFile(name); } catch (_) { /* Optional file. */ }
}

async function timedEncode(state, args, sizeAdjustment = false) {
  batchMetrics.stage = "encoding";
  state.encodingAttempts += 1;
  const start = performance.now();
  diagnostic.configure(args, encoderMode, DIAGNOSTIC_ASSET_BUILD, state.encodingAttempts, sizeAdjustment);
  try {
    return await executeWithActivity("encoding", state.index, () => ffmpeg.exec(args));
  } finally {
    // Include failed encoding attempts and size retries, but exclude engine
    // loading, probing, file copies, remuxing, upload, and Discord delivery.
    state.encodingMs += Math.max(0, performance.now() - start);
  }
}

async function compressOne(state) {
  batchMetrics.stage = "metadata";
  const file = state.file;
  const format = await diagnostic.step("validation", state.index, () => videoSignature(file, state.index), "INVALID_SIGNATURE");
  state.inputFormat = format;
  diagnostic.emit("stage_finished", state.index, {stage: "validation", input_bytes: file.size,
    input_format: format, target_bytes: outputTargetBytes(), discord_limit: DISCORD_LIMIT_BYTES});
  const effectiveTargetBytes = outputTargetBytes();
  if (file.size <= unchangedLimitBytes() && format === "mp4") {
    updateFile(state, "Already fits", 100);
    updateFileSizes(state, file.size);
    return { blob: file, name: safeOriginalName(file.name, state.index), state,
      diagnostic: { input_bytes: file.size, duration_seconds: null,
        video_kbps: null, copied: true, input_format: format } };
  }
  // Probe using the same decoder as conversion rather than HTML video support.
  // Browsers cannot read metadata for many otherwise decodable MKV/AVI files.
  const inputName = "input-" + state.index + ".video";
  const outputName = "output-" + state.index + ".mp4";
  const probeName = "probe-" + state.index + ".json";
  try {
    await diagnostic.step("file_read", state.index, async () => {
      await ffmpeg.writeFile(inputName, await fetchFile(file));
    }, "FILE_READ_FAILED");
    updateFile(state, "Reading video metadata", 0);
    const metadataStarted = performance.now();
    await diagnostic.step("metadata", state.index, () => ffmpeg.ffprobe([
      "-v", "error", "-show_error", "-show_entries", "format=duration:stream=codec_type,codec_name,duration,width,height,avg_frame_rate,r_frame_rate,pix_fmt,profile,level,color_transfer,channels,sample_rate:stream_side_data=rotation",
      "-of", "json", inputName, "-o", probeName
    ], 30000), "METADATA_INVALID");
    if (cancelled) throw new DOMException("Cancelled", "AbortError");
    // The pinned wasm core returns -1 even for successful probes; validate its
    // structured output instead of relying on that exit status.
    let metadata;
    try {
      metadata = JSON.parse(await ffmpeg.readFile(probeName, "utf8"));
      if (metadata.error || !Array.isArray(metadata.streams)) throw new Error("Invalid metadata");
    } catch (error) {
      if (cancelled) throw new DOMException("Cancelled", "AbortError");
      throw diagnosticError("Could not determine video metadata for " + file.name +
        ". The file may be damaged or unsupported by this encoder.",
        performance.now() - metadataStarted >= 29500 ? "METADATA_TIMEOUT" : "METADATA_INVALID");
    }
    const videoStream = metadata.streams.find((stream) => stream.codec_type === "video");
    const audioStream = metadata.streams.find((stream) => stream.codec_type === "audio");
    if (!videoStream) throw diagnosticError("No video stream found in " + file.name + ". Choose a video, not an audio-only file.", "NO_VIDEO_STREAM");
    const rateParts = String(videoStream.avg_frame_rate || "0/1").split("/").map(Number);
    state.inputMedia = {input_width: videoStream.width, input_height: videoStream.height,
      input_fps: rateParts[0] / (rateParts[1] || 1), video_codec: videoStream.codec_name,
      audio_codec: audioStream?.codec_name || "none", ...diagnostic.media(videoStream, audioStream)};
    const duration = [metadata.format?.duration, videoStream.duration]
      .map(Number).find((value) => Number.isFinite(value) && value > 0);
    if (!duration) throw diagnosticError(
      "Could not determine the duration of " + file.name + ". Re-export the clip with a finite duration.", "DURATION_UNKNOWN");
    state.inputDuration = duration;
    diagnostic.emit("stage_finished", state.index, {stage: "metadata", ...state.inputMedia,
      input_format: format, input_bytes: file.size, duration_seconds: duration,
      video_codec: videoStream.codec_name, audio_codec: audioStream?.codec_name || "none"});
    // These pinned cores expose the native AV1 decoder but no usable software
    // AV1 implementation. Revisit this guard when upgrading the core build.
    // Fitting MP4 passthrough above deliberately does not require decoding.
    if (videoStream.codec_name === "av1") {
      diagnostic.mark("validation");
      throw diagnosticError(
      "This video uses AV1, which this version of the compressor cannot convert. " +
      "Export it as H.264 MP4 with AAC audio, then replace this file. Your other selected clips are still available.",
      "UNSUPPORTED_CODEC");
    }
    const audioKbps = audioStream ? 96 : 0;
    state.audioKbps = audioKbps;
    const usableBits = effectiveTargetBytes * 8 * OUTPUT_TARGET_RATIO;
    const budgetVideoKbps = Math.floor(usableBits / duration / 1000 - audioKbps);
    function requireEncodingBudget() {
      if (budgetVideoKbps < 100) {
        diagnostic.mark("validation");
        throw diagnosticError(
        "This video is too long to fit the available upload limit with the current video and audio settings. " +
        "Choose a shorter video or a destination with a larger upload allowance. Your other selected clips are still available.",
        "TARGET_NOT_FEASIBLE");
      }
    }
    const scale = "fps=fps='min(source_fps,30)'," +
      "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease," +
      "scale=trunc(iw/2)*2:trunc(ih/2)*2";
    if (format !== "mp4") {
      // Compatible streams can be repackaged into MP4 without re-encoding.
      // Otherwise make a quality-based conversion and check its actual size.
      let remuxTooLarge = false;
      if (videoStream.codec_name === "h264" &&
          (!audioStream || audioStream.codec_name === "aac")) {
        currentAttempt = 1;
        lastFfmpegMessage = "";
        updateFile(state, "Preparing MP4 without re-encoding", 0);
        batchMetrics.stage = "remux";
        const remuxArgs = [
          "-i", inputName, "-map", "0:v:0", "-map", "0:a:0?",
          "-c", "copy", "-movflags", "+faststart", outputName
        ];
        diagnostic.configure(remuxArgs, encoderMode, DIAGNOSTIC_ASSET_BUILD);
        const remuxExit = await executeWithActivity("remux", state.index, () => ffmpeg.exec(remuxArgs));
        if (cancelled) throw new DOMException("Cancelled", "AbortError");
        if (remuxExit === 0) {
          const remuxed = await ffmpeg.readFile(outputName);
          if (remuxed.byteLength <= unchangedLimitBytes()) {
            const blob = new Blob([remuxed], { type: "video/mp4" });
            updateFile(state, "Converted without re-encoding", 100);
            updateFileSizes(state, blob.size);
            return { blob, name: safeStem(file.name, state.index) + "-converted.mp4", state,
              diagnostic: { input_bytes: file.size, duration_seconds: duration,
                video_kbps: null, copied: true, input_format: format } };
          }
          remuxTooLarge = true;
        }
        await removeVirtualFile(outputName);
      }
      if (!remuxTooLarge) {
        requireEncodingBudget();
        currentAttempt = 1;
        lastFfmpegMessage = "";
        updateFile(state, "Converting to MP4", 0);
        const conversionExit = await timedEncode(state, [
          "-i", inputName, "-map", "0:v:0", "-map", "0:a:0?",
          "-c:v", "libx264", "-threads", encoderMode === "multithreaded" ? "4" : "1",
          "-preset", "veryfast", "-crf", "18", "-vf", scale, "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", audioKbps + "k", "-movflags", "+faststart", outputName
        ]);
        if (cancelled) throw new DOMException("Cancelled", "AbortError");
        if (conversionExit !== 0) throw diagnosticError("The encoder could not read " + file.name +
          ". The video may use an unsupported or damaged codec.", "ENCODE_FAILED");
        const converted = await ffmpeg.readFile(outputName);
        if (converted.byteLength <= unchangedLimitBytes()) {
          const blob = new Blob([converted], { type: "video/mp4" });
          updateFile(state, "Converted to MP4", 100);
          updateFileSizes(state, blob.size);
          return { blob, name: safeStem(file.name, state.index) + "-converted.mp4", state,
            diagnostic: { input_bytes: file.size, duration_seconds: duration,
              video_kbps: null, copied: false, input_format: format } };
        }
        await removeVirtualFile(outputName);
      }
      updateFile(state, "Converted MP4 is too large; adjusting size", 0);
    }
    requireEncodingBudget();
    let videoKbps = budgetVideoKbps;
    async function encode(bitrate) {
      lastFfmpegMessage = "";
      return timedEncode(state, [
        "-i", inputName, "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-threads", encoderMode === "multithreaded" ? "4" : "1",
        "-preset", "veryfast", "-b:v", bitrate + "k", "-maxrate", bitrate + "k",
        "-bufsize", (bitrate * 2) + "k", "-vf", scale, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", audioKbps + "k", "-movflags", "+faststart", outputName
      ], format !== "mp4" || currentAttempt > 1);
    }
    currentAttempt = 1;
    let exitCode = await encode(videoKbps);
    if (cancelled) throw new DOMException("Cancelled", "AbortError");
    if (exitCode !== 0) throw diagnosticError("The encoder could not read " + file.name +
      ". The video may use an unsupported or damaged codec.", "ENCODE_FAILED");
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
      if (exitCode !== 0) throw diagnosticError("The final size adjustment failed for " +
        file.name + ". Try that video by itself.", "ENCODE_FAILED");
      output = await ffmpeg.readFile(outputName);
    }
    if (output.byteLength > effectiveTargetBytes) {
      throw diagnosticError(file.name + " could not be reduced below Discord's limit. " +
        "Trim it into a shorter clip and try again.", "OUTPUT_TOO_LARGE");
    }
    const blob = new Blob([output], { type: "video/mp4" });
    updateFile(state, "Compressed", 100);
    updateFileSizes(state, blob.size);
    return { blob, name: safeStem(file.name, state.index) + "-compressed.mp4", state,
      diagnostic: { input_bytes: file.size, duration_seconds: duration,
        video_kbps: videoKbps, copied: false, input_format: format } };
  } finally {
    if (state.finalSize && state.encodingAttempts > 0 && !cancelled) {
      // Header-only metadata read of the existing virtual output: no copy or encode.
      try {
        await ffmpeg.ffprobe(["-v", "error", "-select_streams", "v:0", "-show_entries",
          "stream=width,height,avg_frame_rate", "-of", "json", outputName, "-o", probeName], 5000);
        const stream = JSON.parse(await ffmpeg.readFile(probeName, "utf8")).streams?.[0];
        if (stream) {
          const rate = String(stream.avg_frame_rate || "0/1").split("/").map(Number);
          state.outputMedia = {output_width: stream.width, output_height: stream.height,
            output_fps: rate[0] / (rate[1] || 1)};
        }
      } catch (_) { /* Optional metadata cannot fail delivery. */ }
    }
    await removeVirtualFile(inputName);
    await removeVirtualFile(outputName);
    await removeVirtualFile(probeName);
  }
}

async function compressWithRetry(state) {
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      currentState = state;
      diagnostic.attempt(state.index, attempt);
      updateFile(state, attempt === 1 ? "Starting" : "Retrying automatically", 0);
      const result = await compressOne(state);
      if (cancelled) throw new DOMException("Cancelled", "AbortError");
      result.diagnostic.encoding_seconds = Math.round(state.encodingMs) / 1000;
      result.diagnostic.telemetry = {encoding_attempts: state.encodingAttempts,
        path: result.diagnostic.copied ? (result.diagnostic.duration_seconds ? "remuxed" : "unchanged") :
          result.diagnostic.video_kbps === null ? "converted" : "compressed",
        ...state.inputMedia, ...state.outputMedia};
      if (state.encodingAttempts === 0 && state.inputMedia) {
        Object.assign(result.diagnostic.telemetry, {output_width: state.inputMedia.input_width,
          output_height: state.inputMedia.input_height, output_fps: state.inputMedia.input_fps});
      }
      diagnostic.emit("file_ready", state.index, {...result.diagnostic, ...result.diagnostic.telemetry,
        output_bytes: result.blob.size, audio_kbps: state.encodingAttempts ? state.audioKbps : undefined});
      return result;
    } catch (value) {
      // Optional diagnostics may not be loaded. Handle worker string/null
      // rejections here too, so retries and user-facing errors remain safe.
      const error = processingError(value);
      try { error.diagnosticFileIndex = state.index; } catch (_) { /* Optional context. */ }
      if (cancelled || error.name === "AbortError") throw error;
      console.error("Compression attempt failed", error, lastFfmpegMessage);
      batchMetrics.processing_failures = (batchMetrics.processing_failures || 0) + 1;
      const nonRetryable = new Set(["UNSUPPORTED_CODEC", "TARGET_NOT_FEASIBLE", "INVALID_SIGNATURE",
        "NO_VIDEO_STREAM", "DURATION_UNKNOWN", "INVALID_MEDIA"]);
      if (attempt === 2 || nonRetryable.has(diagnostic.code(error)) || /too long|recognized video signature|Could not determine|No video stream/.test(error.message)) {
        updateFile(state, "Needs attention", 0);
        throw error;
      }
      updateFile(state, "Retrying encoder", 0);
      diagnostic.emit("retry", state.index, {...diagnostic.failureContext(error), attempt: attempt + 1,
        failed_attempt: attempt, retry_reason: "same_settings",
        error_code: diagnostic.code(error), retryable: true});
      phaseCopy.textContent = "The encoder stopped unexpectedly. Reloading it and retrying " +
        state.file.name + " once.";
      await resetEncoder();
    }
  }
}

async function reportBrowserFailure(stage, error) {
  // Reuse the same run ID so retrying this fallback report cannot duplicate
  // failure counts or alerts. No raw exception/log text is transmitted.
  const context = diagnostic.failureContext(error);
  const environment = diagnostic.context();
  const state = currentState?.index === context.file_index ? currentState : null;
  const body = JSON.stringify({ ...environment, stage, operation: environment.stage,
    ...(state ? {input_bytes: state.file.size, input_format: state.inputFormat,
      duration_seconds: state.inputDuration, ...state.inputMedia} : {}), ...context,
    run_id: diagnostic.run(), error_code: diagnostic.code(error) });
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const response = await fetch(window.location.pathname.replace(/\/$/, "") + "/failure", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Upload-Session": SESSION_SECRET
        },
        body, signal: AbortSignal.timeout(4000),
        cache: "no-store",
        credentials: "same-origin",
        keepalive: true
      });
      if (response.ok || ![408, 500, 502, 503, 504].includes(response.status)) return;
    } catch (reportError) {
      console.debug("The failure notification could not reach the relay.", reportError);
    }
    if (attempt === 0) await new Promise(resolve => setTimeout(resolve, 500));
  }
}

function friendlyError(error) {
  if (error.code === "upload_connection_reset") return "Your video finished compressing, but the upload connection was interrupted before the relay confirmed receipt. Keep this page open and use Check delivery / retry, or save your prepared files below. They do not need to be compressed again.";
  if (error.code === "session_expired" || error.code === "session_used") {
    return "This session expired or the service restarted. Check Discord before creating a new session. You can save any prepared files below.";
  }
  if (error.code === "discord_delivery_failed") return error.message;
  if (error.status === 0) {
    return "Delivery could not be confirmed. Check your connection and Discord, then use Check delivery / retry. Prepared files will not be recompressed.";
  }
  if (diagnostic.code(error) === "BROWSER_MEMORY") {
    return "The video engine could not allocate enough memory in this browser. Close other tabs and try fewer or smaller videos.";
  }
  if (diagnostic.code(error) === "WASM_TRAP") {
    return "Compression failed because the video engine hit a runtime error. Its underlying cause could not be confirmed. Try the clip by itself or use another desktop browser.";
  }
  const code = diagnostic.code(error);
  if (code === "FILE_READ_FAILED") return "This clip could not be read. Reselect it from a saved folder and try again.";
  if (code === "WORKER_CRASH") return "Compression stopped because the video engine's background worker crashed. The underlying cause was not reported. Try again, or use another desktop browser. Contact support with the error reference if it repeats.";
  if (code === "WORKER_MESSAGE_ERROR") return "Compression stopped because the browser could not communicate with the video engine. Reload the page and try again. Contact support with the error reference if it repeats.";
  if (code === "UNSUPPORTED_CODEC") return (currentState?.inputMedia?.video_codec === "av1"
    ? "This video uses AV1, which this version of the compressor cannot convert. "
    : "This video's codec is not supported for conversion by this version of the compressor. ") +
    "Export it as H.264 MP4 with AAC audio, then replace this file. Your other selected clips are still available.";
  if (code === "TARGET_NOT_FEASIBLE") return error.message;
  if (code === "INVALID_MEDIA") return "Compression failed because the video engine reported invalid media data. Try playing the original video, then re-export it and try again.";
  if (["ENGINE_LOAD_TIMEOUT", "ENGINE_LOAD_FAILED"].includes(code)) return "Compression could not start because the video engine failed to load" + (code === "ENGINE_LOAD_TIMEOUT" ? " in time" : "") + ". Check your connection, reload the page and try again.";
  if (code === "ENCODE_FAILED") return "Compression failed while encoding this video. The engine did not report a specific cause. Try the clip by itself or another desktop browser. If it fails again, contact support with the error reference.";
  if (error.diagnosticCode || error.status || error.code) return error.message || "The request failed. Contact support with the error reference.";
  return "Compression failed because the video engine stopped unexpectedly. A specific cause was not reported. Try again, or contact support with the error reference.";
}

function startClock() {
  clearInterval(timer);
  timer = setInterval(() => {
    elapsedElement.textContent = "Elapsed " + readableTime((performance.now() - startedAt) / 1000);
    updateActivityWarning();
  }, 500);
}

function stopClock() {
  clearInterval(timer);
  timer = null;
}

function setProcessingView(active) {
  document.getElementById("preparation-help").hidden = active;
  form.classList.toggle("processing", active);
  document.querySelector("main").classList.toggle("processing-view", active);
  document.getElementById("picker-row").hidden = active;
  selection.hidden = active;
  document.querySelector(".caption-label").hidden = active;
  caption.hidden = active;
  document.querySelector(".caption-help").hidden = active;
  sessionNote.hidden = active;
  pageIntro.hidden = active;
  formatHelp.hidden = active;
  performanceHelp.hidden = active;
  submit.hidden = active;
  pageHeading.textContent = active ? "Compression in progress" : "Compress videos for Discord";
}

function finishRun() {
  running = false;
  document.getElementById("stall-warning").hidden = true;
  stopHeartbeat();
  pauseRequest = relay.sessionRequest("pause").catch(() => {});
  backgroundWarning.hidden = true;
  void releaseWakeLock();
  renderDocumentTitle();
  cancelButton.hidden = true;
  clips.disabled = preparedResults !== null;
  clearSelectionButton.disabled = clips.disabled || clips.files.length === 0;
  clearSelectionButton.hidden = clips.disabled;
  for (const remove of filesElement.querySelectorAll(".remove-file")) {
    remove.hidden = clips.disabled;
  }
  caption.disabled = preparedResults !== null;
  submit.disabled = sessionExpired() && !preparedResults;
  submit.textContent = preparedResults ? "Check delivery / retry" : "Try again";
  // Restore selection only when local processing can safely start again.
  // A prepared batch may already be queued: retain the focused delivery view.
  setProcessingView(preparedResults !== null);
  if (!preparedResults) fileSelection.refreshPreparation();
  submit.hidden = false;
  if (preparedResults) pageHeading.textContent = "Delivery needs attention";
  preparedDownloads.show(preparedResults);
  stopClock();
}

cancelButton.addEventListener("click", () => {
  if (!running) return;
  cancelled = true;
  reportProgress("cancelled");
  relay.abortUpload();
  if (ffmpeg) {
    ffmpeg.terminate();
    ffmpeg = null;
  }
  for (const state of fileSelection.files) {
    if (!state.finalSize) updateFile(state, "Cancelled", 0);
  }
  setPhase("compress", "Cancelled", "No additional files will be sent. You can start again on this page.");
  setRunDetail("Cancelled");
  showMessage("Cancelled. Your original videos were not changed.", "error");
  cancelButton.disabled = true;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (running || fileSelection.hasPending()) return;
  if (sessionExpired() && !preparedResults) {
    updateExpiry();
    showMessage("This session expired. Run /compress for a new link before processing videos.");
    return;
  }
  if (fileSelection.files.length < 1 || fileSelection.files.length > MAX_CLIPS) {
    showSelectionError("Choose between 1 and " + MAX_CLIPS + " videos.");
    return;
  }
  if (!preparedResults && fileSelection.files.some(state => !fileSelection.isReady(state))) {
    showSelectionError("Wait until every clip is Ready. Reselect or remove any clip that could not be prepared.");
    return;
  }
  diagnostic.begin();
  fileSelection.flushPreparationReports();
  reportProgress("started");
  cancelled = false;
  running = true;
  setProcessingView(true);
  pageHeading.focus({preventScroll: true});
  document.querySelector("main").scrollIntoView({block: "start"});
  submit.disabled = true;
  clips.disabled = true;
  clearSelectionButton.disabled = true;
  clearSelectionButton.hidden = true;
  for (const remove of filesElement.querySelectorAll(".remove-file")) remove.hidden = true;
  caption.disabled = true;
  cancelButton.hidden = Boolean(preparedResults);
  cancelButton.disabled = false;
  work.hidden = false;
  hideMessage();
  feedbackActions.hidden = true;
  startedAt = performance.now();
  if (!preparedResults) {
    batchStarted = startedAt;
    batchMetrics = {batch_id: diagnostic.run(), engine_seconds: 0, engine: "unused", upload_attempts: 0};
    hiddenMs = 0;
  }
  hiddenStarted = document.hidden ? performance.now() : null;
  runStage = preparedResults ? "Relay upload or Discord delivery" : "Browser compression";
  if (preparedResults) {
    pageHeading.textContent = "Checking Discord delivery";
    setPhase("send", "Checking Discord delivery", "Checking the existing batch before retrying delivery.");
  } else {
    setPhase("compress", "Preparing videos", "Preparing videos on this device. Nothing has been sent yet.");
  }
  overallProgress.value = 0;
  backgroundWarning.hidden = true;
  setRunDetail("Checking files");
  void requestWakeLock();
  startClock();
  if (!preparedResults) {
    for (const state of fileSelection.files) {
      state.finalSize = 0;
      state.encodingMs = 0;
      state.encodingAttempts = 0;
      state.inputMedia = null;
      state.outputMedia = null;
      state.inputFormat = undefined;
      state.inputDuration = undefined;
      state.meta.textContent = readableSize(state.file.size) + " original";
      updateFile(state, "Checking file", 0);
    }
  }
  try {
    await pauseRequest;
    if (cancelled) throw new DOMException("Cancelled", "AbortError");
    if (!preparedResults) {
      const session = await diagnostic.step("session", 0, () => relay.sessionRequest("heartbeat"), "SESSION_EXPIRED");
      if (cancelled) throw new DOMException("Cancelled", "AbortError");
      if (!session.ready) throw new Error("This session already has a delivery in progress.");
      stopHeartbeat();
      heartbeatTimer = setInterval(() => { void keepSessionAlive(); }, 60000);
      const formats = await Promise.all(fileSelection.files.map(async state => {
        // Size is available even if container or metadata reading fails.
        diagnostic.emit("stage_started", state.index, {stage: "validation", input_bytes: state.file.size});
        const format = await diagnostic.step("validation", state.index, () => videoSignature(state.file, state.index), "INVALID_SIGNATURE");
        diagnostic.emit("stage_finished", state.index, {stage: "validation", input_format: format, input_bytes: state.file.size});
        return format;
      }));
      if (cancelled) throw new DOMException("Cancelled", "AbortError");
      const needsEncoder = fileSelection.files.some((state, index) =>
        state.file.size > unchangedLimitBytes() || formats[index] !== "mp4");
      if (needsEncoder && !ffmpeg) {
        setPhase("compress", "Loading video engine",
          "The browser is preparing its video tools. A first or uncached visit may take a few minutes on a slow connection; your video stays on this device.");
        encoderMode = await loadEncoder();
      }
      setPhase("compress", "Compressing on your device",
        "Compression runs locally. Nothing is sent until every video is ready.");
      const results = [];
      for (const state of fileSelection.files) {
        if (cancelled) throw new DOMException("Cancelled", "AbortError");
        results.push(await compressWithRetry(state));
      }
      preparedResults = results;
      batchMetrics.engine = needsEncoder ? encoderMode : "unused";
      batchMetrics.pre_upload_seconds = (performance.now() - batchStarted) / 1000;
    }
    stopHeartbeat();
    currentState = null;
    overallProgress.value = 80;
    cancelButton.hidden = true;
    runStage = "Relay upload or Discord delivery";
    pageHeading.textContent = "Sending to Discord";
    setPhase("send", "Sending finished files",
      "Compression is complete. Finished MP4 files are now being sent through the relay to Discord.");
    const response = await relay.uploadWithRetry(preparedResults);
    reportMetrics("delivered");
    diagnostic.flush();
    for (const result of preparedResults) updateFile(result.state, "Delivered", 100);
    overallProgress.value = 100;
    setRunDetail("Complete");
    setPhase("done", "Delivered to Discord", response.message);
    showMessage("You can close this page and return to Discord. To upload more videos, run /compress again.", "success");
    deliveryComplete = true;
    showFeedback("success");
    pageHeading.textContent = "Delivered to Discord";
    form.classList.add("completed");
    const completionSummary = document.getElementById("completion-summary");
    completionSummary.textContent = "✓ " + preparedResults.length +
      (preparedResults.length === 1 ? " video delivered" : " videos delivered") +
      " · Completed in " + readableTime((performance.now() - startedAt) / 1000);
    completionSummary.hidden = false;
    work.hidden = true;
    pageIntro.hidden = true;
    document.getElementById("picker-row").hidden = true;
    selection.hidden = true;
    document.querySelector(".caption-label").hidden = true;
    caption.hidden = true;
    document.querySelector(".caption-help").hidden = true;
    sessionNote.hidden = true;
    document.getElementById("preparation-help").hidden = true;
    stayOpen.hidden = true;
    performanceHelp.hidden = true;
    formatHelp.hidden = true;
    clips.disabled = true;
    submit.hidden = true;
    cancelButton.hidden = true;
    running = false;
    clearPreparedFiles();
    fileSelection.dispose();
    backgroundWarning.hidden = true;
    void releaseWakeLock();
    renderDocumentTitle();
    stopClock();
  } catch (value) {
    const error = processingError(value);
    if (!cancelled && error.name !== "AbortError") {
      try { diagnostic.fail(error); } catch (_) { /* Optional diagnostic only. */ }
    }
    reportMetrics(cancelled || error.name === "AbortError" ? "cancelled" : "failed", error);
    if (cancelled || error.name === "AbortError") { finishRun(); return; }
    console.error(error);
    void reportBrowserFailure(runStage, error);
    setPhase("compress", "Action needed", preparedResults ? "Delivery could not be confirmed. Check Discord before retrying." : "Compression failed. No videos from this batch were sent to Discord. See the reason below.");
    setRunDetail("Stopped");
    showMessage((error.diagnosticFileIndex ? "Video " + error.diagnosticFileIndex + ": " : "") + friendlyError(error));
    showFeedback("failure");
    finishRun();
    if (!preparedResults) {
      for (const state of fileSelection.files) {
        if (state.index !== error.diagnosticFileIndex && !state.finalSize &&
            fileSelection.isReady(state)) updateFile(state, "Ready", 0);
      }
    }
    if (["FILE_READ_FAILED", "UNSUPPORTED_CODEC", "TARGET_NOT_FEASIBLE", "INVALID_MEDIA"].includes(diagnostic.code(error)) && !preparedResults) {
      fileSelection.markNeedsReplacement(error.diagnosticFileIndex, friendlyError(error));
    }
    const replaceableInput = ["FILE_READ_FAILED", "UNSUPPORTED_CODEC", "TARGET_NOT_FEASIBLE", "INVALID_MEDIA"].includes(diagnostic.code(error));
    pageHeading.textContent = preparedResults ? "Delivery needs attention" : replaceableInput ? "Video needs attention" : "Compression failed";
    if (replaceableInput && !preparedResults) phaseCopy.textContent = "Replace or remove the affected video, then continue. Your other selected clips and caption are still available. No videos from this batch were sent.";
    message.focus();
    if ([403, 409, 410].includes(error.status) || error.code === "discord_delivery_failed") {
      submit.hidden = true;
      clips.disabled = true;
      clearSelectionButton.disabled = true;
      clearSelectionButton.hidden = true;
      for (const remove of filesElement.querySelectorAll(".remove-file")) remove.hidden = true;
    }
  }
});

function updateExpiry() {
  const remaining = Math.max(0, Math.ceil((relay.expiresAt() - Date.now()) / 1000));
  expiresElement.textContent = remaining > 0
    ? "Expires in " + readableTime(remaining)
    : "Session expired";
  if (remaining === 0) {
    if (!running) {
      // Prepared files may already be queued; checking their receipt is safe.
      submit.disabled = !preparedResults;
      if (!preparedResults && !clips.disabled) {
        clips.disabled = true;
        clearSelectionButton.disabled = true;
        fileSelection.dispose();
        for (const state of fileSelection.files) {
          updateFile(state, "Session expired", 0);
          state.reselect.disabled = true;
        }
      }
    } else if (runStage === "Browser compression") {
      cancelled = true;
      if (ffmpeg) { ffmpeg.terminate(); ffmpeg = null; }
      showMessage("This session expired before encoding finished. Run /compress for a new link.");
    }
  }
}
// No browser event can interleave this synchronous finalization. Expiry must
// still disable selection and clean up storage if the session elapsed at startup.
clips.disabled = false;
updateExpiry();
expiryTimer = setInterval(updateExpiry, 1000);
window.compressorStartup.ready();
}
await initializeCompressor().catch(() => window.compressorStartup.fail());
