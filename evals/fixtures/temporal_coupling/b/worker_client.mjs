// Contract: ready may become true only after transport initialization finishes.
export class WorkerClient {
  constructor() { this.ready = false; this.transport = null; }
  async initialize(connect) {
    this.transport = await connect();
    this.ready = true;
  }
  send(msg) { if (!this.ready) throw new Error('not ready'); return this.transport.send(msg); }
}
