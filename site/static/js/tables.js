// Wide tables reachable by keyboard. A table wider than the screen scrolls
// inside its .table-wrap; a box that scrolls is given a place in the tab order
// and a name (the table's caption, else the heading before it), so a keyboard
// user can scroll it with the arrow keys. A box that fits isn't, so the tab
// order doesn't grow on a wide screen. Checked again when the box changes size:
// a table in a closed <details> has no size until it opens. A box given its
// place in the template (tabindex) is left as it is.
// A table of years, newest first, marked data-fold shows its latest years and a button for the
// rest; the attribute holds the button's label. Without this script every year shows.
(function () {
  "use strict";
  var SHOWN = 5;
  var tables = document.querySelectorAll("table[data-fold]");
  for (var i = 0; i < tables.length; i++) fold(tables[i]);

  function fold(table) {
    var rows = table.tBodies.length ? table.tBodies[0].rows : [];
    // Hiding one or two rows saves less than the button costs.
    if (rows.length <= SHOWN + 2) return;
    var hidden = [];
    for (var r = SHOWN; r < rows.length; r++) { rows[r].hidden = true; hidden.push(rows[r]); }
    var button = document.createElement("button");
    button.type = "button";
    button.className = "fold-more";
    button.textContent = table.getAttribute("data-fold");
    button.addEventListener("click", function () {
      for (var h = 0; h < hidden.length; h++) hidden[h].hidden = false;
      button.remove();
    });
    var box = table.closest(".table-wrap") || table;
    box.parentNode.insertBefore(button, box.nextSibling);
  }
})();

(function () {
  "use strict";
  if (!window.ResizeObserver) return;
  var wraps = document.querySelectorAll(".table-wrap:not([tabindex])");
  if (!wraps.length) return;

  function text(el) {
    return el ? el.textContent.replace(/\s+/g, " ").trim() : "";
  }

  // The nearest heading before el in the page.
  function heading(el) {
    var all = document.querySelectorAll("main h1, main h2, main h3");
    var found = null;
    for (var i = 0; i < all.length; i++) {
      if (all[i].compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) found = all[i];
    }
    return found;
  }

  function name(wrap) {
    return text(wrap.querySelector("caption")) || text(heading(wrap));
  }

  function update(wrap) {
    if (wrap.scrollWidth > wrap.clientWidth + 1) {
      wrap.setAttribute("tabindex", "0");
      wrap.setAttribute("role", "region");
      var label = name(wrap);
      if (label) wrap.setAttribute("aria-label", label);
    } else {
      wrap.removeAttribute("tabindex");
      wrap.removeAttribute("role");
      wrap.removeAttribute("aria-label");
    }
  }

  var observer = new ResizeObserver(function (entries) {
    for (var i = 0; i < entries.length; i++) update(entries[i].target);
  });
  for (var i = 0; i < wraps.length; i++) observer.observe(wraps[i]);
})();
