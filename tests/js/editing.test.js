// The editing controls in the viewer: captions, favorites, hiding, covers, day text.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { fixture, sleep, load, press, thumbs, isOpen, readContent, writeContent } = require('./lib');

const DAY = '/day/2026-05-01';
const byId = (w, id) => w.document.getElementById(id);

test('the editor sees every photo, with the hidden one dimmed', async () => {
  const { w } = await load(fixture.server, DAY);
  assert.equal(thumbs(w).length, 3);
  assert.equal(thumbs(w).filter((t) => t.classList.contains('is-hidden')).length, 1);
  assert.ok(byId(w, 'day-edit'), 'edit button present');
});

test('a caption saves, updates the thumbnail text and the viewer, and lands in the file', async () => {
  const { w } = await load(fixture.server, DAY);
  thumbs(w)[0].click();
  const cap = byId(w, 'edit-caption');
  assert.equal(cap.value, 'Sunrise over the canal');
  cap.value = 'Golden hour, redone';
  cap.dispatchEvent(new w.Event('change'));
  await sleep(400);
  assert.equal(thumbs(w)[0].querySelector('img').alt, 'Golden hour, redone');
  assert.equal(w.document.querySelector('.cap-text').textContent, 'Golden hour, redone');
  assert.equal(readContent().photos['a1.jpg'].caption, 'Golden hour, redone');
});

test('a caption containing HTML is shown as plain text, never run', async () => {
  const { w } = await load(fixture.server, DAY);
  thumbs(w)[0].click();
  const nasty = '<b>bold</b> & <img src=x onerror="window.__xss=1"> <script>window.__xss=2</script>';
  const cap = byId(w, 'edit-caption');
  cap.value = nasty;
  cap.dispatchEvent(new w.Event('change'));
  await sleep(500);
  const shown = w.document.querySelector('.cap-text');
  assert.equal(shown.textContent, nasty);
  assert.equal(shown.children.length, 0, 'no elements were created from the caption');
  assert.equal(thumbs(w)[0].querySelector('img').alt, nasty);
  assert.equal(w.__xss, undefined);
  // and when the page is loaded again from the saved file
  const again = await load(fixture.server, DAY);
  assert.equal(again.d.querySelector('.thumb img').alt, nasty);
  assert.equal(again.d.querySelectorAll('.thumb b, .thumb script').length, 0);
  assert.equal(again.w.__xss, undefined);
});

test('typing in the caption box does not trigger shortcuts or navigation', async () => {
  const { w } = await load(fixture.server, DAY);
  thumbs(w)[0].click();
  const cap = byId(w, 'edit-caption');
  cap.focus();
  const before = w.eval('currentSlide');
  press(w, 's', cap);
  press(w, 'ArrowRight', cap);
  press(w, ' ', cap);
  await sleep(300);
  assert.equal(w.eval('currentSlide'), before);
  assert.equal(w.eval('playing'), false);
  assert.equal(readContent().photos['a1.jpg'].favorite, true, 'still a favorite');
});

test('S toggles the favorite star and saves it', async () => {
  const { w } = await load(fixture.server, DAY);
  thumbs(w)[1].click();                                  // a2: not a favorite yet
  assert.equal(thumbs(w)[1].classList.contains('is-fav'), false);
  press(w, 's');
  await sleep(400);
  assert.equal(thumbs(w)[1].classList.contains('is-fav'), true);
  assert.equal(readContent().photos['a2.jpg'].favorite, true);
  press(w, 's');
  await sleep(400);
  assert.equal(thumbs(w)[1].classList.contains('is-fav'), false);
  assert.equal(readContent().photos['a2.jpg'], undefined, 'back to defaults: the entry is gone');
});

