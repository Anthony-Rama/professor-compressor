const { SESSION_SECRET, DIAGNOSTIC_REFERENCE } = JSON.parse(
  document.getElementById("delivery-config").textContent,
);
const referenceLine = document.getElementById("diagnostic-reference");
referenceLine.textContent = "Error reference: " + DIAGNOSTIC_REFERENCE;
const statusLine = document.getElementById("delivery-status");
const heading = document.getElementById("delivery-heading");
const explanation = document.getElementById("delivery-explanation");
const reselect = document.getElementById("reselect");
const checkAgain = document.getElementById("check-again");

// Waits after a temporary failure (network error, unreadable or overloaded
// response). After the last one, polling stops until the visitor asks again.
// Checking only reads the receipt; it never resends clips.
const RETRY_DELAYS_MS = [3000, 6000, 12000, 24000, 30000];
let temporaryFailures = 0;

function retryLater(serverBusy = false) {
  referenceLine.hidden = !DIAGNOSTIC_REFERENCE;
  explanation.hidden = true;
  if (temporaryFailures >= RETRY_DELAYS_MS.length) {
    statusLine.textContent =
      "Delivery status could not be checked. Check Discord before running /compress again, or check status again.";
    checkAgain.hidden = false;
    return;
  }
  statusLine.textContent = serverBusy
    ? "The relay is busy. Checking delivery again shortly…"
    : "Connection lost while checking delivery. Retrying safely…";
  setTimeout(checkDelivery, RETRY_DELAYS_MS[temporaryFailures]);
  temporaryFailures += 1;
}

checkAgain.addEventListener("click", () => {
  checkAgain.hidden = true;
  temporaryFailures = 0;
  statusLine.textContent = "Checking status…";
  void checkDelivery();
});

async function checkDelivery() {
  try {
    const response = await fetch(location.pathname + "/status", {
      headers: { "X-Upload-Session": SESSION_SECRET },
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
    const body = await response.json();
    const recognized =
      body.pending || body.ok || body.ready || body.code === "discord_delivery_failed";
    if (!recognized && (response.status === 429 || response.status >= 500)) {
      retryLater(true);
      return;
    }
    temporaryFailures = 0;
    referenceLine.hidden = !DIAGNOSTIC_REFERENCE || Boolean(body.pending || body.ok);
    if (body.pending) {
      statusLine.textContent =
        "Delivery is still in progress. Check Discord before starting a new session.";
      setTimeout(checkDelivery, 2000);
    } else if (body.ok) {
      heading.textContent = "Delivery complete";
      explanation.hidden = true;
      statusLine.textContent =
        "Delivered to Discord. You can return to your channel. To upload more videos, run /compress again.";
      statusLine.classList.add("success");
      document.getElementById("review-prompt").hidden = false;
    } else if (body.ready) {
      heading.textContent = "Upload interrupted";
      explanation.hidden = true;
      statusLine.textContent = "The upload stopped before Discord delivery.";
      reselect.hidden = false;
    } else if (body.code === "discord_delivery_failed") {
      heading.textContent =
        body.delivery_outcome === "rejected" ? "Delivery failed" : "Delivery needs checking";
      explanation.hidden = true;
      statusLine.textContent =
        body.error ||
        "Delivery could not be confirmed. Check the Discord channel before starting another session to avoid duplicate clips.";
      statusLine.classList.add("error");
    } else {
      heading.textContent = "Delivery status unavailable";
      explanation.hidden = true;
      statusLine.textContent =
        "Delivery could not be confirmed. Check Discord before running /compress again.";
    }
  } catch (_) {
    retryLater();
  }
}
void checkDelivery();
