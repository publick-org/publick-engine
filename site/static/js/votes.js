// A meeting's decisions in the order taken (macros.html decision_groups). The stylesheet already shows them in that
// order without this script, by each row's place (--taken); this moves the rows themselves, so the keyboard and screen
// readers go through them in the order shown, and puts them back in their groups when the main ones are chosen first.
(function () {
  "use strict";
  var each = function (list, fn) { Array.prototype.forEach.call(list, fn); };
  each(document.querySelectorAll(".vote-groups"), function (groups) {
    var lists = groups.querySelectorAll(".vote-list > .decision-rows");
    var taken = groups.querySelector('.vote-order input[value="taken"]');
    if (!lists.length || !taken) return;
    var rows = [];
    each(lists, function (list) {
      each(list.children, function (li) { rows.push({ li: li, list: list }); });
    });
    var place = function (row) { return Number(row.li.style.getPropertyValue("--taken")) || 0; };
    var inOrder = rows.slice().sort(function (a, b) { return place(a) - place(b); });
    function show() {
      if (taken.checked) inOrder.forEach(function (row) { lists[0].appendChild(row.li); });
      else rows.forEach(function (row) { row.list.appendChild(row.li); });
      // The lists left empty aren't announced as lists.
      each(lists, function (list) { list.hidden = !list.children.length; });
    }
    groups.addEventListener("change", show);
    // A browser that kept the choice from an earlier visit to the page has it checked already.
    if (taken.checked) show();
  });
})();
