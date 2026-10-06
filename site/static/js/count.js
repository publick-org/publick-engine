// Page view counting with GoatCounter (no cookies), loaded only when the site
// config turns it on. Sends the page path without any query string, the page's
// built title, where the visitor came from (without its query string), and the
// screen width. A visit from a link that names where it's from (?ref=digest-email, the
// weekly digest's email, where mail apps send no referrer) is counted as from there.
// Searches and street lookups are counted as events, never with
// what was searched. When several sites share one GoatCounter site, data-prefix
// names this one (e.g. "malden-ma"), and goes in front of every path and event
// so the shared dashboard can tell the sites apart.
(function () {
  "use strict";
  var me = document.currentScript;
  var endpoint = me && me.getAttribute("data-endpoint");
  if (!endpoint || !navigator.sendBeacon) return;
  var prefix = me.getAttribute("data-prefix") || "";
  if (/^(localhost|127\.|\[::1\])/.test(location.hostname)) return;

  function named(path) {
    if (!prefix) return path;
    return prefix + (path.charAt(0) === "/" ? "" : "/") + path;
  }

  function referrer() {
    var ref = new URLSearchParams(location.search).get("ref");
    if (ref && /^[a-z0-9-]{1,40}$/.test(ref)) return ref;
    if (!document.referrer) return "";
    try {
      var r = new URL(document.referrer);
      return r.host === location.host ? named(r.pathname) : r.origin + r.pathname;
    } catch (e) { return ""; }
  }

  function send(params) {
    var q = [];
    for (var k in params) q.push(k + "=" + encodeURIComponent(params[k]));
    navigator.sendBeacon(endpoint + "?" + q.join("&") + "&rnd=" + Math.random().toString(36).slice(2));
  }

  send({ p: named(location.pathname), t: me.getAttribute("data-title") || "", r: referrer(), s: window.screen.width });
  var searched = new URLSearchParams(location.search).get("q");
  if (searched && /^\/(streets|meetings\/search)\/$/.test(location.pathname)) {
    send({ p: named(location.pathname === "/streets/" ? "street-lookup" : "meeting-search"), t: "", e: "true", s: window.screen.width });
  }
})();
