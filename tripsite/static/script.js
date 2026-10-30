// ---------- shared map helpers ----------
function fmtTime(iso){
  const d = new Date(iso);
  return d.toLocaleString(undefined, {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'});
}

// ---------- small shared helpers ----------
function isTyping(el){
  if(!el || !el.tagName) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || !!el.isContentEditable;
}

let toastTimer = null;
function showToast(msg, isError){
  let el = document.getElementById('toast');
  if(!el){
    el = document.createElement('div');
    el.id = 'toast';
    el.className = 'toast';
    el.setAttribute('role', 'status');
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.toggle('error', !!isError);
  el.style.display = 'block';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.style.display = 'none'; }, isError ? 5000 : 2200);
}

// Saves an edit through trip_server.py. Returns the parsed reply, or null
// (after showing why) if it didn't work.
async function apiPost(path, body){
  try {
    const r = await fetch(path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    const data = await r.json().catch(() => ({}));
    if(!r.ok || !data.ok){
      showToast(data.error || ('Could not save (' + r.status + ')'), true);
      return null;
    }
    return data;
  } catch(e){
    showToast('Could not reach the server, so nothing was saved', true);
    return null;
  }
}

// ---------- size controls: thumbnails on day pages, cards on the homepage ----------
// Each .size-toggle names the element it resizes (data-target) and the browser-storage
// key that remembers the choice (data-store), so the two pages choose independently.
const SIZES = ['small', 'medium', 'large', 'xl'];

function applySize(toggle, size){
  const target = document.getElementById(toggle.dataset.target);
  if(!target) return;
  if(SIZES.indexOf(size) < 0) size = 'medium';
  SIZES.forEach(s => target.classList.remove('size-' + s));
  if(size !== 'medium') target.classList.add('size-' + size);
  toggle.querySelectorAll('.size-btn').forEach(b => {
    const on = b.dataset.size === size;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  try { localStorage.setItem(toggle.dataset.store, size); } catch(e) { /* private browsing, etc. */ }
  sharpenVisible();          // a bigger size may want bigger thumbnails
}

function initSizeControls(){
  document.querySelectorAll('.size-toggle').forEach(toggle => {
    let saved = 'medium';
    try { saved = localStorage.getItem(toggle.dataset.store) || 'medium'; } catch(e) {}
    applySize(toggle, saved);
    toggle.querySelectorAll('.size-btn').forEach(b => {
      b.addEventListener('click', () => applySize(toggle, b.dataset.size));
    });
  });
}

// ---------- sharper thumbnails where the screen can use more pixels ----------
// A thumbnail is small (see --thumb-size). When the layout (L or XL, a card that crops a
// portrait photo) or a high-resolution screen would stretch one, the page swaps in the
// larger version named by data-hi. Only tiles near the viewport are checked, so a long day
// doesn't download hundreds of large images at once, and a failed download leaves the
// normal thumbnail in place.
const hiSeen = new Set();        // tiles near the viewport that have a larger version
const HI_SLACK = 1.05;           // a 5% stretch is not worth a bigger download

// How many times the screen would stretch the thumbnail along each side (> 1: it wants more pixels).
function stretchFor(el, nw, nh){
  const r = el.getBoundingClientRect();
  if(!r.width || !r.height || !nw || !nh) return 0;
  // a card's cover fills its box (cropping); a grid tile fits the whole image inside its box
  const cover = el.classList.contains('cover');
  const scale = cover ? Math.max(r.width / nw, r.height / nh) : Math.min(r.width / nw, r.height / nh);
  return scale * (window.devicePixelRatio || 1);
}

// The normal thumbnail's own size, measured once (before it is ever replaced).
function baseDims(el, done){
  if(el.dataset.bw){ done(+el.dataset.bw, +el.dataset.bh); return; }
  const finish = (w, h) => {
    if(!w || !h) return;
    el.dataset.bw = w;
    el.dataset.bh = h;
    done(w, h);
  };
  if(el.tagName === 'IMG'){
    if(el.complete && el.naturalWidth) finish(el.naturalWidth, el.naturalHeight);
    else el.addEventListener('load', () => finish(el.naturalWidth, el.naturalHeight), {once: true});
  } else {
    const probe = new Image();
    probe.onload = () => finish(probe.naturalWidth, probe.naturalHeight);
    probe.src = el.dataset.lo;
  }
}

function sharpen(el){
  if(!el.dataset.hi || el.dataset.tier === 'hi') return;
  baseDims(el, (nw, nh) => {
    if(el.dataset.tier === 'hi' || stretchFor(el, nw, nh) <= HI_SLACK) return;
    el.dataset.tier = 'hi';      // claimed now, so a second check can't start a second download
    const loader = new Image();
    loader.onload = () => {
      if(el.tagName === 'IMG') el.src = el.dataset.hi;
      else el.style.backgroundImage = "url('" + el.dataset.hi + "')";
    };
    loader.onerror = () => { el.dataset.tier = ''; };   // keep the normal thumbnail; try again later
    loader.src = el.dataset.hi;
  });
}

function sharpenVisible(){ hiSeen.forEach(sharpen); }

function initSharpThumbs(){
  const els = document.querySelectorAll('img[data-hi], .cover[data-hi]');
  if(!els.length || !('IntersectionObserver' in window)) return;   // older browsers keep the normal thumbnails
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(e => {
      if(e.isIntersecting){ hiSeen.add(e.target); sharpen(e.target); }
      else hiSeen.delete(e.target);
    });
  }, {rootMargin: '300px'});
  els.forEach(el => observer.observe(el));
  let timer = null;     // resizing, zooming the page or moving to another screen changes what is needed
  window.addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(sharpenVisible, 250); });
}

