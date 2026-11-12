// The site's search (site/pages/search/), over its Pagefind index (pipeline/search_index.py). The index is loaded
// on the first search, a few small files for each word. Results show as they're typed: decisions first, then pages,
// then meetings, each kind with its count in the filter above them. Decisions are listed newest first; meetings
// upcoming first, the soonest first, then past ones, the newest first; pages by how well they match. Above them,
// the streets, boards and officials the search names, from the site's answers (pipeline/search_answers.py, which
// says how they're matched). The words of a question ("who is", "when does") are left out of both. ?q= is kept in
// the address, so a search can be linked or reloaded.
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
  var box = document.getElementById("search-answers");
  // Dates written as the page's language writes them in the US.
  var lang = document.documentElement.lang + "-US";
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
  // A number of things, from the page's key_one or key_other.
  function count(key, n) {
    return t(key + (n === 1 ? "_one" : "_other"), { n: n.toLocaleString(lang) });
  }

  var pagefind = null, answers = null, shown = "all", current = null, typed = 0;
  // How many answers show at most, and how far below the best one another can score and still show.
  var ANSWERS = { most: 3, within: 0.1 };

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

  // The answers file, ready to match: each answer with its names as words (words()), and which words of each name
  // name no one thing. A search that can't load it searches without answers.
  function loadAnswers() {
    if (!answers) {
      answers = fetch(root.getAttribute("data-answers")).then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.json();
      }).then(function (data) {
        // Lookups without an object's own keys ("constructor"), which a word could be.
        function set(list) {
          var out = Object.create(null);
          list.forEach(function (w) { out[w] = true; });
          return out;
        }
        function map(object) {
          var out = Object.create(null);
          Object.keys(object).forEach(function (k) { out[k] = object[k]; });
          return out;
        }
        data.stopSet = set(data.stop);
        data.suffixes = map(data.suffixes);
        data.directions = map(data.directions);
        data.letters = map(data.letters);
        var directions = Object.keys(data.directions).map(function (k) { return data.directions[k]; });
        data.genericSet = set(data.generic.concat(directions));
        // Each street type with its spellings, so a partly typed one ("stre") is read as it.
        data.types = Object.create(null);
        Object.keys(data.suffixes).forEach(function (spelling) {
          var w = data.suffixes[spelling];
          (data.types[w] = data.types[w] || []).push(spelling);
        });
        data.answers.forEach(function (a) {
          a.names = [a.n].concat(a.a || []).map(function (name) {
            var w = words(name, data);
            // A street's type, its last word, needn't be named ("sargent" names Sargent Street), and alone names no
            // one thing; a word elsewhere in a name ("Terrace Lane", "Court Street") does.
            var type = w.map(function (word, i) { return a.k === "street" && i === w.length - 1 && !!data.types[word]; });
            return { w: w, type: type, generic: w.map(function (word, i) { return type[i] || !!data.genericSet[word]; }) };
          });
        });
        return data;
      }).catch(function () { return null; });
    }
    return answers;
  }

  // Text as search_answers.words() reads it: lowercase, no accents or punctuation, street types and directions
  // written one way.
  function plain(text, data) {
    var lower = text.toLowerCase();
    if (data) lower = lower.replace(/./g, function (c) { return data.letters[c] || c; });
    return lower.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/['’]/g, "");
  }
  function words(text, data) {
    return plain(text, data).split(/[^a-z0-9]+/).map(function (w) { return data.directions[w] || data.suffixes[w] || w; })
      .filter(function (w) { return w && !data.stopSet[w]; });
  }

  // Whether two words are a letter or two apart: one for a word of 4 letters or more, two for 8 or more.
  function close(a, b) {
    var most = a.length >= 8 ? 2 : a.length >= 4 ? 1 : 0;
    if (!most || Math.abs(a.length - b.length) > most || /\d/.test(a + b)) return false;
    var row = [];
    for (var j = 0; j <= b.length; j++) row.push(j);
    for (var i = 1; i <= a.length; i++) {
      var prev = row[0], low = i;
      row[0] = i;
      for (j = 1; j <= b.length; j++) {
        var here = row[j];
        row[j] = Math.min(row[j] + 1, row[j - 1] + 1, prev + (a[i - 1] === b[j - 1] ? 0 : 1));
        prev = here;
        low = Math.min(low, row[j]);
      }
      if (low > most) return false;
    }
    return row[b.length] <= most;
  }

  // How well a searched word matches a word of a name: the same (1), the start of it, the last word as it's typed
  // (0.9), or a letter or two off (0.8); 0 when it doesn't.
  var TYPO = 0.8;
  function wordScore(q, w, last, data) {
    if (q === w) return 1;
    if (last && q.length >= 3 && [w].concat(data.types[w] || []).some(function (s) { return s.indexOf(q) === 0; })) {
      return 0.9;
    }
    return close(q, w) ? TYPO : 0;
  }

  // How well a search matches one of an answer's names: {score, typo}, or null if it doesn't. It matches when it
  // names all of the name but a street's type, or when every word of it is in the name and it names one thing: half
  // the name's words that aren't generic, or the first or last of them (a surname), or with two words or more. The
  // score is how much of the name it matches, its words that aren't generic counted, and more for every word
  // searched or every word of the name matched.
  function nameScore(query, name, data) {
    var used = [], quality = [], allQuery = true, typo = false;
    query.forEach(function (q, i) {
      var best = 0, at = -1;
      name.w.forEach(function (w, j) {
        if (used[j]) return;
        var s = wordScore(q, w, i === query.length - 1, data);
        if (s > best) { best = s; at = j; }
      });
      if (at < 0) { allQuery = false; return; }
      used[at] = true;
      quality[at] = best;
      if (best === TYPO) typo = true;
    });
    var specific = [], sum = 0;
    name.w.forEach(function (w, j) {
      if (name.generic[j]) return;
      specific.push(j);
      if (used[j]) sum += quality[j];
    });
    if (!specific.some(function (j) { return used[j]; })) return null;
    var cover = sum / specific.length;
    var allName = name.w.every(function (w, j) { return used[j]; });
    var allButType = name.w.every(function (w, j) { return used[j] || name.type[j]; });
    var edge = [specific[0], specific[specific.length - 1]].some(function (j) { return used[j]; });
    if (!allButType && !(allQuery && (cover >= 0.5 || query.length > 1 || edge))) return null;
    // Of two as good, the one with fewer words left over: "main" is Main Street before E Main Street.
    var over = name.w.filter(function (w, j) { return !used[j]; }).length;
    return { score: cover + (allQuery ? 0.5 : 0) + (allName ? 0.5 : 0) - 0.01 * over, typo: typo };
  }

  // The answers a search names, the best first: at most ANSWERS.most, each within ANSWERS.within of the best, and
  // none a letter or two off when another matches as typed ("pine": Pine Street, not Vine Street).
  function match(q, data) {
    var query = data ? words(q, data) : [];
    if (!query.length) return [];
    var scored = [];
    data.answers.forEach(function (a) {
      var best = null;
      a.names.forEach(function (name) {
        var s = nameScore(query, name, data);
        if (s && (!best || s.score > best.score)) best = s;
      });
      if (best) scored.push({ answer: a, score: best.score, typo: best.typo });
    });
    if (scored.some(function (s) { return !s.typo; })) scored = scored.filter(function (s) { return !s.typo; });
    scored.sort(function (x, y) { return y.score - x.score; });
    return scored.filter(function (s) { return s.score >= scored[0].score - ANSWERS.within - 1e-9; })
      .slice(0, ANSWERS.most).map(function (s) { return s.answer; });
  }

  // What the search asks Pagefind: its words but a question's ("who is the mayor" -> "mayor"), unless it's all
  // such words or quotes a phrase.
  function pagefindQuery(q, data) {
    if (!data || /["\u201c\u201d]/.test(q)) return q;
    var kept = q.split(/\s+/).filter(function (w) { return !data.stopSet[plain(w, data).replace(/[^a-z0-9]+/g, "")]; });
    return kept.length ? kept.join(" ") : q;
  }

  // A day as "Wed, Nov 18", with the year when it isn't this year's.
  function day(iso) {
    var d = new Date(iso + "T12:00:00");
    var options = { weekday: "short", month: "short", day: "numeric" };
    if (d.getFullYear() !== new Date().getFullYear()) options.year = "numeric";
    return d.toLocaleDateString(lang, options);
  }

  function isoToday() {
    var d = new Date();
    return [d.getFullYear(), ("0" + (d.getMonth() + 1)).slice(-2), ("0" + d.getDate()).slice(-2)].join("-");
  }

  // An answer's line under its name: a street's counts, when a board meets next or last met, an official's seats.
  function detail(a) {
    if (a.k === "official") return a.d;
    if (a.k === "street") {
      return [a.c.meetings && count("street_mentions", a.c.meetings), a.c.requests && count("street_requests", a.c.requests),
              a.c.permits && count("street_permits", a.c.permits)].filter(Boolean).join(" · ");
    }
    var today = isoToday();
    if (a.next && a.next >= today) return t("board_next", { date: day(a.next) });
    // A meeting the build had to come may since have been held.
    var last = [a.last, a.next].filter(Boolean).sort().pop();
    return last ? t("board_last", { date: day(last) }) : "";
  }

  function showAnswers(found) {
    var ul = box.querySelector("ul");
    ul.textContent = "";
    found.forEach(function (a) {
      var li = el("li", { "class": "search-answer" });
      li.appendChild(el("span", { "class": "search-answer-kind" }, strings[a.k]));
      li.appendChild(el("a", { href: a.u, "class": "search-answer-name" }, a.n));
      var line = detail(a);
      if (line) li.appendChild(el("p", { "class": "search-answer-detail" }, line));
      ul.appendChild(li);
    });
    box.hidden = !found.length;
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
      status.textContent = t(found.answers.length ? "only_answers" : "none", { query: query });
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
      box.hidden = true;
      status.textContent = "";
      list.textContent = "";
      return;
    }
    if (!current) status.textContent = strings.searching;
    var named = [];
    Promise.all([load(), loadAnswers()]).then(function (loaded) {
      var pf = loaded[0], data = loaded[1];
      named = data ? match(q, data) : [];
      var words = pagefindQuery(q, data);
      return Promise.all(KINDS.map(function (k) {
        return Promise.all(SEARCHES[k].map(function (s) {
          return pf.search(words, { filters: s.filters, sort: s.sort }).then(function (found) {
            var results = found ? found.results : [];
            if (s.upcoming) results.forEach(function (r) { r.upcoming = true; });
            return results;
          });
        })).then(function (lists) { return [].concat.apply([], lists); });
      }));
    }).then(function (searches) {
      if (mine !== typed) return;
      var found = { query: q, answers: named };
      KINDS.forEach(function (k, i) { found[k] = searches[i]; });
      current = found;
      showAnswers(named);
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
