// The Cinema, Journal and Scrapbook templates, on the static site built with --template cinema.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { fixture, load } = require('./lib');

test('the Cinema homepage shows its days even when the map library cannot load', async () => {
  // no internet, or a blocked CDN: Leaflet never arrives
  const { w, d } = await load(fixture.cinemaSite, '/index.html', { block: ['leaflet'] });
  assert.equal(typeof w.L, 'undefined');
  const scenes = Array.from(d.querySelectorAll('.cn-scene[data-index]'));
  assert.ok(scenes.length > 1);
  // the content of a scene is invisible until the scene is marked in view
  scenes.forEach((s) => assert.ok(s.classList.contains('in-view'), s.id + ' stays hidden'));
  assert.ok(d.getElementById('cn-mini').hidden);   // the map's corner is not left empty
});

test('the Cinema homepage with its map works as before', async () => {
  const { w, d } = await load(fixture.cinemaSite, '/index.html');
  if (typeof w.L === 'undefined') return;           // Leaflet is not installed
  assert.ok(!d.getElementById('cn-mini').hidden);
  assert.ok(d.querySelector('#overview-map.leaflet-container'));
  d.querySelectorAll('.cn-scene[data-index]').forEach((s) => assert.ok(s.classList.contains('in-view')));
});
