// Formation step: the mini pitches take a team's colour as soon as it is
// picked, before saving. Without this file they show it after the save.
(function () {
  "use strict";
  document.querySelectorAll(".formation-side input[type=color]").forEach(function (input) {
    input.addEventListener("input", function () {
      input.closest(".formation-side").style.setProperty("--shirt", input.value);
    });
  });
})();
