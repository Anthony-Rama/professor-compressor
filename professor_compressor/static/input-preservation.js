// Original bytes stay on this device. No filenames or media are sent to a server.
export const INPUT_FILE_LIMIT = 1024 ** 3;
export const INPUT_BATCH_LIMIT = 4 * 1024 ** 3;
const CHUNK_BYTES = 1024 ** 2;
const ROOT_NAME = "professor-compressor-inputs-v1";
const SESSION_NAME = /^session-[a-f0-9-]{36}$/;

function failure(message, code) {
  const error = new Error(message);
  error.diagnosticCode = code;
  return error;
}

function storageFailure(error) {
  if (error?.name === "QuotaExceededError") return failure(
    "Not enough browser storage to prepare this clip. Remove some clips or free disk space, then reselect it.",
    "INPUT_STORAGE_FULL");
  return failure(
    "This browser could not save a temporary local copy. Try an updated Chrome or Edge window with browser storage enabled, then reselect the clip.",
    "INPUT_STORAGE_UNAVAILABLE");
}

function checkCancelled(entry) {
  if (entry.cancelled) throw new DOMException("Local preparation cancelled", "AbortError");
}

function within(promise, milliseconds = 30000, onLate = () => {}) {
  return new Promise((resolve, reject) => {
    let expired = false;
    const timer = setTimeout(() => { expired = true; reject(failure(
      "Preparing this clip took too long. Reselect it from a saved folder and try again.",
      "INPUT_PREPARATION_TIMEOUT")); }, milliseconds);
    Promise.resolve(promise).then(value => {
      if (expired) { void Promise.resolve().then(() => onLate(value)).catch(() => {}); return; }
      clearTimeout(timer); resolve(value);
    }, error => { clearTimeout(timer); reject(error); });
  });
}

export class InputPreserver {
  constructor() {
    this.entries = new Map();
    this.reservedBytes = 0;
    this.closed = false;
    this.sessionName = "session-" + crypto.randomUUID();
    this.ready = within(this.open()).catch(error => {
      this.initializationError = error;
      this.closed = true;
      this.unlock?.();
      throw error;
    });
    // Storage availability is reported against the selected file, not as an
    // unhandled startup rejection. Do not fall back to unbounded RAM copies.
    void this.ready.catch(() => {});
  }

  async open() {
    const checkOpen = () => {
      if (this.closed) throw failure("Local storage initialization stopped. Reload this page and reselect your clips.", "INPUT_STORAGE_UNAVAILABLE");
    };
    if (!navigator.storage?.getDirectory || !navigator.locks?.request) {
      throw storageFailure(null);
    }
    try {
      this.root = await (await navigator.storage.getDirectory())
        .getDirectoryHandle(ROOT_NAME, {create: true});
      checkOpen();
      // Only remove our anonymous directories when no live tab owns their lock.
      // A crashed/closed tab releases its browser lock automatically.
      let checked = 0;
      for await (const [name, handle] of this.root.entries()) {
        checkOpen();
        if (++checked > 100) break;
        if (handle.kind !== "directory" || !SESSION_NAME.test(name)) continue;
        await navigator.locks.request(ROOT_NAME + "/" + name, {ifAvailable: true}, async lock => {
          if (lock) {
            try { await this.root.removeEntry(name, {recursive: true}); }
            catch (error) { if (error?.name !== "NotFoundError") throw error; }
          }
        });
      }
      checkOpen();
      const released = new Promise(resolve => { this.unlock = resolve; });
      await new Promise((resolve, reject) => {
        this.lock = navigator.locks.request(ROOT_NAME + "/" + this.sessionName, async () => {
          resolve();
          await released;
        });
        void this.lock.catch(reject);
      });
      checkOpen();
      this.directory = await this.root.getDirectoryHandle(this.sessionName, {create: true});
      checkOpen();
      return this.directory;
    } catch (error) {
      if (this.directory) void this.root.removeEntry(this.sessionName, {recursive: true}).catch(() => {});
      this.unlock?.();
      throw error?.diagnosticCode ? error : storageFailure(error);
    }
  }

  create(file, onProgress = () => {}) {
    if (this.closed) throw this.initializationError || failure("Local storage is no longer available for this session. Reload this page and reselect your clips.", "INPUT_STORAGE_UNAVAILABLE");
    if (file.size > INPUT_FILE_LIMIT) throw failure(
      "Local preparation supports clips up to 1 GiB each. Choose a smaller exported clip.", "INPUT_TOO_LARGE");
    if (this.entries.size >= 10 || this.reservedBytes + file.size > INPUT_BATCH_LIMIT) throw failure(
      "Local preparation supports up to 10 clips and 4 GiB total. Remove some clips before adding more.", "INPUT_TOO_LARGE");
    const entry = {id: crypto.randomUUID(), size: file.size, cancelled: false};
    this.entries.set(entry.id, entry);
    this.reservedBytes += entry.size;
    entry.promise = this.copy(entry, file, onProgress).catch(error => {
      entry.cancelled = true;
      // A stalled storage API must not prevent the UI reporting the failure.
      // Keep space reserved until its asynchronous cleanup actually succeeds.
      void this.cleanup(entry).catch(() => {});
      throw error;
    });
    return {
      promise: entry.promise,
      dispose: async () => {
        entry.cancelled = true;
        await entry.promise.catch(() => {});
        await this.cleanup(entry);
      }
    };
  }