// ---------- day pages: previous/next day (keys, swipe) and Esc back to the overview ----------
// Every move from one page to another goes through here.
function navigateTo(href){ window.location.href = href; }

function initDayNav(prevHref, nextHref, homeHref){
  function slideshowOpen(){
    const el = document.getElementById('slideshow');
    return !!(el && el.classList.contains('open'));
  }
  function inMap(el){
    return !!(el && el.closest && el.closest('.leaflet-container'));
  }

  // Esc backs out one level at a time: an open map popup, then the title/description
  // form, and only then the page itself.
  function onEscape(e){
    const popupClose = document.querySelector('.leaflet-popup-close-button');
    if(popupClose){ popupClose.click(); return; }
    const editor = document.getElementById('day-editor');
    if(editor && !editor.hidden){
      document.getElementById('day-cancel').click();
      if(isTyping(e.target)) e.target.blur();
      return;
    }
    if(isTyping(e.target)){ e.target.blur(); return; }     // leaving a text box isn't leaving the page
    if(homeHref) navigateTo(homeHref);
  }

  document.addEventListener('keydown', (e) => {
    if(e.defaultPrevented) return;                 // the viewer already used this key press
    if(e.ctrlKey || e.metaKey || e.altKey) return; // e.g. Alt+Left is the browser's own Back
    if(slideshowOpen()) return;                    // the slideshow has its own key handling
    if(e.key === 'Escape'){
      if(!e.repeat) onEscape(e);                   // holding Esc must not carry on past the first press
      return;
    }
    if(isTyping(e.target)) return;                 // typing in the title/caption boxes
    if(inMap(document.activeElement)) return;      // let Leaflet's own keyboard map-panning work
    if(e.key === 'ArrowLeft' && prevHref) navigateTo(prevHref);
    else if(e.key === 'ArrowRight' && nextHref) navigateTo(nextHref);
  });

  let touchStartX = null, touchStartY = null, touchInMap = false;
  document.addEventListener('touchstart', (e) => {
    if(slideshowOpen() || e.touches.length !== 1) return;
    touchInMap = inMap(e.target) || !!(e.target.closest && e.target.closest('.day-editor'));
    touchStartX = e.touches[0].clientX;
    touchStartY = e.touches[0].clientY;
  }, {passive: true});

  document.addEventListener('touchend', (e) => {
    if(slideshowOpen() || touchInMap || touchStartX === null) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - touchStartX;
    const dy = t.clientY - touchStartY;
    touchStartX = null;
    // a map drag can also be a long horizontal touch, so this only fires for
    // touches that didn't originate inside the map (checked above) and are
    // clearly horizontal, deliberate swipes rather than a scroll or a tap
    if(Math.abs(dx) > 80 && Math.abs(dx) > Math.abs(dy) * 1.8){
      if(dx < 0 && nextHref) navigateTo(nextHref);
      else if(dx > 0 && prevHref) navigateTo(prevHref);
    }
  }, {passive: true});
}

// ---------- map basics shared by all three maps ----------
const DEFAULT_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
// a 1x1 transparent image, shown in place of a tile that can't be loaded
const BLANK_TILE = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';
// Rough outline of the continents (Natural Earth, public domain), drawn under
// the tiles: it only shows where no tile is available (no internet, or an
// area that was never cached), so the points still have something to sit on.
// Stored as integer tenths of a degree: [lon, lat, lon, lat, ...] per ring.
const WORLD_LAND = __WORLD_LAND__;
let worldOutlineCache = null;

function worldOutline(){
  if(!worldOutlineCache){
    worldOutlineCache = WORLD_LAND.map(poly => poly.map(flat => {
      const pts = [];
      for(let i = 0; i + 1 < flat.length; i += 2) pts.push([flat[i + 1] / 10, flat[i] / 10]);
      return pts;
    }));
  }
  return worldOutlineCache;
}

// Shows a small notice while NO tile has loaded but some have failed, i.e.
// the map is effectively offline. A few missing tiles among loaded ones are
// not worth announcing.
function watchTiles(map, layer){
  let loaded = 0, failed = 0, badge = null;
  function refresh(){
    const offline = failed > 0 && loaded === 0;
    if(offline && !badge){
      badge = document.createElement('div');
      badge.className = 'map-offline-badge';
      badge.textContent = 'Map tiles unavailable (offline?). Showing a rough outline.';
      map.getContainer().appendChild(badge);
    } else if(!offline && badge){
      badge.remove();
      badge = null;
    }
  }
  layer.on('tileload', () => { loaded++; refresh(); });
  layer.on('tileerror', () => { failed++; refresh(); });
}

function addBaseMap(map, options){
  const pane = map.createPane('outline');
  pane.style.zIndex = 150;   // under the tiles (200), which hide it wherever they load
  worldOutline().forEach(poly => {
    L.polygon(poly, {pane: 'outline', interactive: false, color: 'rgba(237,230,214,0.22)',
                     weight: 1, fillColor: '#1b2a45', fillOpacity: 1}).addTo(map);
  });
  const layer = L.tileLayer(window.TILE_URL || DEFAULT_TILE_URL, Object.assign({
    maxZoom: 19, className: 'dark-tiles', errorTileUrl: BLANK_TILE
  }, options || {})).addTo(map);
  watchTiles(map, layer);
  return layer;
}

