// Optional diagnostics: no filenames, captions, URLs, raw errors, or video bytes.
// Every method is best-effort; the compressor must work if this transport fails.
function normalizeError(value) {
  // FFmpeg workers reject with strings. Always use a fresh, mutable Error;
  // DOMExceptions and frozen Error objects must also retain their context.
  const error = new Error("The video engine stopped unexpectedly.");
  try {
    if (typeof value === "string") {
      error.message = value;
      const kind = value.match(/^(?:Uncaught )?(TypeError|RangeError|RuntimeError|AbortError):/);
      if (kind) error.name = kind[1];
    }
    else if (value) {
      for (const key of ["message", "name", "code", "status", "diagnosticCode", "diagnosticFileIndex"]) {
        if (["string", "number"].includes(typeof value[key])) error[key] = value[key];
      }
      if (value.diagnosticFacts) error.diagnosticFacts = value.diagnosticFacts;
    }
  } catch (_) { /* Unknown thrown values must not break error reporting. */ }
  return error;
}
// Classify locally. Never retain or transmit the line that matched.
export function engineSignal(value) {
  if (typeof value !== "string") return "";
  const line = value.slice(0, 2048);
  // FFmpeg can print arbitrary embedded tag values; don't classify metadata
  // headings (e.g. a title named "out of memory") as engine failures.
  if (/^\s+[\w ._-]+\s*:\s/.test(line)) return "";
  if (/Aborted\(\s*OOM\s*\)|out of memory/i.test(line)) return "oom";
  if (/cannot allocate memory|allocation failed|failed to allocate/i.test(line)) return "allocation_failed";
  if (/memory access out of bounds/i.test(line)) return "wasm_bounds";
  if (/RuntimeError:.*(?:unreachable|indirect call.*mismatch)/i.test(line)) return "wasm_trap";
  if (/unknown decoder|decoder .* not found|unsupported codec/i.test(line)) return "unsupported_codec";
  if (/invalid data found when processing input/i.test(line)) return "invalid_media";
  return "";
}

export function commandSettings(args) {
  const value = flag => { const index = args.indexOf(flag); return index < 0 ? undefined : args[index + 1]; };
  const input = args.indexOf("-i"), decoder = args.slice(0, Math.max(0, input));
  const encoder = args.slice(input + 2), threads = encoder.indexOf("-threads");
  const fixed = decoder.indexOf("-threads");
  const codec = value("-c:v") || value("-c");
  const facts = {decoder_mode: fixed < 0 ? "auto" : "fixed"};
  if (fixed >= 0) facts.decoder_threads = Number(decoder[fixed + 1]);
  if (threads >= 0) facts.encoder_threads = Number(encoder[threads + 1]);
  if (["libx264", "copy"].includes(codec)) facts.video_encoder = codec;
  facts.command_mode = codec === "copy" ? "copy" : args.includes("-crf") ? "quality" : "bitrate";
  if (value("-preset") === "veryfast") facts.encoder_preset = "veryfast";
  if (/^\d+k$/.test(value("-b:v") || "")) facts.video_kbps = Number(value("-b:v").slice(0, -1));
  return facts;
}

