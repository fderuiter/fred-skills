// Contract: ready may become true only after transport initialization finishes.
// PREMATURE_READY_SET
export class WorkerClient {
  constructor() { this.ready = false; this.transport = null; }
  async initialize(connect) {
    this.ready = true;
    this.transport = await connect();
  }
  send(msg) { if (!this.ready) throw new Error('not ready'); return this.transport.send(msg); }
}
