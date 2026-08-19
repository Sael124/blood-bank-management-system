/* Progressive enhancement for the reporting screen.
   The server renders every fetched row and marks the ones beyond the preview,
   so the tables are complete even when this script does not run. Here we only
   reveal the toggle and switch between the preview and the full table. */

(function () {
  "use strict";

  var COLLAPSED_CLASS = "is-collapsed";

  function setUpCollapsibleTable(container) {
    var toggle = container.querySelector("[data-collapsible-toggle]");
    if (!toggle) {
      // Nothing to expand: the report is shorter than the preview.
      container.classList.remove(COLLAPSED_CLASS);
      return;
    }

    var label = toggle.querySelector("[data-collapsible-label]");

    toggle.addEventListener("click", function () {
      var collapsed = container.classList.toggle(COLLAPSED_CLASS);
      toggle.setAttribute("aria-expanded", String(!collapsed));
      if (label) {
        label.textContent = collapsed
          ? toggle.dataset.labelMore
          : toggle.dataset.labelLess;
      }
      if (collapsed) {
        // Returning to the preview can leave the viewport past the table.
        container.scrollIntoView({ block: "nearest" });
      }
    });

    toggle.hidden = false;
  }

  document.querySelectorAll("[data-collapsible]").forEach(setUpCollapsibleTable);
})();
