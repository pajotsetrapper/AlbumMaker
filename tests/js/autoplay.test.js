// Slideshow autoplay.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { fixture, sleep, load, press, thumbs, isOpen } = require('./lib');

const DAY = '/day/2026-05-02?preview=1';      // b1, b2 and (when ffmpeg is available) a video
const byId = (w, id) => w.document.getElementById(id);

// Every slide shown during the next `ms`, sampled often: with a short playMs a single
// look at the end could land on the starting slide again after a full loop.
async function slidesSeen(w, ms) {
  const seen = new Set([w.eval('currentSlide')]);
  for (let t = 0; t < ms; t += 25) { await sleep(25); seen.add(w.eval('currentSlide')); }
  return seen;
}
const stays = async (w, ms) => assert.equal((await slidesSeen(w, ms)).size, 1, 'it should not have moved on');
const movesOn = async (w, ms) => assert.ok((await slidesSeen(w, ms)).size > 1, 'it should have moved on');

async function playing(playMs = 150, path = DAY) {
  const page = await load(fixture.server, path);
  page.w.eval(`playMs = ${playMs}`);
  return page;
}

test('Play advances by itself, shows a progress bar and a speed button', async () => {
  const { w } = await playing();
  thumbs(w)[0].click();
  assert.match(byId(w, 'slideshow-play').textContent, /Play/);
  assert.equal(byId(w, 'slideshow-speed').hidden, true);
  byId(w, 'slideshow-play').click();
  assert.equal(w.eval('playing'), true);
  assert.equal(byId(w, 'slideshow-play').getAttribute('aria-pressed'), 'true');
  assert.equal(byId(w, 'slideshow-speed').hidden, false);
  // on a photo the bar fills over exactly the chosen time
  assert.equal(w.eval("SLIDES[currentSlide].type"), 'image');
  assert.match(byId(w, 'slideshow-progress-bar').style.transition, /width 150ms linear/);
  const seen = new Set([w.eval('currentSlide')]);
  for (let i = 0; i < 14; i++) { await sleep(60); seen.add(w.eval('currentSlide')); }
  assert.ok(seen.size >= 2, 'moved on: ' + [...seen]);
});

test('the progress bar is idle on a video, which moves on when it ends', async (t) => {
  const { w } = await playing();
  const vi = w.eval('SLIDES.map(s => s.type)').indexOf('video');
  if (vi < 0) return t.skip('no video in this fixture (ffmpeg missing)');
  thumbs(w)[vi].click();
  byId(w, 'slideshow-play').click();
  assert.equal(byId(w, 'slideshow-progress-bar').style.transition, 'none');
});

test('it loops back to the first photo after the last', async () => {
  const { w } = await playing(80);
  const n = w.eval('SLIDES.length');
  thumbs(w)[n - 1].click();
  byId(w, 'slideshow-play').click();
  await sleep(400);
  const visited = w.eval('currentSlide');
  assert.ok(visited >= 0 && visited < n);
  assert.equal(w.eval('playing'), true);
});

test('Space pauses and resumes', async () => {
  const { w } = await playing();
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  press(w, ' ');
  assert.equal(w.eval('playing'), false);
  await stays(w, 500);
  press(w, ' ');
  assert.equal(w.eval('playing'), true);
  await movesOn(w, 500);
});

test('using the arrows keeps it playing', async () => {
  const { w } = await playing();
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  press(w, 'ArrowRight');
  assert.equal(w.eval('playing'), true);
});

test('it waits while the Details panel is open and carries on afterwards', async () => {
  const { w } = await playing();
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  press(w, 'i');
  await stays(w, 600);
  press(w, 'i');
  await movesOn(w, 500);
});

test('it waits while zoomed in', async () => {
  const { w } = await playing();
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  w.eval('ZOOM.scale = 2');
  await stays(w, 600);
  w.eval('resetZoom()');
  await movesOn(w, 1000);           // the blocked check looks again every 500ms
});

test('it holds while a Live Photo clip is playing', async () => {
  const { w } = await playing();
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  w.eval('liveActive = true; scheduleAdvance()');
  await stays(w, 600);
});

