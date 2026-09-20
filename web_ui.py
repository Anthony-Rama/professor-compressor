def browser_compressor(max_clips: int, target_bytes: int) -> str:
    """Return the upload form that performs all video encoding in the browser."""
    template = r"""
<img class="brand" src="/brand/professor-compressor.png"
  alt="Professor Compressor mascot">
<h1>Compress videos for Discord</h1>
<p class="intro">Choose up to __MAX_CLIPS__ videos. Your videos are automatically
  formatted to fit Discord's current free upload limit.</p>
<div class="stay-open">
  <span aria-hidden="true">⏱</span>
  <span><strong>Keep this page open and active until it finishes.</strong><br>
  This helps compression run at full speed.</span>
</div>
<form id="upload">
  <input class="file-input" id="clips" name="clips" type="file"
    accept="video/*" multiple required>
  <label class="file-picker" for="clips">
    <span class="picker-plus" aria-hidden="true">+</span>
    <span><strong>Choose videos</strong><small>Select up to __MAX_CLIPS__ files</small></span>
  </label>
  <p id="selection" class="selection" aria-live="polite">No videos selected</p>
  <button id="submit" type="submit" disabled>Compress &amp; send to Discord</button>
  <div id="work" class="work" hidden>
    <progress id="progress" max="100" value="0"></progress>
    <p id="status" aria-live="polite">Preparing…</p>
  </div>
  <p class="privacy"><span aria-hidden="true">🔒</span> Your original videos stay on this device.</p>
</form>

<script type="module">
import { FFmpeg } from "/assets/ffmpeg-esm/index.js";
import { fetchFile, toBlobURL } from "/assets/util-esm/index.js";

const MAX_CLIPS = __MAX_CLIPS__;
const TARGET_BYTES = __TARGET_BYTES__;
const SINGLE_CORE_BASE = "/assets/core-esm";
const MULTI_CORE_BASE = "/assets/core-mt-esm";

const form = document.getElementById("upload");
const clips = document.getElementById("clips");
const submit = document.getElementById("submit");
const progress = document.getElementById("progress");
const status = document.getElementById("status");
const selection = document.getElementById("selection");
const work = document.getElementById("work");
let ffmpeg;
let encoderMode = "single-threaded";
let currentClip = 0;
let currentAttempt = 1;
let totalClips = 0;
let lastFfmpegMessage = "";

function createEncoder() {
  const encoder = new FFmpeg();
  encoder.on("log", ({ message }) => {
    lastFfmpegMessage = message;
    console.debug("[ffmpeg] " + message);
  });
  encoder.on("progress", ({ progress: fraction }) => {
    const percent = Math.max(0, Math.min(100, Math.round(fraction * 100)));
    progress.value = percent;
    status.textContent =
      "Compressing video " + currentClip + " of " + totalClips +
      (currentAttempt === 1 ? "" : " (final adjustment)") +
      ", " + percent + "%";
  });
  return encoder;
}

async function loadEncoder() {
  const supportsThreads =
    self.crossOriginIsolated && typeof SharedArrayBuffer !== "undefined";

  if (supportsThreads) {
    status.textContent = "Preparing the compressor…";
    ffmpeg = createEncoder();
    try {
      await ffmpeg.load({
        coreURL: await toBlobURL(
          MULTI_CORE_BASE + "/ffmpeg-core.js",
          "text/javascript"
        ),
        wasmURL: await toBlobURL(
          MULTI_CORE_BASE + "/ffmpeg-core.wasm",
          "application/wasm"
        ),
        workerURL: await toBlobURL(
          MULTI_CORE_BASE + "/ffmpeg-core.worker.js",
          "text/javascript"
        )
      });
      return "multithreaded";
    } catch (error) {
      console.warn("Multithreaded encoder unavailable; using fallback.", error);
      ffmpeg.terminate();
    }
  }

  status.textContent = "Preparing the compressor…";
  ffmpeg = createEncoder();
  await ffmpeg.load({
    coreURL: await toBlobURL(
      SINGLE_CORE_BASE + "/ffmpeg-core.js",
      "text/javascript"
    ),
    wasmURL: await toBlobURL(
      SINGLE_CORE_BASE + "/ffmpeg-core.wasm",
      "application/wasm"
    )
  });
  return "single-threaded";
}

function showError(message) {
  status.textContent = message;
  status.className = "error";
}

function readableSize(bytes) {
  if (bytes < 1024 * 1024) return Math.max(1, Math.round(bytes / 1024)) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

clips.addEventListener("change", () => {
  const selected = Array.from(clips.files);
  const validCount = selected.length >= 1 && selected.length <= MAX_CLIPS;
  submit.disabled = !validCount;
  selection.className = validCount || selected.length === 0
    ? "selection"
    : "selection error";
  if (selected.length === 0) {
    selection.textContent = "No videos selected";
  } else if (selected.length > MAX_CLIPS) {
    selection.textContent = "Choose no more than " + MAX_CLIPS + " videos.";
  } else {
    const totalBytes = selected.reduce((sum, file) => sum + file.size, 0);
    selection.textContent = selected.length +
      (selected.length === 1 ? " video" : " videos") +
      " selected · " + readableSize(totalBytes);
  }
});

function durationOf(file) {
  return new Promise((resolve, reject) => {
    const video = document.createElement("video");
    const url = URL.createObjectURL(file);
    video.preload = "metadata";
    video.onloadedmetadata = () => {
      const duration = video.duration;
      URL.revokeObjectURL(url);
      if (Number.isFinite(duration) && duration > 0) resolve(duration);
      else reject(new Error("Could not determine the duration of " + file.name));
    };
    video.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("The browser could not read " + file.name));
    };
    video.src = url;
  });
}

function safeStem(filename, index) {
  const withoutExtension = filename.replace(/\.[^/.]+$/, "");
  const cleaned = withoutExtension.replace(/[^a-zA-Z0-9._ -]/g, "_").trim();
  return cleaned || "clip-" + index;
}

function safeOriginalName(filename, index) {
  const extension = filename.match(/\.[a-zA-Z0-9]{1,10}$/)?.[0] || "";
  return safeStem(filename, index) + extension;
}

function isMp4(file) {
  return file.type === "video/mp4" || /\.mp4$/i.test(file.name);
}

async function removeVirtualFile(name) {
  try {
    await ffmpeg.deleteFile(name);
  } catch (_) {
    // It is okay if FFmpeg did not create an optional pass-log file.
  }
}

async function compressOne(file, index) {
  if (file.size <= TARGET_BYTES && isMp4(file)) {
    status.textContent =
      "Video " + currentClip + " of " + totalClips +
      " already fits. No compression needed.";
    progress.value = 100;
    return {
      blob: file,
      name: safeOriginalName(file.name, index)
    };
  }

  const duration = await durationOf(file);
  const audioKbps = 96;
  const usableBits = TARGET_BYTES * 8 * 0.85;
  const totalKbps = usableBits / duration / 1000;
  let videoKbps = Math.floor(totalKbps - audioKbps);
  if (videoKbps < 100) {
    throw new Error(
      file.name + " is too long to fit Discord's limit at a usable quality."
    );
  }

  const inputName = "input-" + index + ".video";
  const outputName = "output-" + index + ".mp4";
  const scale =
    "fps=fps='min(source_fps,30)'," +
    "scale=w='min(1280,iw)':h='min(720,ih)':" +
    "force_original_aspect_ratio=decrease," +
    "scale=trunc(iw/2)*2:trunc(ih/2)*2";
  async function encode(bitrate) {
    lastFfmpegMessage = "";
    return ffmpeg.exec([
      "-i", inputName,
      "-map", "0:v:0",
      "-map", "0:a:0?",
      "-c:v", "libx264",
      "-threads", encoderMode === "multithreaded" ? "4" : "1",
      "-preset", "veryfast",
      "-b:v", bitrate + "k",
      "-maxrate", bitrate + "k",
      "-bufsize", (bitrate * 2) + "k",
      "-vf", scale,
      "-pix_fmt", "yuv420p",
      "-c:a", "aac",
      "-b:a", audioKbps + "k",
      "-movflags", "+faststart",
      outputName
    ]);
  }

  try {
    await ffmpeg.writeFile(inputName, await fetchFile(file));
    currentAttempt = 1;
    let exitCode = await encode(videoKbps);
    if (exitCode !== 0) {
      console.error("Encoder failure:", lastFfmpegMessage);
      throw new Error("Could not compress " + file.name + ". Please try again.");
    }

    let output = await ffmpeg.readFile(outputName);
    if (output.byteLength > TARGET_BYTES) {
      const correction = TARGET_BYTES / output.byteLength;
      videoKbps = Math.max(100, Math.floor(videoKbps * correction * 0.92));
      await removeVirtualFile(outputName);
      currentAttempt = 2;
      progress.value = 0;
      exitCode = await encode(videoKbps);
      if (exitCode !== 0) {
        console.error("Final adjustment failure:", lastFfmpegMessage);
        throw new Error("Could not finish " + file.name + ". Please try again.");
      }
      output = await ffmpeg.readFile(outputName);
    }
    if (output.byteLength > TARGET_BYTES) {
      throw new Error(file.name + " could not be reduced below Discord's limit.");
    }
    return {
      blob: new Blob([output], { type: "video/mp4" }),
      name: safeStem(file.name, index) + "-compressed.mp4"
    };
  } finally {
    await removeVirtualFile(inputName);
    await removeVirtualFile(outputName);
  }
}

async function uploadResults(results) {
  const data = new FormData();
  for (const result of results) data.append("clips", result.blob, result.name);

  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", window.location.href);
    request.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      const percent = Math.round(event.loaded / event.total * 100);
      progress.value = percent;
      status.textContent = "Sending videos to Discord: " + percent + "%";
    });
    request.addEventListener("load", () => {
      document.open();
      document.write(request.responseText);
      document.close();
      resolve();
    });
    request.addEventListener("error", () => {
      reject(new Error("The compressed-file upload failed."));
    });
    request.send(data);
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const selected = Array.from(clips.files);
  if (selected.length < 1 || selected.length > MAX_CLIPS) {
    showError("Choose between 1 and " + MAX_CLIPS + " clips.");
    return;
  }

  submit.disabled = true;
  clips.disabled = true;
  work.hidden = false;
  progress.style.display = "block";
  status.className = "";
  totalClips = selected.length;

  try {
    if (selected.some((file) => file.size > TARGET_BYTES || !isMp4(file))) {
      encoderMode = await loadEncoder();
      status.textContent = "Compressor ready. Starting…";
    }

    const results = [];
    for (let index = 0; index < selected.length; index += 1) {
      currentClip = index + 1;
      progress.value = 0;
      results.push(await compressOne(selected[index], currentClip));
    }

    status.textContent = "Sending videos to Discord…";
    await uploadResults(results);
  } catch (error) {
    console.error(error);
    showError(error.message || "Compression failed in this browser.");
    submit.disabled = false;
    clips.disabled = false;
  }
});
</script>
"""
    return (
        template.replace("__MAX_CLIPS__", str(max_clips))
        .replace("__TARGET_BYTES__", str(target_bytes))
        .replace("__TARGET_MB__", f"{target_bytes / (1024 * 1024):.1f}")
    )
