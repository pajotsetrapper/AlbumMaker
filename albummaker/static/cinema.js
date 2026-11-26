/* Cinema template (--template cinema): scenes that fade in as you reach them,
 * and a mini-map that flies to the day being read. Added after script.js,
 * whose helpers (addBaseMap, textNode, escapeHtml, openSlideshow, ...) it uses.
 * AlbumMaker - Copyright (c) 2026 Pieter Remi Coppens
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 */

const CN_ACCENT = '#ffb347';

function cnReducedMotion(){
  return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

function cnAnchor(day){
  if(day.route && day.route.length) return day.route[0];
  const stretch = (day.track || [])[0];
  return stretch && stretch.length ? stretch[0] : null;
}

function cnDayPoints(day){
  const pts = [];
  (day.track || []).forEach(s => s.forEach(p => pts.push(p)));
  (day.route || []).forEach(p => pts.push(p));
  return pts;
}

// ---------- the trip homepage ----------
// The scenes fade in whether or not the map loads: without Leaflet (no internet, a
// blocked CDN) the days must still be readable, so the map is set up on its own.
function initCinemaOverview(elId, days){
  let show = () => {};
  try { show = cnOverviewMap(elId, days); }
  catch(e){
    console.error('Map failed to load (check your internet connection):', e);
    const mini = document.getElementById('cn-mini');
    if(mini) mini.hidden = true;
  }
  cnScenes(days, i => show(i));
}

// The mini-map; returns show(i), which flies to day i (-1: the whole trip).
function cnOverviewMap(elId, days){
  const mini = document.getElementById('cn-mini');
  const toggle = document.getElementById('cn-mini-toggle');
  const map = L.map(elId, {scrollWheelZoom: false, zoomControl: false, dragging: false, touchZoom: false, doubleClickZoom: false, boxZoom: false, keyboard: false});
  L.control.zoom({position: 'bottomright'}).addTo(map);
  addBaseMap(map);

  const all = [];
  const lines = {}, dots = {};
  days.forEach((day, i) => {
    const group = [];
    (day.track || []).filter(s => s.length > 1).forEach(s => group.push(L.polyline(s, {color: CN_ACCENT, weight: 2, opacity: .4, interactive: false}).addTo(map)));
    if(!group.length) splitLongHops(day.route).forEach(line => group.push(L.polyline(line, {color: CN_ACCENT, weight: 2, opacity: .4, dashArray: '2 6', interactive: false}).addTo(map)));
    lines[i] = group;
    cnDayPoints(day).forEach(p => all.push(p));
    const a = cnAnchor(day);
    if(a){
      const dot = L.circleMarker(a, {radius: 5, color: '#0b0c10', weight: 2, fillColor: CN_ACCENT, fillOpacity: .55}).addTo(map);
      dot.bindTooltip(textNode(day.title || day.label), {direction: 'top'});
      dot.on('click', () => {
        const scene = document.getElementById('step-' + i);
        closeMap();
        if(scene) scene.scrollIntoView({behavior: cnReducedMotion() ? 'auto' : 'smooth'});
      });
      dots[i] = dot;
    }
  });
  const everything = () => { if(all.length) map.fitBounds(all, {padding: [24, 24], maxZoom: 9, animate: !cnReducedMotion()}); else map.setView([20, 0], 1); };
  everything();

  let current = -2;
  function show(i){
    if(i === current) return;
    current = i;
    Object.keys(lines).forEach(k => lines[k].forEach(l => l.setStyle({opacity: +k === i ? 1 : .35, weight: +k === i ? 4 : 2})));
    Object.keys(dots).forEach(k => dots[k].setStyle({radius: +k === i ? 9 : 5, fillOpacity: +k === i ? 1 : .55}));
    const pts = i >= 0 ? cnDayPoints(days[i]) : [];
    const opts = {padding: [30, 30], maxZoom: 12, animate: !cnReducedMotion(), duration: 1.1};
    if(pts.length) map.flyToBounds(pts, opts);
    else everything();
  }

  // ---- the map can be opened up to see (and drag) the whole route
  let veil = null;
  function openMap(){
    mini.classList.add('is-open');
    toggle.setAttribute('aria-expanded', 'true');
    veil = document.createElement('div');
    veil.className = 'cn-mini-veil';
    veil.addEventListener('click', closeMap);
    document.body.appendChild(veil);
    ['dragging', 'touchZoom', 'doubleClickZoom', 'scrollWheelZoom', 'boxZoom'].forEach(h => map[h].enable());
    setTimeout(() => { map.invalidateSize(); everything(); }, 480);
  }
  function closeMap(){
    if(!mini.classList.contains('is-open')) return;
    mini.classList.remove('is-open');
    toggle.setAttribute('aria-expanded', 'false');
    if(veil){ veil.remove(); veil = null; }
    ['dragging', 'touchZoom', 'doubleClickZoom', 'scrollWheelZoom', 'boxZoom'].forEach(h => map[h].disable());
    setTimeout(() => { map.invalidateSize(); const c = current; current = -2; show(c); }, 480);
  }
  toggle.addEventListener('click', () => mini.classList.contains('is-open') ? closeMap() : openMap());
  // Esc closes the enlarged map, and only that: the key press is used up, so it does not
  // also leave the page (initHomeNav, in a library)
  document.addEventListener('keydown', e => {
    if(e.key === 'Escape' && mini.classList.contains('is-open')){ e.preventDefault(); closeMap(); }
  });
  return show;
}

// Scenes fade in as they are reached, and onReading(i) is told which day is being read.
function cnScenes(days, onReading){
  const scenes = Array.prototype.slice.call(document.querySelectorAll('.cn-scene[data-index]'));
  const dotLinks = Array.prototype.slice.call(document.querySelectorAll('.cn-dots a'));
  const count = document.getElementById('cn-count');
  const pad = n => (n < 10 ? '0' : '') + n;
  function reading(i){
    dotLinks.forEach(a => a.classList.toggle('is-active', +a.dataset.i === i));
    if(count) count.textContent = i >= 0 ? pad(i + 1) + ' / ' + pad(days.length) : '';
    onReading(i);
  }
  if(!('IntersectionObserver' in window)){
    scenes.forEach(s => s.classList.add('in-view'));
    return;
  }
  // in-view is set on reaching a scene and cleared once it is fully off screen, so the
  // entrance animation plays every time you come back to a day, not just the first time
  const seen = new IntersectionObserver(entries => entries.forEach(e => {
    if(e.intersectionRatio >= .18) e.target.classList.add('in-view');
    else if(!e.isIntersecting) e.target.classList.remove('in-view');
  }), {threshold: [0, .18]});
  const focus = new IntersectionObserver(entries => entries.forEach(e => { if(e.isIntersecting) reading(+e.target.dataset.index); }),
                                         {threshold: .55});
  scenes.forEach(s => { seen.observe(s); focus.observe(s); });
}

// ---------- a day ----------
function initCinemaDayMap(elId, route, thumbs, slideIndices, track){
  const map = L.map(elId, {scrollWheelZoom: false});
  addBaseMap(map);
  const all = [];
  (track || []).filter(s => s.length > 1).forEach(s => {
    L.polyline(s, {color: CN_ACCENT, weight: 8, opacity: .18, interactive: false}).addTo(map);
    L.polyline(s, {color: CN_ACCENT, weight: 3, opacity: .95, lineCap: 'round', interactive: false}).addTo(map);
    s.forEach(p => all.push(p));
  });
  if(!all.length){
    splitLongHops(route).forEach(line => L.polyline(line, {color: CN_ACCENT, weight: 2, opacity: .8, dashArray: '3 7', interactive: false}).addTo(map));
  }
  route.forEach((p, i) => {
    all.push(p);
    const marker = L.marker(p, {icon: L.divIcon({className: 'cn-dot', html: '<span></span>', iconSize: [16, 16], iconAnchor: [8, 8]}), keyboard: false}).addTo(map);
    if(thumbs[i]){
      // DOM nodes rather than an HTML string, so nothing in the URL can be read as markup
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
  if(all.length) map.fitBounds(all, {padding: [48, 48], maxZoom: 16});
  else map.setView([20, 0], 2);
}