test('closing the viewer stops it, and reopening starts paused', async () => {
  const { w } = await playing();
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  byId(w, 'slideshow-close').click();
  assert.equal(w.eval('playing'), false);
  thumbs(w)[0].click();
  assert.match(byId(w, 'slideshow-play').textContent, /Play/);
  assert.equal(byId(w, 'slideshow-speed').hidden, true);
  await stays(w, 500);
});

test('Space on a focused thumbnail opens the viewer without starting playback', async () => {
  const { w } = await playing();
  const t = thumbs(w)[0];
  t.focus();
  t.dispatchEvent(new w.KeyboardEvent('keydown', { key: ' ', bubbles: true }));
  assert.ok(isOpen(w));
  assert.equal(w.eval('playing'), false);
});

test('Space with the clicked thumbnail still focused behind the viewer plays, and does not jump back', async () => {
  const { w } = await playing(5000);
  const t = thumbs(w)[0];
  t.focus();                                                // a click focuses it in a real browser
  t.click();
  const entries = w.history.length;
  press(w, 'ArrowRight');
  t.dispatchEvent(new w.KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true }));
  assert.equal(w.eval('playing'), true);
  assert.equal(w.eval('currentSlide'), 1);
  assert.equal(w.history.length, entries, 'no second history entry');
});

test('the speed button cycles and the choice is remembered', async () => {
  const { w } = await playing(5000);
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  const speeds = new Set();
  for (let i = 0; i < 5; i++) { byId(w, 'slideshow-speed').click(); speeds.add(w.eval('playMs')); }
  assert.deepEqual([...speeds].sort((a, b) => a - b), [3000, 5000, 8000, 12000]);
  assert.equal(w.localStorage.getItem('tripSlideMs'), String(w.eval('playMs')));
});

test('a video holds the slide until it ends', async (t) => {
  const { w } = await playing();
  const types = w.eval('SLIDES.map(s => s.type)');
  const vi = types.indexOf('video');
  if (vi < 0) return t.skip('no video in this fixture (ffmpeg missing)');
  thumbs(w)[vi].click();
  byId(w, 'slideshow-play').click();
  await sleep(600);
  assert.equal(w.eval('currentSlide'), vi, 'stays on the video while it plays');
  w.document.querySelector('#slideshow-media video').dispatchEvent(new w.Event('ended'));
  await sleep(100);
  assert.notEqual(w.eval('currentSlide'), vi);
});

test('a video the browser will not start does not stop the slideshow', async () => {
  // works without ffmpeg: the first photo is presented as a video; jsdom never plays it,
  // like a browser that blocks autoplay
  const { w } = await playing();
  w.eval("SLIDES[0].type = 'video'; VIDEO_START_GRACE_MS = 400");
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  await stays(w, 300);
  await movesOn(w, 1000);
});

test('a video that ends while the Details panel is open waits for it, then moves on', async () => {
  // works without ffmpeg: the first photo is presented as a video, and its end is announced by hand
  const { w } = await playing();
  w.eval("SLIDES[0].type = 'video'");
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  press(w, 'i');
  const v = w.document.querySelector('#slideshow-media video');
  Object.defineProperty(v, 'ended', { value: true });       // as in a browser once it has finished
  v.dispatchEvent(new w.Event('ended'));
  await stays(w, 700);
  press(w, 'i');                                             // closing the panel must not lose the pending move
  await movesOn(w, 1000);
});

test('Play on a video that has already ended (or failed) moves on after the usual time', async () => {
  const { w } = await playing();
  w.eval("SLIDES[0].type = 'video'");
  thumbs(w)[0].click();
  const v = w.document.querySelector('#slideshow-media video');
  Object.defineProperty(v, 'ended', { value: true });       // it played to the end before Play was pressed
  v.dispatchEvent(new w.Event('ended'));
  byId(w, 'slideshow-play').click();
  assert.match(byId(w, 'slideshow-progress-bar').style.transition, /width 150ms linear/);
  await movesOn(w, 600);
});

