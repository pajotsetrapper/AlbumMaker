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
  // Esc on changed text asks first: "keep editing" keeps it
  const asked = [];
  w.confirm = (q) => { asked.push(q); return false; };
  key(w, 'Escape', {}, title);
  assert.equal(asked.length, 1);
  assert.equal(d.getElementById('day-editor').hidden, false);
  assert.equal(title.value, saved + ' (draft)');
  w.confirm = () => true;                                   // "discard"
  key(w, 'Escape', {}, title);
  assert.equal(d.getElementById('day-editor').hidden, true);
  assert.equal(title.value, saved);
  assert.deepEqual(went(), []);
  key(w, 'Escape');
  assert.equal(went().length, 1);
});

test('the day text editor keeps its text from stray keys', async () => {
  const { w, went } = await page(fixture.server, '/day/2026-05-01');
  const d = w.document;
  d.getElementById('day-edit').click();
  const title = d.getElementById('edit-title');
  title.value = 'half typed';
  // an input method (Japanese, Chinese ...) uses Esc to cancel what it is composing
  key(w, 'Escape', { isComposing: true }, title);
  assert.equal(d.getElementById('day-editor').hidden, false);
  assert.equal(title.value, 'half typed');
  // arrows with the focus on Save or Cancel do not go to another day
  const save = d.getElementById('day-save');
  save.focus();
  key(w, 'ArrowRight', {}, save);
  key(w, 'ArrowLeft', {}, save);
  assert.deepEqual(went(), []);
  // leaving the page asks the browser to confirm, as long as the text is changed
  const leaving = () => { const e = new w.Event('beforeunload', { cancelable: true }); w.dispatchEvent(e); return e.defaultPrevented; };
  assert.equal(leaving(), true);
  d.getElementById('day-cancel').click();
  assert.equal(leaving(), false);
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

test("in a library, Esc on a trip's homepage goes back to the list of trips", async () => {
  const { w, went } = await page(fixture.server, '/?preview=1');
  w.eval("initHomeNav('/')");                                // what a trip's homepage in a library calls
  key(w, 'Escape', { repeat: true });
  key(w, 'Escape', { metaKey: true });
  assert.deepEqual(went(), []);
  const close = w.document.createElement('a');               // an open map popup closes first
  close.className = 'leaflet-popup-close-button';
  close.addEventListener('click', (e) => { e.preventDefault(); close.remove(); });
  w.document.body.appendChild(close);
  key(w, 'Escape');
  assert.deepEqual(went(), []);
  key(w, 'Escape');
  assert.deepEqual(went(), ['/']);
});

// A touch event with the given fingers still down (`touches`) and the ones that moved.
function touch(w, type, touches, changed, target) {
  const e = new w.Event(type, { bubbles: true, cancelable: true });
  const pts = (list) => list.map(([x, y]) => ({ clientX: x, clientY: y }));
  Object.defineProperty(e, 'touches', { value: pts(touches) });
  Object.defineProperty(e, 'changedTouches', { value: pts(changed) });
  (target || w.document.body).dispatchEvent(e);
}

test('a one-finger swipe goes to the next day, a pinch never does', async () => {
  const { w, went } = await page(fixture.server, DAY1);
  // a pinch-zoom on the thumbnails: two fingers, then lifted one at a time
  touch(w, 'touchstart', [[300, 300]], [[300, 300]]);
  touch(w, 'touchstart', [[300, 300], [320, 320]], [[320, 320]]);
  // the first finger lifts 200 px to the left of where it began, then the other one
  touch(w, 'touchend', [[340, 320]], [[100, 300]]);
  touch(w, 'touchend', [], [[340, 320]]);
  assert.deepEqual(went(), []);
  // a swipe
  touch(w, 'touchstart', [[300, 300]], [[300, 300]]);
  touch(w, 'touchend', [], [[100, 305]]);
  assert.equal(went().length, 1);
  assert.match(went()[0], /2026-05-02/);
});

test('the viewer is a modal dialog: it takes the focus, keeps Tab inside and gives it back', async () => {
  const { w } = await page(fixture.server, DAY1);
  const d = w.document;
  const thumb = thumbs(w)[0];
  thumb.focus();
  thumb.click();
  const viewer = d.getElementById('slideshow');
  assert.equal(d.activeElement, viewer);
  for (const id of ['slideshow-prev', 'slideshow-next']) {
    const b = d.getElementById(id);
    assert.equal(b.tagName, 'BUTTON');
    assert.ok(b.getAttribute('aria-label'));
  }
  assert.equal(d.getElementById('slideshow-caption').getAttribute('aria-live'), 'polite');
  // Tab past the last control comes back to the first; the closed details panel is skipped
  const close = d.getElementById('slideshow-close');
  const play = d.getElementById('slideshow-play');
  const tab = (target, shiftKey) => {
    const e = new w.KeyboardEvent('keydown', { key: 'Tab', shiftKey, bubbles: true, cancelable: true });
    target.dispatchEvent(e);
    return e.defaultPrevented;
  };
  const last = d.getElementById('slideshow-next');
  last.focus();
  assert.ok(tab(last, false));
  assert.equal(d.activeElement, play);
  assert.ok(tab(play, true));
  assert.equal(d.activeElement, last);
  assert.ok(!d.getElementById('info-close').matches(':focus'));
  close.click();
  assert.equal(d.activeElement, thumb);
});
