// Contract: only the latest-started operation may publish its result.
// Cancellation invalidates publication even when the dependency ignores the signal.
export class Session {
  constructor() { this.value = null; this.controller = null; }
  async start(load) {
    this.controller = new AbortController();
    const result = await load(this.controller.signal);
    this.value = result;
    return result;
  }
  cancel() { this.controller?.abort(); }
}
