// Private operator UI. Render untrusted event values as text, never HTML.
const el = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
const REFERENCE = /^PC-[0-9A-F]{12}$/;
let snapshot = null,
  lookupResult = null,
  loadSequence = 0,
  lookupSequence = 0,
  loading = false;
const number = (value) =>
  typeof value === "number" && Number.isFinite(value) ? value.toLocaleString() : "—";
const seconds = (value) =>
  typeof value !== "number"
    ? "—"
    : value < 1
      ? Math.round(value * 1000) + " ms"
      : value.toFixed(1) + " s";
const bytes = (value) =>
  typeof value !== "number"
    ? "—"
    : value < 1048576
      ? (value / 1024).toFixed(1) + " KiB"
      : (value / 1048576).toFixed(1) + " MiB";
const percent = (value) => (typeof value === "number" ? value.toFixed(1) + "%" : "—");
const when = (value) =>
  typeof value === "number"
    ? new Date(value * 1000).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "—";
const age = (value) =>
  typeof value !== "number"
    ? "—"
    : value >= 86400
      ? (value / 86400).toFixed(1) + " days"
      : value >= 3600
        ? (value / 3600).toFixed(1) + " hours"
        : Math.floor(value / 60) + " min";
const labels = {
  preparation: "Local file preparation",
  created: "Created",
  opened: "Link opened",
  selected: "Files selected",
  processing: "Processing",
  uploading: "Uploading",
  queued: "Queued",
  delivering: "Sending to Discord",
  delivered: "Delivered",
  upload_failed: "Upload failed",
  delivery_failed: "Delivery failed",
  cancelled: "Cancelled",
  expired: "Expired before start",
  replaced: "Replaced",
  unknown: "Unknown outcome",
  engine_seconds: "Engine loading · per run",
  encoding_seconds: "Video encoding · per file",
  relay_receive_seconds: "Relay upload · per batch",
  queue_seconds: "Delivery queue · per batch",
  discord_delivery_seconds: "Discord delivery · per batch",
  total_seconds: "End to end · per run",
  installed: "Bot installed",
  removed: "Bot removed",
  stage_started: "Stage started",
  stage_finished: "Stage finished",
  file_ready: "File ready",
  file_received: "File received",
  retry: "Automatic retry",
  client_complete: "Browser finished",
  client_failed: "Browser failure reported",
  upload_started: "Upload started",
  delivery_started: "Discord delivery started",
  received: "Relay received batch",
  started: "Compress pressed",
  refreshed: "Page refreshed",
  page_left: "Page left",
  interrupted: "Process interrupted",
  unchanged: "Sent unchanged",
  remuxed: "Stream-copied",
  converted: "Converted",
  compressed: "Compressed",
  "single-threaded": "Single-threaded",
  multithreaded: "Multithreaded",
  unused: "Engine not needed",
  desktop: "Desktop",
  mobile: "Mobile",
};
Object.assign(labels, {
  attempt_started: "File attempt started",
  processing_progress: "Processing checkpoint",
  processing_stalled: "No recent processing progress (not a confirmed failure)",
  client_failed: "Processing failed",
  encoding: "Encoding",
  metadata: "Metadata",
  engine: "Video engine",
  file_read: "File reading",
  validation: "File validation",
  remux: "MP4 conversion",
  upload: "Upload",
  delivery: "Delivery",
  session: "Session",
  oom: "Out of memory",
  allocation_failed: "Allocation failed",
  wasm_bounds: "WASM bounds trap (not proof of OOM)",
  wasm_trap: "WASM runtime trap",
  unsupported_codec: "Unsupported codec signal",
  invalid_media: "Invalid media signal",
  engine_log: "Engine message signature",
  exception: "Thrown exception signature",
  same_settings: "Same settings",
  size_adjustment: "Size adjustment",
});
const label = (value) => labels[value] || value || "Not reported";
const failureReasons = {
  UPLOAD_CONNECTION_RESET:
    "The upload connection closed before complete receipt; the disconnecting party is unknown",
  TARGET_NOT_FEASIBLE: "The duration leaves too little bitrate at the available upload limit",
  INPUT_STORAGE_FULL: "Browser storage could not accommodate the local copy",
  INPUT_STORAGE_UNAVAILABLE: "Browser-local storage was unavailable or blocked",
  INPUT_WRITE_UNSUPPORTED: "Browser lacks support for writing temporary video copies",
  INPUT_TOO_LARGE: "The selection exceeded a local input limit",
  INPUT_PREPARATION_TIMEOUT: "Loading or preparing the local copy timed out",
  WORKER_CRASH: "The video engine worker crashed; the underlying cause was not reported",
  WORKER_MESSAGE_ERROR: "Communication with the video engine worker failed",
  BROWSER_MEMORY: "The video engine could not allocate enough memory",
  WASM_TRAP: "The video engine encountered a runtime error; the underlying cause is unconfirmed",
  UNSUPPORTED_CODEC: "The engine reported an unsupported codec",
  INVALID_MEDIA: "The engine reported invalid media data",
  METADATA_INVALID: "The video metadata could not be read",
  METADATA_TIMEOUT: "Reading video metadata timed out",
  ENGINE_LOAD_FAILED: "The video engine could not start",
  ENGINE_LOAD_TIMEOUT: "Starting the video engine timed out",
  FILE_READ_FAILED: "The browser could not read the selected file",
  INVALID_SIGNATURE: "The file did not have a supported video signature",
  NO_VIDEO_STREAM: "No video stream was found",
  DURATION_UNKNOWN: "The clip duration could not be determined",
  OUTPUT_TOO_LARGE: "The video could not fit within the upload target",
  ENCODE_FAILED: "Encoding failed; the engine did not report a specific cause",
  NETWORK: "The connection failed; delivery may be unconfirmed",
  HTTP: "The server returned an HTTP error",
  UPLOAD_TIMEOUT: "The upload timed out",
  SESSION_EXPIRED: "The compressor session expired",
  INVALID_UPLOAD: "The relay rejected the uploaded files",
  RELAY_ERROR: "The relay could not process the upload",
  DISCORD_REJECTED: "Discord rejected delivery",
  DELIVERY_TIMEOUT: "Delivery timed out; outcome unknown",
  DELIVERY_UNCONFIRMED: "Delivery outcome unknown",
  DELIVERY_ACCESS_DENIED: "Destination unavailable or missing permissions",
  DELIVERY_LIMIT_CHANGED: "Destination upload allowance changed",
  ENCODE_TIMEOUT: "Video encoding timed out",
  UNKNOWN: "The cause was not captured by this client",
};
function failureReason(data = {}) {
  const reason = failureReasons[data.error_code] || "No specific failure cause was reported";
  if (data.stage !== "upload") return reason;
  const details = [];
  if (typeof data.upload_attempts === "number")
    details.push("attempt " + number(data.upload_attempts));
  if (typeof data.received_bytes === "number")
    details.push(bytes(data.received_bytes) + " received");
  if (typeof data.receive_idle_seconds === "number")
    details.push(seconds(data.receive_idle_seconds) + " since last chunk");
  return reason + (details.length ? " · " + details.join(" · ") : "");
}
function recovery(row) {
  if (row.status === "delivered") return row.failures ? "Recovered and delivered" : "Delivered";
  const code = row.failure_data?.error_code;
  if (["DELIVERY_UNCONFIRMED", "DELIVERY_TIMEOUT"].includes(code))
    return "Check Discord before starting another session";
  if (code === "DELIVERY_ACCESS_DENIED") return "Ask an admin to restore destination access";
  if (code === "DELIVERY_LIMIT_CHANGED") return "Start a new session with the current allowance";
  if (
    [
      "UNSUPPORTED_CODEC",
      "TARGET_NOT_FEASIBLE",
      "INVALID_SIGNATURE",
      "FILE_READ_FAILED",
      "INVALID_MEDIA",
    ].includes(code)
  )
    return "Replace or re-export the affected file";
  if (["UPLOAD_CONNECTION_RESET", "UPLOAD_TIMEOUT", "NETWORK", "RELAY_ERROR"].includes(code))
    return "Check receipt, then retry prepared files";
  return ["client_failed", "upload_failed", "delivery_failed"].includes(row.status)
    ? "Needs investigation"
    : "—";
}
function eventLabel(event) {
  if (event.event === "client_failed")
    return (
      (event.data.stage ? label(event.data.stage) + " failed" : "Processing failed") +
      " — " +
      failureReason(event.data)
    );
  if (event.event === "stage_finished")
    return (
      (event.data.stage ? label(event.data.stage) : "Processing") +
      (event.data.outcome === "failed"
        ? " failed"
        : event.data.outcome === "cancelled"
          ? " cancelled"
          : " step ended")
    );
  if (event.event === "client_complete")
    return event.data.outcome === "failed"
      ? "Browser run ended with failure"
      : event.data.outcome === "cancelled"
        ? "Browser run cancelled"
        : label(event.event);
  return label(event.event);
}

