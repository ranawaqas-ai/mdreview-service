// scripts/thread-fold-check.mjs - #387. Samples the RENDERED outcome of the comment rail's thread
// fold and per-entry clamp in a real browser, never the presence of a CSS declaration (the #265
// lesson). Fixture and case letters come from tests/thread_fold_selfcheck.sh.
//
// Zero-dep: Node's built-in WebSocket + fetch driving CDP, same shape as
// scripts/addressed-check.mjs. The Chrome profile lives under the project's .scratch/, per the
// repo's hard rule on temp files.
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const url = process.argv[2];
const ids = JSON.parse(process.argv[3] || '{}');   // {a,b,c,d}: comment_ids from the seeded fixture
const CHROME = process.env.CHROME || [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
].find(p => existsSync(p));
if (!CHROME) { console.error('no Chrome found; set CHROME='); process.exit(2); }
if (!url || !ids.a || !ids.b || !ids.c || !ids.d) {
  console.error('usage: node scripts/thread-fold-check.mjs <review-url> <json {a,b,c,d}>');
  process.exit(2);
}

let failed = 0;
const ok = (n, c, d) => { console.log((c ? 'ok   - ' : 'FAIL - ') + n + (c ? '' : `  (${d})`)); if (!c) failed++; };
const port = 9500 + (Date.now() % 400);
const scratch = join(dirname(fileURLToPath(import.meta.url)), '..', '.scratch');
mkdirSync(scratch, { recursive: true });
const profile = mkdtempSync(join(scratch, 'thread-fold-check-'));
const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
let ws;
try {
  let tabs, target;
  for (let i = 0; i < 60; i++) {
    try {
      tabs = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      target = (tabs || []).find(t => t.type === 'page' && !String(t.url).startsWith('chrome-extension://'));
      if (target) break;
    } catch {}
    await sleep(250);
  }
  if (!target) { console.error('no page target found'); process.exit(2); }
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r, { once: true }));
  let id = 0; const pending = new Map();
  ws.addEventListener('message', e => { const m = JSON.parse(e.data); if (pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
  const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const evalJs = async expr => (await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }))?.result?.result?.value;
  // Every probe returns a JSON string built inside the page; J() parses it (or surfaces a page
  // exception as a failed, non-vacuous assertion instead of a crash here).
  const J = async expr => { const v = await evalJs(expr); try { return JSON.parse(v); } catch { return { probeError: String(v) }; } };

  await send('Page.enable'); await send('Runtime.enable');
  await send('Page.navigate', { url });
  let ready = false;
  for (let i = 0; i < 80; i++) {
    const st = await evalJs(`document.readyState + '|' + !!document.querySelector('#gutter')`);
    if (typeof st === 'string' && st.startsWith('complete') && st.endsWith('true')) { ready = true; break; }
    await sleep(250);
  }
  ok('the viewer actually loaded (probe is not vacuous)', ready, await evalJs(`document.readyState + ' ' + location.href`));
  if (!ready) { console.log('\naborting: nothing to sample'); ws.close(); chrome.kill(); process.exit(1); }

  // Wide viewport -> the rail runs "gutter-on", so .gcard renders in place (layoutComments' railFits).
  await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await sleep(400);

  const cardProbe = (sel) => `(()=>{
    const card=document.querySelector(${JSON.stringify(sel)});
    if(!card) return JSON.stringify({present:false});
    const entries=[...card.querySelectorAll('.gentry')];
    const fold=card.querySelector('.gfold');
    const last=entries[entries.length-1];
    return JSON.stringify({present:true,
      n:entries.length, idx:entries.map(e=>+e.dataset.idx),
      fold:fold?{text:fold.textContent, expanded:fold.getAttribute('aria-expanded'), type:fold.getAttribute('type')}:null,
      badgeOnLast: !!(last&&last.querySelector('.gaddr')),
      badgeCount: card.querySelectorAll('.gaddr').length});
  })()`;
  const A = `.gcard[data-id=${JSON.stringify(ids.a)}]`, B = `.gcard[data-id=${JSON.stringify(ids.b)}]`;
  const C = `.gcard[data-id=${JSON.stringify(ids.c)}]`, D = `.rcard[data-id=${JSON.stringify(ids.d)}]`;

  // Positive control: three open cards exist, so an absent fold below cannot pass by absence.
  const cards = await evalJs(`document.querySelectorAll('.gcard').length`);
  ok('fixture produced the three open .gcard elements', cards === 3, `cardCount=${cards}`);

  // Case A: 5 entries -> root + "2 earlier replies" + the last two; badge still on the newest.
  let a = await J(cardProbe(A));
  ok('case A (5 entries): renders root + last two (3 visible entries)', a.present && a.n === 3, JSON.stringify(a));
  ok('case A: visible entries are thread indices 0, 3, 4 (data-idx is the original index)', a.present && a.idx.join() === '0,3,4', JSON.stringify(a));
  ok('case A: one fold control reading "2 earlier replies", a real button, aria-expanded=false',
     a.present && a.fold && /^2 earlier replies$/.test(a.fold.text.trim()) && a.fold.expanded === 'false' && a.fold.type === 'button', JSON.stringify(a));
  ok('case A: Addressed badge sits on the visible newest entry while folded', a.present && a.badgeOnLast && a.badgeCount === 1, JSON.stringify(a));

  // Clamp, while folded: idx3 (long, not newest) is clamped to CLAMP_LINES with a real button;
  // idx4 (long, newest) is never clamped. Sampled as rendered heights, not class names alone.
  const clampProbe = (sel) => `(()=>{
    const card=document.querySelector(${JSON.stringify(sel)});
    if(!card) return JSON.stringify({present:false});
    const r={present:true,e:{}};
    card.querySelectorAll('.gentry').forEach(en=>{
      const t=en.querySelector('.gtext'),m=en.querySelector('.gmore');
      r.e[en.dataset.idx]={clamped:en.classList.contains('clamped'),undecided:en.classList.contains('clampable'),
        more:m?{text:m.textContent.trim(),expanded:m.getAttribute('aria-expanded'),type:m.getAttribute('type')}:null,
        client:t.clientHeight,scroll:t.scrollHeight,lh:parseFloat(getComputedStyle(t).lineHeight)};
    });
    return JSON.stringify(r);
  })()`;
  let k = await J(clampProbe(A));
  const e3 = k.e && k.e[3], e4 = k.e && k.e[4];
  ok('clamp A: long non-newest entry (idx3) is clamped, with a Show more button, aria-expanded=false',
     e3 && e3.clamped && e3.more && e3.more.text === 'Show more' && e3.more.expanded === 'false' && e3.more.type === 'button', JSON.stringify(e3));
  ok('clamp A: idx3 rendered height is the CLAMP_LINES cap and its content overflows it',
     e3 && Math.abs(e3.client - 8 * e3.lh) < 1 && e3.scroll > e3.client, JSON.stringify(e3));
  ok('clamp A: the newest entry (idx4) is long but never clamped, no button, full height',
     e4 && !e4.clamped && !e4.undecided && e4.more === null && e4.client === e4.scroll, JSON.stringify(e4));
  ok('clamp A: every visible entry has been decided (no .clampable left in a laid-out card)',
     k.present && Object.values(k.e).every(x => !x.undecided), JSON.stringify(k.e));

  // Hysteresis is a pure predicate (a browser fixture exactly one line over the cap is flaky):
  // one extra line over the cap shows in full, two-plus clamps.
  const hy = await J(`JSON.stringify({one:needsClamp(9*19.5,19.5), two:needsClamp(10*19.5,19.5), twoPlus:needsClamp(10*19.5+1,19.5), cap:needsClamp(8*19.5,19.5)})`);
  ok('clamp predicate: at the cap or one line over -> shown in full', hy.cap === false && hy.one === false, JSON.stringify(hy));
  ok('clamp predicate: exactly two lines over is still full; beyond two lines clamps', hy.two === false && hy.twoPlus === true, JSON.stringify(hy));

  // Show more: opens in place, and stays open across the live-reload re-render.
  await evalJs(`document.querySelector(${JSON.stringify(A)} + ' .gentry[data-idx="3"] .gmore').click(); true`);
  await sleep(150);
  k = await J(clampProbe(A));
  ok('clamp A: Show more removes the clamp and the button; entry at full height',
     k.e[3] && !k.e[3].clamped && k.e[3].more === null && k.e[3].client === k.e[3].scroll, JSON.stringify(k.e[3]));
  await evalJs(`renderAll(); true`);
  await sleep(150);
  k = await J(clampProbe(A));
  ok('clamp A: opened entry survives a full renderAll() (live-reload re-render)',
     k.e[3] && !k.e[3].clamped && k.e[3].more === null, JSON.stringify(k.e[3]));

  // Unfold: one click reveals every entry, no pagination, no re-fold control.
  await evalJs(`document.querySelector(${JSON.stringify(A)} + ' .gfold').focus(); document.activeElement.click(); true`);
  await sleep(150);
  const f0 = await J(`JSON.stringify({idx:document.activeElement.closest('.gentry')?.dataset.idx, inA:!!document.activeElement.closest(${JSON.stringify(A)})})`);
  ok('focus: after unfolding, focus is on the first revealed entry (idx1)', f0.inA && f0.idx === '1', JSON.stringify(f0));
  a = await J(cardProbe(A));
  ok('case A: one click on the fold reveals all 5 entries', a.present && a.n === 5 && a.idx.join() === '0,1,2,3,4', JSON.stringify(a));
  ok('case A: no fold control remains after expanding (no re-fold)', a.present && a.fold === null, JSON.stringify(a));

  // Live reload: renderAll() is exactly what the 2s comments_updated poll calls. The expanded
  // state must survive it, keyed by comment id.
  await evalJs(`renderAll(); true`);
  await sleep(150);
  a = await J(cardProbe(A));
  ok('case A: expanded state survives a full renderAll() (live-reload re-render)', a.present && a.n === 5 && a.fold === null, JSON.stringify(a));
  // The entry the fold had hidden (idx1, long) gets its clamp decided when it first lays out.
  k = await J(clampProbe(A));
  ok('clamp A: the long entry revealed by unfolding (idx1) is clamped on first layout', k.e[1] && k.e[1].clamped && k.e[1].more !== null, JSON.stringify(k.e[1]));
  ok('clamp A: the entry opened earlier (idx3) is still open after unfold + re-render', k.e[3] && !k.e[3].clamped, JSON.stringify(k.e[3]));

  // Case B: 4 entries. Root + last two would hide exactly ONE entry; never fold one.
  const b = await J(cardProbe(B));
  ok('case B (4 entries): renders in full, no fold (never hide a single entry)', b.present && b.n === 4 && b.fold === null, JSON.stringify(b));
  // Case C: 3 entries, nothing to fold.
  const c = await J(cardProbe(C));
  ok('case C (3 entries): renders in full, no fold', c.present && c.n === 3 && c.fold === null, JSON.stringify(c));

  // Case D: the same fold inside the Resolved panel's .rcard (same threadHtml()).
  await evalJs(`document.querySelector('#resbtn').click(); true`);
  await sleep(200);
  let d = await J(cardProbe(D));
  ok('case D (resolved, 5 entries): .rcard folds to root + last two', d.present && d.n === 3 && d.fold && /^2 earlier replies$/.test(d.fold.text.trim()), JSON.stringify(d));
  await evalJs(`document.querySelector(${JSON.stringify(D)} + ' .gfold').click(); true`);
  await sleep(150);
  d = await J(cardProbe(D));
  ok('case D: fold opens inside the Resolved panel too', d.present && d.n === 5 && d.fold === null, JSON.stringify(d));
  // The panel is OPEN now. A live re-render (renderAll) rebuilds its cards; the long non-newest
  // entry (idx3) must come back clamped without toggling the panel, and the fold must stay open.
  await evalJs(`renderAll(); true`);
  await sleep(150);
  d = await J(cardProbe(D));
  const dk = await J(clampProbe(D));
  ok('case D: fold stays open across renderAll() while the panel is open', d.present && d.n === 5 && d.fold === null, JSON.stringify(d));
  ok('case D: long entry in the already-open Resolved panel is clamped after a live re-render', dk.e && dk.e[3] && dk.e[3].clamped && dk.e[3].more !== null && !dk.e[3].undecided, JSON.stringify(dk.e && dk.e[3]));
  ok('case D: no entry in the open panel is left undecided', dk.e && Object.values(dk.e).every(x => !x.undecided), JSON.stringify(dk.e));

  // Focus after activation: both controls remove themselves, so focus must land on what they
  // revealed, not drop to body. Show more on D idx3 -> its .gtext; the fold on a fresh card -> the
  // first revealed entry.
  await evalJs(`document.querySelector(${JSON.stringify(D)} + ' .gentry[data-idx="3"] .gmore').focus(); document.activeElement.click(); true`);
  await sleep(100);
  const f1 = await J(`JSON.stringify({tag:document.activeElement.tagName, cls:document.activeElement.className, idx:document.activeElement.closest('.gentry')?.dataset.idx, inD:!!document.activeElement.closest(${JSON.stringify(D)})})`);
  ok('focus: after Show more, focus is on the revealed text of that entry', f1.inD && f1.idx === '3' && /gtext/.test(f1.cls), JSON.stringify(f1));

} finally {
  try { ws?.close(); } catch {}
  try { chrome.kill(); } catch {}
  try { rmSync(profile, { recursive: true, force: true }); } catch {}
}
console.log(failed ? `\n${failed} case(s) failed` : '\nall thread-fold cases pass');
process.exit(failed ? 1 : 0);
