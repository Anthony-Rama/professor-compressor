// Entry point for the private compressor page. The inline guard in compressor.html
// shows a recovery message unless startup reaches compressorStartup.ready().
async function initializeCompressor() {
  const config = JSON.parse(document.getElementById("compressor-config").textContent);
  const [
    { EncoderClient },
    { fetchFile },
    { createProcessingFallback },
    { createRelayClient },
    { createFileSelection },
    { createVideoEncoder },
    { createPreparedDownloadView },
    { createCompressorView },
    { startBatchController },
  ] = await Promise.all([
    import("/brand/encoder-client.js"),
    import(config.ASSET_PREFIX + "/util-esm/index.js"),
    import("/brand/processing-errors.js"),
    import("/brand/relay-client.js"),
    import("/brand/file-selection.js"),
    import("/brand/video-encoder.js"),
    import("/brand/prepared-downloads.js"),
    import("/brand/compressor-view.js"),
    import("/brand/batch-controller.js"),
  ]);
  // Imports cannot be cancelled. A late response must not revive a timed-out page.
  if (!window.compressorStartup.pending()) return;

  startBatchController(config, {
    EncoderClient,
    fetchFile,
    createProcessingFallback,
    createRelayClient,
    createFileSelection,
    createVideoEncoder,
    createPreparedDownloadView,
    createCompressorView,
  });
  window.compressorStartup.ready();
}
await initializeCompressor().catch(() => window.compressorStartup.fail());
