// Shared helpers for the viewer tests. They load the real pages and the real
// script.js into jsdom (a browser without a screen) and drive them like a user.
const { JSDOM, ResourceLoader } = require('jsdom');
const fs = require('fs');
const { after } = require('node:test');

const fixture = JSON.parse(process.env.TRIP_FIXTURE || '{}');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Every page opened is closed when the file's tests finish: autoplay and polling timers
// inside a window would otherwise keep Node running forever.
const opened = [];
after(() => { opened.forEach((w) => { try { w.close(); } catch (e) { /* already closed */ } }); });

// Only the test server's own files are loaded; anything else (a CDN) is "unreachable".
class Loader extends ResourceLoader {
  constructor(base) { super(); this.base = base; }
  fetch(url, options) {
    return url.startsWith(this.base) ? super.fetch(url, options) : Promise.resolve(Buffer.from(''));
  }
}

// Loads a page and lets its scripts run. jsdom has no layout engine and no SVG
// measuring, so the few things Leaflet needs from a real browser are supplied.
async function load(base, path, { settle = 900 } = {}) {
  const errors = [];
  const dom = await JSDOM.fromURL(base + path, {
    runScripts: 'dangerously',
    resources: new Loader(base),
    pretendToBeVisual: true,
    beforeParse(w) {
      Object.defineProperty(w.HTMLElement.prototype, 'clientWidth', { get() { return 800; } });
      Object.defineProperty(w.HTMLElement.prototype, 'clientHeight', { get() { return 400; } });
      w.SVGSVGElement.prototype.createSVGRect = () => ({});
      w.fetch = (u, o) => fetch(new URL(u, base), o);
      w.addEventListener('error', (e) => errors.push(String(e.message)));
      const original = w.console.error;
      w.console.error = (...a) => errors.push(a.map(String).join(' '));
    },
  });
  opened.push(dom.window);
  await sleep(settle);
  return { w: dom.window, errors, d: dom.window.document };
}

// Errors jsdom itself raises about features it doesn't have (not the page's fault).
const realErrors = (errors) => errors.filter((e) => !/Not implemented|Could not load|ECONNREFUSED/.test(e));

function press(w, key, target) {
  (target || w.document).dispatchEvent(new w.KeyboardEvent('keydown', { key, bubbles: true }));
}

const thumbs = (w) => [...w.document.querySelectorAll('.thumb')];
const isOpen = (w) => w.document.getElementById('slideshow').classList.contains('open');
const readContent = () => JSON.parse(fs.readFileSync(fixture.contentFile, 'utf8'));
const writeContent = (obj) => fs.writeFileSync(fixture.contentFile, typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2));

module.exports = { fixture, sleep, load, realErrors, press, thumbs, isOpen, readContent, writeContent };
