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

// What jsdom lacks and the sharper-thumbnail logic needs: element sizes, image sizes, the screen's
// pixel density, and "which tiles are near the viewport". Each is a function the test supplies:
//   box(el) -> [width, height] in CSS pixels      natural(img) -> [w, h] of a loaded <img>
//   sizes(url) -> [w, h] of an image being loaded  failHi(url) -> true to make a download fail
//   visible(el) -> whether the tile is near the viewport    noObserver -> no IntersectionObserver
// They can be changed after the page has loaded through w.__box, w.__natural and so on.
function installLayoutStubs(w, stubs) {
  if (stubs.dpr) Object.defineProperty(w, 'devicePixelRatio', { value: stubs.dpr, configurable: true });
  w.__box = stubs.box || null;
  w.__natural = stubs.natural || null;
  w.__sizes = stubs.sizes || null;
  w.__failHi = stubs.failHi || null;
  w.__visible = stubs.visible || null;
  w.__downloads = [];
  w.Element.prototype.getBoundingClientRect = function () {
    const [width, height] = (w.__box && w.__box(this)) || [0, 0];
    return { width, height, top: 0, left: 0, right: width, bottom: height, x: 0, y: 0 };
  };
  const image = w.HTMLImageElement.prototype;
  Object.defineProperty(image, 'naturalWidth', { configurable: true, get() { const n = w.__natural && w.__natural(this); return n ? n[0] : 0; } });
  Object.defineProperty(image, 'naturalHeight', { configurable: true, get() { const n = w.__natural && w.__natural(this); return n ? n[1] : 0; } });
  Object.defineProperty(image, 'complete', { configurable: true, get() { return true; } });
  w.Image = class {
    constructor() { this.onload = null; this.onerror = null; this.naturalWidth = 0; this.naturalHeight = 0; }
    get src() { return this._src; }
    set src(value) {
      this._src = value;
      w.__downloads.push(value);
      setTimeout(() => {
        if (w.__failHi && w.__failHi(value)) { if (this.onerror) this.onerror(); return; }
        const size = (w.__sizes && w.__sizes(value, w)) || [800, 533];   // (the window is passed in: the page may not be ready yet)
        this.naturalWidth = size[0];
        this.naturalHeight = size[1];
        if (this.onload) this.onload();
      }, 0);
    }
  };
  if (!stubs.noObserver) {
    w.IntersectionObserver = class {
      constructor(callback) { this.callback = callback; }
      observe(el) {
        const near = w.__visible ? w.__visible(el) : true;
        setTimeout(() => this.callback([{ isIntersecting: near, target: el }]), 0);
      }
      unobserve() {}
      disconnect() {}
    };
  }
}

// Loads a page and lets its scripts run. jsdom has no layout engine and no SVG
// measuring, so the few things Leaflet needs from a real browser are supplied.
async function load(base, path, { settle = 900, storage = null, breakStorage = false, stubs = null } = {}) {
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
      if (stubs) installLayoutStubs(w, stubs);
      // `storage` seeds what the browser already remembers; `breakStorage` imitates private browsing
      if (breakStorage) Object.defineProperty(w, 'localStorage', { get() { throw new w.Error('storage is blocked'); } });
      else if (storage) for (const [k, v] of Object.entries(storage)) w.localStorage.setItem(k, v);
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
  // cancelable, like a real key press: the page's handlers rely on preventDefault()
  (target || w.document).dispatchEvent(new w.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
}

const thumbs = (w) => [...w.document.querySelectorAll('.thumb')];
const isOpen = (w) => w.document.getElementById('slideshow').classList.contains('open');
const readContent = () => JSON.parse(fs.readFileSync(fixture.contentFile, 'utf8'));
const writeContent = (obj) => fs.writeFileSync(fixture.contentFile, typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2));

module.exports = { fixture, sleep, load, realErrors, press, thumbs, isOpen, readContent, writeContent };
