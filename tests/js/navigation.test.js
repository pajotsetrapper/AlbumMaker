// Day pages: the arrow keys go to the previous/next day and Esc goes back to the
// overview, one level at a time, without getting in the way of the viewer, text boxes
// or the browser's own shortcuts.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { fixture, sleep, load, realErrors, press, thumbs, isOpen } = require('./lib');

const DAY1 = '/day/2026-05-01?preview=1';
const DAY2 = '/day/2026-05-02?preview=1';

// Loads a page and records where it would go instead of really leaving it.
async function page(base, path) {
  const p = await load(base, path);
  p.w.eval('window.__went = []; navigateTo = (href) => { __went.push(href); };');
  p.went = () => JSON.parse(p.w.eval('JSON.stringify(__went)'));     // a plain Node array, not one from the page
  return p;
}

function key(w, k, opts = {}, target) {
  (target || w.document).dispatchEvent(new w.KeyboardEvent('keydown', Object.assign({ key: k, bubbles: true, cancelable: true }, opts)));
}

test('arrow keys go to the next and previous day, where there is one', async () => {
  const first = await page(fixture.server, DAY1);
  press(first.w, 'ArrowLeft');                              // the first day has no previous day
  assert.deepEqual(first.went(), []);
  press(first.w, 'ArrowRight');
  assert.equal(first.went().length, 1);
  assert.match(first.went()[0], /2026-05-02/);

  const second = await page(fixture.server, DAY2);
  press(second.w, 'ArrowLeft');
  assert.match(second.went()[0], /2026-05-01/);
  press(second.w, 'ArrowRight');                            // and the last day has no next day
  assert.equal(second.went().length, 1);
});

test('Esc goes back to the overview, once per press', async () => {
  const { w, went, errors } = await page(fixture.server, DAY1);
  key(w, 'Escape', { repeat: true });                       // a held key, still repeating from the page before
  assert.deepEqual(went(), []);
  key(w, 'Escape');
  assert.equal(went().length, 1);
  assert.equal(new URL(went()[0], w.location.href).pathname, '/');
  assert.deepEqual(realErrors(errors).filter((e) => !/_leaflet_id/.test(e)), []);
});

test("the browser's own shortcuts (Alt+Left, Cmd/Ctrl+arrow) are left alone", async () => {
  const { w, went } = await page(fixture.server, DAY2);
  for (const mod of ['altKey', 'metaKey', 'ctrlKey']) {
    key(w, 'ArrowLeft', { [mod]: true });
    key(w, 'Escape', { [mod]: true });
  }
  assert.deepEqual(went(), []);
});

test('with the viewer open, arrows change photo and Esc closes the viewer, not the page', async () => {
  const { w, went } = await page(fixture.server, DAY1);
  thumbs(w)[0].click();
  assert.ok(isOpen(w));
  press(w, 'ArrowRight');
  assert.equal(w.eval('currentSlide'), 1);
  press(w, 'Escape');
  assert.equal(isOpen(w), false);
  assert.deepEqual(went(), []);
  press(w, 'Escape');                                       // the viewer is closed now: this one leaves
  assert.equal(went().length, 1);
});

test('an open map popup is closed first', async () => {
  const { w, went } = await page(fixture.server, DAY1);
  const close = w.document.createElement('a');             // what Leaflet puts in an open popup
  close.className = 'leaflet-popup-close-button';
  let clicked = 0;
  close.addEventListener('click', (e) => { e.preventDefault(); clicked++; close.remove(); });
  w.document.body.appendChild(close);
  press(w, 'Escape');
  assert.equal(clicked, 1);
  assert.deepEqual(went(), []);
  press(w, 'Escape');
  assert.equal(went().length, 1);
});

test('in the day text editor, arrows move the cursor and Esc cancels the form before leaving', async () => {
  const { w, went } = await page(fixture.server, '/day/2026-05-01');    // the editor's view
  const d = w.document;
  d.getElementById('day-edit').click();
  const title = d.getElementById('edit-title');
  assert.equal(d.getElementById('day-editor').hidden, false);
  const saved = title.value;
  title.value = saved + ' (draft)';
  key(w, 'ArrowLeft', {}, title);
  key(w, 'ArrowRight', {}, title);
  assert.deepEqual(went(), []);
  key(w, 'Escape', {}, title);
  assert.equal(d.getElementById('day-editor').hidden, true);
  assert.equal(title.value, saved);
  assert.deepEqual(went(), []);
  key(w, 'Escape');
  assert.equal(went().length, 1);
});

test('the static site uses relative links for the same keys', async () => {
  const { w, went } = await page(fixture.staticSite, '/days/2026-05-01.html');
  press(w, 'ArrowRight');
  press(w, 'Escape');
  const [next, home] = went();
  assert.equal(new URL(next, w.location.href).pathname, '/days/2026-05-02.html');
  assert.equal(new URL(home, w.location.href).pathname, '/index.html');
  assert.ok(!next.startsWith('/') && !home.startsWith('/'), 'relative, so the folder works anywhere: ' + went());
});

test('Esc on the overview page does nothing', async () => {
  const { w } = await load(fixture.server, '/?preview=1');
  const before = w.location.href;
  press(w, 'Escape');
  await sleep(100);
  assert.equal(w.location.href, before);
});