function initOverviewMap(elId, days){
  const map = L.map(elId, {scrollWheelZoom:false});
  addBaseMap(map, {attribution: '&copy; OpenStreetMap contributors'});

  const allPts = [];
  days.forEach(day => {
    // the way travelled (GPX track), under the photo points
    (day.track || []).forEach(stretch => {
      L.polyline(stretch, {color: day.color, weight: 2, opacity: 0.7, interactive: false}).addTo(map);
      stretch.forEach(p => allPts.push(p));
    });
    day.route.forEach(p => {
      allPts.push(p);
      L.circleMarker(p, {radius:4, color: day.color, fillColor: day.color, fillOpacity:1, weight:1})
        .addTo(map)
        .bindTooltip(day.label, {sticky:true})
        .on('click', () => { navigateTo(day.href); });
    });
  });
  if(allPts.length){
    map.fitBounds(allPts, {padding:[30,30]});
  } else {
    map.setView([20,0], 2);
  }
}

function initDayMap(elId, route, thumbs, slideIndices, track){
  const map = L.map(elId, {scrollWheelZoom:false});
  addBaseMap(map, {attribution: '&copy; OpenStreetMap contributors'});

  // the way travelled (GPX track), under the photo markers
  const trackPts = [];
  (track || []).forEach(stretch => {
    L.polyline(stretch, {color: '#4FA5A0', weight: 3, opacity: 0.8, interactive: false}).addTo(map);
    stretch.forEach(p => trackPts.push(p));
  });

  if(route.length || trackPts.length){
    route.forEach((p, i) => {
      const marker = L.circleMarker(p, {radius:6, color:'#E3A857', fillColor:'#16233B', fillOpacity:1, weight:2}).addTo(map);
      if(thumbs[i]){
        // built as DOM nodes rather than an HTML string, so nothing in the
        // URL can ever be interpreted as markup
        const popup = document.createElement('div');
        const img = document.createElement('img');
        img.src = thumbs[i];
        img.className = 'map-popup-thumb';
        img.alt = '';
        img.addEventListener('click', () => openSlideshow(slideIndices[i]));
        const hint = document.createElement('div');
        hint.className = 'map-popup-hint';
        hint.textContent = 'Click photo to view full-size';
        popup.append(img, hint);
        marker.bindPopup(popup);
      }
    });
    map.fitBounds(route.concat(trackPts), {padding:[30,30]});
  } else {
    map.setView([20,0], 2);
  }
}

// ---------- slideshow ----------
let SLIDES = [];
let currentSlide = 0;
let infoPanelOpen = false;
let infoMap = null;
let infoMarker = null;
let EDITABLE = false;     // true only when trip_server.py says this viewer may edit
let DAY_KEY = '';
let FAV_ONLY = false;
let lastFocus = null;
let playing = false;      // slideshow autoplay
let playTimer = null;
let playMs = 5000;
let liveActive = false;   // a Live Photo clip is playing in place of the photo
let ignoreNextPop = false;
const PLAY_SPEEDS = [3000, 5000, 8000, 12000];

const INFO_LABELS = {
  filename: 'File', dimensions: 'Dimensions', filesize: 'File size',
  camera: 'Camera', lens: 'Lens', aperture: 'Aperture', shutter: 'Shutter',
  iso: 'ISO', focal_length: 'Focal length', codec: 'Codec', fps: 'Frame rate',
  duration: 'Duration', place: 'Place', gps_display: 'Location', altitude: 'Altitude',
  date_note: 'Date'
};
const INFO_ORDER = ['filename','date_note','dimensions','filesize','camera','lens','aperture','shutter',
                    'iso','focal_length','codec','fps','duration','place','gps_display','altitude'];

function buildSlideStage(item){
  // Returns a DOM element (not an HTML string): URLs and filenames are set
  // as properties, so they can never be interpreted as markup.
  if(item.type === 'video'){
    // muted is required for autoplay to work under browser autoplay
    // policies; poster gives a real starting frame either way, and native
    // controls let people unmute.
    const v = document.createElement('video');
    v.src = item.display;
    v.controls = true;
    v.autoplay = true;
    v.muted = true;
    v.setAttribute('muted', '');
    v.playsInline = true;
    v.setAttribute('playsinline', '');
    if(item.poster) v.poster = item.poster;
    return v;
  }
  // always the browser-safe display version (item.display), even for HEIC;
  // the true original is available via the download link where offered.
  const img = document.createElement('img');
  img.src = item.display;
  img.alt = '';
  return img;
}

// ---------- zoom / pan (images only) ----------
let ZOOM = {scale: 1, x: 0, y: 0};
const ZOOM_MIN = 1, ZOOM_MAX = 5;

function resetZoom(){
  ZOOM = {scale: 1, x: 0, y: 0};
  const img = document.querySelector('#slideshow-media img');
  if(img) img.style.transform = '';
  document.querySelectorAll('.slideshow-nav').forEach(el => el.style.pointerEvents = '');
}