test('a video that ended while paused and is replayed still moves on when it ends again', async () => {
  const { w } = await playing();
  w.eval("SLIDES[0].type = 'video'");
  thumbs(w)[0].click();
  const v = w.document.querySelector('#slideshow-media video');
  v.dispatchEvent(new w.Event('ended'));                     // finished while paused
  byId(w, 'slideshow-play').click();                         // ... replayed with its controls, then Play
  await stays(w, 400);                                       // still playing: it holds the slide
  v.dispatchEvent(new w.Event('ended'));
  await movesOn(w, 300);
});

test('pausing cancels the wait after a video failed, so Play starts a full new wait', async () => {
  const { w } = await playing(400);
  w.eval("SLIDES[0].type = 'video'");
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  const v = w.document.querySelector('#slideshow-media video');
  Object.defineProperty(v, 'error', { value: { code: 4 } });   // as a browser reports it (e.g. HEVC in Chrome)
  v.dispatchEvent(new w.Event('error'));
  await sleep(150);
  press(w, ' ');                                             // pause ...
  await sleep(100);
  press(w, ' ');                                             // ... and play again: 400ms from here
  await stays(w, 300);                                       // the first wait (due at 400ms) must not fire
  await movesOn(w, 400);
});

test('typing a space in the caption box does not start playback (editor view)', async () => {
  const { w } = await load(fixture.server, '/day/2026-05-02');
  thumbs(w)[0].click();
  const cap = byId(w, 'edit-caption');
  cap.focus();
  press(w, ' ', cap);
  assert.equal(w.eval('playing'), false);
});

// ---------- Ken Burns: a slow pan and zoom while playing, the visitor's choice ----------

// jsdom never loads images, so a photo would never count as "there": say it is
async function kenBurnsPage(opts) {
  const page = await load(fixture.server, DAY, opts);
  Object.defineProperty(page.w.HTMLImageElement.prototype, 'complete', { configurable: true, get() { return true; } });
  page.w.eval('playMs = 4000');
  return page;
}
const kbImg = (w) => w.document.querySelector('#slideshow-media img');

test('Ken Burns is off until the visitor switches it on, and the choice is remembered', async () => {
  const { w } = await kenBurnsPage();
  const btn = byId(w, 'slideshow-kb');
  assert.ok(btn, 'there is a button');
  assert.equal(btn.hidden, false);
  assert.equal(btn.getAttribute('aria-pressed'), 'false');
  btn.click();
  assert.equal(btn.getAttribute('aria-pressed'), 'true');
  assert.ok(btn.classList.contains('on'));
  assert.equal(w.localStorage.getItem('tripKenBurns'), '1');
  btn.click();
  assert.equal(btn.getAttribute('aria-pressed'), 'false');
  assert.equal(w.localStorage.getItem('tripKenBurns'), '0');
  // the next visit starts as it was left
  const again = await load(fixture.server, DAY, { storage: { tripKenBurns: '1' } });
  assert.equal(byId(again.w, 'slideshow-kb').getAttribute('aria-pressed'), 'true');
});

test('the K key switches it, only with the viewer open', async () => {
  const { w } = await kenBurnsPage();
  press(w, 'k');
  assert.equal(w.eval('kenBurns'), false, 'nothing happens with the viewer closed');
  thumbs(w)[0].click();
  press(w, 'k');
  assert.equal(w.eval('kenBurns'), true);
  press(w, 'K');
  assert.equal(w.eval('kenBurns'), false);
});

test('while playing with it on, each photo gets a slow move that lasts about as long as the photo stays', async () => {
  const { w } = await kenBurnsPage({ storage: { tripKenBurns: '1' } });
  thumbs(w)[0].click();
  assert.ok(!kbImg(w).classList.contains('kb'), 'a still photo is not animated');
  byId(w, 'slideshow-play').click();
  const img = kbImg(w);
  assert.ok(img.classList.contains('kb'));
  assert.ok(byId(w, 'slideshow-media').classList.contains('kb-on'));
  assert.equal(img.style.getPropertyValue('--kb-ms'), '4800ms');
  const scales = ['--kb-s0', '--kb-s1'].map((p) => parseFloat(img.style.getPropertyValue(p)));
  assert.ok(scales.every((s) => s >= 1 && s <= 1.12), 'a gentle zoom: ' + scales);
  assert.notEqual(scales[0], scales[1]);
  // another photo, another move
  const first = ['--kb-x1', '--kb-y1', '--kb-s1'].map((p) => img.style.getPropertyValue(p)).join();
  w.eval('stepSlide(1)');
  const next = kbImg(w);
  assert.ok(next.classList.contains('kb'));
  assert.notEqual(['--kb-x1', '--kb-y1', '--kb-s1'].map((p) => next.style.getPropertyValue(p)).join(), first);
});

