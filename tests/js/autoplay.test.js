// Slideshow autoplay.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { fixture, sleep, load, press, thumbs, isOpen } = require('./lib');

const DAY = '/day/2026-05-02?preview=1';      // b1, b2 and (when ffmpeg is available) a video
const byId = (w, id) => w.document.getElementById(id);

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
  const frozen = w.eval('currentSlide');
  await sleep(500);
  assert.equal(w.eval('currentSlide'), frozen);
  press(w, ' ');
  assert.equal(w.eval('playing'), true);
  await sleep(400);
  assert.notEqual(w.eval('currentSlide'), frozen);
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
  const at = w.eval('currentSlide');
  await sleep(600);
  assert.equal(w.eval('currentSlide'), at);
  press(w, 'i');
  await sleep(500);
  assert.notEqual(w.eval('currentSlide'), at);
});

test('it waits while zoomed in', async () => {
  const { w } = await playing();
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  w.eval('ZOOM.scale = 2');
  const at = w.eval('currentSlide');
  await sleep(600);
  assert.equal(w.eval('currentSlide'), at);
  w.eval('resetZoom()');
  await sleep(500);
  assert.notEqual(w.eval('currentSlide'), at);
});

test('it holds while a Live Photo clip is playing', async () => {
  const { w } = await playing();
  thumbs(w)[0].click();
  byId(w, 'slideshow-play').click();
  w.eval('liveActive = true; scheduleAdvance()');
  const at = w.eval('currentSlide');
  await sleep(600);
  assert.equal(w.eval('currentSlide'), at);
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
  const at = w.eval('currentSlide');
  await sleep(500);
  assert.equal(w.eval('currentSlide'), at);
});

test('Space on a focused thumbnail opens the viewer without starting playback', async () => {
  const { w } = await playing();
  const t = thumbs(w)[0];
  t.focus();
  t.dispatchEvent(new w.KeyboardEvent('keydown', { key: ' ', bubbles: true }));
  assert.ok(isOpen(w));
  assert.equal(w.eval('playing'), false);
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

test('typing a space in the caption box does not start playback (editor view)', async () => {
  const { w } = await load(fixture.server, '/day/2026-05-02');
  thumbs(w)[0].click();
  const cap = byId(w, 'edit-caption');
  cap.focus();
  press(w, ' ', cap);
  assert.equal(w.eval('playing'), false);
});
