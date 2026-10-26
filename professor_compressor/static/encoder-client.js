// Application-owned transport for the pinned @ffmpeg/ffmpeg 0.12.15 worker.
// Keep the engine/worker unmodified. Transfer buffers without an extra copy.
// Only the RPC methods used by this application are exposed.
export class EncoderClient {
  constructor(workerURL) {
    this.workerURL = workerURL;
    this.worker = null;
    this.pending = new Map();
    this.listeners = {log: [], progress: []};
    this.nextId = 0;
    this.loaded = false;
    this.failure = null;
  }
  on(event, callback) { this.listeners[event]?.push(callback); }
  stop(error) {
    const worker = this.worker;
    this.worker = null;
    this.loaded = false;
    this.failure = error;
    if (worker) {
      worker.onmessage = worker.onerror = worker.onmessageerror = null;
      worker.terminate();
    }
    for (const request of this.pending.values()) request.reject(error);
    this.pending.clear();
  }
  transportFailure(code) {
    const error = new Error('The video engine connection stopped unexpectedly.');
    error.diagnosticCode = code;
    this.stop(error);
  }
  load(config) {
    if (!this.worker) {
      this.failure = null;
      const worker = new Worker(this.workerURL, {type: 'module'});
      this.worker = worker;
      worker.onerror = event => {
        event.preventDefault();
        if (this.worker === worker) this.transportFailure('WORKER_CRASH');
      };
      worker.addEventListener('messageerror', () => {
        if (this.worker === worker) this.transportFailure('WORKER_MESSAGE_ERROR');
      });
      worker.onmessage = ({data: message}) => {
        if (this.worker !== worker) return;
        if (!message || typeof message !== 'object') {
          this.transportFailure('WORKER_MESSAGE_ERROR'); return;
        }
        const {id, type, data} = message;
        const callbacks = type === 'LOG' ? this.listeners.log : type === 'PROGRESS' ? this.listeners.progress : null;
        if (callbacks) {
          for (const callback of callbacks) {
            try { callback(data); } catch (_) { /* UI/diagnostics must not strand an RPC. */ }
          }
          return;
        }
        if (!Number.isSafeInteger(id) || id < 1 || id > this.nextId) {
          this.transportFailure('WORKER_MESSAGE_ERROR'); return;
        }
        const request = this.pending.get(id);
        if (!request) return; // Ignore late/duplicate replies.
        if (type !== request.type && type !== 'ERROR') {
          this.transportFailure('WORKER_MESSAGE_ERROR'); return;
        }
        this.pending.delete(id);
        if (type === 'ERROR') request.reject(data);
        else {
          if (type === 'LOAD') this.loaded = true;
          request.resolve(data);
        }
      };
    }
    return this.send('LOAD', config);
  }
  send(type, data, transfer = []) {
    if (!this.worker) return Promise.reject(this.failure || new Error('Video engine is not loaded.'));
    return new Promise((resolve, reject) => {
      const id = ++this.nextId;
      this.pending.set(id, {type, resolve, reject});
      try { this.worker.postMessage({id, type, data}, transfer); }
      catch (_) { this.transportFailure('WORKER_MESSAGE_ERROR'); }
    });
  }
  exec(args, timeout = -1) { return this.send('EXEC', {args, timeout}); }
  ffprobe(args, timeout = -1) { return this.send('FFPROBE', {args, timeout}); }
  writeFile(path, data) { return this.send('WRITE_FILE', {path, data}, data instanceof Uint8Array ? [data.buffer] : []); }
  readFile(path, encoding = 'binary') { return this.send('READ_FILE', {path, encoding}); }
  deleteFile(path) { return this.send('DELETE_FILE', {path}); }
  terminate() { this.stop(new DOMException('Video processing was stopped.', 'AbortError')); }
}
