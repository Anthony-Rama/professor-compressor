// DOM references and rendering for the compressor page. State lives in the
// batch controller; these functions only write what they are given.
import { fileStatusKind, readableSize } from "./shared.js";

export function createCompressorView({ diagnosticReference: DIAGNOSTIC_REFERENCE, isRunning }) {
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
  const stepLabel = document.getElementById("step-label");
  const submitHelp = document.getElementById("submit-help");
  const sizeSummary = document.getElementById("size-summary");
  const ORIGINAL_TITLE = document.title;

  let tabProgress = "Preparing files";
  // Files with a known finished size, for the totals shown after delivery.
  const sizedFiles = new Set();

  // Screen-reader wording for a step's visual marker.
  function describeStep(step) {
    step.querySelector(".step-state").textContent = step.classList.contains("done")
      ? ", completed"
      : step.classList.contains("active")
        ? ", in progress"
        : step.classList.contains("halted")
          ? ", stopped"
          : "";
    if (step.classList.contains("active")) step.setAttribute("aria-current", "step");
    else step.removeAttribute("aria-current");
  }

  // A stopped run is shown on the step where it stopped. Finished files mean
  // compression was complete and delivery is the step that needs attention.
  function stoppedStep() {
    const cards = Array.from(filesElement.querySelectorAll(".file-card"));
    const compressed = cards.length > 0 && cards.every((card) => card.dataset.status === "done");
    return compressed ? sendStep : compressStep;
  }

  function setPhase(phase, title, copy) {
    phaseTitle.textContent = title;
    phaseCopy.textContent = copy;
    compressStep.className = "phase-step";
    sendStep.className = "phase-step";
    const stopped = phase === "compress" && (title === "Action needed" || title === "Cancelled");
    if (stopped) {
      const step = stoppedStep();
      step.classList.add("halted");
      if (step === sendStep) compressStep.classList.add("done");
      describeStep(compressStep);
      describeStep(sendStep);
      stepLabel.textContent = "Stopped at step " + (step === sendStep ? "3" : "2") + " of 3";
      return;
    }
    if (phase === "compress") compressStep.classList.add("active");
    if (phase === "send") {
      compressStep.classList.add("done");
      sendStep.classList.add("active");
    }
    if (phase === "done") {
      compressStep.classList.add("done");
      sendStep.classList.add("done");
    }
    describeStep(compressStep);
    describeStep(sendStep);
    stepLabel.textContent =
      phase === "compress" ? "Step 2 of 3" : phase === "send" ? "Step 3 of 3" : "All steps done";
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

  function showSizeSummary(visible) {
    let original = 0;
    let finished = 0;
    for (const state of sizedFiles) {
      if (!state.card.isConnected || !state.finalSize) continue;
      original += state.file.size;
      finished += state.finalSize;
    }
    sizeSummary.hidden = !visible || !finished;
    if (sizeSummary.hidden) return;
    const arrow = document.createElement("span");
    arrow.setAttribute("aria-hidden", "true");
    arrow.textContent = " → ";
    const spoken = document.createElement("span");
    spoken.className = "visually-hidden";
    spoken.textContent = " to ";
    sizeSummary.replaceChildren(
      "Total size " + readableSize(original),
      arrow,
      spoken,
      readableSize(finished),
    );
  }

  function showFeedback(outcome) {
    showSizeSummary(outcome === "success");
    document.getElementById("review-prompt").hidden = outcome !== "success";
    document.getElementById("contact-help").hidden = outcome === "success";
    document.getElementById("processing-privacy").hidden = outcome === "success";
    feedbackLink.href =
      "mailto:professorcompressor.support@gmail.com?subject=" +
      encodeURIComponent("Professor Compressor feedback (" + outcome + ")");
    feedbackActions.hidden = false;
  }

  function renderDocumentTitle() {
    if (!isRunning()) {
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

  function updateFile(state, status, percent = null) {
    state.status.textContent = status;
    state.card.dataset.status = fileStatusKind(status);
    if (percent !== null) state.progress.value = Math.max(0, Math.min(100, percent));
  }

  function updateFileSizes(state, finalBytes) {
    state.finalSize = finalBytes;
    sizedFiles.add(state);
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
    pageHeading.textContent = active ? "Compression in progress" : "Send videos to Discord";
  }

  // Explains the primary action, and why it is unavailable, beside the button.
  function actionHelp() {
    if (submit.hidden) {
      return isRunning() && cancelButton.hidden ? "Sending can't be cancelled once it starts." : "";
    }
    if (isRunning() || !submit.disabled) return "";
    if (expiresElement.textContent === "Session expired")
      return "This link has expired. Run /compress in Discord for a new one.";
    const statuses = Array.from(filesElement.querySelectorAll(".file-card"), (card) =>
      fileStatusKind(card.querySelector(".file-status").textContent),
    );
    const count = (kind) => statuses.filter((value) => value === kind).length;
    const preparing = count("preparing");
    if (preparing)
      return (
        "Waiting for " +
        preparing +
        (preparing === 1 ? " clip" : " clips") +
        " to finish preparing."
      );
    if (count("attention")) return "Replace or remove the clip that needs attention.";
    return "";
  }

  function renderActionHelp() {
    const text = actionHelp();
    if (submitHelp.textContent !== text) submitHelp.textContent = text;
    const empty = submit.hidden && cancelButton.hidden && !text;
    const controls = submit.parentElement;
    if (controls.classList.contains("is-empty") !== empty)
      controls.classList.toggle("is-empty", empty);
  }
  // Presentation only: observing the page keeps this text in step with every
  // place that enables, hides or relabels the controls.
  new MutationObserver(renderActionHelp).observe(form, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: ["disabled", "hidden"],
  });
  new MutationObserver(renderActionHelp).observe(expiresElement, {
    childList: true,
    characterData: true,
  });
  // After the controller finishes its synchronous setup.
  queueMicrotask(renderActionHelp);

  return {
    elements: {
      form,
      clips,
      clearSelectionButton,
      caption,
      submit,
      cancelButton,
      selection,
      sessionNote,
      stayOpen,
      performanceHelp,
      formatHelp,
      pageHeading,
      pageIntro,
      filesElement,
      work,
      message,
      feedbackActions,
      feedbackLink,
      overallProgress,
      phaseTitle,
      phaseCopy,
      compressStep,
      sendStep,
      elapsedElement,
      runDetailElement,
      expiresElement,
      backgroundWarning,
      stallWarning,
    },
    setPhase,
    showMessage,
    showSelectionError,
    hideMessage,
    showFeedback,
    renderDocumentTitle,
    setRunDetail,
    updateFile,
    updateFileSizes,
    setProcessingView,
  };
}
