// FFmpeg lifecycle, media probing, and size-targeted encoding.
import { codedError, processingError } from "./shared.js";

export function createVideoEncoder({
  assetPrefix: ASSET_PREFIX,
  targetBytes: TARGET_BYTES,
  discordLimitBytes: DISCORD_LIMIT_BYTES,
  maxBatchBytes: MAX_BATCH_BYTES,
  EncoderClient,
  fetchFile,
  diagnostic,
  getFiles,
  getBatchMetrics,
  isCancelled,
  executeWithActivity,
  noteEncodingProgress,
  events,
}) {
  const OUTPUT_TARGET_RATIO = 0.97;
  const SINGLE_CORE_BASE = ASSET_PREFIX + "/core-esm";
  const MULTI_CORE_BASE = ASSET_PREFIX + "/core-mt-esm";
  const DIAGNOSTIC_ASSET_BUILD = ASSET_PREFIX.split("/").pop();

  let ffmpeg = null;
  let encoderMode = "single-threaded";
  let currentState = null;
  let currentAttempt = 1;
  let lastFfmpegMessage = "";

  function safeStem(filename, index) {
    const withoutExtension = filename.replace(/\.[^/.]+$/, "");
    const cleaned = withoutExtension.replace(/[^a-zA-Z0-9._ -]/g, "_").trim();
    return cleaned || "clip-" + index;
  }

  function safeOriginalName(filename, index) {
    return safeStem(filename, index) + ".mp4";
  }

  function outputTargetBytes() {
    const batchTargetBytes = Math.floor(MAX_BATCH_BYTES / Math.max(1, getFiles().length));
    return Math.min(TARGET_BYTES, batchTargetBytes);
  }

  function unchangedLimitBytes() {
    const batchTargetBytes = Math.floor(MAX_BATCH_BYTES / Math.max(1, getFiles().length));
    return Math.min(DISCORD_LIMIT_BYTES, batchTargetBytes);
  }

  function createEncoder() {
    const encoder = new EncoderClient(
      new URL(ASSET_PREFIX + "/ffmpeg-esm/worker.js", location.origin),
    );
    encoder.on("log", ({ message: line }) => {
      lastFfmpegMessage = line;
      diagnostic.log(line);
    });
    let lastProgressState = null;
    let lastProgressAttempt = 0;
    let lastProgressPercent = -1;
    encoder.on("progress", ({ progress: fraction, time }) => {
      if (!currentState || isCancelled()) return;
      diagnostic.progress(fraction, time);
      noteEncodingProgress(fraction, time);
      const percent = Math.max(0, Math.min(100, Math.round(fraction * 100)));
      if (
        currentState === lastProgressState &&
        currentAttempt === lastProgressAttempt &&
        percent === lastProgressPercent
      )
        return;
      lastProgressState = currentState;
      lastProgressAttempt = currentAttempt;
      lastProgressPercent = percent;
      const adjustment = currentAttempt > 1 ? "Adjusting size" : "Compressing";
      events.emit("fileStatus", currentState, adjustment + " " + percent + "%", percent);
      const completed = getFiles().filter((state) => state.finalSize > 0).length;
      const overall = ((completed + fraction) / getFiles().length) * 80;
      events.emit("progress", Math.max(0, Math.min(80, overall)));
      events.emit(
        "runDetail",
        "Video " + currentState.index + " of " + getFiles().length + " · " + percent + "%",
      );
    });
    return encoder;
  }

  async function startEncoder(config) {
    const encoder = ffmpeg;
    let timer;
    try {
      await Promise.race([
        encoder.load(config),
        new Promise((_, reject) => {
          timer = setTimeout(() => {
            const error = new Error(
              "The video engine took too long to start. Check your connection and try again.",
            );
            error.code = "encoder_start_timeout";
            reject(error);
          }, 300000);
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
  }

  async function loadEncoder() {
    const batchMetrics = getBatchMetrics();
    const start = performance.now();
    batchMetrics.stage = "engine";
    try {
      const mode = await diagnostic.step(
        "engine",
        0,
        () => loadEncoderCore(),
        "ENGINE_LOAD_FAILED",
      );
      batchMetrics.engine = mode;
      return mode;
    } finally {
      batchMetrics.engine_seconds =
        (batchMetrics.engine_seconds || 0) + (performance.now() - start) / 1000;
    }
  }

  async function loadEncoderCore() {
    if (isCancelled()) throw new DOMException("Cancelled", "AbortError");
    for (const state of getFiles()) {
      if (!state.finalSize) events.emit("fileStatus", state, "Loading video engine", 0);
    }
    events.emit("runDetail", "Loading video engine");
    const supportsThreads = self.crossOriginIsolated && typeof SharedArrayBuffer !== "undefined";
    if (supportsThreads) {
      ffmpeg = createEncoder();
      try {
        events.emit("runDetail", "Starting video engine");
        await startEncoder({
          coreURL: MULTI_CORE_BASE + "/ffmpeg-core.js",
          wasmURL: MULTI_CORE_BASE + "/ffmpeg-core.wasm",
          workerURL: MULTI_CORE_BASE + "/ffmpeg-core.worker.js",
        });
        if (isCancelled()) throw new DOMException("Cancelled", "AbortError");
        return "multithreaded";
      } catch (error) {
        if (isCancelled()) throw error;
        console.warn("Multithreaded encoder unavailable; using fallback.", error);
        ffmpeg.terminate();
        if (error?.code === "encoder_start_timeout") {
          ffmpeg = null;
          throw error;
        }
      }
    }
    ffmpeg = createEncoder();
    try {
      for (const state of getFiles()) {
        if (!state.finalSize) events.emit("fileStatus", state, "Loading compatibility engine", 0);
      }
      events.emit("runDetail", "Loading compatibility engine");
      await startEncoder({
        coreURL: SINGLE_CORE_BASE + "/ffmpeg-core.js",
        wasmURL: SINGLE_CORE_BASE + "/ffmpeg-core.wasm",
      });
    } catch (error) {
      if (ffmpeg) ffmpeg.terminate();
      ffmpeg = null;
      throw error;
    }
    if (isCancelled()) throw new DOMException("Cancelled", "AbortError");
    return "single-threaded";
  }

  async function resetEncoder() {
    if (ffmpeg) ffmpeg.terminate();
    ffmpeg = null;
    encoderMode = await loadEncoder();
  }

  async function videoSignature(file, index) {
    let bytes;
    try {
      bytes = new Uint8Array(await file.slice(0, 512).arrayBuffer());
    } catch (_) {
      const error = codedError(
        "This clip could not be read. Reselect it from a saved folder and try again.",
        "FILE_READ_FAILED",
      );
      error.diagnosticFileIndex = index;
      throw error;
    }
    const ascii = (start, length) => String.fromCharCode(...bytes.slice(start, start + length));
    if (bytes.length >= 16 && ascii(4, 4) === "ftyp") {
      const majorBrand = ascii(8, 4);
      if (majorBrand === "qt  ") return "mov";
      if (/^3g/.test(majorBrand)) return "3gp";
      const boxLength = Math.min(bytes.length, new DataView(bytes.buffer).getUint32(0));
      const allowedBrands = new Set([
        "avc1",
        "dash",
        "iso2",
        "iso3",
        "iso4",
        "iso5",
        "iso6",
        "isom",
        "M4V ",
        "mp41",
        "mp42",
        "MSNV",
      ]);
      for (let offset = 8; offset + 4 <= boxLength; offset += 4) {
        if (allowedBrands.has(ascii(offset, 4))) return "mp4";
      }
      throw codedError(
        file.name +
          " uses an unsupported media-container signature. " +
          "Choose a supported video format listed below the upload controls.",
        "UNSUPPORTED_CONTAINER",
      );
    }
    if (
      bytes.length >= 4 &&
      bytes[0] === 0x1a &&
      bytes[1] === 0x45 &&
      bytes[2] === 0xdf &&
      bytes[3] === 0xa3
    ) {
      const header = ascii(0, bytes.length).toLowerCase();
      if (header.includes("matroska")) return "mkv";
      if (header.includes("webm")) return "webm";
      return "webm/mkv";
    }
    if (bytes.length >= 12 && ascii(0, 4) === "RIFF" && ascii(8, 4) === "AVI ") return "avi";
    if (bytes.length >= 4 && ascii(0, 4) === "OggS") return "ogg";
    if (bytes.length >= 3 && ascii(0, 3) === "FLV") return "flv";
    const asfHeader = [
      0x30, 0x26, 0xb2, 0x75, 0x8e, 0x66, 0xcf, 0x11, 0xa6, 0xd9, 0x00, 0xaa, 0x00, 0x62, 0xce,
      0x6c,
    ];
    if (asfHeader.every((value, index) => bytes[index] === value)) return "asf/wmv";
    if (
      bytes.length >= 4 &&
      bytes[0] === 0x00 &&
      bytes[1] === 0x00 &&
      bytes[2] === 0x01 &&
      [0xba, 0xb3].includes(bytes[3])
    )
      return "mpeg";
    if (bytes.length >= 377 && bytes[0] === 0x47 && bytes[188] === 0x47) return "ts";
    if (bytes.length >= 389 && bytes[4] === 0x47 && bytes[196] === 0x47 && bytes[388] === 0x47)
      return "m2ts";
    throw codedError(
      file.name +
        " does not have a recognized video signature. " +
        "Choose a supported video format listed below the upload controls.",
      "INVALID_SIGNATURE",
    );
  }

  async function removeVirtualFile(name) {
    if (!ffmpeg) return;
    try {
      await ffmpeg.deleteFile(name);
    } catch (_) {
      /* Optional file. */
    }
  }

  async function timedEncode(state, args, sizeAdjustment = false) {
    getBatchMetrics().stage = "encoding";
    state.encodingAttempts += 1;
    const start = performance.now();
    diagnostic.configure(
      args,
      encoderMode,
      DIAGNOSTIC_ASSET_BUILD,
      state.encodingAttempts,
      sizeAdjustment,
    );
    try {
      return await executeWithActivity("encoding", state.index, () => ffmpeg.exec(args));
    } finally {
      // Include failed encoding attempts and size retries, but exclude engine
      // loading, probing, file copies, remuxing, upload, and Discord delivery.
      state.encodingMs += Math.max(0, performance.now() - start);
    }
  }

  async function compressOne(state) {
    const batchMetrics = getBatchMetrics();
    batchMetrics.stage = "metadata";
    const file = state.file;
    const format = await diagnostic.step(
      "validation",
      state.index,
      () => videoSignature(file, state.index),
      "INVALID_SIGNATURE",
    );
    state.inputFormat = format;
    diagnostic.emit("stage_finished", state.index, {
      stage: "validation",
      input_bytes: file.size,
      input_format: format,
      target_bytes: outputTargetBytes(),
      discord_limit: DISCORD_LIMIT_BYTES,
    });
    const effectiveTargetBytes = outputTargetBytes();
    if (file.size <= unchangedLimitBytes() && format === "mp4") {
      events.emit("fileStatus", state, "Already fits", 100);
      events.emit("fileSizes", state, file.size);
      return {
        blob: file,
        name: safeOriginalName(file.name, state.index),
        state,
        diagnostic: {
          input_bytes: file.size,
          duration_seconds: null,
          video_kbps: null,
          copied: true,
          input_format: format,
        },
      };
    }
    // Probe using the same decoder as conversion rather than HTML video support.
    // Browsers cannot read metadata for many otherwise decodable MKV/AVI files.
    const inputName = "input-" + state.index + ".video";
    const outputName = "output-" + state.index + ".mp4";
    const probeName = "probe-" + state.index + ".json";
    try {
      await diagnostic.step(
        "file_read",
        state.index,
        async () => {
          await ffmpeg.writeFile(inputName, await fetchFile(file));
        },
        "FILE_READ_FAILED",
      );
      events.emit("fileStatus", state, "Reading video metadata", 0);
      const metadataStarted = performance.now();
      await diagnostic.step(
        "metadata",
        state.index,
        () =>
          ffmpeg.ffprobe(
            [
              "-v",
              "error",
              "-show_error",
              "-show_entries",
              "format=duration:stream=codec_type,codec_name,duration,width,height,avg_frame_rate,r_frame_rate,pix_fmt,profile,level,color_transfer,channels,sample_rate:stream_side_data=rotation",
              "-of",
              "json",
              inputName,
              "-o",
              probeName,
            ],
            30000,
          ),
        "METADATA_INVALID",
      );
      if (isCancelled()) throw new DOMException("Cancelled", "AbortError");
      // The pinned wasm core returns -1 even for successful probes; validate its
      // structured output instead of relying on that exit status.
      let metadata;
      try {
        metadata = JSON.parse(await ffmpeg.readFile(probeName, "utf8"));
        if (metadata.error || !Array.isArray(metadata.streams)) throw new Error("Invalid metadata");
      } catch (_) {
        if (isCancelled()) throw new DOMException("Cancelled", "AbortError");
        throw codedError(
          "Could not determine video metadata for " +
            file.name +
            ". The file may be damaged or unsupported by this encoder.",
          performance.now() - metadataStarted >= 29500 ? "METADATA_TIMEOUT" : "METADATA_INVALID",
        );
      }
      const videoStream = metadata.streams.find((stream) => stream.codec_type === "video");
      const audioStream = metadata.streams.find((stream) => stream.codec_type === "audio");
      if (!videoStream)
        throw codedError(
          "No video stream found in " + file.name + ". Choose a video, not an audio-only file.",
          "NO_VIDEO_STREAM",
        );
      const rateParts = String(videoStream.avg_frame_rate || "0/1")
        .split("/")
        .map(Number);
      state.inputMedia = {
        input_width: videoStream.width,
        input_height: videoStream.height,
        input_fps: rateParts[0] / (rateParts[1] || 1),
        video_codec: videoStream.codec_name,
        audio_codec: audioStream?.codec_name || "none",
        ...diagnostic.media(videoStream, audioStream),
      };
      const duration = [metadata.format?.duration, videoStream.duration]
        .map(Number)
        .find((value) => Number.isFinite(value) && value > 0);
      if (!duration)
        throw codedError(
          "Could not determine the duration of " +
            file.name +
            ". Re-export the clip with a finite duration.",
          "DURATION_UNKNOWN",
        );
      state.inputDuration = duration;
      diagnostic.emit("stage_finished", state.index, {
        stage: "metadata",
        ...state.inputMedia,
        input_format: format,
        input_bytes: file.size,
        duration_seconds: duration,
        video_codec: videoStream.codec_name,
        audio_codec: audioStream?.codec_name || "none",
      });
      // These pinned cores expose the native AV1 decoder but no usable software
      // AV1 implementation. Revisit this guard when upgrading the core build.
      // Fitting MP4 passthrough above deliberately does not require decoding.
      if (videoStream.codec_name === "av1") {
        diagnostic.mark("validation");
        throw codedError(
          "This video uses AV1, which this version of the compressor cannot convert. " +
            "Export it as H.264 MP4 with AAC audio, then replace this file. Your other selected clips are still available.",
          "UNSUPPORTED_CODEC",
        );
      }
      const audioKbps = audioStream ? 96 : 0;
      state.audioKbps = audioKbps;
      const usableBits = effectiveTargetBytes * 8 * OUTPUT_TARGET_RATIO;
      const budgetVideoKbps = Math.floor(usableBits / duration / 1000 - audioKbps);
      function requireEncodingBudget() {
        if (budgetVideoKbps < 100) {
          diagnostic.mark("validation");
          throw codedError(
            "This video is too long to fit the available upload limit with the current video and audio settings. " +
              "Choose a shorter video or a destination with a larger upload allowance. Your other selected clips are still available.",
            "TARGET_NOT_FEASIBLE",
          );
        }
      }
      const scale =
        "fps=fps='min(source_fps,30)'," +
        "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease," +
        "scale=trunc(iw/2)*2:trunc(ih/2)*2";
      if (format !== "mp4") {
        // Compatible streams can be repackaged into MP4 without re-encoding.
        // Otherwise make a quality-based conversion and check its actual size.
        let remuxTooLarge = false;
        if (
          videoStream.codec_name === "h264" &&
          (!audioStream || audioStream.codec_name === "aac")
        ) {
          currentAttempt = 1;
          lastFfmpegMessage = "";
          events.emit("fileStatus", state, "Preparing MP4 without re-encoding", 0);
          batchMetrics.stage = "remux";
          const remuxArgs = [
            "-i",
            inputName,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            outputName,
          ];
          diagnostic.configure(remuxArgs, encoderMode, DIAGNOSTIC_ASSET_BUILD);
          const remuxExit = await executeWithActivity("remux", state.index, () =>
            ffmpeg.exec(remuxArgs),
          );
          if (isCancelled()) throw new DOMException("Cancelled", "AbortError");
          if (remuxExit === 0) {
            const remuxed = await ffmpeg.readFile(outputName);
            if (remuxed.byteLength <= unchangedLimitBytes()) {
              const blob = new Blob([remuxed], { type: "video/mp4" });
              events.emit("fileStatus", state, "Converted without re-encoding", 100);
              events.emit("fileSizes", state, blob.size);
              return {
                blob,
                name: safeStem(file.name, state.index) + "-converted.mp4",
                state,
                diagnostic: {
                  input_bytes: file.size,
                  duration_seconds: duration,
                  video_kbps: null,
                  copied: true,
                  input_format: format,
                },
              };
            }
            remuxTooLarge = true;
          }
          await removeVirtualFile(outputName);
        }
        if (!remuxTooLarge) {
          requireEncodingBudget();
          currentAttempt = 1;
          lastFfmpegMessage = "";
          events.emit("fileStatus", state, "Converting to MP4", 0);
          const conversionExit = await timedEncode(state, [
            "-i",
            inputName,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-threads",
            encoderMode === "multithreaded" ? "4" : "1",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-vf",
            scale,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            audioKbps + "k",
            "-movflags",
            "+faststart",
            outputName,
          ]);
          if (isCancelled()) throw new DOMException("Cancelled", "AbortError");
          if (conversionExit !== 0)
            throw codedError(
              "The encoder could not read " +
                file.name +
                ". The video may use an unsupported or damaged codec.",
              "ENCODE_FAILED",
            );
          const converted = await ffmpeg.readFile(outputName);
          if (converted.byteLength <= unchangedLimitBytes()) {
            const blob = new Blob([converted], { type: "video/mp4" });
            events.emit("fileStatus", state, "Converted to MP4", 100);
            events.emit("fileSizes", state, blob.size);
            return {
              blob,
              name: safeStem(file.name, state.index) + "-converted.mp4",
              state,
              diagnostic: {
                input_bytes: file.size,
                duration_seconds: duration,
                video_kbps: null,
                copied: false,
                input_format: format,
              },
            };
          }
          await removeVirtualFile(outputName);
        }
        events.emit("fileStatus", state, "Converted MP4 is too large; adjusting size", 0);
      }
      requireEncodingBudget();
      let videoKbps = budgetVideoKbps;
      async function encode(bitrate) {
        lastFfmpegMessage = "";
        return timedEncode(
          state,
          [
            "-i",
            inputName,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-threads",
            encoderMode === "multithreaded" ? "4" : "1",
            "-preset",
            "veryfast",
            "-b:v",
            bitrate + "k",
            "-maxrate",
            bitrate + "k",
            "-bufsize",
            bitrate * 2 + "k",
            "-vf",
            scale,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            audioKbps + "k",
            "-movflags",
            "+faststart",
            outputName,
          ],
          format !== "mp4" || currentAttempt > 1,
        );
      }
      currentAttempt = 1;
      let exitCode = await encode(videoKbps);
      if (isCancelled()) throw new DOMException("Cancelled", "AbortError");
      if (exitCode !== 0)
        throw codedError(
          "The encoder could not read " +
            file.name +
            ". The video may use an unsupported or damaged codec.",
          "ENCODE_FAILED",
        );
      let output = await ffmpeg.readFile(outputName);
      if (output.byteLength > effectiveTargetBytes) {
        const correction = effectiveTargetBytes / output.byteLength;
        videoKbps = Math.max(100, Math.floor(videoKbps * correction * OUTPUT_TARGET_RATIO));
        await removeVirtualFile(outputName);
        currentAttempt = 2;
        events.emit("fileStatus", state, "Adjusting final size", 0);
        exitCode = await encode(videoKbps);
        if (isCancelled()) throw new DOMException("Cancelled", "AbortError");
        if (exitCode !== 0)
          throw codedError(
            "The final size adjustment failed for " + file.name + ". Try that video by itself.",
            "ENCODE_FAILED",
          );
        output = await ffmpeg.readFile(outputName);
      }
      if (output.byteLength > effectiveTargetBytes) {
        throw codedError(
          file.name +
            " could not be reduced below Discord's limit. " +
            "Trim it into a shorter clip and try again.",
          "OUTPUT_TOO_LARGE",
        );
      }
      const blob = new Blob([output], { type: "video/mp4" });
      events.emit("fileStatus", state, "Compressed", 100);
      events.emit("fileSizes", state, blob.size);
      return {
        blob,
        name: safeStem(file.name, state.index) + "-compressed.mp4",
        state,
        diagnostic: {
          input_bytes: file.size,
          duration_seconds: duration,
          video_kbps: videoKbps,
          copied: false,
          input_format: format,
        },
      };
    } finally {
      if (state.finalSize && state.encodingAttempts > 0 && !isCancelled()) {
        // Header-only metadata read of the existing virtual output: no copy or encode.
        try {
          await ffmpeg.ffprobe(
            [
              "-v",
              "error",
              "-select_streams",
              "v:0",
              "-show_entries",
              "stream=width,height,avg_frame_rate",
              "-of",
              "json",
              outputName,
              "-o",
              probeName,
            ],
            5000,
          );
          const stream = JSON.parse(await ffmpeg.readFile(probeName, "utf8")).streams?.[0];
          if (stream) {
            const rate = String(stream.avg_frame_rate || "0/1")
              .split("/")
              .map(Number);
            state.outputMedia = {
              output_width: stream.width,
              output_height: stream.height,
              output_fps: rate[0] / (rate[1] || 1),
            };
          }
        } catch (_) {
          /* Optional metadata cannot fail delivery. */
        }
      }
      await removeVirtualFile(inputName);
      await removeVirtualFile(outputName);
      await removeVirtualFile(probeName);
    }
  }

  async function compressWithRetry(state) {
    const batchMetrics = getBatchMetrics();
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      try {
        currentState = state;
        diagnostic.attempt(state.index, attempt);
        events.emit("fileStatus", state, attempt === 1 ? "Starting" : "Retrying automatically", 0);
        const result = await compressOne(state);
        if (isCancelled()) throw new DOMException("Cancelled", "AbortError");
        result.diagnostic.encoding_seconds = Math.round(state.encodingMs) / 1000;
        result.diagnostic.telemetry = {
          encoding_attempts: state.encodingAttempts,
          path: result.diagnostic.copied
            ? result.diagnostic.duration_seconds
              ? "remuxed"
              : "unchanged"
            : result.diagnostic.video_kbps === null
              ? "converted"
              : "compressed",
          ...state.inputMedia,
          ...state.outputMedia,
        };
        if (state.encodingAttempts === 0 && state.inputMedia) {
          Object.assign(result.diagnostic.telemetry, {
            output_width: state.inputMedia.input_width,
            output_height: state.inputMedia.input_height,
            output_fps: state.inputMedia.input_fps,
          });
        }
        diagnostic.emit("file_ready", state.index, {
          ...result.diagnostic,
          ...result.diagnostic.telemetry,
          output_bytes: result.blob.size,
          audio_kbps: state.encodingAttempts ? state.audioKbps : undefined,
        });
        return result;
      } catch (value) {
        // Optional diagnostics may not be loaded. Handle worker string/null
        // rejections here too, so retries and user-facing errors remain safe.
        const error = processingError(value);
        try {
          error.diagnosticFileIndex = state.index;
        } catch (_) {
          /* Optional context. */
        }
        if (isCancelled() || error.name === "AbortError") throw error;
        console.error("Compression attempt failed", error, lastFfmpegMessage);
        batchMetrics.processing_failures = (batchMetrics.processing_failures || 0) + 1;
        const nonRetryable = new Set([
          "UNSUPPORTED_CODEC",
          "TARGET_NOT_FEASIBLE",
          "INVALID_SIGNATURE",
          "NO_VIDEO_STREAM",
          "DURATION_UNKNOWN",
          "INVALID_MEDIA",
        ]);
        if (
          attempt === 2 ||
          nonRetryable.has(diagnostic.code(error)) ||
          /too long|recognized video signature|Could not determine|No video stream/.test(
            error.message,
          )
        ) {
          events.emit("fileStatus", state, "Needs attention", 0);
          throw error;
        }
        events.emit("fileStatus", state, "Retrying encoder", 0);
        diagnostic.emit("retry", state.index, {
          ...diagnostic.failureContext(error),
          attempt: attempt + 1,
          failed_attempt: attempt,
          retry_reason: "same_settings",
          error_code: diagnostic.code(error),
          retryable: true,
        });
        events.emit(
          "phaseCopy",
          "The encoder stopped unexpectedly. Reloading it and retrying " +
            state.file.name +
            " once.",
        );
        await resetEncoder();
      }
    }
  }

  return {
    videoSignature,
    unchangedLimitBytes,
    compressWithRetry,
    get loaded() {
      return ffmpeg !== null;
    },
    get mode() {
      return encoderMode;
    },
    get currentFile() {
      return currentState;
    },
    clearCurrentFile() {
      currentState = null;
    },
    async start() {
      encoderMode = await loadEncoder();
    },
    terminate() {
      if (ffmpeg) ffmpeg.terminate();
      ffmpeg = null;
    },
  };
}