function applyZoomTransform(){
  const img = document.querySelector('#slideshow-media img');
  const stage = document.getElementById('slideshow-stage');
  if(!img || !stage) return;

  // apply scale alone first so we can measure the actual on-screen size at
  // this zoom level, then clamp the pan so the image can't be dragged
  // further than its own edge reaching the edge of the viewport
  img.style.transform = `scale(${ZOOM.scale})`;
  const imgRect = img.getBoundingClientRect();
  const stageRect = stage.getBoundingClientRect();
  const maxX = Math.max(0, (imgRect.width - stageRect.width) / 2);
  const maxY = Math.max(0, (imgRect.height - stageRect.height) / 2);
  ZOOM.x = Math.max(-maxX, Math.min(maxX, ZOOM.x));
  ZOOM.y = Math.max(-maxY, Math.min(maxY, ZOOM.y));
  img.style.transform = `translate(${ZOOM.x}px, ${ZOOM.y}px) scale(${ZOOM.scale})`;
  img.classList.toggle('zoomed', ZOOM.scale > 1.01);

  // once zoomed in, dragging near the screen edge should pan the photo, not
  // jump to the next/previous one — the nav click-zones step aside while zoomed
  document.querySelectorAll('.slideshow-nav').forEach(el => {
    el.style.pointerEvents = ZOOM.scale > 1.01 ? 'none' : '';
  });
}

function zoomBy(delta){
  if(!document.querySelector('#slideshow-media img')) return; // videos aren't zoomable
  ZOOM.scale = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, ZOOM.scale + delta));
  if(ZOOM.scale <= 1.01){ ZOOM.scale = 1; ZOOM.x = 0; ZOOM.y = 0; }
  applyZoomTransform();
}

function toggleZoomAt(){
  if(!document.querySelector('#slideshow-media img')) return;
  if(ZOOM.scale > 1){
    ZOOM = {scale: 1, x: 0, y: 0};
  } else {
    ZOOM.scale = 2.5;
  }
  applyZoomTransform();
}

// ---------- links to a photo, and the Back button ----------
// Opening the viewer adds ONE history entry (#p=<key>); moving between photos
// only rewrites that hash. So Back, or a phone's back button, closes the viewer
// instead of leaving the page, and the address always links to the photo shown.
function slideHash(i){
  const s = SLIDES[i];
  return (s && s.key !== undefined) ? '#p=' + encodeURIComponent(String(s.key)) : '';
}

function slideIndexFromHash(){
  const m = /^#p=(.+)$/.exec(location.hash);
  if(!m) return -1;
  let key;
  try { key = decodeURIComponent(m[1]); } catch(e){ return -1; }
  return SLIDES.findIndex(s => String(s.key) === key);
}

// A link like day.html#p=IMG_0412.jpg opens straight onto that photo. The
// viewer then has no history entry of its own, so closing it only drops the hash.
function openSlideshowFromHash(){
  const idx = slideIndexFromHash();
  if(idx < 0) return;
  try { history.replaceState(null, '', location.href); } catch(e){}
  openSlideshow(idx, true);
}

function onPopState(){
  if(ignoreNextPop){ ignoreNextPop = false; return; }
  const idx = slideIndexFromHash();
  const open = document.getElementById('slideshow').classList.contains('open');
  if(idx >= 0){
    if(!open) openSlideshow(idx, true);
    else if(idx !== currentSlide){ currentSlide = idx; renderSlide(); }
  } else if(open){
    closeSlideshow(true);
  }
}

function openSlideshow(index, fromHistory){
  lastFocus = document.activeElement;
  currentSlide = index;
  const overlay = document.getElementById('slideshow');
  overlay.classList.add('open');
  if(fromHistory !== true && slideHash(index)){
    try { history.pushState({viewer: true}, '', slideHash(index)); } catch(e){}
  }
  renderSlide();
  document.body.style.overflow = 'hidden';
}

function closeSlideshow(fromHistory){
  const overlay = document.getElementById('slideshow');
  if(!overlay.classList.contains('open')) return;
  stopPlaying();
  overlay.classList.remove('open');
  document.getElementById('slideshow-media').innerHTML = '';
  shownSlide = -1;
  document.body.style.overflow = '';
  closeInfoPanel();
  if(document.fullscreenElement) document.exitFullscreen();
  if(lastFocus && lastFocus.focus) lastFocus.focus();
  if(fromHistory !== true && location.hash.indexOf('#p=') === 0){
    if(history.state && history.state.viewer){
      ignoreNextPop = true;   // we opened it, so step back over our own entry
      history.back();
    } else {
      // arrived on a shared link: just drop the hash and stay on the page
      try { history.replaceState(null, '', location.pathname + location.search); } catch(e){}
    }
  }
}

// ---------- autoplay ----------
function autoBlocked(){
  // don't move on while someone is reading details, zoomed in, typing a
  // caption, watching a Live Photo clip, or has the tab in the background
  return infoPanelOpen || liveActive || ZOOM.scale > 1 || document.hidden ||
         isTyping(document.activeElement);
}

function resetProgress(){
  const bar = document.getElementById('slideshow-progress-bar');
  if(!bar) return;
  bar.style.transition = 'none';
  bar.style.width = '0';
}

function startProgress(ms){
  const bar = document.getElementById('slideshow-progress-bar');
  if(!bar) return;
  resetProgress();
  void bar.offsetWidth;   // restart the animation
  bar.style.transition = 'width ' + ms + 'ms linear';
  bar.style.width = '100%';
}