test('Hide dims the photo, labels the button Unhide, and Unhide undoes it', async () => {
  const { w } = await load(fixture.server, DAY);
  thumbs(w)[0].click();
  byId(w, 'edit-hide').click();
  await sleep(400);
  assert.ok(thumbs(w)[0].classList.contains('is-hidden'));
  assert.equal(byId(w, 'edit-hide').textContent, 'Unhide');
  assert.match(byId(w, 'toast').textContent, /Hidden from visitors/);
  assert.equal(readContent().photos['a1.jpg'].hidden, true);
  byId(w, 'edit-hide').click();
  await sleep(400);
  assert.equal(thumbs(w)[0].classList.contains('is-hidden'), false);
  assert.equal(readContent().photos['a1.jpg'].hidden, undefined);
});

test('Set as cover is saved for the day', async () => {
  const { w } = await load(fixture.server, DAY);
  thumbs(w)[1].click();
  byId(w, 'edit-cover').click();
  await sleep(400);
  assert.match(byId(w, 'toast').textContent, /Cover photo set/);
  assert.equal(readContent().days['2026-05-01'].cover, 'a2.jpg');
});

test('the favorites filter shows only favorites and steps the viewer through them', async () => {
  const { w } = await load(fixture.server, DAY);
  const btn = byId(w, 'fav-filter');
  assert.equal(btn.hidden, false);
  assert.match(btn.textContent, /Favorites \(1\)/);
  btn.click();
  assert.ok(byId(w, 'thumb-grid').classList.contains('fav-only'));
  thumbs(w)[0].click();
  press(w, 'ArrowRight');                                // only one favorite: it stays on it
  assert.equal(w.eval('currentSlide'), 0);
});

test('the day title and description can be edited and saved', async () => {
  const { w } = await load(fixture.server, DAY);
  byId(w, 'day-edit').click();
  assert.equal(byId(w, 'day-editor').hidden, false);
  byId(w, 'edit-title').value = 'Rainy canals';
  byId(w, 'edit-desc').value = 'First.\n\nSecond.';
  byId(w, 'day-save').click();
  await sleep(500);
  const day = readContent().days['2026-05-01'];
  assert.equal(day.title, 'Rainy canals');
  assert.equal(day.description, 'First.\n\nSecond.');
});

test('a day without a title is offered one made of its place names', async () => {
  const { w } = await load(fixture.server, '/day/2026-05-02');     // no title saved for this day
  byId(w, 'day-edit').click();
  const offer = byId(w, 'use-suggestion');
  assert.ok(offer, 'the suggestion button is there');
  assert.ok(offer.dataset.title.length > 0);
  assert.equal(byId(w, 'edit-title').placeholder, offer.dataset.title);
  offer.click();
  assert.equal(byId(w, 'edit-title').value, offer.dataset.title);
  assert.equal(w.document.activeElement, byId(w, 'edit-title'));
  assert.equal(readContent().days?.['2026-05-02']?.title ?? '', '', 'nothing is saved until Save');
});

test('Cancel puts the form back as it was', async () => {
  const { w } = await load(fixture.server, DAY);
  const saved = () => readContent().days?.['2026-05-01']?.title ?? '';
  const before = saved();
  byId(w, 'day-edit').click();
  const original = byId(w, 'edit-title').value;
  byId(w, 'edit-title').value = 'Typed but abandoned';
  byId(w, 'day-cancel').click();
  assert.equal(byId(w, 'day-editor').hidden, true);
  assert.equal(byId(w, 'edit-title').value, original);
  assert.equal(saved(), before, 'nothing was saved');
});

test('when the file cannot be saved the screen says so and nothing changes', async () => {
  const { w } = await load(fixture.server, DAY);
  thumbs(w)[1].click();
  const broken = '{"title": "half-finished edit",}';
  writeContent(broken);
  try {
    press(w, 's');
    await sleep(500);
    const toast = byId(w, 'toast');
    assert.match(toast.textContent, /Nothing was saved/);
    assert.ok(toast.classList.contains('error'));
    assert.equal(thumbs(w)[1].classList.contains('is-fav'), false, 'the star must not appear for an unsaved change');
    assert.equal(require('fs').readFileSync(fixture.contentFile, 'utf8'), broken);
  } finally {
    writeContent(fixture.initialContent);
  }
});

