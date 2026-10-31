// The Decisions page's topic filter: the meetings with a decision on one topic, and only those decisions,
// opening a folded list or month that holds one. Without this script the filter stays hidden and every
// decision is shown.
(function () {
  "use strict";
  var bar = document.querySelector(".topic-filter");
  if (!bar) return;
  var buttons = Array.prototype.slice.call(bar.querySelectorAll("button[data-topic]"));
  var each = function (selector, root, fn) { Array.prototype.forEach.call((root || document).querySelectorAll(selector), fn); };
  var shown = function (el) { return !el.hidden; };

  function show(topic) {
    buttons.forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-topic") === topic)); });
    each(".decision-meeting", null, function (meeting) {
      var any = false;
      each(".decision-rows > li", meeting, function (row) {
        row.hidden = !!topic && row.getAttribute("data-topic") !== topic;
        if (!row.hidden) any = true;
      });
      meeting.hidden = !any;
      // A label over a list with none of its decisions shown goes too; a folded list with one opens.
      each(".decision-label", meeting, function (label) {
        var list = label.nextElementSibling;
        label.hidden = !!list && !Array.prototype.some.call(list.querySelectorAll(".decision-rows > li"), shown);
      });
      each("details", meeting, function (fold) {
        var has = Array.prototype.some.call(fold.querySelectorAll(".decision-rows > li"), shown);
        fold.hidden = !!topic && !has;
        if (topic && has) fold.open = true;
      });
    });
    // A month with no meeting shown is left out; a folded month with one opens.
    each(".decision-list", null, function (list) {
      var has = Array.prototype.some.call(list.querySelectorAll(".decision-meeting"), shown);
      var fold = list.closest("details.month-fold");
      if (fold) {
        fold.hidden = !has;
        if (topic && has) fold.open = true;
      } else {
        list.hidden = !has;
        var heading = list.previousElementSibling;
        if (heading && heading.tagName === "H2") heading.hidden = !has;
      }
    });
  }

  buttons.forEach(function (b) {
    b.addEventListener("click", function () { show(b.getAttribute("data-topic")); });
  });
  bar.hidden = false;
})();