  async checkAvailable(additionalBytes) {
    if (typeof globalThis.FileSystemFileHandle?.prototype?.createWritable !== "function") {
      throw failure(
        "This browser cannot make the temporary copy needed to prepare your videos. Nothing was uploaded. Update your browser or use a current desktop browser, then run /compress again in Discord for a new link.",
        "INPUT_WRITE_UNSUPPORTED");
    }
    // Estimates are advisory, not a disk-space guarantee. Missing, invalid or
    // stalled estimates fall back to the hard caps and actual write handling.
    if (!navigator.storage?.estimate) return;
    // Completed files are already represented by usage. Only reserve pending
    // copies, sampled before the estimate so completion cannot undercount them.
    const pendingBytes = Array.from(this.entries.values()).reduce(
      (sum, entry) => sum + (entry.committed ? 0 : entry.size), 0);
    let estimate;
    try { estimate = await within(navigator.storage.estimate(), 3000); }
    catch (_) { return; }
    const {quota, usage} = estimate || {};
    if (!Number.isFinite(quota) || !Number.isFinite(usage) || quota <= 0 || usage < 0) return;
    const remaining = Math.max(0, quota - usage);
    const headroom = Math.max(256 * 1024 ** 2, remaining * 0.1);
    if (additionalBytes + pendingBytes > Math.max(0, remaining - headroom)) {
      throw failure(
        "These videos may not fit in available browser storage. Existing clips were kept. Add fewer videos or free disk space and try again.",
        "INPUT_STORAGE_FULL");
    }
  }

  async copy(entry, source, onProgress) {
    let writer;
    try {
      const directory = await within(this.ready);
      checkCancelled(entry);
      const handle = await within(directory.getFileHandle(entry.id, {create: true}), 30000,
        () => this.cleanup(entry));
      checkCancelled(entry);
      writer = await within(handle.createWritable(), 30000, async lateWriter => {
        try { await within(lateWriter.abort(), 5000); } finally { await this.cleanup(entry); }
      });
      checkCancelled(entry);
      for (let offset = 0; offset < source.size; offset += CHUNK_BYTES) {
        checkCancelled(entry);
        let bytes;
        const end = Math.min(source.size, offset + CHUNK_BYTES);
        try {
          bytes = await within(source.slice(offset, end).arrayBuffer());
          if (bytes.byteLength !== end - offset) throw new Error("Short source read");
        } catch (error) {
          if (error?.diagnosticCode === "INPUT_PREPARATION_TIMEOUT") throw error;
          throw failure(
            "This clip could not be read. It may have changed since export. Finish exporting, then reselect it from a saved folder.",
            "FILE_READ_FAILED");
        }
        checkCancelled(entry);
        await within(writer.write(bytes));
        onProgress(end / Math.max(1, source.size));
      }
      checkCancelled(entry);
      await within(writer.close(), 30000, () => this.cleanup(entry));
      writer = null;
      entry.committed = true;
      checkCancelled(entry);
      const saved = await within(handle.getFile());
      if (saved.size !== source.size) throw storageFailure(null);
      return new File([saved], source.name, {type: source.type, lastModified: source.lastModified});
    } catch (error) {
      if (writer) { try { await within(writer.abort(), 5000, () => this.cleanup(entry)); } catch (_) { /* Best-effort abort. */ } }
      if (error?.name === "AbortError" || error?.diagnosticCode) throw error;
      throw storageFailure(error);
    }
  }

  async cleanup(entry) {
    if (entry.cleaning) return entry.cleaning;
    const operation = (async () => {
      const directory = await this.ready.catch(() => null);
      if (directory) {
        try { await directory.removeEntry(entry.id); }
        catch (error) {
          // Retain the reservation if deletion failed; do not silently grow disk use.
          if (error?.name !== "NotFoundError") return;
        }
      }
      if (this.entries.delete(entry.id)) this.reservedBytes -= entry.size;
    })();
    entry.cleaning = operation.finally(() => { entry.cleaning = null; });
    return entry.cleaning;
  }

  async close() {
    if (this.closing) return this.closing;
    this.closed = true;
    this.closing = (async () => {
      const entries = Array.from(this.entries.values());
      for (const entry of entries) entry.cancelled = true;
      await Promise.allSettled(entries.map(entry => entry.promise));
      try {
        const directory = await this.ready.catch(() => null);
        if (directory) await this.root.removeEntry(this.sessionName, {recursive: true});
      } finally {
        this.unlock?.();
      }
    })();
    return this.closing;
  }
}