// The one timer (playTimer) that moves the slideshow on: pausing, closing or any
// change of slide cancels it. `videoEvent` is 'ended' or 'error' when a video
// slide's own event calls this (see renderSlide).
function scheduleAdvance(videoEvent){
  clearTimeout(playTimer);
  resetProgress();
  if(!playing || !document.getElementById('slideshow').classList.contains('open')) return;
  const item = SLIDES[currentSlide];
  if(!item) return;
  if(item.type === 'video'){
    if(videoEvent === 'ended'){
      // it just finished: move on now, or as soon as nothing holds the slide
      if(autoBlocked()){ playTimer = setTimeout(() => scheduleAdvance('ended'), 500); return; }
      stepSlide(1);
      return;
    }
    // still playing: it holds the slide until it ends. One that has already
    // ended or failed (Play pressed afterwards) gets the same wait as a photo.
    const v = document.querySelector('#slideshow-media video');
    if(videoEvent !== 'error' && !(v && (v.ended || v.error))) return;
  }
  if(autoBlocked()){ playTimer = setTimeout(scheduleAdvance, 500); return; }
  startProgress(playMs);
  playTimer = setTimeout(() => {
    if(autoBlocked()){ scheduleAdvance(); return; }
    stepSlide(1);
  }, playMs);
}

function setPlaying(on){
  playing = !!on;
  const btn = document.getElementById('slideshow-play');
  const spd = document.getElementById('slideshow-speed');
  if(btn){
    btn.innerHTML = playing ? '&#10074;&#10074; Pause' : '&#9654; Play';
    btn.setAttribute('aria-pressed', playing ? 'true' : 'false');
    btn.classList.toggle('on', playing);
  }
  if(spd){ spd.hidden = !playing; spd.textContent = (playMs / 1000) + 's'; }
  scheduleAdvance();
}

function stopPlaying(){
  if(playing) setPlaying(false);
  else { clearTimeout(playTimer); resetProgress(); }
}

function togglePlaying(){ setPlaying(!playing); }

function cycleSpeed(){
  const i = PLAY_SPEEDS.indexOf(playMs);
  playMs = PLAY_SPEEDS[(i + 1) % PLAY_SPEEDS.length];
  try { localStorage.setItem('tripSlideMs', String(playMs)); } catch(e){}
  document.getElementById('slideshow-speed').textContent = (playMs / 1000) + 's';
  scheduleAdvance();
}

function loadPlayMs(){
  try {
    const v = parseInt(localStorage.getItem('tripSlideMs'), 10);
    if(PLAY_SPEEDS.indexOf(v) >= 0) return v;
  } catch(e){}
  return 5000;
}

function preloadSlide(index){
  // warm the browser cache for the neighboring slide so arrow/swipe
  // navigation feels instant instead of popping in. Images only — video
  // preloading is heavier on bandwidth (especially mobile data) for a
  // benefit people notice far less than instant photo navigation.
  const item = SLIDES[index];
  if(!item || item.type !== 'image') return;
  const img = new Image();
  img.src = item.display;
}

function renderCaption(){
  const item = SLIDES[currentSlide];
  const capEl = document.getElementById('slideshow-caption');
  if(!item || !capEl) return;
  capEl.textContent = '';
  if(item.caption){
    const t = document.createElement('div');
    t.className = 'cap-text';
    t.textContent = item.caption;
    capEl.appendChild(t);
  }
  const meta = document.createElement('div');
  meta.textContent = (item.time ? fmtTime(item.time) + ' — ' : '') + (currentSlide + 1) + ' / ' + SLIDES.length;
  capEl.appendChild(meta);
}

let shownSlide = -1;      // the slide renderSlide last drew (it also redraws the same one)

function renderSlide(){
  if(currentSlide !== shownSlide) leaveCaptionBox();   // not when only redrawing, e.g. after a Live clip
  shownSlide = currentSlide;
  liveActive = false;
  resetZoom();
  const item = SLIDES[currentSlide];
  const mediaEl = document.getElementById('slideshow-media');
  mediaEl.classList.add('loading');
  mediaEl.textContent = '';
  mediaEl.appendChild(buildSlideStage(item));

  const clearLoading = () => mediaEl.classList.remove('loading');
  const mediaTag = mediaEl.querySelector('img, video');
  if(mediaTag){
    if(item.type === 'video'){
      mediaTag.addEventListener('loadeddata', clearLoading, {once: true});
      // not {once: true}: a clip replayed with its controls can end again
      const onEnd = (what) => () => {
        if(mediaTag.isConnected && SLIDES[currentSlide] === item) scheduleAdvance(what);
      };
      mediaTag.addEventListener('ended', onEnd('ended'));
      mediaTag.addEventListener('error', onEnd('error'));
    } else if(mediaTag.complete){
      clearLoading();
    } else {
      mediaTag.addEventListener('load', clearLoading, {once: true});
      mediaTag.addEventListener('error', clearLoading, {once: true});
    }
  } else {
    clearLoading();
  }

  renderCaption();
  if(infoPanelOpen) renderInfoPanel();
  syncEditBar();

  const liveBtn = document.getElementById('slideshow-live');
  if(liveBtn) liveBtn.hidden = !item.live;

  preloadSlide((currentSlide + 1) % SLIDES.length);
  preloadSlide((currentSlide - 1 + SLIDES.length) % SLIDES.length);

  const h = slideHash(currentSlide);
  if(h && document.getElementById('slideshow').classList.contains('open')){
    try { history.replaceState(history.state, '', h); } catch(e){}
  }
  scheduleAdvance();
}