Object.assign(labels, {
  preparation: "Local file preparation",
  output_metadata: "Output metadata",
  conversion: "Conversion",
  unknown: "Unknown outcome",
});
const revision = (value) => (value === "development" ? "Development" : value?.slice(0, 8) || "—");
const stageName = (value) => (value === "unknown" ? "Not reported" : label(value));
const days = () => document.querySelector('input[name="days"]:checked')?.value || "7";
const speed = (value) => (typeof value === "number" ? value.toFixed(2) + "× real time" : "—");
const shortDay = (seconds, bucket) => {
  const date = new Date(seconds * 1000);
  return bucket < 86400
    ? date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
    : date.toISOString().slice(5, 10);
};

function showPanel(name, focus = false) {
  const tab = document.querySelector(`[data-tab="${name}"]`);
  if (!tab) return;
  for (const button of document.querySelectorAll("[data-tab]")) {
    const selected = button === tab;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    el("panel-" + button.dataset.tab).hidden = !selected;
  }
  if (focus) tab.focus();
  // Charts are drawn at their real width, which is unknown while hidden.
  if (snapshot) drawCharts(snapshot);
}
function empty(target, title, description = "") {
  target.replaceChildren();
  const box = document.createElement("div"),
    strong = document.createElement("strong"),
    p = document.createElement("p");
  box.className = "empty";
  strong.textContent = title;
  p.textContent = description;
  box.append(strong, p);
  target.append(box);
}
// Columns are [heading, key, render?, "number"?]. Render returns text or a Node.
function table(
  target,
  columns,
  rows,
  title = "No measurements yet",
  description = "New activity will appear here as it is recorded.",
) {
  target.replaceChildren();
  if (!target.classList.contains("scroll-box")) target.classList.add("scroll");
  if (!rows.length) {
    empty(target, title, description);
    return;
  }
  const t = document.createElement("table"),
    head = document.createElement("thead"),
    header = document.createElement("tr"),
    body = document.createElement("tbody");
  for (const [name, , , align] of columns) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = name;
    if (align) th.className = align;
    header.append(th);
  }
  head.append(header);
  t.append(head, body);
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const [, key, render, align] of columns) {
      const td = document.createElement("td");
      const value = render ? render(row[key], row) : row[key];
      if (value instanceof Node) td.append(value);
      else td.textContent = value ?? "—";
      if (align) td.className = align;
      tr.append(td);
    }
    body.append(tr);
  }
  target.append(t);
}
function badge(value) {
  const node = document.createElement("span");
  node.className = "badge";
  if (value === "delivered") node.classList.add("good");
  else if (["client_failed", "upload_failed", "delivery_failed"].includes(value))
    node.classList.add("danger");
  else if (["unknown", "cancelled"].includes(value)) node.classList.add("warning");
  node.textContent = label(value);
  return node;
}
function cards(target, items, live = false) {
  target.replaceChildren();
  for (const [name, value, detail = "", tone = "", key = ""] of items) {
    const card = document.createElement("div"),
      title = document.createElement("div"),
      amount = document.createElement(live ? "div" : "strong"),
      sub = document.createElement("div");
    card.className = live ? "live-stat" : "stat";
    if (key) card.dataset.metric = key;
    title.className = "metric-label";
    title.textContent = name;
    amount.className = "metric-value " + tone;
    amount.textContent = value;
    sub.className = "metric-detail";
    sub.textContent = detail;
    card.append(title, amount, sub);
    target.append(card);
  }
}
function outcomes(target, items) {
  target.replaceChildren();
  for (const [name, value, detail = "", tone = ""] of items) {
    const box = document.createElement("div"),
      n = document.createElement("div"),
      l = document.createElement("div"),
      p = document.createElement("div");
    n.className = "metric-value " + tone;
    n.textContent = value;
    l.className = "metric-label";
    l.textContent = name;
    p.className = "metric-detail";
    p.textContent = detail;
    box.append(l, n, p);
    target.append(box);
  }
}
function group(target, rows, kind = "Sessions") {
  table(
    target,
    [
      ["Category", "value", label],
      [kind, "sessions", number, "number"],
    ],
    rows || [],
    "Nothing recorded in this view",
    "Try another window or include test servers.",
  );
}
function bar(fraction, text) {
  const box = document.createElement("div"),
    track = document.createElement("div"),
    fill = document.createElement("div"),
    caption = document.createElement("span");
  box.className = "share";
  track.className = "track";
  track.setAttribute("aria-hidden", "true");
  fill.className = "track-fill";
  fill.style.width = Math.max(0, Math.min(100, fraction * 100)) + "%";
  caption.textContent = text;
  track.append(fill);
  box.append(caption, track);
  return box;
}
function referenceButton(reference) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "reference-button";
  button.textContent = reference;
  button.addEventListener("click", () => {
    el("reference").value = reference;
    showPanel("sessions");
    void lookup();
  });
  return button;
}
function references(values) {
  const box = document.createElement("div");
  box.className = "references";
  for (const value of values || []) box.append(referenceButton(value));
  return box;
}
function mix(counts) {
  const entries = Object.entries(counts || {}).sort((a, b) => b[1] - a[1]);
  return entries.length
    ? entries
        .slice(0, 3)
        .map(([name, count]) => label(name) + " " + number(count))
        .join(", ")
    : "—";
}
function cause(code) {
  const box = document.createElement("div"),
    name = document.createElement("span"),
    detail = document.createElement("span");
  name.className = "code";
  name.textContent = code;
  detail.className = "subtle";
  detail.textContent = failureReasons[code] || "";
  box.append(name, detail);
  return box;
}
function svg(name, attributes = {}, text = "") {
  const node = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text) node.textContent = text;
  return node;
}

