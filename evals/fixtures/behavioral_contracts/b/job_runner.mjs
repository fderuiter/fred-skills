// Contract: replacing an active job suppresses completion events from superseded runs.
export class JobRunner {
  constructor(emit) { this.emit = emit; this.epoch = 0; }
  async replace(task) {
    const runEpoch = ++this.epoch;
    const output = await task();
    if (runEpoch === this.epoch) this.emit({ type: 'completed', output });
  }
}
