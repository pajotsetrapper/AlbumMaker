/* Scrapbook template (--template scrapbook): the maps.
 * Added after script.js, whose helpers (addBaseMap, textNode, escapeHtml,
 * openSlideshow, ...) it uses. Days are red push-pins joined by a hand-drawn
 * looking dashed route; hovering a page lights its pin, and a pin scrolls to
 * its page.
 * AlbumMaker - Copyright (c) 2026 Pieter Remi Coppens
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 */

const SB_RED = '#c4452d';
const SB_INK = '#6b5a4a';

function sbReducedMotion(){
  return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

function sbZoom(map){
  L.control.zoom({position: 'bottomright'}).addTo(map);
}

// The first point of a day: where its pin goes.
function sbAnchor(day){
  if(day.route && day.route.length) return day.route[0];
  const stretch = (day.track || [])[0];
  return stretch && stretch.length ? stretch[0] : null;
}

function sbPin(label, extra){
  return L.divIcon({className: 'sb-pin' + (extra ? ' ' + extra : ''), html: '<span>' + escapeHtml(label) + '</span>',
                    iconSize: [32, 32], iconAnchor: [16, 16]});
}

function initScrapbookOverviewMap(elId, days){
  const map = L.map(elId, {scrollWheelZoom: false, zoomControl: false});
  sbZoom(map);
  addBaseMap(map, {className: 'sb-tiles'});

  const all = [], joined = [], pins = {};
  days.forEach(day => {
    (day.track || []).filter(s => s.length > 1).forEach(s => {
      L.polyline(s, {color: SB_RED, weight: 3, opacity: .85, interactive: false}).addTo(map);
      s.forEach(p => all.push(p));
    });
    (day.route || []).forEach(p => all.push(p));
    const a = sbAnchor(day);
    if(a) joined.push(a);
  });
  splitLongHops(joined).forEach(line => {      // (no line across the world between two days)
    L.polyline(line, {color: SB_INK, weight: 3, opacity: .85, dashArray: '9 9', lineCap: 'round', interactive: false}).addTo(map);
  });
  const pages = Array.prototype.slice.call(document.querySelectorAll('.sb-page'));
  function light(i, on){
    const pin = pins[i];
    if(pin && pin.getElement()) pin.getElement().classList.toggle('is-hover', on);
  }
  days.forEach((day, i) => {
    const a = sbAnchor(day);
    if(!a) return;
    const pin = L.marker(a, {icon: sbPin(day.day_number ? String(day.day_number) : '?', day.is_unknown ? 'is-unknown' : ''),
                             keyboard: false, riseOnHover: true}).addTo(map);
    pin.bindTooltip(textNode(day.title || day.label), {direction: 'top', offset: [0, -14]});
    pin.on('click', () => {
      const page = pages[i];
      if(!page) return;
      page.scrollIntoView({behavior: sbReducedMotion() ? 'auto' : 'smooth', block: 'center'});
      page.classList.add('is-active');
      setTimeout(() => page.classList.remove('is-active'), 1600);
    });
    pins[i] = pin;
  });
  pages.forEach((page, i) => {
    page.addEventListener('mouseenter', () => light(i, true));
    page.addEventListener('mouseleave', () => light(i, false));
    page.addEventListener('focusin', () => light(i, true));
    page.addEventListener('focusout', () => light(i, false));
  });
  if(all.length) map.fitBounds(all, {padding: [48, 48], maxZoom: 13});
  else map.setView([20, 0], 2);
}

function initScrapbookDayMap(elId, route, thumbs, slideIndices, track){
  const map = L.map(elId, {scrollWheelZoom: false, zoomControl: false});
  sbZoom(map);
  addBaseMap(map, {className: 'sb-tiles'});
  const all = [];
  (track || []).filter(s => s.length > 1).forEach(s => {
    L.polyline(s, {color: SB_RED, weight: 4, opacity: .9, lineCap: 'round', interactive: false}).addTo(map);
    s.forEach(p => all.push(p));
  });
  if(!all.length){
    splitLongHops(route).forEach(line => L.polyline(line, {color: SB_INK, weight: 3, opacity: .85, dashArray: '8 8', lineCap: 'round', interactive: false}).addTo(map));
  }
  route.forEach((p, i) => {
    all.push(p);
    const marker = L.marker(p, {icon: L.divIcon({className: 'sb-dot', html: '<span></span>', iconSize: [18, 18], iconAnchor: [9, 9]}),
                                keyboard: false}).addTo(map);
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