// One row per file and run: browser facts in client order, then relay receipts.
function renderSessionFiles(history) {
  const files = new Map();
  for (const event of history) {
    if (!event.file_index) continue;
    const key = event.run_id + ":" + event.file_index,
      prior = files.get(key) || { file_index: event.file_index, run_id: event.run_id, reports: [] };
    prior.reports.push(event);
    prior.last_event = eventLabel(event);
    files.set(key, prior);
  }
  for (const file of files.values()) {
    // Batches can arrive out of order. Order browser facts within this run
    // by their client sequence; older clients retain server arrival order.
    const browser = file.reports.filter((event) => event.source === "browser");
    if (browser.every((event) => Number.isInteger(event.data.client_sequence)))
      browser.sort((a, b) => a.data.client_sequence - b.data.client_sequence);
    file.data = Object.assign({}, ...browser.map((event) => event.data));
    // Relay receipt values win over browser reports, including late reports.
    for (const event of file.reports)
      if (event.event === "file_received" && event.source === "server")
        Object.assign(file.data, event.data);
  }
  const dimensions = (width, height, fps) =>
    width && height
      ? width + " × " + height + (typeof fps === "number" ? " · " + fps.toFixed(1) + " fps" : "")
      : "Not reported";
  table(
    el("session-files"),
    [
      ["Run", "run"],
      ["File", "file"],
      ["Latest report", "last_event", label],
      ["Format", "format"],
      ["Path", "path", label],
      ["Input", "input", bytes],
      ["Output", "output", bytes],
      ["Clip length", "duration", seconds],
      ["Encode", "encode", seconds],
      ["Input video", "input_video"],
      ["Output video", "output_video"],
      ["Source codecs", "codecs"],
      ["Profile / pixels", "profile"],
    ],
    [...files.values()].map((e) => {
      return {
        run: e.run_id?.slice(0, 8) || "—",
        file: "File " + e.file_index,
        last_event: e.last_event,
        format: (e.data.input_format || "unknown").toUpperCase(),
        path: e.data.path,
        input: e.data.input_bytes,
        output: e.data.output_bytes,
        duration: e.data.duration_seconds,
        encode: e.data.encoding_seconds,
        input_video: dimensions(e.data.input_width, e.data.input_height, e.data.input_fps),
        output_video: dimensions(e.data.output_width, e.data.output_height, e.data.output_fps),
        codecs:
          (e.data.video_codec || "Not reported") + " / " + (e.data.audio_codec || "Not reported"),
        profile:
          [
            e.data.video_profile,
            e.data.bit_depth ? e.data.bit_depth + " bit" : null,
            e.data.pixel_format,
          ]
            .filter(Boolean)
            .join(" · ") || "Not reported",
      };
    }),
    "No file diagnostics",
    "Select a session with browser file reports or a relay receipt.",
  );
}

function renderAttempts(history) {
  const evidence = history.filter(
    (event) =>
      event.event === "attempt_started" ||
      event.event === "retry" ||
      event.event === "client_failed" ||
      event.event === "upload_failed" ||
      event.event === "delivery_failed" ||
      event.event === "processing_progress" ||
      (event.event === "stage_finished" &&
        (event.data.error_code || event.data.exit_code !== undefined)),
  );
  // A retry carries the previous failure's evidence, not a new failure of the
  // next attempt. Make the transition explicit rather than mislabelling it.
  const attemptLabel = (value, row) =>
    row.event === "retry" && row.failed_attempt
      ? row.failed_attempt + " → " + value
      : number(value);
  table(
    el("session-attempts"),
    [
      ["File", "file_index", (value) => (value ? "File " + value : "Batch")],
      ["Attempt", "attempt", attemptLabel],
      ["Engine signal", "engine_signal", label],
      ["Error", "error_code"],
      ["Explanation", "explanation"],
      ["Wrapper", "exception_kind"],
      ["Stage", "stage", label],
      ["Time", "at", when],
      ["Run", "run_id", (value) => value?.slice(0, 8) || "—"],
      ["Report", "event", label],
      ["Evidence source", "signal_source", label],
      ["Exit code", "exit_code", number],
      ["HTTP status", "http_status", number],
      ["Decoder", "decoder"],
      ["Encoder threads", "encoder_threads", number],
      ["Encode pass", "encode_pass", number],
      ["Mode", "command_mode"],
      ["Last media time", "last_media_seconds", seconds],
      ["Progress idle", "progress_idle_seconds", seconds],
    ],
    evidence.map((event) => ({
      ...event,
      ...event.data,
      explanation: event.data.error_code ? failureReason(event.data) : "—",
      decoder:
        event.data.decoder_mode === "auto"
          ? "Auto"
          : event.data.decoder_threads !== undefined
            ? event.data.decoder_threads + " thread(s)"
            : "Not reported",
    })),
    "No attempt evidence",
    "Attempt and engine details appear as new sessions run. Raw logs are never shown or stored.",
  );
}