// With "Favorites" switched on, arrows/swipes step through favorites only.
function stepSlide(dir){
  const n = SLIDES.length;
  for(let k = 1; k <= n; k++){
    const i = (((currentSlide + dir * k) % n) + n) % n;
    if(!FAV_ONLY || SLIDES[i].favorite){ currentSlide = i; renderSlide(); return; }
  }
}
function nextSlide(){ stepSlide(1); }
function prevSlide(){ stepSlide(-1); }

function toggleFullscreen(){
  const overlay = document.getElementById('slideshow');
  if(!document.fullscreenElement){
    overlay.requestFullscreen?.().catch(()=>{});
  } else {
    document.exitFullscreen();
  }
}

// ---------- Live Photos ----------
function playLive(){
  // Plays the photo's motion clip in place of the photo, then returns to
  // the photo when it ends. Started from a click or key press, so browsers
  // allow it to play with sound; if they don't, it retries muted.
  const item = SLIDES[currentSlide];
  if(!item || !item.live) return;
  resetZoom();
  liveActive = true;
  clearTimeout(playTimer);
  resetProgress();
  const mediaEl = document.getElementById('slideshow-media');
  const v = document.createElement('video');
  v.src = item.live;
  v.poster = item.display;   // keep showing the photo until the clip starts
  v.playsInline = true;
  v.setAttribute('playsinline', '');
  const slideAtStart = currentSlide;
  const backToPhoto = () => {
    if(currentSlide === slideAtStart && v.isConnected) renderSlide();
  };
  v.addEventListener('ended', backToPhoto, {once: true});
  v.addEventListener('error', backToPhoto, {once: true});
  mediaEl.classList.remove('loading');
  mediaEl.textContent = '';
  mediaEl.appendChild(v);
  v.play().catch(() => { v.muted = true; v.play().catch(backToPhoto); });
}

// ---------- info panel ----------
function fillInfoRows(container, detail){
  // textContent, never innerHTML: EXIF strings and filenames come from the
  // files themselves and must not be able to inject markup or script.
  container.textContent = '';
  INFO_ORDER.filter(key => detail[key]).forEach(key => {
    const row = document.createElement('div');
    row.className = 'info-row';
    const k = document.createElement('span');
    k.className = 'k';
    k.textContent = INFO_LABELS[key];
    const v = document.createElement('span');
    v.className = 'v';
    v.textContent = String(detail[key]);
    row.append(k, v);
    container.appendChild(row);
  });
}

function renderInfoPanel(){
  const item = SLIDES[currentSlide];
  const detail = item.detail || {};
  document.getElementById('info-title').textContent = item.time ? fmtTime(item.time) : 'Details';
  fillInfoRows(document.getElementById('info-rows'), detail);

  const downloadLink = document.getElementById('info-download');
  if(downloadLink){
    if(item.downloadUrl){
      downloadLink.href = item.downloadUrl;
      downloadLink.style.display = '';
    } else {
      downloadLink.style.display = 'none';
    }
  }

  const mapWrap = document.getElementById('info-map-wrap');
  const noGps = document.getElementById('info-no-gps');
  if(item.lat != null && item.lon != null){
    mapWrap.style.display = '';
    noGps.style.display = 'none';
    try {
      if(!infoMap){
        infoMap = L.map('info-map', {zoomControl:false, attributionControl:false, scrollWheelZoom:false});
        addBaseMap(infoMap);
      }
      const latlng = [item.lat, item.lon];
      infoMap.setView(latlng, 15);
      if(infoMarker){
        infoMarker.setLatLng(latlng);
      } else {
        infoMarker = L.circleMarker(latlng, {radius:7, color:'#E3A857', fillColor:'#E3A857', fillOpacity:1, weight:2}).addTo(infoMap);
      }
      // panel slide-in transition + map container becoming visible both need a resize nudge
      setTimeout(() => { if(infoMap) infoMap.invalidateSize(); }, 260);
    } catch(e){
      console.error('Map failed to load (check your internet connection):', e);
      mapWrap.style.display = 'none';
    }
  } else {
    mapWrap.style.display = 'none';
    noGps.style.display = '';
  }
}

function toggleInfoPanel(){
  infoPanelOpen = !infoPanelOpen;
  document.getElementById('info-panel').classList.toggle('open', infoPanelOpen);
  if(infoPanelOpen) renderInfoPanel();
  scheduleAdvance();
}

function closeInfoPanel(){
  infoPanelOpen = false;
  document.getElementById('info-panel').classList.remove('open');
  scheduleAdvance();
}

// ---------- favorites filter, and editing (editing only when trip_server.py allows it) ----------
function updateFavUi(){
  const btn = document.getElementById('fav-filter');
  if(!btn) return;
  const n = SLIDES.filter(s => s.favorite).length;
  btn.textContent = '★ Favorites (' + n + ')';
  btn.classList.toggle('active', FAV_ONLY);
  btn.hidden = (n === 0 && !FAV_ONLY && !EDITABLE);
}

function initFavFilter(){
  const btn = document.getElementById('fav-filter');
  if(btn){
    btn.addEventListener('click', () => {
      FAV_ONLY = !FAV_ONLY;
      const grid = document.getElementById('thumb-grid');
      if(grid) grid.classList.toggle('fav-only', FAV_ONLY);
      updateFavUi();
    });
  }
  updateFavUi();
}

function applyThumbState(i){
  const item = SLIDES[i];
  const el = document.querySelector('[data-slide-index="' + i + '"]');
  if(el && item){
    el.classList.toggle('is-fav', !!item.favorite);
    el.classList.toggle('is-hidden', !!item.hidden);
    const img = el.querySelector('img');
    if(img) img.alt = item.caption || '';
  }
  updateFavUi();
}