test('with it off, playing shows photos as before', async () => {
  const { w } = await kenBurnsPage();
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  assert.ok(!kbImg(w).classList.contains('kb'));
  assert.ok(!byId(w, 'slideshow-media').classList.contains('kb-on'));
});

test('switching it on or off during Play takes effect at once', async () => {
  const { w } = await kenBurnsPage();
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  byId(w, 'slideshow-kb').click();
  assert.ok(kbImg(w).classList.contains('kb'));
  byId(w, 'slideshow-kb').click();
  assert.ok(!kbImg(w).classList.contains('kb'));
  assert.ok(!byId(w, 'slideshow-media').classList.contains('kb-on'));
});

test('pausing holds the photo where it is; playing again starts the move again', async () => {
  const { w } = await kenBurnsPage({ storage: { tripKenBurns: '1' } });
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  byId(w, 'slideshow-play').click();                       // pause
  assert.ok(kbImg(w).classList.contains('kb'));
  assert.ok(kbImg(w).classList.contains('kb-paused'));
  byId(w, 'slideshow-play').click();                       // play
  assert.ok(kbImg(w).classList.contains('kb'));
  assert.ok(!kbImg(w).classList.contains('kb-paused'));
});

test('zooming in by hand stops it, and it does not come back while zoomed', async () => {
  const { w } = await kenBurnsPage({ storage: { tripKenBurns: '1' } });
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  assert.ok(kbImg(w).classList.contains('kb'));
  w.eval('zoomBy(1)');
  assert.ok(!kbImg(w).classList.contains('kb'));
  assert.ok(!byId(w, 'slideshow-media').classList.contains('kb-on'));
  byId(w, 'slideshow-kb').click();
  byId(w, 'slideshow-kb').click();
  assert.ok(!kbImg(w).classList.contains('kb'), 'still zoomed in');
});

test('closing the viewer leaves nothing animated behind', async () => {
  const { w } = await kenBurnsPage({ storage: { tripKenBurns: '1' } });
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  press(w, 'Escape');
  assert.equal(w.document.querySelectorAll('.kb, .kb-on').length, 0);
  thumbs(w)[0].click();
  assert.ok(!kbImg(w).classList.contains('kb'), 'a newly opened viewer is not playing');
});

test('a video is never animated', async (t) => {
  const { w } = await kenBurnsPage({ storage: { tripKenBurns: '1' } });
  const vi = w.eval('SLIDES.map(s => s.type)').indexOf('video');
  if (vi < 0) return t.skip('no video in this fixture (ffmpeg missing)');
  thumbs(w)[vi].click();
  byId(w, 'slideshow-play').click();
  assert.equal(w.document.querySelectorAll('.kb, .kb-on').length, 0);
});

test('people who ask for less motion get no button and no animation', async () => {
  const page = await load(fixture.server, DAY, { storage: { tripKenBurns: '1' } });
  const { w } = page;
  w.matchMedia = (q) => ({ matches: /reduce/.test(q), media: q, addEventListener() {}, removeEventListener() {} });
  Object.defineProperty(w.HTMLImageElement.prototype, 'complete', { configurable: true, get() { return true; } });
  w.eval('syncKenBurnsButton()');
  assert.equal(byId(w, 'slideshow-kb').hidden, true);
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  assert.ok(!kbImg(w).classList.contains('kb'));
});

test('a browser that cannot store anything still works', async () => {
  const { w, errors } = await load(fixture.server, DAY, { breakStorage: true });
  byId(w, 'slideshow-kb').click();
  assert.equal(w.eval('kenBurns'), true);
  assert.deepEqual(errors.filter((e) => !/Not implemented|Could not load|ECONNREFUSED/.test(e)), []);
});