export function mediaDetails(video, audio) {
  const oneOf = (value, allowed) => allowed.includes(value) ? value : "other";
  const pixel = oneOf(video.pix_fmt, ["yuv420p", "yuv420p10le", "yuv420p12le", "yuv422p", "yuv422p10le", "yuv444p", "yuv444p10le", "nv12", "p010le"]);
  const facts = {
    pixel_format: pixel,
    video_profile: oneOf(video.profile, ["Main", "Main 10", "Main Still Picture", "Baseline", "Constrained Baseline", "High", "High 10", "High 4:2:2", "High 4:4:4 Predictive", "Simple Profile", "Advanced Simple Profile", "Profile 0", "Profile 1", "Profile 2", "Profile 3"]),
    color_transfer: oneOf(video.color_transfer, ["bt709", "smpte2084", "arib-std-b67", "smpte170m", "bt2020-10", "bt2020-12", "unknown"]),
    chroma_subsampling: pixel.includes("420") || ["nv12", "p010le"].includes(pixel) ? "420" : pixel.includes("422") ? "422" : pixel.includes("444") ? "444" : "other"
  };
  const depth = pixel.match(/p(10|12)le$/);
  if (pixel !== "other") facts.bit_depth = pixel === "p010le" ? 10 : depth ? Number(depth[1]) : 8;
  for (const [key, raw] of [["pixel_format", video.pix_fmt], ["video_profile", video.profile], ["color_transfer", video.color_transfer]]) {
    if (typeof raw !== "string") delete facts[key];
  }
  if (typeof video.pix_fmt !== "string") delete facts.chroma_subsampling;
  for (const [key, value] of [["video_level", video.level], ["audio_channels", audio?.channels], ["audio_sample_rate", audio?.sample_rate]]) {
    if (value !== undefined && value !== null && value !== "" && Number.isFinite(Number(value)) && Number(value) > 0) facts[key] = Number(value);
  }
  const fps = value => {const [n,d] = String(value || "0/0").split("/").map(Number); return d > 0 ? n/d : NaN;};
  const average = fps(video.avg_frame_rate), nominal = fps(video.r_frame_rate);
  if (average > 0 && nominal > 0) facts.frame_rates_differ = Math.abs(average - nominal) > 0.01;
  const rotation = video.side_data_list?.find(item => Number.isFinite(item.rotation))?.rotation;
  if (rotation !== undefined && rotation % 90 === 0) facts.rotation_quarter_turns = ((rotation / 90) % 4 + 4) % 4;
  return facts;
}
export function createDiagnostics(secret, reference, collect = true) {
  let runId = "";
  let queue = [];
  let timer = null;
  let stage = "processing";
  let sequence = 0, runStarted = performance.now(), command = {}, signal = "", signalSource = "";
  let attempt = 0, activeFile = 0, lastProgress = null, lastMedia = null, lastPercent = null, lastProgressReport = 0;
  const facts = () => ({...command, ...(attempt ? {attempt} : {}),
    ...(signal ? {engine_signal: signal, signal_source: signalSource} : {}),
    ...(lastProgress === null ? {} : {progress_idle_seconds: Math.max(0, (performance.now() - lastProgress) / 1000)}),
    ...(lastMedia === null ? {} : {last_media_seconds: lastMedia}),
    ...(lastPercent === null ? {} : {last_progress_percent: lastPercent}), page_hidden: document.hidden});
  const failureFacts = error => ({...facts(), ...error?.diagnosticFacts,
    ...(Number.isInteger(error?.status) && error.status >= 100 && error.status <= 599 ? {http_status: error.status} : {})});
  const memorySignals = new Set(["oom", "allocation_failed"]);
  const signalPriority = value => memorySignals.has(value) ? 3 : ["wasm_bounds", "wasm_trap"].includes(value) ? 2 : value ? 1 : 0;
  function observeSignal(next, source) {
    if (next && signalPriority(next) > signalPriority(signal)) { signal = next; signalSource = source; }
  }
  const id = () => {
    try {
      const bytes = new Uint8Array(16);
      crypto.getRandomValues(bytes);
      return Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
    } catch (_) { return ""; }
  };
  const environment = () => {
    const agent = navigator.userAgent;
    const patterns = [["Edge", /Edg\/(\d+)/], ["Opera", /OPR\/(\d+)/],
      ["Firefox", /Firefox\/(\d+)/], ["Chrome", /Chrome\/(\d+)/],
      ["Safari", /Version\/(\d+).*Safari/]];
    for (const [browser, pattern] of patterns) {
      const match = agent.match(pattern);
      if (match) return {browser, browser_major: Number(match[1]),
        device: /Mobile|Android|iPhone|iPad/.test(agent) ? "mobile" : "desktop"};
    }
    return {browser: "Other", device: "unknown"};
  };
  function flush() {
    try {
      clearTimeout(timer);
      timer = null;
      if (!runId || !queue.length) return;
      const batchRun = queue[0].run;
      const events = [];
      let bytes = 0;
      while (queue.length && events.length < 20 && queue[0].run === batchRun) {
        const size = new TextEncoder().encode(JSON.stringify(queue[0].item)).length;
        if (size > 12000) { queue.shift(); continue; }
        if (bytes + size > 12000) break;
        bytes += size;
        events.push(queue.shift().item);
      }
      if (!events.length) {
        if (queue.length) timer = setTimeout(flush, 1000);
        return;
      }
      void fetch(location.pathname + "/events", {
        method: "POST", headers: {"Content-Type": "application/json", "X-Upload-Session": secret},
        body: JSON.stringify({run_id: batchRun, events}), keepalive: true,
        signal: AbortSignal.timeout(4000), cache: "no-store"
      }).catch(() => {});
      if (queue.length) timer = setTimeout(flush, 1000);
    } catch (_) { queue = []; }
  }
  function emit(event, fileIndex = 0, data = {}) {
    try {
      if (!collect || !runId || queue.length >= 100) return;
      queue.push({run: runId, item: {event_id: event === "client_failed" ? runId : id(), event, file_index: fileIndex,
        data: {...data, client_sequence: ++sequence, client_elapsed_seconds: Math.max(0, (performance.now() - runStarted) / 1000)}}});
      if (!timer) timer = setTimeout(flush, 1000);
    } catch (_) { /* Diagnostics never affect processing. */ }
  }
  function code(error) {
    try {
      error = normalizeError(error);
      if (error.name === "AbortError") return "CANCELLED";
      const recorded = failureFacts(error).engine_signal;
      const thrown = engineSignal(error.message);
      const evidence = signalPriority(thrown) > signalPriority(recorded) ? thrown : recorded;
      if (memorySignals.has(evidence)) return "BROWSER_MEMORY";
      if (["wasm_bounds", "wasm_trap"].includes(evidence)) return "WASM_TRAP";
      if (error.diagnosticCode) return error.diagnosticCode;
      if (error.status === 0) return "NETWORK";
      if (error.code === "session_expired") return "SESSION_EXPIRED";
      if (error.status) return "HTTP";
      return "UNKNOWN";
    } catch (_) { return "UNKNOWN"; }
  }
  async function step(operation, fileIndex, action, failureCode) {
    stage = operation;
    const execution = ["encoding", "remux"].includes(operation);
    // Signal evidence belongs to one operation, never a previous file/retry.
    signal = ""; signalSource = ""; lastProgress = null; lastMedia = null; lastPercent = null;
    lastProgressReport = performance.now();
    if (!execution) command = {};
    const start = performance.now();
    emit("stage_started", fileIndex, {stage, ...facts()});
    try {
      const result = await action();
      if (execution && Number.isInteger(result) && result >= 0 && result <= 255) command.exit_code = result;
      emit("stage_finished", fileIndex, {stage: operation, ...facts(), seconds: (performance.now() - start) / 1000,
        ...(execution && result !== 0 ? {outcome: "failed", error_code: code({diagnosticCode: "ENCODE_FAILED"})} : {})});
      return result;
    } catch (value) {
      const error = normalizeError(value);
      observeSignal(engineSignal(error.message), "exception");
      error.diagnosticFacts = {...facts(), stage: operation,
        exception_kind: ["Error", "TypeError", "RangeError", "RuntimeError", "AbortError"].includes(error.name) ? error.name : "other"};
      try {
        if (!error.diagnosticCode) error.diagnosticCode =
          error.name === "AbortError" ? "CANCELLED" :
          memorySignals.has(signal) ? "BROWSER_MEMORY" :
          error.code === "encoder_start_timeout" ? "ENGINE_LOAD_TIMEOUT" :
          operation === "metadata" && performance.now() - start >= 29500 ? "METADATA_TIMEOUT" : failureCode;
        error.diagnosticFileIndex = fileIndex;
      } catch (_) { /* Some errors are immutable. */ }
      error.diagnosticCode = code(error);
      emit("stage_finished", fileIndex, {...error.diagnosticFacts, stage: operation,
        seconds: (performance.now() - start) / 1000, error_code: code(error),
        outcome: code(error) === "CANCELLED" ? "cancelled" : "failed"});
      throw error;
    }
  }
  return {
    begin() { flush(); runId = id(); sequence = 0; runStarted = performance.now(); stage = "validation";
      command = {}; signal = ""; signalSource = ""; attempt = 0; activeFile = 0;
      lastProgress = null; lastMedia = null; lastPercent = null;
      emit("stage_started", 0, {stage, cross_origin_isolated: self.crossOriginIsolated === true,
        shared_array_buffer: typeof SharedArrayBuffer !== "undefined", webassembly: typeof WebAssembly !== "undefined"});
      return runId; },
    run() { return runId; }, reference, emit, flush, step, code,
    resume(existing) { if (/^[0-9a-f]{32}$/.test(existing)) runId = existing; },
    mark(operation) { stage = operation; command = {}; signal = ""; signalSource = "";
      attempt = 0; activeFile = 0; lastProgress = null; lastMedia = null; lastPercent = null;
      emit("stage_started", 0, {stage}); },
    configure(args, engine, build, pass, adjustment) { try {
      command = {...commandSettings(args), engine, asset_build: build,
        ...(pass === undefined ? {} : {encode_pass: pass, size_adjustment: adjustment === true})};
    } catch (_) { command = {}; } },
    media(video, audio) { try { return mediaDetails(video, audio); } catch (_) { return {}; } },
    attempt(fileIndex, number) { activeFile = fileIndex; attempt = number; command = {}; signal = ""; signalSource = "";
      lastProgress = null; lastMedia = null; lastPercent = null;
      emit("attempt_started", fileIndex, {attempt: number}); },
    log(line) { try { observeSignal(engineSignal(line), "engine_log"); } catch (_) {} },
    progress(fraction, time) { try {
      if (Number.isFinite(fraction)) lastPercent = Math.max(0, Math.min(100, fraction * 100));
      if (Number.isFinite(time) && time >= 0) lastMedia = time / 1000000;
      lastProgress = performance.now();
      if (lastProgress - lastProgressReport >= 15000 && ["encoding", "remux"].includes(stage)) {
        lastProgressReport = lastProgress;
        emit("processing_progress", activeFile, {stage, ...facts()});
      }
    } catch (_) {} },
    failureContext(error) { try { return {...failureFacts(error), file_index: error?.diagnosticFileIndex ?? activeFile}; } catch (_) { return {}; } },
    context() { try { return {...environment(), stage}; } catch (_) { return {}; } },
    fail(error) { try { error = normalizeError(error); emit("client_failed", error.diagnosticFileIndex ?? activeFile,
      {...environment(), stage, ...failureFacts(error), error_code: code(error), retryable: false}); flush(); } catch (_) { /* Optional. */ } }
  };
}
