// The generated static site, served over HTTP the way it would be hosted.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { fixture, sleep, load, realErrors, press, thumbs, isOpen } = require('./lib');

const DAY = '/days/2026-05-02.html';

test('slide keys are unique and include the awkward file name', async () => {
  const { w } = await load(fixture.staticSite, DAY);
  const keys = w.eval('SLIDES.map(s => s.key)');
  assert.equal(new Set(keys).size, keys.length);
  assert.ok(keys.some((k) => k.includes('#')), 'the file with # & spaces and accents is present: ' + keys);
});

test('a link to a photo with spaces, #, & and accents in its name round-trips', async () => {
  const { w } = await load(fixture.staticSite, DAY);
  const keys = w.eval('SLIDES.map(s => s.key)');
  const i = keys.findIndex((k) => k.includes('#'));
  thumbs(w)[i].click();
  const hash = w.location.hash;
  assert.ok(!hash.slice(1).includes('#'), 'only one # in the address: ' + hash);
  assert.match(hash, /%23/);
  const again = await load(fixture.staticSite, DAY + hash);
  assert.ok(isOpen(again.w));
  assert.equal(again.w.eval('SLIDES[currentSlide].key'), keys[i]);
});

test('the static site has no editing controls', async () => {
  const { w } = await load(fixture.staticSite, DAY);
  assert.equal(w.document.getElementById('edit-caption'), null);
  assert.equal(w.document.getElementById('day-edit'), null);
  thumbs(w)[0].click();
  press(w, 's');                                         // S only favorites when editing is on
  await sleep(200);
  assert.equal(w.eval('EDITABLE'), false);
});

test('captions and favorites from the content file are shown, and favorites can be filtered', async () => {
  const { w, errors } = await load(fixture.staticSite, '/days/2026-05-01.html');
  assert.equal(thumbs(w).length, 2, 'the hidden photo is not in the site');
  assert.equal(w.document.querySelectorAll('.thumb.is-fav').length, 1);
  assert.equal(thumbs(w)[0].querySelector('img').alt, 'Sunrise over the canal');
  const btn = w.document.getElementById('fav-filter');
  assert.equal(btn.hidden, false);
  btn.click();
  assert.ok(w.document.getElementById('thumb-grid').classList.contains('fav-only'));
  assert.deepEqual(realErrors(errors).filter((e) => !/_leaflet_id/.test(e)), []);
});