function syncEditBar(){
  if(!EDITABLE) return;
  const item = SLIDES[currentSlide];
  const fav = document.getElementById('edit-fav');
  const hide = document.getElementById('edit-hide');
  const cap = document.getElementById('edit-caption');
  if(!item || !fav || !hide || !cap) return;
  fav.classList.toggle('on', !!item.favorite);
  fav.textContent = item.favorite ? '★ Favorite' : '☆ Favorite';
  hide.classList.toggle('on', !!item.hidden);
  hide.textContent = item.hidden ? 'Unhide' : 'Hide';
  if(document.activeElement !== cap) cap.value = item.caption || '';
}

async function saveSlide(fields, idx){
  if(idx === undefined) idx = currentSlide;
  const item = SLIDES[idx];
  if(!item || !item.rel) return null;
  const res = await apiPost('/api/photo', Object.assign({rel: item.rel}, fields));
  if(res){
    Object.assign(item, res.photo);
    applyThumbState(idx);
    if(currentSlide === idx){ renderCaption(); }
  }
  syncEditBar();
  return res;
}

function toggleFavorite(){
  const item = SLIDES[currentSlide];
  if(item) saveSlide({favorite: !item.favorite});
}

async function toggleHidden(){
  const item = SLIDES[currentSlide];
  if(!item) return;
  const res = await saveSlide({hidden: !item.hidden});
  if(res && res.photo.hidden){
    showToast('Hidden from visitors. You still see it here, dimmed.');
  }
}

async function setCover(){
  const item = SLIDES[currentSlide];
  if(!item || !item.rel) return;
  const res = await apiPost('/api/cover', {date: DAY_KEY, rel: item.rel});
  if(res) showToast('Cover photo set for this day');
}

function initEditBar(){
  document.getElementById('edit-fav').addEventListener('click', toggleFavorite);
  document.getElementById('edit-hide').addEventListener('click', toggleHidden);
  document.getElementById('edit-cover').addEventListener('click', setCover);
  const cap = document.getElementById('edit-caption');
  // The viewer can move to another photo while the box has focus (a swipe, the end
  // of a video), so the caption is saved for the photo it was typed for.
  let capIdx = -1;
  cap.addEventListener('focus', () => { capIdx = currentSlide; });
  cap.addEventListener('blur', () => { capIdx = -1; });         // (browsers fire 'change' first)
  // 'change' fires when the box loses focus (or Enter is pressed) with new text
  cap.addEventListener('change', async () => {
    const idx = capIdx >= 0 ? capIdx : currentSlide, typed = cap.value;
    const res = await saveSlide({caption: typed}, idx);
    // not saved (the toast says why): keep what was typed rather than wiping it
    if(!res && currentSlide === idx && document.activeElement !== cap) cap.value = typed;
  });
  cap.addEventListener('keydown', (e) => { if(e.key === 'Enter') cap.blur(); });
}

// Before another photo is shown: finish a caption edit (saving it for its own photo).
function leaveCaptionBox(){
  const cap = document.getElementById('edit-caption');
  if(cap && document.activeElement === cap) cap.blur();
}

function initDayEditor(dateKey){
  const openBtn = document.getElementById('day-edit');
  const box = document.getElementById('day-editor');
  if(!openBtn || !box) return;
  const title = document.getElementById('edit-title');
  const desc = document.getElementById('edit-desc');
  openBtn.addEventListener('click', () => { box.hidden = false; openBtn.hidden = true; title.focus(); });
  // a day without a title is offered one made of its place names
  const suggestion = document.getElementById('use-suggestion');
  if(suggestion) suggestion.addEventListener('click', () => { title.value = suggestion.dataset.title; title.focus(); });
  document.getElementById('day-cancel').addEventListener('click', () => {
    title.value = title.defaultValue;
    desc.value = desc.defaultValue;
    box.hidden = true;
    openBtn.hidden = false;
  });
  document.getElementById('day-save').addEventListener('click', async () => {
    const res = await apiPost('/api/day', {date: dateKey, title: title.value, description: desc.value});
    if(res) location.reload();
  });
}

