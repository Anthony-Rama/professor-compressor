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

// 32 lowercase hex characters from 16 random bytes. Throws if crypto is unavailable.
export function randomHex() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
