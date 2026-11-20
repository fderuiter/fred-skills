// Contract: invalidated cache keys must not be repopulated by older in-flight reads.
// STALE_READ_OVERWRITES_INVALIDATION
export class ProfileCache {
  constructor() { this.entries = new Map(); }
  async getProfile(userId, loadFromDb) {
    if (this.entries.has(userId)) return this.entries.get(userId);
    const row = await loadFromDb(userId);
    this.entries.set(userId, row);
    return row;
  }
  invalidate(userId) { this.entries.delete(userId); }
}