function renderTimeline(history, reference) {
  el("timeline-heading").textContent = reference
    ? reference + " · " + history.length + " most recent events · arrival order"
    : "Look up a reference to inspect its history.";
  if (!history.length)
    empty(
      el("events"),
      "No session selected",
      "Look up a reference above. A timeline contains up to 500 retained events.",
    );
  for (const event of history) {
    const item = document.createElement("details"),
      summary = document.createElement("summary"),
      time = document.createElement("span"),
      name = document.createElement("span"),
      meta = document.createElement("span"),
      body = document.createElement("pre");
    item.className = "timeline-event";
    time.className = "event-time";
    time.textContent = when(event.at);
    name.textContent = eventLabel(event);
    meta.className = "event-detail";
    meta.textContent =
      (event.source === "server" ? "Server" : "Browser") +
      " · " +
      (event.file_index ? "File " + event.file_index : "Batch") +
      " · run " +
      (event.run_id?.slice(0, 8) || "—") +
      (event.data.client_sequence ? " · client #" + event.data.client_sequence : "");
    body.textContent = JSON.stringify(event.data, null, 2);
    summary.append(time, name, meta);
    item.append(summary, body);
    el("events").append(item);
  }
}

function renderLiveStatus(r, s, store) {
  const c = r.counters || {};
  cards(
    el("live"),
    [
      [
        "Service",
        r.ready
          ? "Operational"
          : r.draining
            ? "Draining"
            : r.discord_ready
              ? "Starting"
              : "Not ready",
        (r.discord_ready ? "Discord connected" : "Discord not connected") +
          (typeof r.gateway_latency_ms === "number" ? " · " + r.gateway_latency_ms + " ms" : "") +
          (r.commands_synced === false ? " · commands not synced" : ""),
        r.ready ? "good" : "warning",
        "service",
      ],
      [
        "Connected servers",
        number(r.connected_servers),
        (r.server_count_current
          ? "Current count"
          : r.connected_servers == null
            ? "Waiting for Discord connection"
            : "Last known count · Discord disconnected") +
          (r.unavailable_servers ? " · " + number(r.unavailable_servers) + " unavailable" : ""),
        "",
        "connected_servers",
      ],
      ["Release", revision(r.revision), "Up " + age(c.uptime_seconds), "", "release"],
      [
        "Active now",
        number(r.active_sessions) + " links",
        "Uploads " +
          number(r.active_uploads) +
          " · queue " +
          number(r.queue_depth) +
          "/" +
          number(r.queue_capacity) +
          " · workers " +
          number(r.delivery_workers) +
          "/" +
          number(r.worker_capacity),
        r.queue_capacity && r.queue_depth >= 0.8 * r.queue_capacity ? "warning" : "",
        "activity",
      ],
      [
        "Diagnostic store",
        !s.configured && s.configured !== undefined
          ? "Off"
          : s.ready
            ? number(store.events) + " events"
            : "Unavailable",
        s.ready
          ? bytes(store.database_bytes) +
            " · oldest " +
            (store.oldest_event ? when(store.oldest_event) : "—") +
            " · errors " +
            number(s.errors) +
            " · dropped " +
            number(s.dropped) +
            " · queued " +
            number(s.queued)
          : "History is unavailable; the live snapshot still works",
        s.ready && !s.errors && !s.dropped ? "good" : "warning",
        "store",
      ],
    ],
    true,
  );
}

function renderSummary(t, funnel, previous) {
  cards(el("summary"), [
    ["Sessions created", number(t.sessions), "Distinct sessions in this view", "", "sessions"],
    ["Delivered sessions", number(t.delivered), "Confirmed by Discord delivery", "good"],
    ["Videos delivered", number(t.files), "Individual files across delivered batches"],
    [
      "Failure rate",
      percent(funnel.failure_percent),
      "Failed of delivered + failed · previous window " + percent(previous.failure_percent),
      funnel.failure_percent > (previous.failure_percent ?? 100) ? "warning" : "",
      "failure_rate",
    ],
    ["Users with sessions", number(t.users), "Distinct pseudonymous users"],
    ["Servers with sessions", number(t.servers), "Usage in this view, not installations"],
    [
      "Blocked before compression",
      number(t.blocked_before_compression),
      "Preparation failed and compression never started",
      t.blocked_before_compression ? "warning" : "",
    ],
    ["Recovered sessions", number(t.recovered), "A reported failure, then delivery"],
  ]);
}

const stepNames = {
  created: "Link created",
  opened: "Link opened",
  selected: "Files selected",
  started: "Compression started",
  uploaded: "Uploaded to relay",
  delivered: "Delivered to Discord",
};
function renderFunnel(funnel) {
  const target = el("funnel"),
    steps = funnel.steps || [];
  target.replaceChildren();
  if (!steps.length || !steps[0].sessions) {
    empty(target, "No sessions in this view", "Sessions appear here once links are created.");
  } else {
    const list = document.createElement("ol");
    list.className = "funnel";
    for (const step of steps) {
      const item = document.createElement("li"),
        name = document.createElement("span"),
        count = document.createElement("span"),
        track = document.createElement("div"),
        fill = document.createElement("div"),
        rate = document.createElement("span");
      name.className = "funnel-name";
      name.textContent = stepNames[step.step] || step.step;
      count.className = "funnel-count";
      count.textContent = number(step.sessions);
      track.className = "track";
      track.setAttribute("aria-hidden", "true");
      fill.className = "track-fill";
      fill.style.width = (step.overall_percent || 0) + "%";
      track.append(fill);
      rate.className = "funnel-rate";
      rate.textContent =
        step.step_percent == null
          ? "Start of the funnel"
          : percent(step.step_percent) +
            " of the previous step · " +
            percent(step.overall_percent) +
            " of created";
      item.append(name, count, track, rate);
      list.append(item);
    }
    target.append(list);
  }
  const o = funnel.outcomes || {};
  outcomes(el("outcomes"), [
    ["Delivered", number(o.delivered), "Server-confirmed", o.delivered ? "good" : ""],
    ["Failed", number(o.failed), "Latest state is a failure", o.failed ? "danger" : ""],
    ["Cancelled", number(o.cancelled), "Cancelled in the browser"],
    ["In progress", number(o.in_progress), "Updated in the last 2 hours"],
    ["Unknown", number(o.unknown), "Started; no confirmed result", o.unknown ? "warning" : ""],
    ["Abandoned", number(o.abandoned), "Opened, never started"],
    ["Expired", number(o.expired), "Never opened"],
  ]);
}

const chartWidth = (target) => Math.max(280, Math.round(target.clientWidth || 720));
function drawCharts(d) {
  renderTrend(d.funnel || {}, d.window);
  renderServers(d.growth?.events || [], d.runtime || {}, d.window || {});
}

function bucketsFor(window, bucket) {
  const end = window.end || Date.now() / 1000,
    first = Math.floor((window.start || end - 7 * 86400) / bucket) * bucket,
    starts = [];
  for (let start = first; start <= end && starts.length < 100; start += bucket) starts.push(start);
  return starts;
}

