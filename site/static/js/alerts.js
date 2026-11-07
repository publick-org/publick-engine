// Address alerts' confirm and manage pages (site/templates/alerts.html): the token from the email's link
// (?t= to confirm a follow, ?m= for a reader's own page) is posted to the Worker (worker/alerts.js), which
// answers in JSON. Confirming takes a press of the button, so a mail scanner opening the link confirms nothing.
(function () {
  "use strict";
  var root = document.getElementById("alerts");
  if (!root || !window.fetch) return;
  var strings = JSON.parse(root.getAttribute("data-strings") || "{}");
  var status = document.getElementById("alerts-status");
  var params = new URLSearchParams(window.location.search);
  function t(key, values) {
    return strings[key].replace(/\{(\w+)\}/g, function (m, k) { return values[k]; });
  }
  function post(path, fields) {
    return fetch(path, { method: "POST", body: new URLSearchParams(fields) }).then(function (r) {
      return r.json().catch(function () { return { ok: false }; }).then(function (body) { body.status = r.status; return body; });
    });
  }

  if (root.getAttribute("data-page") === "confirm") {
    var form = document.getElementById("alerts-confirm");
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      post("/alerts/confirm", { t: params.get("t") || "" }).then(function (body) {
        if (body.ok) { form.hidden = true; status.textContent = t("confirmed", { address: body.name }); }
        else status.textContent = t(body.status === 404 || body.status === 400 ? "not_found" : "failed");
      }).catch(function () { status.textContent = t("failed"); });
    });
    return;
  }

  var list = document.getElementById("alerts-follows");
  var stopAll = document.getElementById("alerts-stop-all");
  var token = params.get("m") || "";
  function show(body) {
    if (!body.ok) { status.textContent = t(body.status === 404 || body.status === 400 ? "not_found" : "failed"); return; }
    list.textContent = "";
    stopAll.hidden = !body.follows.length;
    if (!body.follows.length) { status.textContent = t("none"); return; }
    body.follows.forEach(function (f) {
      var li = document.createElement("li");
      li.appendChild(document.createTextNode(f.name + " "));
      var button = document.createElement("button");
      button.className = "button";
      button.type = "button";
      button.textContent = strings.remove;
      button.addEventListener("click", function () {
        post("/alerts/manage", { m: token, remove: f.town + "|" + f.key }).then(show).catch(function () { status.textContent = t("failed"); });
      });
      li.appendChild(button);
      list.appendChild(li);
    });
  }
  stopAll.addEventListener("submit", function (event) {
    event.preventDefault();
    post("/alerts/manage", { m: token, remove: "all" }).then(function (body) {
      if (body.ok) { list.textContent = ""; stopAll.hidden = true; status.textContent = t("removed_all"); } else show(body);
    }).catch(function () { status.textContent = t("failed"); });
  });
  post("/alerts/manage", { m: token }).then(show).catch(function () { status.textContent = t("failed"); });
})();
