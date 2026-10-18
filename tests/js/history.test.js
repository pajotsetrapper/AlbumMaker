// Links to a photo and the Back button.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { fixture, sleep, load, realErrors, press, thumbs, isOpen } = require('./lib');

const DAY = '/day/2026-05-01?preview=1';

test('opening a photo adds exactly one history entry; browsing only rewrites the hash', async () => {
  const { w } = await load(fixture.server, DAY);
  const before = w.history.length;
  thumbs(w)[0].click();
  assert.ok(isOpen(w));
  assert.match(w.location.hash, /^#p=/);
  assert.equal(w.history.length, before + 1);
  const first = w.location.hash;
  press(w, 'ArrowRight');
  assert.notEqual(w.location.hash, first);
  press(w, 'ArrowLeft');
  assert.equal(w.location.hash, first);
  assert.equal(w.history.length, before + 1);
});

test('Back closes the viewer and Forward reopens it on the same photo', async () => {
  const { w } = await load(fixture.server, DAY);
  thumbs(w)[0].click();
  press(w, 'ArrowRight');
  const shown = w.location.hash;
  w.history.back();
  await sleep(300);
  assert.equal(isOpen(w), false);
  assert.equal(w.location.hash, '');
  w.history.forward();
  await sleep(300);
  assert.ok(isOpen(w));
  assert.equal(w.location.hash, shown);
});

test('closing with the X button or Esc clears the address and leaves history usable', async () => {
  const { w } = await load(fixture.server, DAY);
  thumbs(w)[0].click();
  w.document.getElementById('slideshow-close').click();
  await sleep(300);
  assert.equal(isOpen(w), false);
  assert.equal(w.location.hash, '');
  thumbs(w)[1].click();
  press(w, 'Escape');
  await sleep(300);
  assert.equal(isOpen(w), false);
  assert.equal(w.location.hash, '');
  thumbs(w)[0].click();
  w.history.back();                                      // Back must still work afterwards
  await sleep(300);
  assert.equal(isOpen(w), false);
});

test('a link to a photo opens straight onto it, and closing it stays on the page', async () => {
  const first = await load(fixture.server, DAY);
  const key = first.w.eval('SLIDES[1].key');
  const { w, errors } = await load(fixture.server, DAY + '#p=' + encodeURIComponent(key));
  assert.ok(isOpen(w));
  assert.equal(w.eval('SLIDES[currentSlide].key'), key);
  const entries = w.history.length;
  w.document.getElementById('slideshow-close').click();
  await sleep(300);
  assert.equal(isOpen(w), false);
  assert.equal(w.location.hash, '');
  assert.equal(w.location.pathname, '/day/2026-05-01');
  assert.equal(w.history.length, entries);
  assert.deepEqual(realErrors(errors).filter((e) => !/_leaflet_id/.test(e)), []);
});

test('a link to a photo that no longer exists is ignored', async () => {
  const { w } = await load(fixture.server, DAY + '#p=does-not-exist');
  assert.equal(isOpen(w), false);
});

test('the address follows the photo while browsing (so copying it shares that photo)', async () => {
  const { w } = await load(fixture.server, DAY);
  thumbs(w)[0].click();
  const key0 = w.eval('SLIDES[0].key');
  assert.equal(decodeURIComponent(w.location.hash.slice(3)), key0);
  press(w, 'ArrowRight');
  assert.equal(decodeURIComponent(w.location.hash.slice(3)), w.eval('SLIDES[1].key'));
});
