// The Officials page's address box: the ward a street address is in, and who
// represents it.
//
// The address never leaves the browser. It's looked up in the town's list of
// house number ranges, each already given its ward when the site was built
// (pipeline/fetch_addresses.py), loaded the first time the box is used. A page
// opened as /officials/#address=Main%20St looks that address up: the part after
// "#" isn't sent with the page's request. Reading the address, and finding its
// street, works as pipeline/fetch_addresses.py's parse() and find() do, with
// the words and street types the file gives.
(function () {
  "use strict";
  var root = document.getElementById("address");
  if (!root || !window.fetch) return;
  var form = document.getElementById("address-form");
  var input = document.getElementById("address-q");
  var result = document.getElementById("address-result");
  // The script's wording, from the page in the page's language (data-strings, written by its template).
  var strings = JSON.parse(root.getAttribute("data-strings") || "{}");
  function t(key, values) {
    return strings[key].replace(/\{(\w+)\}/g, function (m, k) { return values[k]; });
  }
  // Words a reader may write after the street: the town and its state.
  var places = JSON.parse(root.getAttribute("data-places") || "[]").concat(["USA", "US", "United States"]);
  var data = null;
  root.hidden = false;

  function load() {
    if (!data) {
      data = fetch(root.getAttribute("data-index"))
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (d) {
          d.keys = {};
          Object.keys(d.streets).forEach(function (name) { d.keys[streetKey(d, name)] = name; });
          return d;
        });
      data.catch(function () { data = null; });
    }
    return data;
  }

  function words(text) {
    return text.replace(/[.,'’]/g, "").replace(/-/g, " ").split(/\s+/).filter(Boolean);
  }

  // "North Main Street" -> "N MAIN ST".
  function streetKey(d, name) {
    return words(name.toUpperCase()).map(function (w) { return d.words[w] || w; }).join(" ");
  }

  // "N MAIN ST" -> "MAIN": without its directions and street type.
  function baseKey(d, key) {
    var w = key.split(" ");
    while (w.length > 1 && d.directions.indexOf(w[w.length - 1]) >= 0) w.pop();
    if (w.length > 1 && d.types.indexOf(w[w.length - 1]) >= 0) w.pop();
    while (w.length > 1 && d.directions.indexOf(w[0]) >= 0) w.shift();
    return w.join(" ");
  }

  // "N MAIN ST" -> "N"; "KILBY ST N" -> "N": the directions before and after a name.
  function directions(d, key) {
    var w = key.split(" ");
    if (w.length < 2) return "";
    return [w[0], w[w.length - 1]].filter(function (x, i, all) {
      return d.directions.indexOf(x) >= 0 && all.indexOf(x) === i;
    }).sort().join(" ");
  }

  function streetType(d, key) {
    var w = key.split(" ");
    return w.length > 1 && d.types.indexOf(w[w.length - 1]) >= 0 ? w[w.length - 1] : "";
  }

  // "12A Main St., Apt 3, Lowell, MA 01852" -> {number: 12, key: "MAIN ST"}.
  function parse(d, text) {
    text = text.toUpperCase().replace(/^[\s,]+|[\s,]+$/g, "").split(",")[0]
      .replace(/\s+(?:(?:APT|APARTMENT|UNIT|STE|SUITE|FL|FLOOR|RM|ROOM)\b|#).*$/, "");
    var number = /^\s*#?(\d+)(?:[A-Z]\b|\s+1\/2|\s*[-–]\s*\d+[A-Z]?)?\s+/.exec(text);
    var w = words(number ? text.slice(number[0].length) : text);
    var tails = places.filter(Boolean).map(function (p) { return p.toUpperCase().split(/\s+/); })
      .sort(function (a, b) { return b.length - a.length; });
    while (w.length) {
      if (/^\d{5}(-\d{4})?$/.test(w[w.length - 1])) { w.pop(); continue; }
      var tail = tails.filter(function (p) {
        return w.length > p.length && w.slice(w.length - p.length).join(" ") === p.join(" ");
      })[0];
      if (!tail) break;
      w.splice(w.length - tail.length, tail.length);
    }
    return { number: number ? parseInt(number[1], 10) : null, key: streetKey(d, w.join(" ")) };
  }

  function distance(a, b) {
    var row = [];
    for (var j = 0; j <= b.length; j++) row.push(j);
    for (var i = 1; i <= a.length; i++) {
      var prev = row[0];
      row[0] = i;
      for (j = 1; j <= b.length; j++) {
        var here = row[j];
        row[j] = Math.min(row[j] + 1, row[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
        prev = here;
      }
    }
    return row[b.length];
  }

  // The street the reader means, or the streets to offer: {name} or {matches}. As typed, or with other
  // directions, is the street; a place name after it is dropped; a different type or spelling is offered.
  function streetFor(d, key) {
    if (d.keys[key]) return { name: d.keys[key] };
    var w = key.split(" "), keys = Object.keys(d.keys);
    for (var drop = 0; drop <= Math.min(3, w.length - 1); drop++) {
      var typed = w.slice(0, w.length - drop).join(" ");
      var dropped = w.slice(w.length - drop);
      if (drop && (dropped.some(function (x) { return d.types.indexOf(x) >= 0 || d.directions.indexOf(x) >= 0; }) ||
                   !d.keys[typed])) continue;
      if (d.keys[typed]) return { name: d.keys[typed] };
      var base = baseKey(d, typed);
      var same = keys.filter(function (k) { return baseKey(d, k) === base; });
      var fits = same.filter(function (k) {
        var ty = streetType(d, typed), dir = directions(d, typed);
        return (!ty || ty === streetType(d, k)) && (!dir || dir === directions(d, k));
      });
      if (fits.length === 1) return { name: d.keys[fits[0]] };
      if (same.length) return { matches: same.map(function (k) { return d.keys[k]; }).sort() };
    }
    base = baseKey(d, key);
    if (base.length < 4) return { matches: [] };
    var near = keys.filter(function (k) { return Math.abs(baseKey(d, k).length - base.length) <= 2; })
      .map(function (k) { return [distance(base, baseKey(d, k)), d.keys[k]]; })
      .filter(function (p) { return p[0] <= (base.length < 7 ? 1 : 2); })
      .sort(function (a, b) { return a[0] - b[0] || (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0); });
    return { matches: near.slice(0, d.matches).map(function (p) { return p[1]; }) };
  }

  function wardsOf(rows) {
    var seen = {};
    rows.forEach(function (r) { [].concat(r[3]).forEach(function (w) { seen[w] = true; }); });
    return Object.keys(seen).sort(function (a, b) {
      var na = /^\d+$/.test(a), nb = /^\d+$/.test(b);
      if (na && nb) return a - b;
      return na ? -1 : nb ? 1 : (a < b ? -1 : a > b ? 1 : 0);
    });
  }

  function el(tag, text, attrs) {
    var node = document.createElement(tag);
    if (text) node.textContent = text;
    for (var k in attrs || {}) node.setAttribute(k, attrs[k]);
    return node;
  }

  function area(ward) { return t("area", { ward: ward }); }

  function list(wards) {
    var names = wards.map(area);
    return names.length === 1 ? names[0] : t("and", { first: names.slice(0, -1).join(", "), last: names[names.length - 1] });
  }

  function say(text) { result.appendChild(el("p", text)); }

  // Who represents a ward, from the ward list on the page, and the members elected citywide.
  function card(ward) {
    var item = document.querySelector('.ward-list > li[data-ward="' + ward + '"]');
    var members = item && item.querySelector("ul, p");
    if (members) result.appendChild(members.cloneNode(true));
    var citywide = document.getElementById("citywide-members");
    if (citywide && citywide.content.querySelector("li")) {
      say(t("citywide"));
      result.appendChild(citywide.content.cloneNode(true));
    }
    mapLink(ward);
    document.dispatchEvent(new CustomEvent("publick:ward", { detail: { ward: ward } }));
  }

  function mapLink(ward) {
    var map = document.getElementById("ward-map");
    var item = document.querySelector('.ward-list > li[data-ward="' + ward + '"]');
    var href = map && !map.hidden ? "#ward-map" : item ? "#" + item.id : null;
    if (!href) return;
    var p = el("p");
    p.appendChild(el("a", t("map"), { href: href }));
    result.appendChild(p);
  }

  function choices(d, number, matches) {
    var ul = el("ul", "", { "class": "choices" });
    matches.forEach(function (name) {
      var li = el("li");
      var button = el("button", (number !== null ? number + " " : "") + name, { type: "button", "class": "button" });
      button.addEventListener("click", function () {
        input.value = (number !== null ? number + " " : "") + name;
        show(d, input.value);
      });
      li.appendChild(button);
      ul.appendChild(li);
    });
    result.appendChild(ul);
  }

  function show(d, text) {
    result.textContent = "";
    var typed = parse(d, text);
    var found = streetFor(d, typed.key);
    var label = text.trim();
    if (!found.name) {
      say(t(found.matches.length ? "choose" : "none", { street: label }));
      if (found.matches.length) choices(d, typed.number, found.matches);
      return;
    }
    var rows = d.streets[found.name];
    var address = (typed.number !== null ? typed.number + " " : "") + found.name;
    if (typed.number === null) {
      var all = wardsOf(rows);
      if (all.length === 1) {
        say(t("street_one", { street: found.name, area: area(all[0]) }));
        card(all[0]);
      } else {
        say(t("street_many", { street: found.name, areas: list(all) }));
      }
      return;
    }
    var n = typed.number;
    var hits = wardsOf(rows.filter(function (r) { return r[0] <= n && n <= r[1] && (r[2] === 0 || r[2] === 2 - n % 2); }));
    if (hits.length === 1) {
      say(t("found", { address: address, area: area(hits[0]) }));
      card(hits[0]);
    } else if (hits.length) {
      say(t("near_line", { address: address, areas: list(hits) }));
      mapLink(hits[0]);
    } else {
      var wards = wardsOf(rows);
      say(t(wards.length === 1 ? "not_listed_one" : "not_listed_many",
            { address: address, street: found.name, area: area(wards[0]), areas: list(wards) }));
      mapLink(wards[0]);
    }
  }

  function lookUp() {
    var text = input.value.trim();
    result.textContent = "";
    if (!text) { say(t("empty")); return; }
    say(t("looking", { address: text }));
    load().then(function (d) { show(d, text); }).catch(function () {
      result.textContent = "";
      say(t("failed"));
    });
  }

  input.addEventListener("focus", function () { load().catch(function () {}); }, { once: true });
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    lookUp();
  });
  var hash = /^#address=(.+)$/.exec(window.location.hash);
  if (hash) {
    try { input.value = decodeURIComponent(hash[1].replace(/\+/g, " ")); } catch (e) { input.value = ""; }
    (document.getElementById("wards") || root).scrollIntoView();
    lookUp();
  }
})();
