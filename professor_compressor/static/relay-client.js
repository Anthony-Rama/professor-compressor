// Relay-owned request cancellation, session deadlines, and delivery reconciliation.
import { UPLOAD_RETRY_STATUSES } from "./shared.js";

export function createRelayClient({
  sessionSecret: SESSION_SECRET,
  expiresInSeconds,
  diagnostic,
  getBatchMetrics,
  hiddenSeconds,
  getCaption,
  isCancelled,
  events,
}) {
  let uploadRequest = null;
  let sessionDeadline = Date.now() + expiresInSeconds * 1000;

  async function sessionRequest(action = "status") {
    let response;
    try {
      response = await fetch(window.location.pathname + "/" + action, {
        method: action === "status" ? "GET" : "POST",
        headers: { "X-Upload-Session": SESSION_SECRET },
        cache: "no-store",
        signal: AbortSignal.timeout(10000),
      });
    } catch (_) {
      throw Object.assign(
        new Error(
          "Could not confirm delivery status. Keep this page open and try again; check Discord before starting a new session.",
        ),
        { status: 0 },
      );
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok)
      throw Object.assign(new Error(body.error || "Session check failed."), {
        status: response.status,
        code: body.code,
      });
    if (!body.ok && !body.ready && !body.pending)
      throw Object.assign(new Error("The relay returned an unreadable status."), { status: 0 });
    if (body.ready) sessionDeadline = Date.now() + body.expires_in * 1000;
    return body;
  }

  function xhrUpload(results) {
    const batchMetrics = getBatchMetrics();
    return new Promise((resolve, reject) => {
      diagnostic.mark("upload");
      batchMetrics.stage = "upload";
      batchMetrics.upload_attempts = (batchMetrics.upload_attempts || 0) + 1;
      Object.assign(results[0].diagnostic.telemetry, batchMetrics, {
        hidden_seconds: hiddenSeconds(),
      });
      const uploadStarted = performance.now();
      const data = new FormData();
      data.append("caption", getCaption().trim());
      data.append("diagnostics", JSON.stringify(results.map((result) => result.diagnostic)));
      for (const result of results) data.append("clips", result.blob, result.name);
      const request = new XMLHttpRequest();
      uploadRequest = request;
      request.open("POST", window.location.href);
      request.responseType = "json";
      request.timeout = 600000;
      request.setRequestHeader("X-Upload-Session", SESSION_SECRET);
      request.setRequestHeader("X-Diagnostic-Run", diagnostic.run());
      request.upload.addEventListener("progress", (event) => {
        if (!event.lengthComputable) return;
        const fraction = event.loaded / event.total;
        events.emit("progress", 80 + fraction * 18);
        const resultBytes = results.reduce((sum, result) => sum + result.blob.size, 0);
        const sentBytes = fraction * resultBytes;
        let previousBytes = 0;
        for (const result of results) {
          const fileFraction = Math.max(
            0,
            Math.min(1, (sentBytes - previousBytes) / result.blob.size),
          );
          if (fileFraction >= 1) events.emit("fileStatus", result.state, "Sent to relay", 100);
          else if (fileFraction > 0)
            events.emit(
              "fileStatus",
              result.state,
              "Sending " + Math.round(fileFraction * 100) + "%",
              fileFraction * 100,
            );
          else events.emit("fileStatus", result.state, "Waiting to send", 0);
          previousBytes += result.blob.size;
        }
        events.emit(
          "phaseCopy",
          "Uploading finished files through the secure relay: " + Math.round(fraction * 100) + "%",
        );
        events.emit("runDetail", "Sending " + Math.round(fraction * 100) + "%");
      });
      request.upload.addEventListener("loadend", () => {
        batchMetrics.upload_seconds =
          (batchMetrics.upload_seconds || 0) + (performance.now() - uploadStarted) / 1000;
      });
      request.upload.addEventListener("load", () => {
        batchMetrics.stage = "delivery";
        diagnostic.mark("delivery");
        events.emit("progress", 98);
        events.emit(
          "phaseCopy",
          "Upload sent. Waiting for the relay to confirm receipt and Discord delivery.",
        );
        events.emit("runDetail", "Waiting for Discord");
        for (const result of results)
          events.emit("fileStatus", result.state, "Waiting for Discord", 100);
      });
      request.addEventListener("load", () => {
        const body = request.response || {};
        if (request.status >= 200 && request.status < 300 && (body.ok || body.pending))
          resolve(body);
        else
          reject(
            Object.assign(new Error(body.error || "The relay rejected the upload."), {
              status: request.status,
              code: body.code,
              retryAfter: body.retry_after,
            }),
          );
      });
      request.addEventListener("error", () =>
        reject(Object.assign(new Error("The network connection was interrupted."), { status: 0 })),
      );
      request.addEventListener("abort", () => reject(new DOMException("Cancelled", "AbortError")));
      request.addEventListener("timeout", () =>
        reject(
          Object.assign(
            new Error(
              "Sending timed out. Check the Discord channel before starting another session.",
            ),
            { status: 504 },
          ),
        ),
      );
      request.send(data);
    });
  }

  async function uploadWithRetry(results) {
    const batchMetrics = getBatchMetrics();
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        // Reconcile an earlier upload before sending bytes again.
        let response = await sessionRequest();
        if (response.ready) response = await xhrUpload(results);
        uploadRequest = null;
        const deadline = performance.now() + 600000;
        while (response.pending) {
          events.emit("runDetail", "Waiting for Discord");
          events.emit(
            "phaseCopy",
            "The relay is processing this batch. You do not need to send it again.",
          );
          if (performance.now() > deadline)
            throw Object.assign(
              new Error("Delivery is still pending. Check its status again without recompressing."),
              { status: 0 },
            );
          await new Promise((resolve) => setTimeout(resolve, 2000));
          response = await sessionRequest();
        }
        if (!response.ok)
          throw Object.assign(new Error("The upload was interrupted before it was queued."), {
            status: 0,
          });
        return response;
      } catch (error) {
        uploadRequest = null;
        batchMetrics.delivery_failures = (batchMetrics.delivery_failures || 0) + 1;
        if (isCancelled() || error.name === "AbortError") throw error;
        const retryable =
          UPLOAD_RETRY_STATUSES.includes(error.status) ||
          (error.status === 502 && error.code !== "discord_delivery_failed");
        if (!retryable || attempt === 3) throw error;
        const requestedWait = Number(error.retryAfter);
        const waitSeconds =
          Number.isFinite(requestedWait) && requestedWait > 0
            ? Math.min(60, Math.ceil(requestedWait))
            : Math.pow(2, attempt) + Math.floor(Math.random() * 3);
        for (let remaining = waitSeconds; remaining > 0; remaining -= 1) {
          if (isCancelled()) throw new DOMException("Cancelled", "AbortError");
          events.emit(
            "phaseCopy",
            "The connection was interrupted. Your compressed files are still available on this page. " +
              "Checking delivery again in " +
              remaining +
              " second" +
              (remaining === 1 ? "" : "s") +
              " (attempt " +
              (attempt + 1) +
              " of 3).",
          );
          await new Promise((resolve) => setTimeout(resolve, 1000));
        }
      }
    }
  }

  return {
    sessionRequest,
    uploadWithRetry,
    abortUpload: () => uploadRequest?.abort(),
    expiresAt: () => sessionDeadline,
    expire: () => {
      sessionDeadline = 0;
    },
  };
}
