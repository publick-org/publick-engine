// The site's search (site/pages/search/), over its Pagefind index (pipeline/search_index.py). The index is loaded
// on the first search, a few small files for each word. Results show as they're typed: decisions first, then pages,
// then meetings, each kind with its count in the filter above them. Decisions are listed newest first; meetings
// upcoming first, the soonest first, then past ones, the newest first; pages by how well they match. ?q= is kept
// in the address, so a search can be linked or reloaded.
(function () {
  "use strict";
  var root = document.getElementById("search");
  if (!root || !window.fetch) return;
  var form = document.querySelector(".search-big");
  var input = document.getElementById("search-q");
  var start = document.getElementById("search-start");
  var bar = root.querySelector(".search-filters");
  var status = document.getElementById("search-status");
  var list = document.getElementById("search-results");
  // The kinds of result (search_index.py), in the order they're shown, and how many of each the list starts with.
  var KINDS = ["decision", "page", "meeting"];
  var FIRST = { all: 5, one: 20 };
  // The searches for each kind, whose results are listed one after the other: their filters, and their order, by
  // the date each result has (search_index.sort_key()); a page has none, and is listed by how well it matches.
  var SEARCHES = {
    decision: [{ filters: { kind: "decision" }, sort: { date: "desc" } }],
    page: [{ filters: { kind: "page" } }],
    meeting: [{ filters: { kind: "meeting", when: "upcoming" }, sort: { date: "asc" }, upcoming: true },
              { filters: { kind: "meeting", when: "past" }, sort: { date: "desc" } }],
  };
  var strings = JSON.parse(root.getAttribute("data-strings") || "{}");
  function t(key, values) {
    return strings[key].replace(/\{(\w+)\}/g, function (m, k) { return values[k]; });
  }

  var pagefind = null, shown = "all", current = null, typed = 0;

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    for (var k in attrs || {}) node.setAttribute(k, attrs[k]);
    if (text) node.textContent = text;
    return node;
  }

  // An excerpt with its matches marked, from Pagefind's HTML, keeping only its text and <mark>s.
  function excerpt(html) {
    var out = document.createDocumentFragment();
    var doc = new DOMParser().parseFromString("<p>" + html + "</p>", "text/html");
    doc.body.firstChild.childNodes.forEach(function (node) {
      if (node.nodeType === 3) out.appendChild(document.createTextNode(node.textContent));
      else if (node.nodeName === "MARK") out.appendChild(el("mark", null, node.textContent));
      else out.appendChild(document.createTextNode(node.textContent));
    });
    return out;
  }

  function load() {
    if (!pagefind) {
      pagefind = import("/pagefind/pagefind.js").then(function (pf) {
        return pf.options({ excerptLength: 36 }).then(function () { return pf; });
      });
    }
    return pagefind;
  }

  function item(kind, data, upcoming) {
    var li = el("li");
    if (kind === "decision") {
      var box = el("div", { "class": "search-decision" });
      box.appendChild(el("p")).appendChild(excerpt(data.excerpt));
      li.appendChild(box);
      var meta = el("p", { "class": "search-meta" });
      meta.appendChild(el("a", { href: data.url }, data.meta.title));
      meta.appendChild(document.createTextNode(" · " + data.meta.date));
      li.appendChild(meta);
      return li;
    }
    // A page's or a meeting's best match: the part of the page with the most of the words, linked, with its excerpt.
    var sub = (data.sub_results || []).reduce(function (best, part) {
      return !best || (part.locations || []).length > (best.locations || []).length ? part : best;
    }, null) || data;
    if (kind === "page" && data.meta.crumb) li.appendChild(el("div", { "class": "search-crumb" }, data.meta.crumb));
    li.appendChild(el("a", { href: sub.url || data.url, "class": "search-title" }, data.meta.title));
    if (upcoming) li.appendChild(el("span", { "class": "search-upcoming" }, strings.upcoming));
    li.appendChild(el("p", { "class": "search-excerpt" })).appendChild(excerpt(sub.excerpt || data.excerpt));
    return li;
  }

  // One kind's results, under its heading: the first few, and the rest a button away.
  function group(kind, found, size) {
    var section = el("section", { "class": "search-group", "aria-labelledby": "results-" + kind });
    section.appendChild(el("h2", { id: "results-" + kind }, strings[kind]));
    var ol = section.appendChild(el("ol", { "class": "search-list" }));
    var more = el("button", { type: "button", "class": "button search-more" }, strings.more);
    var next = 0;
    function show(n) {
      var batch = found.slice(next, next + n);
      next += batch.length;
      more.hidden = next >= found.length;
      return Promise.all(batch.map(function (r) { return r.data(); })).then(function (all) {
        all.forEach(function (data, i) { ol.appendChild(item(kind, data, batch[i].upcoming)); });
      });
    }
    more.addEventListener("click", function () { show(FIRST.one); });
    section.appendChild(more);
    return show(size).then(function () { return section; });
  }

  function filters(counts, total) {
    bar.textContent = "";
    bar.setAttribute("aria-label", strings.filter);
    ["all"].concat(KINDS).forEach(function (kind) {
      var n = kind === "all" ? total : counts[kind];
      if (kind !== "all" && !n) return;
      var b = el("button", { type: "button", "aria-pressed": String(kind === shown), "data-kind": kind },
        t("count", { label: strings[kind], n: n }));
      b.addEventListener("click", function () { shown = kind; render(current); });
      bar.appendChild(b);
    });
    bar.hidden = false;
  }

  function render(found) {
    var query = "“" + found.query + "”", total = 0, counts = {};
    KINDS.forEach(function (k) { counts[k] = found[k].length; total += counts[k]; });
    list.textContent = "";
    if (!total) {
      bar.hidden = true;
      status.textContent = t("none", { query: query });
      return;
    }
    if (shown !== "all" && !counts[shown]) shown = "all";
    filters(counts, total);
    status.textContent = t(total === 1 ? "matches_one" : "matches_other", { n: total, query: query });
    var kinds = shown === "all" ? KINDS : [shown];
    Promise.all(kinds.filter(function (k) { return counts[k]; }).map(function (k) {
      return group(k, found[k], shown === "all" ? FIRST.all : FIRST.one);
    })).then(function (sections) {
      if (current !== found) return;
      sections.forEach(function (s) { list.appendChild(s); });
    });
  }

  function search(q) {
    var mine = ++typed;
    q = q.trim();
    var url = new URL(window.location.href);
    if (q) url.searchParams.set("q", q); else url.searchParams.delete("q");
    window.history.replaceState(null, "", url);
    start.hidden = Boolean(q);
    if (!q) {
      bar.hidden = true;
      status.textContent = "";
      list.textContent = "";
      return;
    }
    if (!current) status.textContent = strings.searching;
    load().then(function (pf) {
      return Promise.all(KINDS.map(function (k) {
        return Promise.all(SEARCHES[k].map(function (s) {
          return pf.search(q, { filters: s.filters, sort: s.sort }).then(function (found) {
            var results = found ? found.results : [];
            if (s.upcoming) results.forEach(function (r) { r.upcoming = true; });
            return results;
          });
        })).then(function (lists) { return [].concat.apply([], lists); });
      }));
    }).then(function (searches) {
      if (mine !== typed) return;
      var found = { query: q };
      KINDS.forEach(function (k, i) { found[k] = searches[i]; });
      current = found;
      render(found);
    }).catch(function () {
      if (mine === typed) status.textContent = strings.failed;
    });
  }

  var wait = null;
  input.addEventListener("input", function () {
    clearTimeout(wait);
    wait = setTimeout(function () { search(input.value); }, 250);
  });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    clearTimeout(wait);
    search(input.value);
  });

  var query = new URLSearchParams(window.location.search).get("q") || "";
  if (query) {
    input.value = query;
    search(query);
  }
})();
