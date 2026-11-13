import assert from 'node:assert/strict';
import { Session as Before } from './source/session.mjs';
import { Session as After } from './source/corrected.mjs';
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise,resolve}; }
async function overlap(Type) {
  const s = new Type(), a = deferred(), b = deferred();
  const pa=s.start(()=>a.promise), pb=s.start(()=>b.promise);
  b.resolve('B'); await pb; a.resolve('A'); await pa; return s.value;
}
assert.equal(await overlap(Before),'A'); // Reproduces the known original violation.
assert.equal(await overlap(After),'B');
for(const order of ['AB','BA']) {
  const s=new After(),a=deferred(),b=deferred();
  const pa=s.start(()=>a.promise),pb=s.start(()=>b.promise);
  if(order==='AB') { a.resolve('A'); await pa; assert.equal(s.value,null); b.resolve('B'); await pb; }
  else { b.resolve('B'); await pb; a.resolve('A'); await pa; }
  assert.equal(s.value,'B');
}
const c = new After(), pending=deferred(), pc=c.start(()=>pending.promise);
c.cancel(); pending.resolve('C'); await pc; assert.equal(c.value,null);
const s=new After(),old=deferred(),a=deferred(),b=deferred();let pb;
const po=s.start(signal=>{signal.addEventListener('abort',()=>{pb=s.start(()=>b.promise);},{once:true});return old.promise;});
const pa=s.start(()=>a.promise);b.resolve('B');await pb;a.resolve('A');await pa;old.resolve('old');await po;
assert.equal(s.value,'B');
console.log(JSON.stringify({original_reproduced:true,regression_passed:true,safe_controls_passed:true,
  result:'Exact synthetic before/after sources checked; overlap, both completion orders, cancellation, and synchronous reentrancy assertions passed.'}));
