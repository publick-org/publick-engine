// Street lookup. The street index is loaded only when there is a query (or
// when the street box is first used, for its suggestions). Street names are
// matched the same way the site builds the index: house numbers dropped and
// street types ("Av", "Avenue", "AVE") written one way.
(function () {
  "use strict";
  var root = document.getElementById("street");
  if (!root || !window.fetch) return;
  var input = document.getElementById("street-q");
  var names = document.getElementById("street-names");
  var status = document.getElementById("street-status");
  var results = document.getElementById("street-results");
  var index = null;
  // The sources this town has: meetings, permits and 311 requests, where available.
  var sources = (root.getAttribute("data-sources") || "").split(" ");
  // Dates and numbers written as the page's language writes them in the US.
  var lang = document.documentElement.lang + "-US";
  // The script's wording, from the page in the page's language (data-strings, written by its template).
  var strings = JSON.parse(root.getAttribute("data-strings") || "{}");
  function t(key, values) {
    return strings[key].replace(/\{(\w+)\}/g, function (m, k) { return values[k]; });
  }

  var query = (new URLSearchParams(window.location.search).get("q") || "").trim();
  if (input) input.value = query;

  function load() {
    if (!index) {
      index = fetch(root.getAttribute("data-index"))
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
    }
    return index;
  }

  function el(tag, text, attrs) {
    var node = document.createElement(tag);
    if (text) node.textContent = text;
    for (var k in attrs || {}) node.setAttribute(k, attrs[k]);
    return node;
  }

  function keyFor(text, suffixes) {
    var words = text.toUpperCase().replace(/[.,'’]/g, "").replace(/\s+(UNIT|APT|#)\s*\S+$/, "")
      .replace(/^\s*#?\d+[A-Z]?(\s*[-–\/&]?\s*\d+[A-Z]?)*\s+/, "").trim().split(/\s+/);
    var last = words[words.length - 1];
    if (words.length > 1 && suffixes[last]) words[words.length - 1] = suffixes[last];
    return words.join(" ");
  }

  function date(iso) {
    var d = new Date(iso + "T12:00:00");
    return d.toLocaleDateString(lang, { month: "short", day: "numeric", year: "numeric" });
  }

  function money(n) { return "$" + Math.round(n).toLocaleString(lang); }

  function section(title, total, shown, items) {
    var wrap = document.createDocumentFragment();
    wrap.appendChild(el("h3", title + " (" + total.toLocaleString(lang) + ")"));
    if (!total) { wrap.appendChild(el("p", t("none"), { "class": "small" })); return wrap; }
    var list = el("ul", "", { "class": "plain-list" });
    items.forEach(function (li) { list.appendChild(li); });
    wrap.appendChild(list);
    if (total > shown) wrap.appendChild(el("p", t("latest", { n: shown }), { "class": "small" }));
    return wrap;
  }

  function item(lead, href, detail, extra) {
    var li = el("li");
    li.appendChild(href ? el("a", lead, { href: href }) : el("strong", lead));
    li.appendChild(el("span", detail, { "class": "item-detail" }));
    if (extra) li.appendChild(el("span", extra, { "class": "item-detail" }));
    return li;
  }

  function show(key, s) {
    results.textContent = "";
    var counts = [];
    function count(key, n) { return t(n === 1 ? key + "_one" : key + "_other", { n: n }); }
    if (sources.indexOf("meetings") >= 0) counts.push(count("mentions", s.meetings_total));
    if (sources.indexOf("permits") >= 0) counts.push(count("permits", s.permits_total));
    if (sources.indexOf("requests") >= 0) counts.push(count("requests", s.requests_total));
    status.textContent = t("counts", { street: s.name, counts: counts.join(", ") });
    results.appendChild(el("h2", s.name, { id: "street-name" }));
    if (sources.indexOf("meetings") >= 0) results.appendChild(section(t("meetings_head"), s.meetings_total, s.meetings.length, s.meetings.map(function (m) {
      return item(m.board, m.url, date(m.date) + " · " + m.doc, m.line);
    })));
    if (sources.indexOf("permits") >= 0) results.appendChild(section(t("permits_head"), s.permits_total, s.permits.length, s.permits.map(function (p) {
      var detail = [p.type, date(p.date), p.status].concat(p.cost ? [t("estimated", { amount: money(p.cost) })] : []).join(" · ");
      return item(p.address, null, detail, p.work);
    })));
    if (sources.indexOf("requests") >= 0) results.appendChild(section(t("requests_head"), s.requests_total, s.requests.length, s.requests.map(function (r) {
      return item(r.category, r.url, [r.address, date(r.date), t(r.status === "open" ? "open" : "closed")].join(" · "));
    })));
  }

  function suggest(matches, streets) {
    results.textContent = "";
    status.textContent = t(matches.length ? "suggest" : "unknown");
    if (!matches.length) return;
    var list = el("ul", "", { "class": "plain-list" });
    matches.slice(0, 12).forEach(function (k) {
      var li = el("li");
      li.appendChild(el("a", streets[k].name, { href: window.location.pathname + "?q=" + encodeURIComponent(streets[k].name) }));
      list.appendChild(li);
    });
    results.appendChild(list);
  }

  if (input && names) {
    input.addEventListener("focus", function fill() {
      input.removeEventListener("focus", fill);
      load().then(function (data) {
        Object.keys(data.streets).forEach(function (k) { names.appendChild(el("option", "", { value: data.streets[k].name })); });
      }).catch(function () {});
    });
  }

  if (!query) return;
  document.title = query + " – " + document.title;
  status.textContent = t("looking", { street: query });
  load().then(function (data) {
    var streets = data.streets, key = keyFor(query, data.suffixes);
    if (streets[key]) { show(key, streets[key]); return; }
    var bare = key.split(" ").filter(function (w) { return !data.suffixes[w]; }).join(" ");
    var matches = Object.keys(streets).filter(function (k) { return bare && k.indexOf(bare) === 0; });
    if (matches.length === 1) show(matches[0], streets[matches[0]]);
    else suggest(matches, streets);
  }).catch(function () {
    status.textContent = t("failed");
  });
})();
