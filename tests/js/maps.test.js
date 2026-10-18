// The maps, with the real Leaflet library: the offline outline, tiles, notices.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { fixture, sleep, load, realErrors, press, thumbs } = require('./lib');

const skip = fixture.hasLeaflet ? false : 'Leaflet is not installed (run npm install in tests/js)';

test('Leaflet loads from the server\'s own vendor folder', { skip }, async () => {
  const { w } = await load(fixture.server, '/day/2026-05-01?preview=1');
  assert.equal(typeof w.L, 'object');
  assert.equal(w.L.version, '1.9.4');
});

test('the day map shows the continents under the tiles, and the photo points over them', { skip }, async () => {
  const { w, errors } = await load(fixture.server, '/day/2026-05-01?preview=1');
  const map = w.document.getElementById('day-map');
  assert.ok(map.classList.contains('leaflet-container'));
  const landmasses = w.eval('WORLD_LAND.length');
  assert.ok(landmasses > 50);
  assert.equal(map.querySelectorAll('.leaflet-outline-pane path').length, landmasses);
  assert.equal(w.getComputedStyle(map.querySelector('.leaflet-outline-pane')).zIndex, '150');
  assert.equal(w.getComputedStyle(map.querySelector('.leaflet-tile-pane')).zIndex, '200');
  assert.ok(map.querySelectorAll('.leaflet-overlay-pane path').length >= 2, 'photo points');
  assert.deepEqual(realErrors(errors), []);
});

test('tiles are requested from the address the page was configured with', { skip }, async () => {
  const { w } = await load(fixture.server, '/day/2026-05-01?preview=1');
  assert.equal(w.eval('window.TILE_URL'), fixture.tileUrl);
  const src = w.document.querySelector('#day-map .leaflet-tile-pane img').getAttribute('src');
  assert.ok(src.startsWith(fixture.tileUrl.split('{z}')[0]), src);
});

test('the homepage overview map gets the outline too', { skip }, async () => {
  const { w, errors } = await load(fixture.server, '/?preview=1');
  const map = w.document.getElementById('overview-map');
  assert.ok(map.classList.contains('leaflet-container'));
  assert.equal(map.querySelectorAll('.leaflet-outline-pane path').length, w.eval('WORLD_LAND.length'));
  assert.deepEqual(realErrors(errors), []);
});

test('the details-panel map in the viewer gets it as well', { skip }, async () => {
  const { w } = await load(fixture.server, '/day/2026-05-01?preview=1');
  thumbs(w)[0].click();                                  // a1 has GPS
  press(w, 'i');
  await sleep(500);
  const info = w.document.getElementById('info-map');
  assert.ok(info.classList.contains('leaflet-container'));
  assert.equal(info.querySelectorAll('.leaflet-outline-pane path').length, w.eval('WORLD_LAND.length'));
});

test('the "tiles unavailable" notice appears only while no tile has loaded', { skip }, async () => {
  const { w } = await load(fixture.server, '/day/2026-05-01?preview=1');
  const div = w.document.createElement('div');
  w.document.body.appendChild(div);
  w.__div = div;
  w.eval('window.__m = L.map(window.__div, {center: [50, 4], zoom: 5}); window.__layer = addBaseMap(window.__m);');
  const badge = () => div.querySelector('.map-offline-badge');
  assert.equal(badge(), null);
  w.eval("window.__layer.fire('tileerror')");
  assert.ok(badge());
  assert.match(badge().textContent, /unavailable/);
  w.eval("window.__layer.fire('tileload')");
  assert.equal(badge(), null, 'gone as soon as one tile loads');
  w.eval("window.__layer.fire('tileerror'); window.__layer.fire('tileerror')");
  assert.equal(badge(), null, 'a few missing tiles among loaded ones are not announced');
  assert.equal(w.eval('window.__layer.options.errorTileUrl'), w.eval('BLANK_TILE'));
});

test('panning and zooming with the outline raises no errors, even across the date line', { skip }, async () => {
  const { w } = await load(fixture.server, '/day/2026-05-01?preview=1');
  w.__div = w.document.createElement('div');
  w.document.body.appendChild(w.__div);
  w.eval(`
    const m = L.map(window.__div, {center: [0, 0], zoom: 2});
    addBaseMap(m);
    m.setView([48.85, 2.35], 12); m.setView([0, 0], 2); m.setView([-17.7, 178.1], 4); m.setView([65, -170], 3);
  `);
});

test('the outline data covers the world and is cut at the date line', { skip }, async () => {
  const { w } = await load(fixture.server, '/day/2026-05-01?preview=1');
  // No ring may jump across the whole map between neighbouring points (that would draw a band across
  // the world). Antarctica is exempt: its outline legitimately runs along the pole.
  const worst = w.eval(`Math.max(...WORLD_LAND.flatMap(p => p.map(flat => {
    let m = 0;
    for (let i = 2; i < flat.length; i += 2) {
      if (flat[i + 1] < -850 || flat[i - 1] < -850) continue;
      m = Math.max(m, Math.abs(flat[i] - flat[i - 2]));
    }
    return m / 10; })))`);
  assert.ok(worst <= 180, 'largest longitude jump: ' + worst);
});
