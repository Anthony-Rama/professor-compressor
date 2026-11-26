// DOM references and rendering for the compressor page. State lives in the
// batch controller; these functions only write what they are given.
import { readableSize } from "./shared.js";

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
  const ORIGINAL_TITLE = document.title;

  let tabProgress = "Preparing files";

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
