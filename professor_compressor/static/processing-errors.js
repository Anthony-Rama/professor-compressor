// Required processing semantics. No network, persistence, or telemetry dependency.
export function normalizeError(value) {
  const error = new Error("The video engine stopped unexpectedly.");
  try {
    if (typeof value === "string") {
      error.message = value;
      const kind = value.match(/^(?:Uncaught )?(TypeError|RangeError|RuntimeError|AbortError):/);
      if (kind) error.name = kind[1];
    } else if (value) {
      for (const key of ["message", "name", "code", "status", "diagnosticCode", "diagnosticFileIndex"]) {
        if (["string", "number"].includes(typeof value[key])) error[key] = value[key];
      }
      if (value.diagnosticFacts) error.diagnosticFacts = value.diagnosticFacts;
    }
  } catch (_) { /* Hostile or inaccessible thrown values remain safe errors. */ }
  return error;
}

export function engineSignal(value) {
  if (typeof value !== "string") return "";
  const line = value.slice(0, 2048);
  if (/^\s+[\w ._-]+\s*:\s/.test(line)) return "";
  if (/Aborted\(\s*OOM\s*\)|out of memory/i.test(line)) return "oom";
  if (/cannot allocate memory|allocation failed|failed to allocate/i.test(line)) return "allocation_failed";
  if (/memory access out of bounds/i.test(line)) return "wasm_bounds";
  if (/RuntimeError:.*(?:unreachable|indirect call.*mismatch)/i.test(line)) return "wasm_trap";
  if (/unknown decoder|no decoder found|decoder .* not found|unsupported codec|platform doesn't supp?port hardware accelerated AV1 decoding/i.test(line)) return "unsupported_codec";
  if (/invalid data found when processing input/i.test(line)) return "invalid_media";
  return "";
}

export const signalPriority = value => ["oom", "allocation_failed"].includes(value) ? 4 :
  ["wasm_bounds", "wasm_trap"].includes(value) ? 3 : value === "unsupported_codec" ? 2 : value ? 1 : 0;

export function errorCode(value, recorded = "") {
  const error = normalizeError(value);
  if (error.name === "AbortError") return "CANCELLED";
  if (["UNSUPPORTED_CODEC", "TARGET_NOT_FEASIBLE"].includes(error.diagnosticCode)) return error.diagnosticCode;
  recorded = error.diagnosticFacts?.engine_signal || recorded;
  const thrown = engineSignal(error.message);
  const signal = signalPriority(thrown) > signalPriority(recorded) ? thrown : recorded;
  if (["oom", "allocation_failed"].includes(signal)) return "BROWSER_MEMORY";
  if (["wasm_bounds", "wasm_trap"].includes(signal)) return "WASM_TRAP";
  if (signal === "unsupported_codec") return "UNSUPPORTED_CODEC";
  if (signal === "invalid_media") return "INVALID_MEDIA";
  if (error.diagnosticCode) return error.diagnosticCode;
  if (error.code === "encoder_start_timeout") return "ENGINE_LOAD_TIMEOUT";
  if (error.status === 0) return "NETWORK";
  if (error.code === "session_expired") return "SESSION_EXPIRED";
  if (error.code === "upload_connection_reset") return "UPLOAD_CONNECTION_RESET";
  return error.status ? "HTTP" : "UNKNOWN";
}

export function stageError(value, operation, fileIndex, elapsed, failureCode, facts = {}) {
  const error = normalizeError(value);
  error.diagnosticFacts = {...error.diagnosticFacts, ...facts, stage: operation};
  error.diagnosticFileIndex = fileIndex;
  if (!error.diagnosticCode && operation === "metadata" && elapsed >= 29500) error.diagnosticCode = "METADATA_TIMEOUT";
  // Recognized exceptions take priority over a generic operation fallback.
  const recognized = errorCode(error);
  error.diagnosticCode = recognized === "UNKNOWN" ? failureCode || recognized : recognized;
  return error;
}

export function createProcessingFallback() {
  let signal = "", source = "";
  const observe = (next, origin) => {
    if (signalPriority(next) > signalPriority(signal)) { signal = next; source = origin; }
  };
  return {
    code: error => errorCode(error, signal),
    log(line) { observe(engineSignal(line), "engine_log"); },
    attempt() { signal = ""; source = ""; },
    mark() { signal = ""; source = ""; },
    async step(operation, fileIndex, action, failureCode) {
      signal = ""; source = "";
      const start = performance.now();
      try { return await action(); }
      catch (value) {
        observe(engineSignal(normalizeError(value).message), "exception");
        throw stageError(value, operation, fileIndex, performance.now() - start, failureCode,
          signal ? {engine_signal: signal, signal_source: source} : {});
      }
    }
  };
}