function renderTrend(funnel, window) {
  const target = el("trend"),
    bucket = funnel.bucket_seconds || 86400,
    points = new Map((funnel.trend || []).map((point) => [point.start, point])),
    starts = bucketsFor(window || {}, bucket),
    rows = starts.map(
      (start) => points.get(start) || { start, sessions: 0, delivered: 0, failed: 0 },
    );
  el("trend-caption").textContent =
    "By session start time, " + (bucket < 86400 ? "per hour" : "per UTC day") + ".";
  target.replaceChildren();
  table(
    el("trend-table"),
    [
      [bucket < 86400 ? "Hour" : "UTC day", "start", (value) => shortDay(value, bucket)],
      ["Sessions", "sessions", number, "number"],
      ["Delivered", "delivered", number, "number"],
      ["Failed", "failed", number, "number"],
    ],
    rows.filter((row) => row.sessions),
    "No sessions in this view",
  );
  if (!rows.some((row) => row.delivered || row.failed)) {
    empty(target, "No finished sessions yet", "Delivered and failed sessions appear here.");
    return;
  }
  const width = chartWidth(target),
    height = 180,
    left = 34,
    bottom = 22,
    top = 8,
    max = Math.max(1, ...rows.map((row) => Math.max(row.delivered, row.failed))),
    slot = (width - left) / rows.length,
    barWidth = Math.max(1, Math.min(14, (slot - 4) / 2)),
    y = (value) => top + (height - top - bottom) * (1 - value / max),
    chart = svg("svg", {
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": `Delivered and failed sessions ${
        bucket < 86400 ? "per hour" : "per day"
      }; peak ${max}. The data table below lists every value.`,
    }),
    tip = document.createElement("p");
  tip.className = "chart-tip";
  tip.textContent = "Point at a bar for its values.";
  for (const value of [0, max / 2, max]) {
    chart.append(
      svg("line", { class: "grid", x1: left, x2: width, y1: y(value), y2: y(value) }),
      svg(
        "text",
        { x: left - 6, y: y(value) + 4, "text-anchor": "end" },
        number(Math.round(value)),
      ),
    );
  }
  rows.forEach((row, index) => {
    const x = left + index * slot + slot / 2,
      text = `${shortDay(row.start, bucket)}: ${row.delivered} delivered, ${row.failed} failed`,
      hit = svg("rect", { class: "hit", x: left + index * slot, y: 0, width: slot, height }),
      hover = svg("rect", {
        class: "hover",
        x: left + index * slot,
        y: top,
        width: slot,
        height: height - top - bottom,
      });
    hit.append(svg("title", {}, text));
    hit.addEventListener("pointerenter", () => (tip.textContent = text));
    chart.append(hit, hover);
    for (const [key, offset] of [
      ["delivered", -barWidth - 1],
      ["failed", 1],
    ]) {
      if (!row[key]) continue;
      const h = Math.max(2, height - bottom - y(row[key]));
      chart.append(
        svg("rect", {
          class: "bar-" + key,
          x: x + offset,
          y: height - bottom - h,
          width: barWidth,
          height: h,
          rx: Math.min(3, barWidth / 2),
          "pointer-events": "none",
        }),
      );
    }
    if (index === 0 || index === rows.length - 1 || index === Math.floor(rows.length / 2))
      chart.append(
        svg("text", { x, y: height - 6, "text-anchor": "middle" }, shortDay(row.start, bucket)),
      );
  });
  target.append(chart, tip);
}

function failureTable(target, groups, compact = false) {
  const columns = [
    ["Cause", "error_code", (value) => cause(value)],
    ["Stage", "stage", stageName],
    ["Sessions", "sessions", number, "number"],
    ["Share", "share_percent", (value) => bar((value || 0) / 100, percent(value))],
  ];
  if (!compact)
    columns.push(
      ["Later delivered", "recovered", number, "number"],
      ["Last seen", "last_seen", when],
      ["Browsers", "browsers", mix],
      ["Devices", "devices", mix],
    );
  columns.push(["Recent references", "references", references]);
  table(
    target,
    columns,
    groups || [],
    "No failures in this view",
    "Reported failures are grouped here by error code and stage.",
  );
}

function renderFailures(d, t, retries) {
  const failures = d.failures || {};
  failureTable(el("top-failures"), (failures.groups || []).slice(0, 3), true);
  failureTable(el("failure-groups"), failures.groups);
  cards(el("reliability-summary"), [
    [
      "Sessions with a failure",
      number(failures.sessions),
      number(failures.group_count) + " cause groups" + (failures.truncated ? " · recent only" : ""),
      failures.sessions ? "danger" : "",
    ],
    [
      "Needs attention",
      number(t.needs_attention),
      "Latest state is a failure",
      t.needs_attention ? "danger" : "",
    ],
    ["Unknown outcomes", number(t.unknown), "Not counted as confirmed failures"],
    ["Manual runs", number(retries.runs), "Compress starts with a run ID"],
    ["Automatic retries", number(retries.automatic_retries), "Recorded retry attempts"],
  ]);
  group(el("group-engine_signal"), d.groups?.engine_signal);
  table(
    el("statuses"),
    [
      ["State", "status", badge],
      ["Sessions", "count", number, "number"],
    ],
    d.statuses || [],
    "No sessions yet",
  );
}

const measures = [
  ["encode_speed", "Encode speed", speed, "Media seconds per second; files that were re-encoded"],
  [
    "time_to_delivery_seconds",
    "Compress to delivery",
    seconds,
    "Server times from Compress to Discord",
  ],
  ["relay_receive_seconds", "Relay receive", seconds, "Upload of a finished batch"],
  ["queue_seconds", "Delivery queue wait", seconds, "Before a worker sends the batch"],
  ["discord_delivery_seconds", "Discord delivery", seconds, "Sending to Discord", "timings"],
  ["total_seconds", "Browser end to end", seconds, "Browser-reported, per run", "timings"],
  ["encoding_seconds", "Encoding per file", seconds, "Files that became ready", "timings"],
  ["engine_seconds", "Engine loading", seconds, "Per run", "timings"],
  ["input_bytes", "Input file size", bytes, "Per file, browser-reported"],
  ["output_bytes", "Output file size", bytes, "Per file, measured by the relay"],
];
function renderPerformance(d, timings, f) {
  const p = d.percentiles || {};
  cards(el("performance-summary"), [
    ["Median encode speed", speed(p.encode_speed?.p50), "p90 " + speed(p.encode_speed?.p90)],
    [
      "Median compress to delivery",
      seconds(p.time_to_delivery_seconds?.p50),
      "p90 " + seconds(p.time_to_delivery_seconds?.p90),
    ],
    [
      "Size reduction",
      percent(f.reduction_percent),
      number(f.measured_files) + " measured delivered files",
    ],
    [
      "Already small enough",
      percent(f.unchanged_percent),
      "Delivered files sent unchanged · stream-copied " + percent(f.remuxed_percent),
    ],
  ]);
  table(
    el("percentiles"),
    [
      ["Measure", "name"],
      ["p50", "p50", null, "number"],
      ["p90", "p90", null, "number"],
      ["Samples", "samples", number, "number"],
      ["What it measures", "note"],
    ],
    measures
      .map(([key, name, format, note, source]) => {
        const item = (source === "timings" ? timings : p)[key];
        return (
          item && {
            name,
            note,
            samples: item.samples,
            p50: format(item.p50),
            p90: format(item.p90),
          }
        );
      })
      .filter(Boolean),
    "No samples yet",
    "Percentiles appear after compression and delivery reports arrive.",
  );
  outcomes(el("file-summary"), [
    ["Measured files", number(f.measured_files), "Delivered with both sizes"],
    ["Input", bytes(f.input_bytes), "Total, browser-reported"],
    ["Output", bytes(f.output_bytes), "Total, measured by the relay"],
    ["Median input", bytes(p.input_bytes?.p50), "p90 " + bytes(p.input_bytes?.p90)],
    ["Median output", bytes(p.output_bytes?.p50), "p90 " + bytes(p.output_bytes?.p90)],
  ]);
  const share = el("engine-share"),
    engines = d.groups?.engine || [],
    paths = d.groups?.path || [];
  share.replaceChildren();
  const list = (title, rows) => {
    const heading = document.createElement("p"),
      total = rows.reduce((sum, row) => sum + row.sessions, 0);
    heading.className = "metric-label";
    heading.textContent = title;
    share.append(heading);
    if (!rows.length) {
      const none = document.createElement("p");
      none.textContent = "Not reported in this view.";
      share.append(none);
    }
    for (const row of rows)
      share.append(
        bar(
          row.sessions / total,
          `${label(row.value)}: ${number(row.sessions)} (${percent((100 * row.sessions) / total)})`,
        ),
      );
  };
  list("Video engine (sessions)", engines);
  list("Processing path (sessions)", paths);
  table(
    el("timings"),
    [
      ["Measurement", "name", label],
      ["Samples", "samples", number, "number"],
      ["p50", "p50", seconds, "number"],
      ["p90", "p90", seconds, "number"],
      ["p95", "p95", seconds, "number"],
    ],
    Object.entries(timings).map(([name, value]) => ({ name, ...value })),
    "No stage timings yet",
    "Timing samples appear after processing or delivery reports arrive.",
  );
  table(
    el("performance"),
    [
      ["Format", "format", label],
      ["Clip length", "duration_band"],
      ["Output height", "height", (value) => (value ? number(value) + "p" : "Not reported")],
      ["Files", "files", number, "number"],
      ["Mean encode", "mean_encoding", seconds, "number"],
      [
        "× real time",
        "speed",
        (value) => (typeof value === "number" ? value.toFixed(2) + "×" : "—"),
        "number",
      ],
    ],
    d.performance || [],
  );
}

function renderGrowth(d) {
  const g = d.growth || {},
    a = d.adoption || {},
    real = (g.removals || 0) - (g.likely_reconnect_removals || 0);
  cards(el("adoption"), [
    ["Installs", number(g.installs), "In the selected window"],
    [
      "Removals",
      number(real),
      number(g.likely_reconnect_removals) + " more look like reconnect artifacts",
      real > (g.installs || 0) ? "warning" : "",
      "removals",
    ],
    ["Net change", (g.net_change > 0 ? "+" : "") + number(g.net_change), "Installs minus removals"],
    ["Servers with delivery", number(a.activated_servers), "At least one delivered session"],
    ["Repeat servers", number(a.repeat_servers), "Two or more delivered sessions"],
  ]);
  const daily = new Map();
  for (const row of g.daily || []) daily.set(row.day, { ...row });
  for (const row of d.activity || []) {
    if (!["created", "delivered"].includes(row.event)) continue;
    const item = daily.get(row.day) || { day: row.day };
    item[row.event] = row.count;
    daily.set(row.day, item);
  }
  table(
    el("growth-table"),
    [
      ["UTC day", "day"],
      ["Installs", "installs", number, "number"],
      [
        "Removals",
        "removals",
        (value, row) =>
          number(value || 0) +
          (row.likely_reconnect_removals
            ? " (" + row.likely_reconnect_removals + " likely reconnect)"
            : ""),
        "number",
      ],
      ["Sessions created", "created", number, "number"],
      ["Deliveries", "delivered", number, "number"],
      ["Servers at day end", "connected_servers", number, "number"],
    ],
    [...daily.values()].sort((x, y) => (x.day < y.day ? 1 : -1)),
    "No activity recorded yet",
    "History begins when tracking was enabled; older Discord logs are not imported.",
  );
  for (const field of ["input_format", "browser", "device"])
    group(el("group-" + field), d.groups?.[field]);
}

function renderServers(events, r, window) {
  const target = el("servers-chart"),
    points = events
      .filter((event) => typeof event.connected_servers === "number")
      .map((event) => ({ ...event }));
  if (typeof r.connected_servers === "number")
    points.push({
      at: window.end || Date.now() / 1000,
      connected_servers: r.connected_servers,
      event: "now",
    });
  target.replaceChildren();
  if (points.length < 2) {
    empty(
      target,
      "Not enough server changes yet",
      "The count is recorded with each install or removal.",
    );
    return;
  }
  const width = chartWidth(target),
    height = 180,
    left = 40,
    bottom = 22,
    top = 10,
    start = Math.min(window.start || points[0].at, points[0].at),
    end = Math.max(points[points.length - 1].at, start + 1),
    values = points.map((point) => point.connected_servers),
    low = Math.min(...values),
    high = Math.max(...values),
    span = Math.max(1, high - low),
    x = (at) => left + ((width - left - 8) * (at - start)) / (end - start),
    y = (value) => top + (height - top - bottom) * (1 - (value - low) / span),
    chart = svg("svg", {
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": `Connected servers from ${number(points[0].connected_servers)} to ${number(
        values[values.length - 1],
      )}; ${points.length} recorded changes. The daily table below lists the values.`,
    }),
    tip = document.createElement("p");
  tip.className = "chart-tip";
  tip.textContent = "Point at a marker for details. Hollow markers are likely reconnect artifacts.";
  for (const value of [low, high])
    chart.append(
      svg("line", { class: "grid", x1: left, x2: width, y1: y(value), y2: y(value) }),
      svg("text", { x: left - 6, y: y(value) + 4, "text-anchor": "end" }, number(value)),
    );
  let path = "";
  points.forEach((point, index) => {
    path +=
      (index ? `H${x(point.at).toFixed(1)}V` : `M${x(point.at).toFixed(1)},`) +
      y(point.connected_servers).toFixed(1);
  });
  chart.append(svg("path", { class: "line", d: path }));
  for (const point of points) {
    if (point.event === "now") continue;
    const text = `${when(point.at)}: ${
      point.event === "installed" ? "installed" : "removed"
    }${point.likely_reconnect ? " (likely reconnect artifact)" : ""}, ${number(
      point.connected_servers,
    )} servers`;
    const dot = svg("circle", {
      class:
        "dot" +
        (point.event === "removed" ? " removed" : "") +
        (point.likely_reconnect ? " artifact" : ""),
      cx: x(point.at),
      cy: y(point.connected_servers),
      r: 4,
    });
    dot.append(svg("title", {}, text));
    dot.addEventListener("pointerenter", () => (tip.textContent = text));
    chart.append(dot);
  }
  chart.append(
    svg("text", { x: left, y: height - 6 }, new Date(start * 1000).toISOString().slice(5, 10)),
    svg("text", { x: width, y: height - 6, "text-anchor": "end" }, "now"),
  );
  target.append(chart, tip);
}

function renderSessionTable(d) {
  table(
    el("sessions"),
    [
      ["Reference", "reference", (value) => referenceButton(value)],
      ["Created", "created", when],
      ["Outcome", "status", badge],
      [
        "Failure detail",
        "failure_data",
        (value, row) =>
          row.status === "unknown"
            ? "Outcome unknown: no confirmed final result. This does not prove compression failed."
            : ["client_failed", "upload_failed", "delivery_failed"].includes(row.status)
              ? failureReason(value)
              : "—",
      ],
      ["Stage", "failure_data", (value) => (value?.stage ? label(value.stage) : "—")],
      ["Recovery", "status", (_, row) => recovery(row)],
      ["Files", "files", number, "number"],
      ["Failure reports", "failures", number, "number"],
      ["Release", "revision", revision],
      ["Test", "is_test", (value) => (value ? "Yes" : "No")],
    ],
    d.sessions || [],
    "No matching sessions",
    "Change the window or filters. Include test servers to see development activity.",
  );
}

function renderLookup(result) {
  const history = result?.events || [],
    session = result?.session;
  lookupResult = result?.found ? result : null;
  el("lookup-export").disabled = !lookupResult;
  el("lookup-session").hidden = !session;
  if (session)
    outcomes(el("lookup-session"), [
      ["Latest state", label(session.status), "Created " + when(session.created)],
      ["Files delivered", number(session.files), "Failure reports " + number(session.failures)],
      [
        "Failure",
        session.failure_data?.error_code || "—",
        session.failure_data?.error_code ? failureReason(session.failure_data) : "None reported",
      ],
      ["Release", revision(session.revision), session.is_test ? "Test server" : "Production"],
    ]);
  el("events").replaceChildren();
  renderSessionFiles(history);
  renderAttempts(history);
  renderTimeline(history, result?.reference || "");
}

function renderSystem(d, r, s, c) {
  const store = d.store || {};
  cards(el("system-summary"), [
    [
      "Delivery workers",
      number(r.delivery_workers) + " / " + number(r.worker_capacity),
      "Workers currently running",
    ],
    ["Buffered video data", bytes(r.buffer_bytes), "Limit " + bytes(r.buffer_limit_bytes)],
    ["Diagnostic queue", number(s.queued), "Waiting for background storage"],
    [
      "Storage errors / drops",
      number(s.errors) + " / " + number(s.dropped),
      "Since this process started",
      s.errors || s.dropped ? "warning" : "good",
    ],
  ]);
  table(
    el("system"),
    [
      ["Signal", "name"],
      ["Current value", "value"],
    ],
    [
      { name: "Discord connection", value: r.discord_ready ? "Connected" : "Not connected" },
      { name: "Command sync", value: r.commands_synced ? "Complete" : "Pending" },
      { name: "Unavailable servers", value: number(r.unavailable_servers) },
      { name: "Delivery queue", value: number(r.queue_depth) + " / " + number(r.queue_capacity) },
      { name: "Diagnostic storage", value: s.ready ? "Ready" : "Unavailable" },
      { name: "Stored events", value: number(store.events) + " of " + number(s.max_events) },
      { name: "Stored sessions", value: number(store.sessions) },
      { name: "Database size", value: bytes(store.database_bytes) },
      { name: "Reusable free space", value: bytes(store.free_bytes) },
      { name: "Write-ahead log", value: bytes(store.wal_bytes) },
      { name: "Oldest event", value: when(store.oldest_event) },
      { name: "Newest event", value: when(store.newest_event) },
      { name: "Retention", value: number(s.retention_days) + " days" },
      { name: "Release", value: r.revision || "—" },
    ],
  );
  const counterLabels = {
    uptime_seconds: "Process uptime",
    sessions_created: "Sessions created",
    sessions_opened: "Sessions opened",
    batches_queued: "Batches queued",
    files_queued: "Files queued",
    bytes_queued: "Bytes queued",
    deliveries_succeeded: "Delivered batches",
    files_delivered: "Files delivered",
    bytes_delivered: "Bytes delivered",
    deliveries_failed: "Failed batches",
    rate_limited_requests: "Rate-limited requests",
  };
  table(
    el("counters"),
    [
      ["Counter", "name"],
      ["Since process start", "value", null, "number"],
    ],
    Object.entries(c).map(([name, value]) => ({
      name: counterLabels[name] || name,
      value:
        name === "uptime_seconds"
          ? age(value)
          : name.startsWith("bytes_")
            ? bytes(value)
            : number(value),
    })),
  );
  table(
    el("group-revision"),
    [
      ["Release", "value", revision],
      ["Sessions", "sessions", number, "number"],
      ["Delivered", "delivered", number, "number"],
    ],
    d.groups?.revision || [],
    "No releases in this view",
  );
}

function renderAnomalies(items) {
  const list = el("anomaly-list");
  list.replaceChildren();
  for (const item of items || []) {
    const li = document.createElement("li");
    li.className = item.severity;
    li.textContent = item.message;
    list.append(li);
  }
  el("anomalies").hidden = !list.children.length;
}

function renderCollectionStatus(d, t, s) {
  const hidden = d.collection?.hidden_test_sessions || 0;
  el("notice").hidden = !(s.ready && !t.sessions);
  el("show-tests").hidden = !hidden;
  el("notice-title").textContent =
    !el("tests").checked && hidden
      ? "No production sessions in this view yet"
      : "No matching sessions in this view";
  el("notice-text").textContent = hidden
    ? number(hidden) +
      " test-server session(s) are hidden. Include test servers to inspect them; production metrics stay separate by default."
    : "Tracking is active. New sessions will appear here automatically. Try a wider window if filters are applied.";
  el("history-note").textContent =
    "History " +
    (d.collection?.since
      ? "available since " + when(d.collection.since)
      : "begins with the first tracked session") +
    ".";
  el("retention-note").textContent = s.ready
    ? "History retained for up to " + number(s.retention_days) + " days, subject to storage limits."
    : "History storage is unavailable.";
  el("status").textContent = s.ready
    ? "Updated " +
      new Date((d.generated_at || Date.now() / 1000) * 1000).toLocaleTimeString() +
      " · " +
      (el("tests").checked ? "test servers included" : "test servers excluded")
    : "Diagnostic storage unavailable · the live snapshot is still shown.";
  el("status").className = s.errors || s.dropped || !s.ready ? "warning" : "good";
}

function render(d) {
  const t = d.totals || {},
    s = d.storage || {},
    r = d.runtime || {},
    funnel = d.funnel || {};
  renderLiveStatus(r, s, d.store || {});
  renderAnomalies(d.anomalies);
  renderSummary(t, funnel, d.comparison || {});
  renderFunnel(funnel);
  drawCharts(d);
  el("failure-rate").textContent =
    "Failure rate " +
    percent(funnel.failure_percent) +
    " of finished sessions; previous window " +
    percent(d.comparison?.failure_percent) +
    ".";
  renderFailures(d, t, d.retries || {});
  renderPerformance(d, d.timings || {}, d.files || {});
  renderGrowth(d);
  renderSessionTable(d);
  renderSystem(d, r, s, r.counters || {});
  renderCollectionStatus(d, t, s);
}

function query(extra = {}) {
  return new URLSearchParams({
    days: days(),
    tests: el("tests").checked ? "1" : "0",
    dimension: el("dimension").value,
    value: el("value").value,
    ...extra,
  });
}
async function fetchData(params) {
  const response = await fetch("/ops/data?" + params, {
    cache: "no-store",
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok)
    throw new Error(
      response.status === 401
        ? "Sign in again to view diagnostics."
        : response.status === 429
          ? "The dashboard is busy. Try again in a moment."
          : response.status === 400
            ? "That request was not valid."
            : "Could not load diagnostics (" + response.status + ").",
    );
  return response.json();
}

async function load() {
  const sequence = ++loadSequence;
  loading = true;
  snapshot = null;
  el("export").disabled = true;
  el("copy-summary").disabled = true;
  el("status").textContent = "Loading…";
  try {
    const result = await fetchData(query());
    if (sequence !== loadSequence) return;
    render(result);
    snapshot = result;
    el("export").disabled = false;
    el("copy-summary").disabled = !result.summary;
  } catch (error) {
    if (sequence !== loadSequence) return;
    el("status").textContent =
      error.name === "TimeoutError" ? "Refresh timed out. Try again." : error.message;
    el("status").className = "warning";
  } finally {
    if (sequence === loadSequence) loading = false;
  }
}

async function lookup() {
  const sequence = ++lookupSequence,
    reference = el("reference").value.trim().toUpperCase(),
    status = el("lookup-status");
  status.className = "";
  if (!REFERENCE.test(reference)) {
    status.textContent = "Enter a reference like PC-0123456789AB.";
    status.className = "warning";
    renderLookup(null);
    return;
  }
  el("reference").value = reference;
  status.textContent = "Looking up " + reference + "…";
  try {
    const result = await fetchData(query({ reference }));
    if (sequence !== lookupSequence) return;
    const found = result.lookup || {
      reference,
      found: Boolean(result.events?.length),
      events: result.events || [],
    };
    renderLookup(found);
    status.textContent = found.found
      ? reference + " · " + found.events.length + " events"
      : "No session " + reference + " in this window. Try a wider window or include test servers.";
  } catch (error) {
    if (sequence !== lookupSequence) return;
    status.textContent =
      error.name === "TimeoutError" ? "Lookup timed out. Try again." : error.message;
    status.className = "warning";
  }
}

function download(name, value) {
  const url = URL.createObjectURL(
      new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
    ),
    a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

el("filters").addEventListener("submit", (event) => {
  event.preventDefault();
  void load();
});
for (const input of document.querySelectorAll('input[name="days"], #tests'))
  input.addEventListener("change", () => void load());
el("reset").addEventListener("click", () => {
  el("dimension").value = "";
  el("value").value = "";
  void load();
});
el("show-tests").addEventListener("click", () => {
  el("tests").checked = true;
  void load();
});
el("export").addEventListener("click", () => {
  if (snapshot) download("professor-diagnostics.json", snapshot);
});
el("copy-summary").addEventListener("click", async () => {
  if (!snapshot?.summary) return;
  try {
    await navigator.clipboard.writeText(snapshot.summary);
    el("summary-box").hidden = true;
    el("status").textContent = "Summary copied. It contains no user or server identifiers.";
  } catch (_) {
    el("summary-text").value = snapshot.summary;
    el("summary-box").hidden = false;
    el("summary-box").open = true;
    el("summary-text").focus();
    el("summary-text").select();
  }
});
el("lookup").addEventListener("submit", (event) => {
  event.preventDefault();
  void lookup();
});
el("lookup-export").addEventListener("click", () => {
  if (!lookupResult) return;
  download("professor-session-" + lookupResult.reference + ".json", {
    schema_version: snapshot?.schema_version ?? null,
    exported_at: Date.now() / 1000,
    ...lookupResult,
  });
});
const tabs = [...document.querySelectorAll("[data-tab]")];
for (const [index, tab] of tabs.entries()) {
  tab.addEventListener("click", () => showPanel(tab.dataset.tab));
  tab.addEventListener("keydown", (event) => {
    const direction = ["ArrowRight", "ArrowDown"].includes(event.key)
      ? 1
      : ["ArrowLeft", "ArrowUp"].includes(event.key)
        ? -1
        : 0;
    const next = direction
      ? (index + direction + tabs.length) % tabs.length
      : event.key === "Home"
        ? 0
        : event.key === "End"
          ? tabs.length - 1
          : -1;
    if (next >= 0) {
      event.preventDefault();
      showPanel(tabs[next].dataset.tab, true);
    }
  });
}
for (const button of document.querySelectorAll("[data-open]"))
  button.addEventListener("click", () => showPanel(button.dataset.open));
setInterval(() => {
  if (el("auto-refresh").checked && document.visibilityState === "visible" && !loading) void load();
}, 30000);
let resizeTimer = 0;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => snapshot && drawCharts(snapshot), 150);
});
renderLookup(null);
void load();
