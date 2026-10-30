// The S/M/L/XL controls: thumbnails on day pages, cards on the homepage. Each page
// remembers its own choice, and a browser that can't store anything still works.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { fixture, load, realErrors } = require('./lib');

const DAY = '/day/2026-05-01?preview=1';
const HOME = '/?preview=1';

function state(w, targetId) {
  const toggle = w.document.querySelector(`.size-toggle[data-target="${targetId}"]`);
  const active = [...toggle.querySelectorAll('.size-btn.active')].map((b) => b.dataset.size);
  const pressed = [...toggle.querySelectorAll('.size-btn[aria-pressed="true"]')].map((b) => b.dataset.size);
  const classes = [...w.document.getElementById(targetId).classList].filter((c) => c.startsWith('size-'));
  return { active, pressed, classes };
}

const click = (w, targetId, size) =>
  w.document.querySelector(`.size-toggle[data-target="${targetId}"] .size-btn[data-size="${size}"]`).click();

const pageErrors = (errors) => realErrors(errors).filter((e) => !/_leaflet_id/.test(e));

test('a day page starts at M, and each button switches the grid to its size', async () => {
  const { w, errors } = await load(fixture.server, DAY);
  assert.deepEqual(state(w, 'thumb-grid'), { active: ['medium'], pressed: ['medium'], classes: [] });
  for (const size of ['small', 'large', 'xl']) {
    click(w, 'thumb-grid', size);
    assert.deepEqual(state(w, 'thumb-grid'), { active: [size], pressed: [size], classes: ['size-' + size] });
    assert.equal(w.localStorage.getItem('tripThumbSize'), size);
  }
  click(w, 'thumb-grid', 'medium');
  assert.deepEqual(state(w, 'thumb-grid').classes, []);           // medium is the stylesheet's default
  assert.deepEqual(pageErrors(errors), []);
});

test('the choice is remembered, and a stored value that makes no sense falls back to M', async () => {
  const remembered = await load(fixture.server, DAY, { storage: { tripThumbSize: 'xl' } });
  assert.deepEqual(state(remembered.w, 'thumb-grid').classes, ['size-xl']);
  const garbage = await load(fixture.server, DAY, { storage: { tripThumbSize: 'huge"><script>' } });
  assert.deepEqual(state(garbage.w, 'thumb-grid'), { active: ['medium'], pressed: ['medium'], classes: [] });
});

test('the homepage cards have their own control and their own remembered size', async () => {
  const { w } = await load(fixture.server, HOME, { storage: { tripThumbSize: 'small' } });
  assert.deepEqual(state(w, 'day-list').classes, [], 'the day pages\' choice does not resize the cards');
  click(w, 'day-list', 'large');
  assert.deepEqual(state(w, 'day-list').classes, ['size-large']);
  assert.equal(w.localStorage.getItem('tripCardSize'), 'large');
  assert.equal(w.localStorage.getItem('tripThumbSize'), 'small');
});

test('with storage blocked (private browsing) the buttons still work', async () => {
  const { w, errors } = await load(fixture.server, DAY, { breakStorage: true });
  assert.deepEqual(state(w, 'thumb-grid').active, ['medium']);
  click(w, 'thumb-grid', 'xl');
  assert.deepEqual(state(w, 'thumb-grid').classes, ['size-xl']);
  assert.deepEqual(pageErrors(errors), []);
});

test('the static site has the same controls', async () => {
  const day = await load(fixture.staticSite, '/days/2026-05-01.html');
  click(day.w, 'thumb-grid', 'large');
  assert.deepEqual(state(day.w, 'thumb-grid').classes, ['size-large']);
  const home = await load(fixture.staticSite, '/index.html');
  click(home.w, 'day-list', 'small');
  assert.deepEqual(state(home.w, 'day-list').classes, ['size-small']);
});
