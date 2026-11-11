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
  const credit = info.querySelector('.leaflet-control-attribution');
  assert.ok(credit, 'the small map credits its tiles too');
  assert.match(credit.textContent, /OpenStreetMap/);
  assert.doesNotMatch(credit.textContent, /Leaflet/, 'compact: no Leaflet prefix');
});

test('the OpenStreetMap credit links to its copyright page', { skip }, async () => {
  const { w } = await load(fixture.server, '/day/2026-05-01?preview=1');
  const link = w.document.querySelector('#day-map .leaflet-control-attribution a[href="https://www.openstreetmap.org/copyright"]');
  assert.ok(link);
  assert.equal(link.textContent, 'OpenStreetMap');
});

test('a credit set with --tile-attribution is shown as plain text', { skip }, async () => {
  const { w } = await load(fixture.server, '/day/2026-05-01?preview=1');
  w.__div = w.document.createElement('div');
  w.document.body.appendChild(w.__div);
  w.eval(`window.TILE_ATTRIBUTION = '<b>Acme</b> tiles & <img src=x onerror="window.__xss=1">';
          addBaseMap(L.map(window.__div, {center: [50, 4], zoom: 5}));`);
  await sleep(50);
  const credit = w.__div.querySelector('.leaflet-control-attribution');
  assert.ok(credit.textContent.includes('<b>Acme</b> tiles & <img src=x onerror="window.__xss=1">'));
  assert.equal(credit.querySelector('b, img'), null, 'no markup was made from it');
  assert.equal(w.__xss, undefined);
});

test('library map tooltips show trip titles as text, never as markup', { skip }, async () => {
  const { w } = await load(fixture.server, '/day/2026-05-01?preview=1');
  const div = w.document.createElement('div');
  div.id = 'library-test-map';
  w.document.body.appendChild(div);
  w.eval(`window.__tips = [];
          const bind = L.Layer.prototype.bindTooltip;
          L.Layer.prototype.bindTooltip = function(content, options){ window.__tips.push(content); return bind.call(this, content, options); };
          initLibraryMap('library-test-map', [{title: '<img src=x onerror="window.__xss=1">Italy', dates: 'May 2026',
                                               href: '#', color: '#f00', points: [[45, 9], [44, 11]]}]);`);
  const tips = w.eval('window.__tips');
  assert.equal(tips.length, 2, 'the line and its start point');
  for (const tip of tips) {
    assert.equal(typeof tip, 'object', 'a DOM node, not an HTML string');
    assert.equal(tip.textContent, '<img src=x onerror="window.__xss=1">Italy \u2014 May 2026');
    assert.equal(tip.children.length, 0);
  }
  await sleep(50);
  assert.equal(w.__xss, undefined);
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

test('a day with a GPS track draws it under the photo points; other days have none', { skip }, async () => {
  const track = (w, el) => w.document.querySelectorAll(`#${el} .leaflet-overlay-pane path[stroke="#4FA5A0"]`);
  const day1 = await load(fixture.server, '/day/2026-05-01?preview=1');
  assert.equal(track(day1.w, 'day-map').length, 1);
  assert.deepEqual(realErrors(day1.errors), []);
  const day2 = await load(fixture.server, '/day/2026-05-02?preview=1');
  assert.equal(track(day2.w, 'day-map').length, 0);
});

test('the overview map draws each day\'s track in that day\'s colour', { skip }, async () => {
  const { w, d, errors } = await load(fixture.server, '/?preview=1');
  const html = d.documentElement.outerHTML;
  const days = JSON.parse(html.match(/initOverviewMap\('overview-map', (.*)\); }/)[1]);
  const withTrack = days.filter((day) => day.track.length);
  assert.equal(withTrack.length, 1);
  const lines = w.document.querySelectorAll(`#overview-map .leaflet-overlay-pane path[stroke="${withTrack[0].color}"][stroke-opacity="0.7"]`);
  assert.equal(lines.length, 1);
  assert.deepEqual(realErrors(errors), []);
});

test('the Details panel names the place', async () => {
  const { w } = await load(fixture.server, '/day/2026-05-01?preview=1');
  thumbs(w)[0].click();                                  // a1 has GPS
  press(w, 'i');
  const rows = [...w.document.querySelectorAll('#info-rows .info-row')].map((r) => r.textContent);
  const place = rows.find((r) => r.startsWith('Place'));
  assert.ok(place && place.length > 'Place'.length + 2, rows.join(' | '));
});

test('the library map draws each trip and opens it on a click', { skip }, async () => {
  const { w, errors } = await load(fixture.server, '/?preview=1');
  w.eval('window.__went = []; navigateTo = (h) => __went.push(h);');
  const div = w.document.createElement('div');
  div.id = 'library-test-map';
  w.document.body.appendChild(div);
  w.eval(`initLibraryMap('library-test-map', [
    {title: 'Belgium', href: '/belgium/', color: '#ff0000', dates: '1 May 2026', points: [[50, 4], [51, 4]]},
    {title: 'One stop', href: '/one/', color: '#00ff00', dates: '2 May 2026', points: [[52, 5]]},
    {title: 'No GPS', href: '/none/', color: '#0000ff', dates: '3 May 2026', points: []}])`);
  assert.equal(div.querySelectorAll('.leaflet-overlay-pane path[stroke="#ff0000"]').length, 2, 'line + start');
  assert.equal(div.querySelectorAll('.leaflet-overlay-pane path[stroke="#00ff00"]').length, 1, 'just a point');
  assert.equal(div.querySelectorAll('.leaflet-overlay-pane path[stroke="#0000ff"]').length, 0);
  div.querySelector('.leaflet-overlay-pane path[stroke="#ff0000"]').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  assert.deepEqual(JSON.parse(w.eval('JSON.stringify(__went)')), ['/belgium/']);
  assert.deepEqual(realErrors(errors), []);
});

test('a library whose trips are all in one spot is not zoomed in to a single street', { skip }, async () => {
  const { w } = await load(fixture.server, '/?preview=1');
  const div = w.document.createElement('div');
  div.id = 'library-one-spot';
  w.document.body.appendChild(div);
  w.eval(`window.__lm = null; const orig = L.map; L.map = (...a) => (window.__lm = orig(...a));
          initLibraryMap('library-one-spot', [{title: 'Here', href: '/h/', color: '#123456', dates: '', points: [[50, 4]]}]);
          L.map = orig;`);
  assert.ok(w.eval('window.__lm.getZoom()') <= 9, 'zoom ' + w.eval('window.__lm.getZoom()'));
});
