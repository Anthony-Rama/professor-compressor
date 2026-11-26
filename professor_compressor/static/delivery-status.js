const { SESSION_SECRET, DIAGNOSTIC_REFERENCE } = JSON.parse(
  document.getElementById("delivery-config").textContent,
);
const referenceLine = document.getElementById("diagnostic-reference");
referenceLine.textContent = "Error reference: " + DIAGNOSTIC_REFERENCE;
const statusLine = document.getElementById("delivery-status");
const heading = document.getElementById("delivery-heading");
const explanation = document.getElementById("delivery-explanation");
const reselect = document.getElementById("reselect");
async function checkDelivery() {
  try {
    const response = await fetch(location.pathname + "/status", {
      headers: { "X-Upload-Session": SESSION_SECRET },
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
    const body = await response.json();
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
    referenceLine.hidden = !DIAGNOSTIC_REFERENCE;
    explanation.hidden = true;
    statusLine.textContent = "Connection lost while checking delivery. Retrying safely…";
    setTimeout(checkDelivery, 3000);
  }
}
void checkDelivery();
