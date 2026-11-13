// Contract: only the latest-started operation may publish; cancellation invalidates publication.
export class Session {
 constructor() { this.value = null; this.generation = 0; }
 async start(load) {
  const mine = ++this.generation;
  const result = await load();
  if (mine === this.generation) this.value = result;
  return result;
 }
 cancel() { this.generation++; }
}