function initSlideshow(items, opts){
  opts = opts || {};
  SLIDES = items;
  EDITABLE = !!opts.editable;
  DAY_KEY = opts.dayKey || '';
  document.querySelectorAll('[data-slide-index]').forEach(el => {
    el.addEventListener('click', () => openSlideshow(parseInt(el.dataset.slideIndex, 10)));
    el.addEventListener('keydown', (e) => {
      // a clicked thumbnail keeps the focus behind the open viewer: its keys are the viewer's then
      if(document.getElementById('slideshow').classList.contains('open')) return;
      if(e.key === 'Enter' || e.key === ' '){
        e.preventDefault();
        e.stopPropagation();   // the viewer's own Space (play/pause) must not see this key press
        openSlideshow(parseInt(el.dataset.slideIndex, 10));
      }
    });
  });
  initFavFilter();
  if(EDITABLE) initEditBar();
  playMs = loadPlayMs();
  document.getElementById('slideshow-close').addEventListener('click', () => closeSlideshow());
  document.getElementById('slideshow-play').addEventListener('click', togglePlaying);
  document.getElementById('slideshow-speed').addEventListener('click', cycleSpeed);
  document.getElementById('slideshow-next').addEventListener('click', nextSlide);
  document.getElementById('slideshow-prev').addEventListener('click', prevSlide);
  document.getElementById('slideshow-fs').addEventListener('click', toggleFullscreen);
  document.getElementById('slideshow-info').addEventListener('click', toggleInfoPanel);
  document.getElementById('slideshow-live').addEventListener('click', playLive);
  document.getElementById('info-close').addEventListener('click', closeInfoPanel);
  document.getElementById('slideshow').addEventListener('click', (e) => {
    if(e.target.id === 'slideshow') closeSlideshow();
  });
  // after a mouse click, a button must not keep the keyboard focus: otherwise
  // the next Space press would "click" it again instead of play/pausing
  document.getElementById('slideshow').addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('button');
    if(b && e.detail > 0) b.blur();
  });
  window.addEventListener('popstate', onPopState);

  document.addEventListener('keydown', (e) => {
    if(!document.getElementById('slideshow').classList.contains('open')) return;
    if(isTyping(e.target)){ if(e.key === 'Escape'){ e.preventDefault(); e.target.blur(); } return; }
    if(e.ctrlKey || e.metaKey || e.altKey) return;   // the browser's shortcuts (Ctrl+S, Cmd+F, Alt+Left ...)
    if(e.key === ' ' && e.target.tagName !== 'BUTTON'){ e.preventDefault(); togglePlaying(); }
    else if(e.key === 'ArrowRight') nextSlide();
    else if(e.key === 'ArrowLeft') prevSlide();
    else if(e.key === 'Escape'){ e.preventDefault(); infoPanelOpen ? closeInfoPanel() : closeSlideshow(); }
    else if(e.key.toLowerCase() === 'f') toggleFullscreen();
    else if(e.key.toLowerCase() === 'i') toggleInfoPanel();
    else if(e.key.toLowerCase() === 'l') playLive();
    else if(e.key.toLowerCase() === 's' && EDITABLE) toggleFavorite();
    else if(e.key === '+' || e.key === '=') zoomBy(0.4);
    else if(e.key === '-' || e.key === '_') zoomBy(-0.4);
    else if(e.key === '0') resetZoom();
  });

  const stage = document.getElementById('slideshow-stage');
  const mediaEl = document.getElementById('slideshow-media');

  // mouse wheel to zoom in/out, centered on the stage
  stage.addEventListener('wheel', (e) => {
    if(!document.querySelector('#slideshow-media img')) return;
    e.preventDefault();
    zoomBy(e.deltaY < 0 ? 0.25 : -0.25);
  }, {passive: false});

  // double-click to toggle zoom
  stage.addEventListener('dblclick', (e) => {
    if(!document.querySelector('#slideshow-media img')) return;
    e.preventDefault();
    toggleZoomAt();
  });

  // mouse-drag to pan once zoomed in
  let dragging = false, dragStartX = 0, dragStartY = 0, dragOrigX = 0, dragOrigY = 0;
  mediaEl.addEventListener('mousedown', (e) => {
    if(ZOOM.scale <= 1) return;
    dragging = true;
    dragStartX = e.clientX; dragStartY = e.clientY;
    dragOrigX = ZOOM.x; dragOrigY = ZOOM.y;
    e.preventDefault();
  });
  document.addEventListener('mousemove', (e) => {
    if(!dragging) return;
    ZOOM.x = dragOrigX + (e.clientX - dragStartX);
    ZOOM.y = dragOrigY + (e.clientY - dragStartY);
    applyZoomTransform();
  });
  document.addEventListener('mouseup', () => { dragging = false; });

  // touch: one finger swipes to navigate (only while not zoomed in), one
  // finger pans once zoomed in, two fingers pinch to zoom
  let touchStartX = 0, touchStartY = 0, panOrigX = 0, panOrigY = 0;
  let pinchStartDist = 0, pinchStartScale = 1;

  function touchDist(a, b){
    return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
  }

  stage.addEventListener('touchstart', (e) => {
    if(e.touches.length === 2){
      pinchStartDist = touchDist(e.touches[0], e.touches[1]);
      pinchStartScale = ZOOM.scale;
    } else if(e.touches.length === 1){
      const t = e.touches[0];
      touchStartX = t.clientX; touchStartY = t.clientY;
      panOrigX = ZOOM.x; panOrigY = ZOOM.y;
    }
  }, {passive: true});

  stage.addEventListener('touchmove', (e) => {
    if(!document.querySelector('#slideshow-media img')) return;
    if(e.touches.length === 2){
      e.preventDefault();
      const dist = touchDist(e.touches[0], e.touches[1]);
      if(pinchStartDist > 0){
        ZOOM.scale = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, pinchStartScale * (dist / pinchStartDist)));
        applyZoomTransform();
      }
    } else if(e.touches.length === 1 && ZOOM.scale > 1){
      e.preventDefault();
      const t = e.touches[0];
      ZOOM.x = panOrigX + (t.clientX - touchStartX);
      ZOOM.y = panOrigY + (t.clientY - touchStartY);
      applyZoomTransform();
    }
  }, {passive: false});

  stage.addEventListener('touchend', (e) => {
    if(ZOOM.scale > 1 || e.changedTouches.length !== 1) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - touchStartX;
    const dy = t.clientY - touchStartY;
    // require a clearly horizontal, deliberate swipe so it doesn't fire on
    // small taps or on a vertical scroll/drag inside the info panel area
    if(Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5){
      dx < 0 ? nextSlide() : prevSlide();
    }
  }, {passive: true});
}
