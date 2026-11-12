// The Officials page's ward map. Tapping a ward shows who represents it, and
// "Find my ward using my location" finds the ward the visitor is in. Where the
// town's ward file names the voting precincts in each ward, each ward's
// polygons come with their precincts (pipeline/officials.py map_data): the
// map draws the precinct lines faintly, and names the precinct with the ward.
//
// The location never leaves the browser: it's checked against the ward shapes
// already loaded with the page, it isn't stored, and the map doesn't move to
// it (moving would load map images of that spot from the map's server). The
// wards are always listed on the page too, so the map is an extra.
(function () {
  "use strict";
  var node = document.getElementById("ward-map");
  if (!node || !window.L || !window.fetch) return;
  var result = document.getElementById("ward-result");
  // The script's wording, from the page in the page's language (data-strings, written by its template).
  var strings = JSON.parse(node.getAttribute("data-strings") || "{}");
  function t(key, values) {
    return strings[key].replace(/\{(\w+)\}/g, function (m, k) { return values[k]; });
  }
  var layers = {};
  var labels = [];

  fetch(node.getAttribute("data-wards"))
    .then(function (r) { return r.json(); })
    .then(draw)
    .catch(function () {});

  function latLngs(rings) {
    return rings.map(function (ring) {
      return ring.map(function (p) { return [p[1], p[0]]; });
    });
  }

  // The middle of a ward's largest piece, for its label.
  function labelPoint(w) {
    var best = null, bestArea = -1;
    w.polygons.forEach(function (poly) {
      var ring = poly[0], a = 0, x = 0, y = 0;
      for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        var f = ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
        a += f;
        x += (ring[j][0] + ring[i][0]) * f;
        y += (ring[j][1] + ring[i][1]) * f;
      }
      if (Math.abs(a) > bestArea && a !== 0) {
        bestArea = Math.abs(a);
        best = [y / (3 * a), x / (3 * a)];
      }
    });
    return best;
  }

  function draw(wards) {
    if (!wards.length) return;
    node.hidden = false;
    // On touch screens, one finger scrolls the page; pinch to zoom the map.
    var touch = window.matchMedia("(pointer: coarse)").matches;
    var map = L.map(node, { scrollWheelZoom: false, dragging: !touch, tap: false, zoomSnap: 0.25 });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'
    }).addTo(map);
    var color = getComputedStyle(document.documentElement).getPropertyValue("--primary").trim() || "#1e3e80";
    var bounds = L.latLngBounds([]);
    wards.forEach(function (w) {
      // A ward of precincts: each precinct's edge in a thin line, under the ward's outline.
      var fill = L.polygon(w.polygons.map(latLngs), {
        stroke: !!w.precincts, color: color, weight: 1, opacity: 0.35, fillColor: color, fillOpacity: 0.06
      }).addTo(map);
      L.polyline(w.outline.map(function (line) {
        return line.map(function (p) { return [p[1], p[0]]; });
      }), { color: color, weight: 2, opacity: 0.9, interactive: false }).addTo(map);
      var at = labelPoint(w);
      if (at) {
        labels.push(L.tooltip({ permanent: true, direction: "center", className: "ward-label", interactive: false })
          .setLatLng(at).setContent(t("ward", { ward: w.ward })).addTo(map));
      }
      fill.on("click", function (e) { show(w.ward, false, precinctAt(w, e.latlng.lng, e.latlng.lat)); });
      layers[w.ward] = { fill: fill, shape: w };
      bounds.extend(fill.getBounds());
    });
    map.fitBounds(bounds, { padding: [12, 12] });
    // Labels of small wards crowd each other when zoomed out: show only the
    // ones that don't overlap a label already shown, and redo it on zooming.
    function declutter() {
      var shown = [];
      labels.forEach(function (t) {
        var el = t.getElement();
        if (!el) return;
        el.style.visibility = "";
        var box = el.getBoundingClientRect();
        var hit = shown.some(function (o) {
          return box.left < o.right && box.right > o.left && box.top < o.bottom && box.bottom > o.top;
        });
        if (hit) el.style.visibility = "hidden";
        else shown.push(box);
      });
    }
    map.on("zoomend", declutter);
    declutter();
    offerLocation();
  }

  // Marks a ward on the map and in the list.
  function select(ward) {
    Object.keys(layers).forEach(function (k) {
      layers[k].fill.setStyle({ fillOpacity: k === ward ? 0.3 : 0.06 });
    });
    document.querySelectorAll(".ward-list > li").forEach(function (li) {
      li.classList.toggle("is-selected", li.getAttribute("data-ward") === ward);
    });
  }
  // The address box (address.js) found a ward: mark it, as the map's own answers do.
  document.addEventListener("publick:ward", function (e) { select(e.detail.ward); });

  // Shows a ward's members under the map, from the ward list on the page, and names the
  // precinct the point is in, where the town's file has precincts.
  function show(ward, found, precinct) {
    select(ward);
    var item = document.querySelector('.ward-list > li[data-ward="' + ward + '"]');
    result.textContent = "";
    var head = document.createElement("p");
    head.className = "ward-result-head";
    var key = found ? "found" : "ward";
    if (precinct && strings[key + "_precinct"]) key += "_precinct";
    head.textContent = t(key, { ward: ward, precinct: precinct });
    result.appendChild(head);
    if (item) {
      var members = item.querySelector("ul, p");
      if (members) result.appendChild(members.cloneNode(true));
    }
  }

  function message(text) {
    result.textContent = "";
    var p = document.createElement("p");
    p.textContent = text;
    result.appendChild(p);
  }

  // Whether a point (longitude, latitude) is inside a polygon: inside an odd
  // number of its rings, so holes count as outside.
  function inPolygon(poly, x, y) {
    var inside = false;
    poly.forEach(function (ring) {
      for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        var xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
        if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
      }
    });
    return inside;
  }

  function contains(shape, x, y) {
    return shape.polygons.some(function (poly) { return inPolygon(poly, x, y); });
  }

  // The precinct of a ward a point is in, or null where the file names no precincts.
  function precinctAt(shape, x, y) {
    if (!shape.precincts) return null;
    for (var i = 0; i < shape.polygons.length; i++) {
      if (inPolygon(shape.polygons[i], x, y)) return shape.precincts[i] || null;
    }
    return null;
  }

  function offerLocation() {
    var box = document.getElementById("locate");
    var button = document.getElementById("locate-button");
    if (!box || !button || !navigator.geolocation) return;
    box.hidden = false;
    var unavailable = t("unavailable");
    button.addEventListener("click", function () {
      button.disabled = true;
      message(t("finding"));
      navigator.geolocation.getCurrentPosition(function (pos) {
        button.disabled = false;
        var x = pos.coords.longitude, y = pos.coords.latitude;
        var ward = Object.keys(layers).filter(function (k) { return contains(layers[k].shape, x, y); })[0];
        if (ward) show(ward, true, precinctAt(layers[ward].shape, x, y));
        else message(t("outside"));
      }, function () {
        button.disabled = false;
        message(unavailable);
      }, { enableHighAccuracy: false, timeout: 15000, maximumAge: 300000 });
    });
  }
})();
