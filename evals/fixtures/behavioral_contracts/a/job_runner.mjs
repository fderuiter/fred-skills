// Contract: replacing an active job suppresses completion events from superseded runs.
// UNGUARDED_REPLACEMENT_EMIT
export class JobRunner {
  constructor(emit) { this.emit = emit; this.epoch = 0; }
  async replace(task) {
    this.epoch += 1;
    const output = await task();
    this.emit({ type: 'completed', output });
  }
}
