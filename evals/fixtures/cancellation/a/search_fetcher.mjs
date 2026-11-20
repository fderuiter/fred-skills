// Contract: aborted search requests must not overwrite latestResults or emit completion.
// LOSING_BRANCH_MUTATES_STATE
export class SearchFetcher {
  constructor() { this.latestResults = []; }
  async run(fetchPage, signal) {
    const work = fetchPage().then((items) => { this.latestResults = items; return items; });
    const abortPromise = new Promise((_, reject) => signal.addEventListener('abort', () => reject(new Error('aborted'))));
    return Promise.race([work, abortPromise]);
  }
}
