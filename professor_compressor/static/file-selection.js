// File picker, drag and drop, and browser-local input preparation.
import { INPUT_BATCH_LIMIT, INPUT_FILE_LIMIT, bytesToGiB, codedError } from "./shared.js";

export function createFileSelection({
  sessionSecret: SESSION_SECRET,
  maxClips: MAX_CLIPS,
  diagnosticEventsEnabled: DIAGNOSTIC_EVENTS_ENABLED,
  diagnostic,
  clips,
  clearSelectionButton,
  submit,
  selection,
  filesElement,
  isRunning,
  sessionExpired,
  readableSize,
  updateFile,
  reportProgress,
  clearPreparedFiles,
  showSelectionError,
  hideMessage,
}) {
  let selectedFiles = [];
  const inputCopies = new Map();
  const preservation = new Promise((resolve, reject) => {
    let expired = false;
    const timer = setTimeout(() => {
      expired = true;
      reject(
        codedError(
          "Local file preparation could not start in time. Reload this page and reselect your clips. Check your connection if this repeats.",
          "INPUT_PREPARATION_TIMEOUT",
        ),
      );
    }, 30000);
    import("/brand/input-preservation.js")
      .then((module) => {
        if (!expired) resolve(new module.InputPreserver());
      })
      .catch(() => {
        if (!expired)
          reject(
            codedError(
              "Local file preparation could not start. Reload this page and reselect your clips, or try an updated Chrome or Edge browser.",
              "INPUT_STORAGE_UNAVAILABLE",
            ),
          );
      })
      .finally(() => clearTimeout(timer));
  });
  void preservation.catch(() => {});
  // Preparation has its own run ID, before Compress is pressed. Never mark a
  // compression session failed or started for a selection/preparation problem.
  let preparationRun = "",
    preparationSequence = 0,
    preparationTimer = null;
  const preparationQueue = [];
  function flushPreparationReports() {
    try {
      clearTimeout(preparationTimer);
      preparationTimer = null;
      if (!preparationQueue.length) return;
      const events = preparationQueue.splice(0, 20);
      void fetch(location.pathname + "/events", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Upload-Session": SESSION_SECRET },
        body: JSON.stringify({ run_id: preparationRun, events }),
        keepalive: true,
        signal: AbortSignal.timeout(4000),
        cache: "no-store",
      }).catch(() => {});
      if (preparationQueue.length) preparationTimer = setTimeout(flushPreparationReports, 1000);
    } catch (_) {
      preparationQueue.length = 0;
    }
  }
  function preparationReport(fileIndex, inputBytes) {
    try {
      if (!DIAGNOSTIC_EVENTS_ENABLED) return () => {};
      if (!preparationRun) preparationRun = crypto.randomUUID().replaceAll("-", "");
      const start = performance.now();
      let finished = false;
      const send = (event, data = {}) => {
        try {
          if (preparationQueue.length >= 100) return;
          const { browser, browser_major, device } = diagnostic.context();
          preparationQueue.push({
            event_id: crypto.randomUUID().replaceAll("-", ""),
            event,
            file_index: fileIndex,
            data: {
              stage: "preparation",
              input_bytes: inputBytes,
              browser,
              browser_major,
              device,
              client_sequence: ++preparationSequence,
              seconds: (performance.now() - start) / 1000,
              ...data,
            },
          });
          if (!preparationTimer) preparationTimer = setTimeout(flushPreparationReports, 1000);
        } catch (_) {
          /* Best-effort, content-free telemetry must never block copying. */
        }
      };
      send("stage_started");
      return (outcome, error) => {
        if (finished) return;
        finished = true;
        const allowed = [
          "FILE_READ_FAILED",
          "INPUT_STORAGE_FULL",
          "INPUT_STORAGE_UNAVAILABLE",
          "INPUT_WRITE_UNSUPPORTED",
          "INPUT_TOO_LARGE",
          "INPUT_PREPARATION_TIMEOUT",
        ];
        send(
          "stage_finished",
          outcome === "ready"
            ? {}
            : {
                outcome,
                error_code:
                  outcome === "cancelled"
                    ? "CANCELLED"
                    : allowed.includes(error?.diagnosticCode)
                      ? error.diagnosticCode
                      : "INPUT_STORAGE_UNAVAILABLE",
              },
        );
      };
    } catch (_) {
      return () => {};
    }
  }
  window.addEventListener("pagehide", (event) => {
    if (!event.persisted) void preservation.then((store) => store.close()).catch(() => {});
  });

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
      const identity = fileIdentity(file);
      let copy = inputCopies.get(identity);
      if (!copy) {
        copy = {
          identity,
          source: file,
          file,
          phase: "preparing",
          fraction: 0,
          removed: false,
          report: preparationReport(index + 1, file.size),
        };
        inputCopies.set(identity, copy);
        void prepareInputCopy(copy);
      }
      status.textContent =
        copy.phase === "ready"
          ? "Ready"
          : copy.phase === "failed"
            ? "Reselect file"
            : "Preparing local copy…";
      const actions = document.createElement("div");
      actions.className = "file-actions";
      const remove = document.createElement("button");
      remove.className = "remove-file";
      remove.type = "button";
      remove.setAttribute("aria-label", "Remove " + file.name);
      remove.title = "Remove " + file.name;
      const icon = clearSelectionButton.querySelector("svg").cloneNode(true);
      icon.setAttribute("width", "18");
      icon.setAttribute("height", "18");
      remove.append(icon);
      remove.addEventListener("click", () => removeSelectedFile(index));
      const reselect = document.createElement("button");
      reselect.type = "button";
      reselect.className = "secondary reselect-file";
      reselect.textContent = "Reselect";
      reselect.setAttribute("aria-label", "Reselect " + file.name);
      reselect.hidden = copy.phase !== "failed";
      reselect.addEventListener("click", () => reselectFile(index));
      actions.append(status, reselect, remove);
      head.append(name, actions);
      const progress = document.createElement("progress");
      progress.max = 100;
      progress.value = copy.phase === "preparing" ? copy.fraction * 100 : 0;
      const meta = document.createElement("div");
      meta.className = "file-meta";
      meta.textContent = copy.error || readableSize(file.size) + " original";
      card.append(head, progress, meta);
      filesElement.append(card);
      return {
        file: copy.file,
        identity,
        index: index + 1,
        card,
        status,
        progress,
        meta,
        reselect,
        finalSize: 0,
      };
    });
    filesElement.hidden = files.length === 0;
  }

  function refreshPreparation() {
    if (isRunning() || clips.disabled) return;
    const ready =
      selectedFiles.length > 0 &&
      selectedFiles.every((state) => inputCopies.get(state.identity)?.phase === "ready");
    const preparing = selectedFiles.some(
      (state) => inputCopies.get(state.identity)?.phase === "preparing",
    );
    submit.disabled = !ready || sessionExpired() || pendingSelections > 0;
    submit.textContent =
      pendingSelections > 0
        ? "Checking storage…"
        : preparing
          ? "Preparing videos…"
          : "Compress and send to Discord";
  }

  function discardInputCopy(copy) {
    copy.removed = true;
    copy.report("cancelled");
    if (copy.task) void copy.task.dispose().catch(() => {});
  }

  async function prepareInputCopy(copy) {
    try {
      const store = await preservation;
      if (copy.removed) return;
      copy.task = store.create(copy.source, (fraction) => {
        copy.fraction = fraction;
        const state = selectedFiles.find((item) => item.identity === copy.identity);
        if (state && !copy.removed && !isRunning()) state.progress.value = fraction * 100;
      });
      const saved = await copy.task.promise;
      if (copy.removed) {
        void copy.task.dispose().catch(() => {});
        return;
      }
      copy.file = saved;
      copy.source = null;
      copy.phase = "ready";
      copy.report("ready");
      const state = selectedFiles.find((item) => item.identity === copy.identity);
      if (state) {
        state.file = saved;
        updateFile(state, "Ready", 0);
        setSelectedFiles(selectedFiles.map((item) => item.file));
      }
    } catch (error) {
      if (copy.removed) return;
      copy.report("failed", error);
      copy.phase = "failed";
      copy.source = null;
      copy.error = error?.diagnosticCode
        ? error.message
        : "Local file preparation could not start. Reload this page and reselect the clip, or try an updated Chrome or Edge browser.";
      const state = selectedFiles.find((item) => item.identity === copy.identity);
      if (state) {
        updateFile(state, "Reselect file", 0);
        state.meta.textContent = copy.error;
        state.reselect.hidden = false;
        showSelectionError("Video " + state.index + ": " + copy.error);
      }
    } finally {
      refreshPreparation();
    }
  }

  function reselectFile(index) {
    if (isRunning() || clips.disabled) return;
    const old = selectedFiles[index];
    if (!old || inputCopies.get(old.identity)?.phase !== "failed") return;
    const picker = document.createElement("input");
    picker.type = "file";
    picker.accept = clips.accept;
    picker.hidden = true;
    document.body.append(picker);
    picker.addEventListener("cancel", () => picker.remove(), { once: true });
    picker.addEventListener(
      "change",
      () => {
        const file = picker.files[0];
        picker.remove();
        if (!file || isRunning() || clips.disabled || selectedFiles[index] !== old) return;
        if (selectedFiles.some((state) => state !== old && state.identity === fileIdentity(file))) {
          showSelectionError("That video is already selected.");
          return;
        }
        const replacementBytes = selectedFiles.reduce(
          (sum, state) => sum + (state === old ? file.size : state.file.size),
          0,
        );
        if (file.size > INPUT_FILE_LIMIT || replacementBytes > INPUT_BATCH_LIMIT) {
          preparationReport(0, replacementBytes)("failed", { diagnosticCode: "INPUT_TOO_LARGE" });
          showSelectionError(
            "That replacement exceeds the 1 GiB per-video or 4 GiB batch limit. Your existing selection was kept.",
          );
          return;
        }
        selectionGeneration += 1;
        discardInputCopy(inputCopies.get(old.identity));
        inputCopies.delete(old.identity);
        setSelectedFiles(selectedFiles.map((state) => (state === old ? file : state.file)));
        hideMessage();
        renderSelection();
      },
      { once: true },
    );
    picker.click();
  }

  function renderSelection() {
    clearPreparedFiles();
    const files = Array.from(clips.files);
    const identities = new Set(files.map(fileIdentity));
    for (const [identity, copy] of inputCopies) {
      if (!identities.has(identity)) {
        discardInputCopy(copy);
        inputCopies.delete(identity);
      }
    }
    clearSelectionButton.disabled = files.length === 0 || isRunning() || clips.disabled;
    const validCount = files.length >= 1 && files.length <= MAX_CLIPS;
    submit.disabled = !validCount || isRunning() || sessionExpired();
    submit.textContent = "Compress and send to Discord";
    selection.className = validCount || files.length === 0 ? "selection" : "selection error";
    if (files.length === 0) {
      selection.textContent = "No videos selected";
    } else if (files.length > MAX_CLIPS) {
      selection.textContent = "Choose no more than " + MAX_CLIPS + " videos.";
    } else {
      const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
      selection.textContent =
        files.length +
        (files.length === 1 ? " video" : " videos") +
        " selected · " +
        bytesToGiB(totalBytes).toFixed(2) +
        " / 4 GiB";
    }
    renderSelectedFiles(files);
    refreshPreparation();
    if (validCount) reportProgress("selected", files.length);
  }

  function setSelectedFiles(files) {
    const transfer = new DataTransfer();
    for (const file of files) transfer.items.add(file);
    clips.files = transfer.files;
  }

  function fileIdentity(file) {
    return [file.name, file.size, file.lastModified, file.type].join("\u0000");
  }

  let selectionQueue = Promise.resolve();
  let pendingSelections = 0;
  let selectionGeneration = 0;
  function addSelectedFiles(incoming) {
    const generation = selectionGeneration;
    pendingSelections += 1;
    refreshPreparation();
    selectionQueue = selectionQueue
      .then(() => {
        if (generation === selectionGeneration) return acceptSelectedFiles(incoming);
      })
      .catch(() => {
        showSelectionError("Could not check this selection. Please select the videos again.");
      })
      .finally(() => {
        pendingSelections -= 1;
        refreshPreparation();
      });
  }

  async function acceptSelectedFiles(incoming) {
    if (isRunning() || clips.disabled) return;
    if (sessionExpired()) {
      showSelectionError(
        "This session expired. Run /compress for a new link before selecting videos.",
      );
      return;
    }
    const current = selectedFiles.map((state) => state.file);
    if (!incoming.length) {
      setSelectedFiles(current);
      return;
    }
    const existing = new Set(current.map(fileIdentity));
    const additions = incoming.filter((file) => {
      const identity = fileIdentity(file);
      if (existing.has(identity)) return false;
      existing.add(identity);
      return true;
    });
    if (current.length + additions.length > MAX_CLIPS) {
      setSelectedFiles(current);
      showSelectionError(
        "Choose no more than " + MAX_CLIPS + " videos total. Remove some before adding more.",
      );
      return;
    }
    if (!additions.length) {
      setSelectedFiles(current);
      showSelectionError("Those videos are already selected.");
      return;
    }
    if (additions.some((file) => file.size > INPUT_FILE_LIMIT)) {
      preparationReport(
        0,
        additions.reduce((sum, file) => sum + file.size, 0),
      )("failed", { diagnosticCode: "INPUT_TOO_LARGE" });
      setSelectedFiles(current);
      showSelectionError("Each video must be 1 GiB or smaller. Existing clips were kept.");
      return;
    }
    const totalBytes = [...current, ...additions].reduce((sum, file) => sum + file.size, 0);
    if (totalBytes > INPUT_BATCH_LIMIT) {
      preparationReport(0, totalBytes)("failed", { diagnosticCode: "INPUT_TOO_LARGE" });
      setSelectedFiles(current);
      showSelectionError(
        "This selection would total " +
          bytesToGiB(totalBytes).toFixed(2) +
          " GiB, above the 4 GiB batch limit. Existing clips were kept. Add fewer videos or remove some first.",
      );
      return;
    }
    // Serialize admissions so rapid drops cannot race the same quota estimate.
    // Removal/clear during an estimate must not resurrect the prior selection.
    const snapshot = selectedFiles;
    const report = preparationReport(
      0,
      additions.reduce((sum, file) => sum + file.size, 0),
    );
    let store;
    try {
      store = await preservation;
      await store.checkAvailable(additions.reduce((sum, file) => sum + file.size, 0));
    } catch (error) {
      if (selectedFiles === snapshot && !isRunning() && !clips.disabled) {
        report("failed", error);
        setSelectedFiles(current);
        showSelectionError(error.message);
      } else report("cancelled");
      return;
    }
    if (selectedFiles !== snapshot || isRunning() || clips.disabled || sessionExpired()) {
      report("cancelled");
      return;
    }
    report("ready");
    setSelectedFiles([...current, ...additions]);
    hideMessage();
    renderSelection();
  }

  function removeSelectedFile(index) {
    if (isRunning() || clips.disabled) return;
    selectionGeneration += 1;
    setSelectedFiles(
      selectedFiles.filter((_, position) => position !== index).map((state) => state.file),
    );
    hideMessage();
    renderSelection();
    const remaining = filesElement.querySelectorAll(".remove-file");
    if (remaining.length) remaining[Math.min(index, remaining.length - 1)].focus();
    else clips.focus();
  }

  clips.addEventListener("change", () => addSelectedFiles(Array.from(clips.files)));
  clearSelectionButton.addEventListener("click", () => {
    if (isRunning() || clips.disabled) return;
    selectionGeneration += 1;
    clips.value = "";
    hideMessage();
    renderSelection();
    clips.focus();
  });

  const dropZone = document.getElementById("drop-zone");
  let dragDepth = 0;
  function clearDrag() {
    dragDepth = 0;
    dropZone.classList.remove("drag-over");
  }
  // Prevent dropped files from navigating away from this single-use session.
  document.addEventListener("dragenter", (event) => {
    if (!Array.from(event.dataTransfer?.types || []).includes("Files")) return;
    event.preventDefault();
    if (isRunning() || clips.disabled) return;
    dragDepth += 1;
    dropZone.classList.add("drag-over");
  });
  document.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) clearDrag();
  });
  document.addEventListener("dragover", (event) => {
    event.preventDefault();
    if (event.dataTransfer)
      event.dataTransfer.dropEffect = isRunning() || clips.disabled ? "none" : "copy";
  });
  document.addEventListener(
    "drop",
    (event) => {
      event.preventDefault();
      clearDrag();
      if (isRunning() || clips.disabled || !event.dataTransfer) return;
      const items = Array.from(event.dataTransfer.items || []);
      if (items.some((item) => item.webkitGetAsEntry?.()?.isDirectory)) {
        showSelectionError("Drop video files, not folders.");
        return;
      }
      const files = Array.from(event.dataTransfer.files);
      if (!files.length) return;
      addSelectedFiles(files);
    },
    true,
  );
  window.addEventListener("dragend", clearDrag);
  window.addEventListener("blur", clearDrag);

  function dispose() {
    for (const copy of inputCopies.values()) discardInputCopy(copy);
    inputCopies.clear();
    void preservation.then((store) => store.close()).catch(() => {});
  }

  function markNeedsReplacement(index, errorMessage) {
    const state = selectedFiles.find((item) => item.index === index);
    const copy = state && inputCopies.get(state.identity);
    if (!copy) return;
    copy.phase = "failed";
    copy.error = errorMessage;
    if (copy.task) void copy.task.dispose().catch(() => {});
    updateFile(state, "Reselect file", 0);
    state.meta.textContent = copy.error;
    state.reselect.hidden = false;
    refreshPreparation();
  }

  return {
    get files() {
      return selectedFiles;
    },
    isReady: (state) => inputCopies.get(state.identity)?.phase === "ready",
    hasPending: () => pendingSelections > 0,
    refreshPreparation,
    flushPreparationReports,
    dispose,
    markNeedsReplacement,
  };
}
