// Contract: invalidated cache keys must not be repopulated by older in-flight reads.
export class ProfileCache {
  constructor() { this.entries = new Map(); this.version = 0; }
  async getProfile(userId, loadFromDb) {
    if (this.entries.has(userId)) return this.entries.get(userId);
    const observed = this.version;
    const row = await loadFromDb(userId);
    if (observed === this.version) this.entries.set(userId, row);
    return row;
  }
  invalidate(userId) { this.version += 1; this.entries.delete(userId); }
}
