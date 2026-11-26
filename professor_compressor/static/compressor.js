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
    ASSET_PREFIX,
  } = JSON.parse(document.getElementById("compressor-config").textContent);
  const [
    { EncoderClient },
    { fetchFile },
    { createProcessingFallback },
    { createRelayClient, createPreparedDownloadView },
    { createFileSelection },
    { createVideoEncoder },
  ] = await Promise.all([
    import("/brand/encoder-client.js"),
    import(ASSET_PREFIX + "/util-esm/index.js"),
    import("/brand/processing-errors.js"),
    import("/brand/relay-client.js"),
    import("/brand/file-selection.js"),
    import("/brand/video-encoder.js"),
  ]);
  // Imports cannot be cancelled. A late response must not revive a timed-out page.
  if (!window.compressorStartup.pending()) return;

  let fallbackDiagnosticRun = "";
  const processing = createProcessingFallback();
  const diagnostic = {
    begin: () => {
      try {
        const bytes = new Uint8Array(16);
        crypto.getRandomValues(bytes);
        fallbackDiagnosticRun = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
      } catch (_) {
        fallbackDiagnosticRun = "";
      }
      return fallbackDiagnosticRun;
    },
    run: () => fallbackDiagnosticRun,
    emit: () => {},
    flush: () => {},
    fail: () => {},
    context: () => ({}),
    code: (error) => error?.diagnosticCode || "UNKNOWN",
    mark: () => {},
    configure: () => {},
    media: () => ({}),
    attempt: () => {},
    log: () => {},
    progress: () => {},
    failureContext: () => ({}),
    step: async (_stage, _file, action) => action(),
    ...processing,
  };
  // Loading diagnostics is optional and must not block the compressor's startup.
  void import("/brand/diagnostics.js")
    .then((module) => {
      const reporter = module.createDiagnostics(
        SESSION_SECRET,
        DIAGNOSTIC_REFERENCE,
        DIAGNOSTIC_EVENTS_ENABLED,
      );
      reporter.resume(diagnostic.run());
      // Telemetry may decorate processing, but cannot replace its error semantics.
      Object.assign(diagnostic, reporter, {
        code: processing.code,
        step: (stage, file, action, failureCode) =>
          processing.step(
            stage,
            file,
            () => reporter.step(stage, file, action, failureCode),
            failureCode,
          ),
        log: (line) => {
          processing.log(line);
          reporter.log(line);
        },
        attempt: (...args) => {
          processing.attempt(...args);
          reporter.attempt(...args);
        },
        mark: (...args) => {
          processing.mark(...args);
          reporter.mark(...args);
        },
      });
    })
    .catch(() => {});
  function diagnosticError(message, code) {
    const error = new Error(message);
    error.diagnosticCode = code;
    return error;
  }
  function processingError(value) {
    // The worker can reject with a primitive even when diagnostics cannot load.
    return value && typeof value === "object"
      ? value
      : new Error(typeof value === "string" ? value : "The video engine stopped unexpectedly.");
  }
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
    stallWarning.hidden =
      !running || !encodingActivity || performance.now() - encodingActivity.lastProgress < 120000;
  }
  function noteEncodingProgress(fraction, time) {
    if (!encodingActivity) return;
    const boundedFraction =
      Number.isFinite(fraction) && fraction >= 0 ? Math.min(1, fraction) : null;
    const mediaTime = Number.isFinite(time) && time >= 0 ? time : null;
    const advanced =
      (mediaTime !== null && mediaTime > encodingActivity.mediaTime) ||
      (boundedFraction !== null && boundedFraction > encodingActivity.fraction);
    if (!advanced) return;
    if (mediaTime !== null)
      encodingActivity.mediaTime = Math.max(encodingActivity.mediaTime, mediaTime);
    if (boundedFraction !== null)
      encodingActivity.fraction = Math.max(encodingActivity.fraction, boundedFraction);
    encodingActivity.lastProgress = performance.now();
    updateActivityWarning();
    if (!document.hidden) backgroundWarning.hidden = true;
  }
  async function executeWithActivity(stage, index, action) {
    encodingActivity = { lastProgress: performance.now(), mediaTime: 0, fraction: 0 };
    if (document.hidden) {
      backgroundWarning.textContent =
        "Compression may slow down or pause while this tab is hidden. Keep this browser in the foreground and your screen unlocked.";
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
      const category = !error
        ? "none"
        : error.name === "AbortError"
          ? "cancelled"
          : error.status === 0
            ? "network"
            : error.status
              ? "http"
              : errorCode === "BROWSER_MEMORY"
                ? "memory"
                : /timeout/i.test(error.code || "")
                  ? "timeout"
                  : "processing";
      const hidden = hiddenSeconds();
      hiddenMs = hidden * 1000;
      hiddenStarted = null;
      void fetch(window.location.pathname + "/telemetry", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Upload-Session": SESSION_SECRET,
          "X-Diagnostic-Run": diagnostic.run(),
        },
        body: JSON.stringify({
          outcome,
          category,
          stage: preparedResults ? "delivery" : "processing",
          ...batchMetrics,
          ...diagnostic.context(),
          ...(error ? { error_code: diagnostic.code(error) } : {}),
          encoding_attempts: fileSelection.files.reduce(
            (sum, state) => sum + (state.encodingAttempts || 0),
            0,
          ),
          total_seconds: (performance.now() - batchStarted) / 1000,
          hidden_seconds: hidden,
        }),
        keepalive: true,
        signal: AbortSignal.timeout(5000),
      }).catch(() => {});
    } catch (_) {
      /* Optional reporting cannot interrupt delivery or cleanup. */
    }
  }
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
    const body = count === undefined ? { event } : { event, count };
    if (diagnostic.run()) body.run_id = diagnostic.run();
    // Observability must never hold up local compression or expose file details.
    const send = () => {
      try {
        return fetch(window.location.pathname + "/progress", {
          method: "POST",
          headers: { "X-Upload-Session": SESSION_SECRET, "Content-Type": "application/json" },
          body: JSON.stringify(body),
          cache: "no-store",
          keepalive: true,
          signal: event === "page_left" ? undefined : AbortSignal.timeout(5000),
        }).catch(() => {});
      } catch (_) {
        return Promise.resolve();
      }
    };
    if (event === "page_left") {
      void send();
    } else {
      progressQueue = progressQueue.then(() => (pageIsLeaving ? undefined : send()));
    }
  }

  window.addEventListener("pagehide", () => {
    fileSelection.flushPreparationReports();
    diagnostic.flush();
    pageIsLeaving = true;
    if (!deliveryComplete && !sessionExpired()) reportProgress("page_left");
  });
  window.addEventListener("pageshow", () => {
    pageIsLeaving = false;
  });

  const relay = createRelayClient({
    sessionSecret: SESSION_SECRET,
    expiresInSeconds: SESSION_EXPIRES_SECONDS,
    diagnostic,
    getBatchMetrics: () => batchMetrics,
    hiddenSeconds,
    getCaption: () => caption.value,
    isCancelled: () => cancelled,
    overallProgress,
    phaseCopy,
    updateFile,
    setRunDetail,
  });
  const preparedDownloads = createPreparedDownloadView();

  function sessionExpired() {
    return Date.now() >= relay.expiresAt();
  }

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
    message.textContent =
      text +
      (kind === "error" && DIAGNOSTIC_REFERENCE ? " Error reference: " + DIAGNOSTIC_REFERENCE : "");
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
    feedbackLink.href =
      "mailto:professorcompressor.support@gmail.com?subject=" +
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
      if (!running) {
        await acquired.release();
        return;
      }
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
        backgroundWarning.textContent =
          "This page was in the background, which can slow or pause compression. Keep this browser visible and your screen unlocked. Waiting for the next progress update; nothing has been uploaded yet.";
        backgroundWarning.hidden = false;
      } else backgroundWarning.hidden = true;
    }
    if (document.hidden) {
      backgroundWarning.textContent =
        "Compression may slow down or pause while this tab is hidden. Keep this browser in the foreground and your screen unlocked.";
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
    const reduction =
      state.file.size > 0 ? Math.max(0, (1 - finalBytes / state.file.size) * 100) : 0;
    state.meta.textContent =
      readableSize(state.file.size) +
      " original · " +
      readableSize(finalBytes) +
      " finished · " +
      reduction.toFixed(1) +
      "% smaller";
  }

  const fileSelection = createFileSelection({
    sessionSecret: SESSION_SECRET,
    maxClips: MAX_CLIPS,
    diagnosticEventsEnabled: DIAGNOSTIC_EVENTS_ENABLED,
    diagnostic,
    diagnosticError,
    clips,
    clearSelectionButton,
    submit,
    selection,
    filesElement,
    isRunning: () => running,
    sessionExpired,
    readableSize,
    updateFile,
    reportProgress,
    clearPreparedFiles,
    showSelectionError,
    hideMessage,
  });

  const encoder = createVideoEncoder({
    assetPrefix: ASSET_PREFIX,
    targetBytes: TARGET_BYTES,
    discordLimitBytes: DISCORD_LIMIT_BYTES,
    maxBatchBytes: MAX_BATCH_BYTES,
    EncoderClient,
    fetchFile,
    diagnostic,
    diagnosticError,
    processingError,
    getFiles: () => fileSelection.files,
    getBatchMetrics: () => batchMetrics,
    isCancelled: () => cancelled,
    executeWithActivity,
    noteEncodingProgress,
    updateFile,
    updateFileSizes,
    setRunDetail,
    overallProgress,
    phaseCopy,
  });

  async function reportBrowserFailure(stage, error) {
    // Reuse the same run ID so retrying this fallback report cannot duplicate
    // failure counts or alerts. No raw exception/log text is transmitted.
    const context = diagnostic.failureContext(error);
    const environment = diagnostic.context();
    const state = encoder.currentFile?.index === context.file_index ? encoder.currentFile : null;
    const body = JSON.stringify({
      ...environment,
      stage,
      operation: environment.stage,
      ...(state
        ? {
            input_bytes: state.file.size,
            input_format: state.inputFormat,
            duration_seconds: state.inputDuration,
            ...state.inputMedia,
          }
        : {}),
      ...context,
      run_id: diagnostic.run(),
      error_code: diagnostic.code(error),
    });
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const response = await fetch(window.location.pathname.replace(/\/$/, "") + "/failure", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Upload-Session": SESSION_SECRET,
          },
          body,
          signal: AbortSignal.timeout(4000),
          cache: "no-store",
          credentials: "same-origin",
          keepalive: true,
        });
        if (response.ok || ![408, 500, 502, 503, 504].includes(response.status)) return;
      } catch (reportError) {
        console.debug("The failure notification could not reach the relay.", reportError);
      }
      if (attempt === 0) await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }

  function friendlyError(error) {
    if (error.code === "upload_connection_reset")
      return "Your video finished compressing, but the upload connection was interrupted before the relay confirmed receipt. Keep this page open and use Check delivery / retry, or save your prepared files below. They do not need to be compressed again.";
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
    if (code === "FILE_READ_FAILED")
      return "This clip could not be read. Reselect it from a saved folder and try again.";
    if (code === "WORKER_CRASH")
      return "Compression stopped because the video engine's background worker crashed. The underlying cause was not reported. Try again, or use another desktop browser. Contact support with the error reference if it repeats.";
    if (code === "WORKER_MESSAGE_ERROR")
      return "Compression stopped because the browser could not communicate with the video engine. Reload the page and try again. Contact support with the error reference if it repeats.";
    if (code === "UNSUPPORTED_CODEC")
      return (
        (encoder.currentFile?.inputMedia?.video_codec === "av1"
          ? "This video uses AV1, which this version of the compressor cannot convert. "
          : "This video's codec is not supported for conversion by this version of the compressor. ") +
        "Export it as H.264 MP4 with AAC audio, then replace this file. Your other selected clips are still available."
      );
    if (code === "TARGET_NOT_FEASIBLE") return error.message;
    if (code === "INVALID_MEDIA")
      return "Compression failed because the video engine reported invalid media data. Try playing the original video, then re-export it and try again.";
    if (["ENGINE_LOAD_TIMEOUT", "ENGINE_LOAD_FAILED"].includes(code))
      return (
        "Compression could not start because the video engine failed to load" +
        (code === "ENGINE_LOAD_TIMEOUT" ? " in time" : "") +
        ". Check your connection, reload the page and try again."
      );
    if (code === "ENCODE_FAILED")
      return "Compression failed while encoding this video. The engine did not report a specific cause. Try the clip by itself or another desktop browser. If it fails again, contact support with the error reference.";
    if (error.diagnosticCode || error.status || error.code)
      return error.message || "The request failed. Contact support with the error reference.";
    return "Compression failed because the video engine stopped unexpectedly. A specific cause was not reported. Try again, or contact support with the error reference.";
  }

  function startClock() {
    clearInterval(timer);
    timer = setInterval(() => {
      elapsedElement.textContent =
        "Elapsed " + readableTime((performance.now() - startedAt) / 1000);
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
    encoder.terminate();
    for (const state of fileSelection.files) {
      if (!state.finalSize) updateFile(state, "Cancelled", 0);
    }
    setPhase(
      "compress",
      "Cancelled",
      "No additional files will be sent. You can start again on this page.",
    );
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
    if (!preparedResults && fileSelection.files.some((state) => !fileSelection.isReady(state))) {
      showSelectionError(
        "Wait until every clip is Ready. Reselect or remove any clip that could not be prepared.",
      );
      return;
    }
    diagnostic.begin();
    fileSelection.flushPreparationReports();
    reportProgress("started");
    cancelled = false;
    running = true;
    setProcessingView(true);
    pageHeading.focus({ preventScroll: true });
    document.querySelector("main").scrollIntoView({ block: "start" });
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
      batchMetrics = {
        batch_id: diagnostic.run(),
        engine_seconds: 0,
        engine: "unused",
        upload_attempts: 0,
      };
      hiddenMs = 0;
    }
    hiddenStarted = document.hidden ? performance.now() : null;
    runStage = preparedResults ? "Relay upload or Discord delivery" : "Browser compression";
    if (preparedResults) {
      pageHeading.textContent = "Checking Discord delivery";
      setPhase(
        "send",
        "Checking Discord delivery",
        "Checking the existing batch before retrying delivery.",
      );
    } else {
      setPhase(
        "compress",
        "Preparing videos",
        "Preparing videos on this device. Nothing has been sent yet.",
      );
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
        const session = await diagnostic.step(
          "session",
          0,
          () => relay.sessionRequest("heartbeat"),
          "SESSION_EXPIRED",
        );
        if (cancelled) throw new DOMException("Cancelled", "AbortError");
        if (!session.ready) throw new Error("This session already has a delivery in progress.");
        stopHeartbeat();
        heartbeatTimer = setInterval(() => {
          void keepSessionAlive();
        }, 60000);
        const formats = await Promise.all(
          fileSelection.files.map(async (state) => {
            // Size is available even if container or metadata reading fails.
            diagnostic.emit("stage_started", state.index, {
              stage: "validation",
              input_bytes: state.file.size,
            });
            const format = await diagnostic.step(
              "validation",
              state.index,
              () => encoder.videoSignature(state.file, state.index),
              "INVALID_SIGNATURE",
            );
            diagnostic.emit("stage_finished", state.index, {
              stage: "validation",
              input_format: format,
              input_bytes: state.file.size,
            });
            return format;
          }),
        );
        if (cancelled) throw new DOMException("Cancelled", "AbortError");
        const needsEncoder = fileSelection.files.some(
          (state, index) =>
            state.file.size > encoder.unchangedLimitBytes() || formats[index] !== "mp4",
        );
        if (needsEncoder && !encoder.loaded) {
          setPhase(
            "compress",
            "Loading video engine",
            "The browser is preparing its video tools. A first or uncached visit may take a few minutes on a slow connection; your video stays on this device.",
          );
          await encoder.start();
        }
        setPhase(
          "compress",
          "Compressing on your device",
          "Compression runs locally. Nothing is sent until every video is ready.",
        );
        const results = [];
        for (const state of fileSelection.files) {
          if (cancelled) throw new DOMException("Cancelled", "AbortError");
          results.push(await encoder.compressWithRetry(state));
        }
        preparedResults = results;
        batchMetrics.engine = needsEncoder ? encoder.mode : "unused";
        batchMetrics.pre_upload_seconds = (performance.now() - batchStarted) / 1000;
      }
      stopHeartbeat();
      encoder.clearCurrentFile();
      overallProgress.value = 80;
      cancelButton.hidden = true;
      runStage = "Relay upload or Discord delivery";
      pageHeading.textContent = "Sending to Discord";
      setPhase(
        "send",
        "Sending finished files",
        "Compression is complete. Finished MP4 files are now being sent through the relay to Discord.",
      );
      const response = await relay.uploadWithRetry(preparedResults);
      reportMetrics("delivered");
      diagnostic.flush();
      for (const result of preparedResults) updateFile(result.state, "Delivered", 100);
      overallProgress.value = 100;
      setRunDetail("Complete");
      setPhase("done", "Delivered to Discord", response.message);
      showMessage(
        "You can close this page and return to Discord. To upload more videos, run /compress again.",
        "success",
      );
      deliveryComplete = true;
      showFeedback("success");
      pageHeading.textContent = "Delivered to Discord";
      form.classList.add("completed");
      const completionSummary = document.getElementById("completion-summary");
      completionSummary.textContent =
        "✓ " +
        preparedResults.length +
        (preparedResults.length === 1 ? " video delivered" : " videos delivered") +
        " · Completed in " +
        readableTime((performance.now() - startedAt) / 1000);
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
        try {
          diagnostic.fail(error);
        } catch (_) {
          /* Optional diagnostic only. */
        }
      }
      reportMetrics(cancelled || error.name === "AbortError" ? "cancelled" : "failed", error);
      if (cancelled || error.name === "AbortError") {
        finishRun();
        return;
      }
      console.error(error);
      void reportBrowserFailure(runStage, error);
      setPhase(
        "compress",
        "Action needed",
        preparedResults
          ? "Delivery could not be confirmed. Check Discord before retrying."
          : "Compression failed. No videos from this batch were sent to Discord. See the reason below.",
      );
      setRunDetail("Stopped");
      showMessage(
        (error.diagnosticFileIndex ? "Video " + error.diagnosticFileIndex + ": " : "") +
          friendlyError(error),
      );
      showFeedback("failure");
      finishRun();
      if (!preparedResults) {
        for (const state of fileSelection.files) {
          if (
            state.index !== error.diagnosticFileIndex &&
            !state.finalSize &&
            fileSelection.isReady(state)
          )
            updateFile(state, "Ready", 0);
        }
      }
      if (
        ["FILE_READ_FAILED", "UNSUPPORTED_CODEC", "TARGET_NOT_FEASIBLE", "INVALID_MEDIA"].includes(
          diagnostic.code(error),
        ) &&
        !preparedResults
      ) {
        fileSelection.markNeedsReplacement(error.diagnosticFileIndex, friendlyError(error));
      }
      const replaceableInput = [
        "FILE_READ_FAILED",
        "UNSUPPORTED_CODEC",
        "TARGET_NOT_FEASIBLE",
        "INVALID_MEDIA",
      ].includes(diagnostic.code(error));
      pageHeading.textContent = preparedResults
        ? "Delivery needs attention"
        : replaceableInput
          ? "Video needs attention"
          : "Compression failed";
      if (replaceableInput && !preparedResults)
        phaseCopy.textContent =
          "Replace or remove the affected video, then continue. Your other selected clips and caption are still available. No videos from this batch were sent.";
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
    expiresElement.textContent =
      remaining > 0 ? "Expires in " + readableTime(remaining) : "Session expired";
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
        encoder.terminate();
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
