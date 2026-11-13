// Same publication contract; reserve ownership before synchronous abort callbacks.
export class Session {
  constructor() { this.value = null; this.generation = 0; this.controller = null; }
  async start(load) {
    const previous = this.controller;
    const mine = ++this.generation;
    const controller = new AbortController();
    this.controller = controller;
    previous?.abort();
    const result = await load(controller.signal);
    if (mine === this.generation && !controller.signal.aborted) this.value = result;
    return result;
  }
  cancel() { this.generation++; this.controller?.abort(); }
}