test('a caption typed before the viewer moves on (a swipe, a video ending) is saved for its own photo', async () => {
  const { w } = await load(fixture.server, DAY);
  thumbs(w)[1].click();                                  // a2
  const cap = byId(w, 'edit-caption');
  // a browser fires 'change' (before 'blur') when a box with new text loses focus; jsdom doesn't
  w.addEventListener('blur', (e) => { if (e.target === cap) cap.dispatchEvent(new w.Event('change')); }, true);
  cap.focus();
  cap.value = 'Typed for a2';
  w.eval('nextSlide()');                                 // what a swipe does, with the box still focused
  await sleep(500);
  assert.equal(w.eval('currentSlide'), 2);
  assert.equal(readContent().photos['a2.jpg'].caption, 'Typed for a2');
  assert.equal(readContent().photos['sub/a3.jpg'].caption, undefined, 'not saved onto the photo now shown');
  assert.equal(cap.value, '', 'the box shows the new photo\'s (empty) caption');
});

test('typing a caption while a Live Photo clip plays is not interrupted when the clip ends', async () => {
  const { w } = await load(fixture.server, DAY);
  w.eval("SLIDES[1].live = '/no-such-clip.mov'");          // a2 presented as a Live Photo
  w.HTMLMediaElement.prototype.play = () => Promise.resolve();   // jsdom has no media playback
  thumbs(w)[1].click();
  w.eval('playLive()');
  const cap = byId(w, 'edit-caption');
  cap.focus();
  cap.value = 'Half-typ';
  w.document.querySelector('#slideshow-media video').dispatchEvent(new w.Event('ended'));   // back to the photo
  assert.ok(w.document.querySelector('#slideshow-media img'), 'the photo is shown again');
  assert.equal(w.document.activeElement, cap, 'still typing');
  assert.equal(cap.value, 'Half-typ');
  press(w, 's', cap);                                      // ... so a letter is text, not a shortcut
  await sleep(300);
  assert.equal(thumbs(w)[1].classList.contains('is-fav'), false);
});

test('a caption that could not be saved stays in the box', async () => {
  const { w } = await load(fixture.server, DAY);
  thumbs(w)[1].click();
  writeContent('{"broken": ');
  try {
    const cap = byId(w, 'edit-caption');
    cap.value = 'Not lost';
    cap.dispatchEvent(new w.Event('change'));
    await sleep(500);
    assert.ok(byId(w, 'toast').classList.contains('error'));
    assert.equal(cap.value, 'Not lost');
  } finally {
    writeContent(fixture.initialContent);
  }
});

test('Ctrl/Cmd/Alt shortcuts in the viewer are left to the browser', async () => {
  const { w } = await load(fixture.server, DAY);
  thumbs(w)[1].click();                                  // a2: not a favorite
  const key = (k, mod) => w.document.dispatchEvent(
    new w.KeyboardEvent('keydown', { key: k, [mod]: true, bubbles: true, cancelable: true }));
  key('s', 'ctrlKey');                                   // "save page" must not star the photo
  key('s', 'metaKey');
  key('ArrowRight', 'altKey');
  key('i', 'metaKey');
  await sleep(400);
  assert.equal(w.eval('currentSlide'), 1);
  assert.equal(w.eval('infoPanelOpen'), false);
  assert.equal(thumbs(w)[1].classList.contains('is-fav'), false);
  assert.equal(readContent().photos['a2.jpg']?.favorite, undefined);
});

test('a visitor preview has no controls and no hidden photo', async () => {
  const { w } = await load(fixture.server, DAY + '?preview=1');
  assert.equal(byId(w, 'edit-caption'), null);
  assert.equal(byId(w, 'day-edit'), null);
  assert.equal(thumbs(w).length, 2);
});
