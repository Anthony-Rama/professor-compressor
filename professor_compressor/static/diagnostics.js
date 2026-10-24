// Optional diagnostics: no filenames, captions, URLs, raw errors, or video bytes.
// Every method is best-effort; the compressor must work if this transport fails.
export function createDiagnostics(secret, reference, collect = true) {
  let runId = "";
  let queue = [];
  let timer = null;
  let stage = "processing";
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
      while (queue.length && events.length < 20 && queue[0].run === batchRun) {
        events.push(queue.shift().item);
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
      queue.push({run: runId, item: {event_id: event === "client_failed" ? runId : id(), event, file_index: fileIndex, data}});
      if (!timer) timer = setTimeout(flush, 1000);
    } catch (_) { /* Diagnostics never affect processing. */ }
  }
  function code(error) {
    try {
      if (error.diagnosticCode) return error.diagnosticCode;
      if (error.name === "AbortError") return "CANCELLED";
      if (/memory|allocation|out of bounds/i.test(error.message || "")) return "BROWSER_MEMORY";
      if (error.status === 0) return "NETWORK";
      if (error.code === "session_expired") return "SESSION_EXPIRED";
      if (error.status) return "HTTP";
      return "UNKNOWN";
    } catch (_) { return "UNKNOWN"; }
  }
  async function step(operation, fileIndex, action, failureCode) {
    stage = operation;
    const start = performance.now();
    emit("stage_started", fileIndex, {stage});
    try {
      const result = await action();
      emit("stage_finished", fileIndex, {stage: operation, seconds: (performance.now() - start) / 1000});
      return result;
    } catch (error) {
      try {
        if (!error.diagnosticCode) error.diagnosticCode =
          error.name === "AbortError" ? "CANCELLED" :
          /memory|allocation|out of bounds/i.test(error.message || "") ? "BROWSER_MEMORY" :
          error.code === "encoder_start_timeout" ? "ENGINE_LOAD_TIMEOUT" :
          operation === "metadata" && performance.now() - start >= 29500 ? "METADATA_TIMEOUT" : failureCode;
        error.diagnosticFileIndex = fileIndex;
      } catch (_) { /* Some errors are immutable. */ }
      emit("stage_finished", fileIndex, {stage: operation,
        seconds: (performance.now() - start) / 1000, error_code: code(error),
        outcome: code(error) === "CANCELLED" ? "cancelled" : "failed"});
      throw error;
    }
  }
  return {
    begin() { flush(); runId = id(); stage = "validation"; return runId; },
    run() { return runId; }, reference, emit, flush, step, code,
    resume(existing) { if (/^[0-9a-f]{32}$/.test(existing)) runId = existing; },
    mark(operation) { stage = operation; emit("stage_started", 0, {stage}); },
    context() { try { return {...environment(), stage}; } catch (_) { return {}; } },
    fail(error) { try { emit("client_failed", error.diagnosticFileIndex || 0,
      {...environment(), stage, error_code: code(error), retryable: false}); flush(); } catch (_) { /* Optional. */ } }
  };
}
