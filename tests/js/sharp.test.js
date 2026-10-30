// Sharper thumbnails: the larger version (data-hi) is swapped in only where the screen
// would otherwise stretch the normal one, only for tiles near the viewport, and a
// failed download keeps the normal thumbnail. jsdom has no layout, so element and
// image sizes come from the stubs in lib.js.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { fixture, sleep, load, realErrors } = require('./lib');

const DAY = '/day/2026-05-01?preview=1';
const THUMB = [400, 300];                                  // the normal thumbnail's pixels

// A grid where every tile is `w` x `h` CSS pixels.
const grid = (w, h) => (el) => (el.tagName === 'IMG' && el.dataset.hi !== undefined ? [w, h] : [0, 0]);

const tiles = (w) => [...w.document.querySelectorAll('#thumb-grid img[data-hi]')];
const sharpened = (w) => tiles(w).filter((img) => img.getAttribute('src') === img.dataset.hi);
const hiDownloads = (w) => w.__downloads.filter((u) => tiles(w).some((t) => t.dataset.hi === u));

test('the server offers a larger version of every thumbnail', async () => {
  const { w } = await load(fixture.server, DAY, { stubs: { box: grid(200, 150), natural: () => THUMB } });
  assert.ok(tiles(w).length >= 2);
  for (const img of tiles(w)) assert.notEqual(img.dataset.hi, img.getAttribute('src'));
});

test('tiles no bigger than the thumbnail keep it, and nothing larger is downloaded', async () => {
  const { w, errors } = await load(fixture.server, DAY, { stubs: { box: grid(300, 225), natural: () => THUMB } });
  assert.deepEqual(sharpened(w), []);
  assert.deepEqual(hiDownloads(w), []);
  assert.deepEqual(realErrors(errors).filter((e) => !/_leaflet_id/.test(e)), []);
});

test('a stretch of 5% or less is not worth a download', async () => {
  const { w } = await load(fixture.server, DAY, { stubs: { box: grid(420, 315), natural: () => THUMB } });
  assert.deepEqual(sharpened(w), []);
});

test('big tiles get the larger version', async () => {
  const { w } = await load(fixture.server, DAY, { stubs: { box: grid(800, 600), natural: () => THUMB } });
  assert.equal(sharpened(w).length, tiles(w).length);
});

test('a high-resolution screen gets it even at the normal size', async () => {
  const { w } = await load(fixture.server, DAY, { stubs: { dpr: 2, box: grid(300, 225), natural: () => THUMB } });
  assert.equal(sharpened(w).length, tiles(w).length);
});

test('choosing a bigger size swaps in the larger version without a reload', async () => {
  const { w } = await load(fixture.server, DAY, { stubs: { box: grid(300, 225), natural: () => THUMB } });
  assert.deepEqual(sharpened(w), []);
  w.__box = grid(900, 675);                                 // what XL would lay out
  w.document.querySelector('.size-btn[data-size="xl"]').click();
  await sleep(100);
  assert.equal(sharpened(w).length, tiles(w).length);
});

test('tiles far from the viewport are left alone', async () => {
  const { w } = await load(fixture.server, DAY, {
    stubs: { box: grid(800, 600), natural: () => THUMB, visible: (el) => el === el.ownerDocument.querySelector('#thumb-grid img') } });
  assert.equal(sharpened(w).length, 1);
  assert.equal(hiDownloads(w).length, 1);
});

test('a failed download keeps the normal thumbnail and can be tried again', async () => {
  const { w } = await load(fixture.server, DAY, { stubs: { box: grid(800, 600), natural: () => THUMB, failHi: () => true } });
  assert.deepEqual(sharpened(w), []);
  for (const img of tiles(w)) assert.notEqual(img.dataset.tier, 'hi');
  w.__failHi = null;
  w.eval('sharpenVisible()');
  await sleep(100);
  assert.equal(sharpened(w).length, tiles(w).length);
});

test('browsers without IntersectionObserver simply keep the normal thumbnails', async () => {
  const { w, errors } = await load(fixture.server, DAY, { stubs: { box: grid(800, 600), natural: () => THUMB, noObserver: true } });
  assert.deepEqual(sharpened(w), []);
  assert.deepEqual(realErrors(errors).filter((e) => !/_leaflet_id/.test(e)), []);
});

test('a homepage card that crops its cover gets the larger version', async () => {
  // a 4:3 thumbnail filling a 600x200 card is cropped and stretched 1.5x
  const box = (el) => (el.classList && el.classList.contains('cover') ? [600, 200] : [0, 0]);
  const { w } = await load(fixture.server, '/?preview=1', { stubs: { box, sizes: () => THUMB } });
  const covers = [...w.document.querySelectorAll('.cover[data-hi]')];
  assert.ok(covers.length >= 1);
  for (const c of covers) assert.ok(c.style.backgroundImage.includes(c.dataset.hi), c.style.backgroundImage);
});

test('the static site only offers larger versions when built with --large-thumb-size', async () => {
  const plain = await load(fixture.staticSite, '/days/2026-05-01.html', { stubs: { box: grid(800, 600), natural: () => THUMB } });
  assert.equal(plain.d.querySelectorAll('[data-hi]').length, 0);
  const large = await load(fixture.staticLargeSite, '/days/2026-05-01.html', { stubs: { box: grid(800, 600), natural: () => THUMB } });
  assert.ok(tiles(large.w).length >= 1);
  for (const img of tiles(large.w)) assert.match(img.dataset.hi, /^\.\.\/thumbs_large\//);
  assert.equal(sharpened(large.w).length, tiles(large.w).length);
});
