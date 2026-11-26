// Helpers and limits shared by the compressor's browser modules.
// Pure values and functions only: no DOM access, network, or import-time side effects.

const GIB = 1024 ** 3;

// Hard caps for local input copies. Available browser storage can lower them further.
export const INPUT_FILE_LIMIT = GIB;
export const INPUT_BATCH_LIMIT = 4 * GIB;

// HTTP statuses that are retried. The two lists differ on purpose.
// The fallback failure report retries only transient relay/server errors.
export const FAILURE_REPORT_RETRY_STATUSES = [408, 500, 502, 503, 504];
// Delivery also retries a lost connection (0) and rate limiting (429). A 502 is
// retried separately, and only when it is not a Discord delivery rejection.
export const UPLOAD_RETRY_STATUSES = [0, 408, 429, 500, 503, 504];

export function bytesToGiB(bytes) {
  return bytes / GIB;
}

// An Error carrying the diagnostic code used for user messages and telemetry.
export function codedError(message, code) {
  const error = new Error(message);
  error.diagnosticCode = code;
  return error;
}

// The worker can reject with a primitive even when diagnostics cannot load.
export function processingError(value) {
  return value && typeof value === "object"
    ? value
    : new Error(typeof value === "string" ? value : "The video engine stopped unexpectedly.");
}

export function readableSize(bytes) {
  if (bytes < 1024 * 1024) return Math.max(1, Math.round(bytes / 1024)) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

export function readableTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "--:--";
  const rounded = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(rounded / 60);
  return minutes + ":" + String(rounded % 60).padStart(2, "0");
}

// Synchronous channel from the processing modules to the page view. Each event
// type has one handler; it runs inside emit(), so ordering and exceptions are
// exactly those of a direct call.
export function createEmitter() {
  const handlers = new Map();
  return {
    on(type, handler) {
      handlers.set(type, handler);
    },
    emit(type, ...args) {
      return handlers.get(type)?.(...args);
    },
  };
}

// 32 lowercase hex characters from 16 random bytes. Throws if crypto is unavailable.
export function randomHex() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
