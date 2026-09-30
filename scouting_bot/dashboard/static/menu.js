// Menus are native <details>: they open and close without this file. This only
// adds what a disclosure lacks as a menu — closing on Escape, on a tap outside,
// and when another menu opens.
(function () {
  "use strict";
  var menus = document.querySelectorAll("details[data-menu]");
  if (!menus.length) return;
  function closeAll(except) {
    menus.forEach(function (m) { if (m !== except) m.open = false; });
  }
  menus.forEach(function (m) {
    m.addEventListener("toggle", function () { if (m.open) closeAll(m); });
  });
  document.addEventListener("click", function (e) {
    menus.forEach(function (m) { if (m.open && !m.contains(e.target)) m.open = false; });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    menus.forEach(function (m) {
      if (m.open) { m.open = false; m.querySelector("summary").focus(); }
    });
  });
})();

// Filters fold away on a phone; on a wide screen they are simply open.
(function () {
  "use strict";
  if (!window.matchMedia("(min-width: 720px)").matches) return;
  document.querySelectorAll("details[data-filters]").forEach(function (d) { d.open = true; });
})();
