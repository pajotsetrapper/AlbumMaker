/* Journal template (--template journal): the maps.
 * Added after script.js, whose helpers (addBaseMap, textNode, escapeHtml,
 * openSlideshow, ...) it uses. The trip map shows one numbered pin per day,
 * joined by the route, and follows the timeline as you scroll; a day's map
 * draws the way travelled with a pin for every photo.
 * AlbumMaker - Copyright (c) 2026 Pieter Remi Coppens
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 */

function jrAccent(){
  const v = getComputedStyle(document.body).getPropertyValue('--jr-accent').trim();
  return v || '#1f6feb';
}

function jrReducedMotion(){
  return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

// below this width the map sits above the timeline instead of beside it
function jrStacked(){
  return !!(window.matchMedia && window.matchMedia('(max-width: 960px)').matches);
}

function jrPin(label, extraClass){
  return L.divIcon({
    className: 'jr-pin' + (extraClass ? ' ' + extraClass : ''),
    html: '<span>' + escapeHtml(label) + '</span>',
    iconSize: [34, 34], iconAnchor: [17, 17]
  });
}

function jrControls(map){
  L.control.zoom({position: 'bottomright'}).addTo(map);
}

// The way travelled, as a line with a white edge, so it stands out from the map.
function jrLine(map, points, accent, dashed){
  const base = {interactive: false, lineCap: 'round', lineJoin: 'round'};
  L.polyline(points, Object.assign({color: '#ffffff', weight: dashed ? 6 : 8, opacity: .95}, base)).addTo(map);
  L.polyline(points, Object.assign({color: accent, weight: dashed ? 3 : 4, opacity: .95,
                                    dashArray: dashed ? '1 7' : null}, base)).addTo(map);
}

// The first point of a day: where its pin goes.
function jrAnchor(day){
  if(day.route && day.route.length) return day.route[0];
  const stretch = (day.track || [])[0];
  return stretch && stretch.length ? stretch[0] : null;
}

// ---------- the trip homepage ----------
function initJournalOverviewMap(elId, days){
  const map = L.map(elId, {scrollWheelZoom: false, zoomControl: false});
  jrControls(map);
  addBaseMap(map, {className: 'jr-tiles'});
  const accent = jrAccent();

  const all = [];
  const pins = {};          // index of the day -> its pin
  const joined = [];        // one point per day, in order: the route between the days
  days.forEach((day, i) => {
    const anchor = jrAnchor(day);
    const stretches = (day.track || []).filter(s => s.length > 1);
    stretches.forEach(s => { jrLine(map, s, accent, false); s.forEach(p => all.push(p)); });
    if(!stretches.length && day.route.length > 1) jrLine(map, day.route, accent, false);
    (day.route || []).forEach(p => all.push(p));
    if(anchor) joined.push(anchor);
  });
  if(joined.length > 1){
    L.polyline(joined, {color: '#7b899c', weight: 2, opacity: .9, dashArray: '2 8', lineCap: 'round', interactive: false}).addTo(map);
  }
  days.forEach((day, i) => {
    const anchor = jrAnchor(day);
    if(!anchor) return;
    const pin = L.marker(anchor, {
      icon: jrPin(day.day_number ? String(day.day_number) : '?', day.is_unknown ? 'is-unknown' : ''),
      keyboard: false, riseOnHover: true
    }).addTo(map);
    pin.bindTooltip(textNode(day.title || day.label), {direction: 'top', offset: [0, -16]});
    pin.on('click', () => focusStep(i));
    pins[i] = pin;
  });
  if(all.length) map.fitBounds(all, {padding: [48, 48], maxZoom: 13});
  else map.setView([20, 0], 2);

  // ---- the timeline and the map follow each other
  const steps = Array.prototype.slice.call(document.querySelectorAll('.jr-step'));
  let active = -1;
  function pinEl(i){ return pins[i] && pins[i].getElement ? pins[i].getElement() : null; }
  function mark(i, cls, on){
    if(steps[i]) steps[i].classList.toggle(cls, on);
    const el = pinEl(i);
    if(el) el.classList.toggle(cls === 'is-active' ? 'is-active' : 'is-hover', on);
    if(cls === 'is-active' && pins[i]) pins[i].setZIndexOffset(on ? 1000 : 0);
  }
  function setActive(i, pan){
    if(i === active) return;
    if(active >= 0) mark(active, 'is-active', false);
    active = i;
    mark(i, 'is-active', true);
    // keep the day in view, but don't make the map jump around for a pin that is already visible
    if(pan && pins[i] && !jrStacked()){
      const at = pins[i].getLatLng();
      if(!map.getBounds().pad(-0.2).contains(at)) map.panTo(at, {animate: !jrReducedMotion(), duration: .7});
    }
  }
  function focusStep(i){
    setActive(i, false);
    if(steps[i]) steps[i].scrollIntoView({behavior: jrReducedMotion() ? 'auto' : 'smooth', block: 'center'});
  }

  steps.forEach((step, i) => {
    step.addEventListener('mouseenter', () => mark(i, 'is-hover', true));
    step.addEventListener('mouseleave', () => mark(i, 'is-hover', false));
    step.addEventListener('focusin', () => mark(i, 'is-hover', true));
    step.addEventListener('focusout', () => mark(i, 'is-hover', false));
  });
  if('IntersectionObserver' in window){
    // the day crossing a band a little above the middle of the screen is the one being read
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(e => {
        if(e.isIntersecting) setActive(steps.indexOf(e.target), true);
      });
    }, {rootMargin: '-35% 0px -55% 0px'});
    steps.forEach(step => observer.observe(step));
  }
}

// ---------- a day ----------
function initJournalDayMap(elId, route, thumbs, slideIndices, track){
  const map = L.map(elId, {scrollWheelZoom: false, zoomControl: false});
  jrControls(map);
  addBaseMap(map, {className: 'jr-tiles'});
  const accent = jrAccent();

  const all = [];
  (track || []).filter(s => s.length > 1).forEach(s => { jrLine(map, s, accent, false); s.forEach(p => all.push(p)); });
  if(!all.length && route.length > 1) jrLine(map, route, accent, true);

  route.forEach((p, i) => {
    all.push(p);
    const marker = L.marker(p, {
      icon: L.divIcon({className: 'jr-photo-pin' + (i === 0 ? ' is-start' : ''), html: '<span></span>',
                       iconSize: [18, 18], iconAnchor: [9, 9]}),
      keyboard: false
    }).addTo(map);
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
  if(all.length) map.fitBounds(all, {padding: [48, 48], maxZoom: 16});
  else map.setView([20, 0], 2);
}
