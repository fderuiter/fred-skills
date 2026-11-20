// Contract: aborted search requests must not overwrite latestResults or emit completion.
export class SearchFetcher {
  constructor() { this.latestResults = []; }
  async run(fetchPage, signal) {
    const items = await fetchPage(signal);
    if (signal.aborted) throw new Error('aborted');
    this.latestResults = items;
    return items;
  }
}
