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

  // A house number at the start of the query ("55 Commercial St"): the address an alert can follow.
  var number = ((query.match(/^\s*#?(\d{1,6}[A-Za-z]?)\s+\S/) || [])[1] || "").toUpperCase();

  // Email alerts (worker/alerts.js) as one sentence with the house number in it: "Email me about [#] Commercial Street".
  // Empty, it follows the whole street; with a number, that address (filled in on an address's results). Then the
  // email field and its button, as the digest's signup has them (digest_line.html).
  function followForm(action, s) {
    var form = el("form", "", { "class": "line-form follow-form", id: "follow", method: "post", action: action });
    var sentence = el("p", "", { "class": "follow-sentence" });
    sentence.appendChild(el("span", t("follow_lead"), { "class": "follow-lead" }));
    var address = el("span", "", { "class": "address-unit" });
    address.appendChild(el("input", "", { id: "follow-number", name: "number", inputmode: "numeric", autocomplete: "off",
      maxlength: "7", pattern: "[0-9]{1,6}[A-Za-z]?", placeholder: "#", "aria-label": t("follow_number"), value: number }));
    address.appendChild(el("span", s.street || s.name));
    sentence.appendChild(address);
    form.appendChild(sentence);
    var row = el("div", "", { "class": "search-row" });
    row.appendChild(el("input", "", { id: "follow-email", name: "email", type: "email", required: "", maxlength: "254",
      autocomplete: "email", placeholder: t("follow_placeholder"), "aria-label": t("follow_placeholder") }));
    row.appendChild(el("button", t("follow_button"), { "class": "button button-primary", type: "submit" }));
    form.appendChild(row);
    [["street", s.key], ["lang", document.documentElement.lang || "en"]].forEach(function (f) {
      form.appendChild(el("input", "", { type: "hidden", name: f[0], value: f[1] }));
    });
    var trap = el("div", "", { hidden: "" });
    trap.appendChild(el("label", t("follow_trap"), { "for": "follow-website" }));
    trap.appendChild(el("input", "", { id: "follow-website", name: "website", type: "text", tabindex: "-1", autocomplete: "off" }));
    form.appendChild(trap);
    return form;
  }

  // The street's feed, and each address's on it (pipeline/mentions.py), as one line at the end.
  function feeds(s) {
    var p = el("p", t("follow_feeds") + " ", { "class": "small" });
    var street = s.street || s.name;
    (s.street ? [] : [[street, s.feed]]).concat((s.addresses || []).map(function (a) { return [a[0] + " " + street, a[1]]; }))
      .forEach(function (f, i) {
        if (i) p.appendChild(document.createTextNode(" · "));
        p.appendChild(el("a", s.street ? f[0] : (i ? f[0].split(" ")[0] : f[0]), { href: f[1], type: "application/rss+xml", "aria-label": f[0] }));
      });
    return p;
  }

  // For an address ("55 Commercial St"), only what's about it: the lines, permits and 311 requests whose house
  // numbers, before the street's first word, include it. A range counts each number on its side of the street
  // ("53-57" is 53, 55 and 57) and a list each one ("171 & 281R"), as the feeds do (pipeline/mentions.py numbers()).
  // The street stays one link away.
  var MAX_RANGE = 25;
  function numbersIn(written) {
    var out = [];
    written.toUpperCase().split(/\s*[&\/,]\s*|\s+/).forEach(function (part) {
      var ends = part.split(/\s*[-–]\s*/).filter(Boolean);
      if (ends.length === 2 && /^\d+$/.test(ends[0]) && /^\d+$/.test(ends[1]) && +ends[0] < +ends[1]) {
        var first = +ends[0], last = +ends[1], step = first % 2 === last % 2 ? 2 : 1;
        if ((last - first) / step + 1 <= MAX_RANGE) { for (var n = first; n <= last; n += step) out.push(String(n)); }
        else out.push(ends[0], ends[1]);
      } else out = out.concat(ends);
    });
    return out.map(function (n) { return n.replace(/^0+(?=\d)/, ""); });
  }
  function atAddress(s) {
    var word = s.name.split(" ")[0].replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    var group = "(\\d+[A-Za-z]?(?:\\s*(?:[-–\\/&,]|and)\\s*\\d+[A-Za-z]?)*)";
    var named = new RegExp("(?:^|[^0-9A-Za-z])" + group + "\\s+" + word + "\\b", "gi");
    var starts = new RegExp("^\\s*" + group + "\\b", "i");
    function names(text) {
      var m; named.lastIndex = 0;
      while ((m = named.exec(text))) { if (numbersIn(m[1].replace(/\band\b/gi, "&")).indexOf(number) >= 0) return true; }
      return false;
    }
    function at(address) { var m = starts.exec(address); return !!m && numbersIn(m[1].replace(/\band\b/gi, "&")).indexOf(number) >= 0; }
    var meetings = s.meetings.filter(function (m) { return names(m.line); });
    var permits = s.permits.filter(function (p) { return at(p.address); });
    var requests = s.requests.filter(function (r) { return at(r.address); });
    return Object.assign({}, s, {
      name: number + " " + s.name, street: s.name,
      meetings: meetings, meetings_total: meetings.length, permits: permits, permits_total: permits.length,
      requests: requests, requests_total: requests.length,
      addresses: (s.addresses || []).filter(function (a) { return a[0] === number; }),
    });
  }

  function show(key, s) {
    if (number) s = atAddress(s);
    results.textContent = "";
    var counts = [];
    function count(key, n) { return t(n === 1 ? key + "_one" : key + "_other", { n: n }); }
    if (sources.indexOf("meetings") >= 0) counts.push(count("mentions", s.meetings_total));
    if (sources.indexOf("permits") >= 0) counts.push(count("permits", s.permits_total));
    if (sources.indexOf("requests") >= 0) counts.push(count("requests", s.requests_total));
    status.textContent = t("counts", { street: s.name, counts: counts.join(", ") });
    results.appendChild(el("h2", s.name, { id: "street-name" }));
    if (s.street) {
      var whole = el("p");
      whole.appendChild(el("a", t("whole_street", { street: s.street }), { href: window.location.pathname + "?q=" + encodeURIComponent(s.street) }));
      results.appendChild(whole);
    }
    // The street's ward, on the Officials page: the street goes after "#", which isn't sent with the request.
    var wardPage = root.getAttribute("data-ward-page");
    if (wardPage) {
      var p = el("p");
      p.appendChild(el("a", t("ward_link", { street: s.name }), { href: wardPage + "#address=" + encodeURIComponent(s.name) }));
      results.appendChild(p);
    }
    if (sources.indexOf("meetings") >= 0) results.appendChild(section(t("meetings_head"), s.meetings_total, s.meetings.length, s.meetings.map(function (m) {
      return item(m.board, m.url, date(m.date) + " · " + m.doc, m.line);
    })));
    var action = root.getAttribute("data-follow");
    if (action && s.feed) results.appendChild(followForm(action, Object.assign({ key: key }, s)));
    if (sources.indexOf("permits") >= 0) results.appendChild(section(t("permits_head"), s.permits_total, s.permits.length, s.permits.map(function (p) {
      var detail = [p.type, date(p.date), p.status].concat(p.cost ? [t("estimated", { amount: money(p.cost) })] : []).join(" · ");
      return item(p.address, null, detail, p.work);
    })));
    if (sources.indexOf("requests") >= 0) results.appendChild(section(t("requests_head"), s.requests_total, s.requests.length, s.requests.map(function (r) {
      return item(r.category, r.url, [r.address, date(r.date), t(r.status === "open" ? "open" : "closed")].join(" · "));
    })));
    if (s.feed) results.appendChild(feeds(s));
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
