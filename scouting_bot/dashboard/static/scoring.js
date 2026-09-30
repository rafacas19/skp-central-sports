// Score sheet, progressively enhanced. Without this file the sheet is a plain
// form: every section on the page and a Guardar button. With it:
//   · one section at a time, switched from the tab strip or Anterior/Siguiente
//     (the URL hash follows, so a reload lands on the same section);
//   · tapping the selected score again clears it;
//   · counts, section averages and the overall mean update as you tap;
//   · every change autosaves.
(function () {
  "use strict";
  var form = document.querySelector("form[data-autosave]");
  if (!form || !window.fetch) return;
  var statusEl = form.querySelector("[data-autosave-status]");
  var timer = null;

  function say(text) { if (statusEl) statusEl.textContent = text; }

  // ── Autosave, resilient to a bad signal at the ground ───────────────────
  // Every change posts the whole sheet. If the post fails (no signal, server
  // down) the latest sheet is kept on the phone and re-sent when the connection
  // returns, on the next visit, or on a timer — the scout's taps are never lost
  // just because the stadium has no coverage. Only the newest state per sheet is
  // kept: each post carries the full sheet, so older ones add nothing.
  var key = "sheet:" + window.location.pathname;
  var retryTimer = null;
  var retryDelay = 5000;

  function entries() {
    var out = [];
    new FormData(form).forEach(function (v, k) { out.push([k, String(v)]); });
    return out;
  }
  function body(list) {
    var fd = new FormData();
    list.forEach(function (kv) { fd.append(kv[0], kv[1]); });
    return fd;
  }
  function remember(list) {
    try { localStorage.setItem(key, JSON.stringify({ ts: Date.now(), entries: list })); } catch (e) { /* storage full or disabled */ }
  }
  function pending() {
    try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (e) { return null; }
  }
  function forget() { try { localStorage.removeItem(key); } catch (e) { /* ignore */ } }

  // A form left open past the CSRF token's lifetime gets a 400: fetch the page
  // again for a fresh token and retry once.
  function freshToken() {
    return fetch(window.location.href, { credentials: "same-origin" })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        var field = form.dataset.csrfField;
        var doc = new DOMParser().parseFromString(html, "text/html");
        var input = doc.querySelector('input[name="' + field + '"]');
        if (!input) throw new Error("no token");
        form.querySelector('input[name="' + field + '"]').value = input.value;
        return input.value;
      });
  }
  function post(list, retried) {
    return fetch(form.action || window.location.href, {
      method: "POST", body: body(list), credentials: "same-origin",
    }).then(function (resp) {
      if (resp.status === 400 && !retried) {
        return freshToken().then(function (token) {
          var field = form.dataset.csrfField;
          return post(list.map(function (kv) { return kv[0] === field ? [kv[0], token] : kv; }), true);
        });
      }
      if (!resp.ok) throw new Error(resp.status);
    });
  }
  function scheduleRetry() {
    clearTimeout(retryTimer);
    retryTimer = setTimeout(flush, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 60000);
  }
  function flush() {
    var p = pending();
    if (!p) return Promise.resolve();
    say("Guardando…");
    return post(p.entries).then(function () {
      var still = pending();
      if (still && still.ts === p.ts) forget();
      retryDelay = 5000;
      say("Guardado");
      var warn = document.querySelector(".callout.warn");
      if (warn) warn.remove();
    }).catch(function () {
      say(navigator.onLine === false ? "Sin conexión — se guardará al volver la señal"
                                     : "No se pudo guardar — se reintentará");
      scheduleRetry();
    });
  }
  function save() {
    remember(entries());
    flush();
  }
  function schedule(delay) { clearTimeout(timer); timer = setTimeout(save, delay); }
  window.addEventListener("online", flush);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") flush();
  });

  // Back on this sheet with taps that never reached the server: put them on
  // the form (only if they are newer than what the server has) and send them.
  (function restore() {
    var p = pending();
    if (!p) return;
    if (p.ts <= Number(form.dataset.updated || 0)) { forget(); return; }
    p.entries.forEach(function (kv) {
      var name = kv[0], value = kv[1];
      var radio = form.querySelector('input[type=radio][name="' + name + '"][value="' + value + '"]');
      if (radio) radio.checked = true;
      var area = form.querySelector('textarea[name="' + name + '"]');
      if (area) area.value = value;
    });
    refresh();
    say("Recuperando cambios sin guardar…");
    flush();
  })();

  // ── Figures: the client's averages (one decimal, half-up, comma) ─────────
  function fmt(avg) { return (Math.floor(avg * 10 + 0.5 + 1e-9) / 10).toFixed(1).replace(".", ","); }
  function scoresIn(scope) {
    var values = [];
    scope.querySelectorAll('input[type=radio][name^="s-"]:checked').forEach(function (r) {
      if (r.value) values.push(Number(r.value));
    });
    return values;
  }
  function refresh() {
    form.querySelectorAll("section[data-section]").forEach(function (sec) {
      var avgEl = sec.querySelector("[data-avg]");
      if (!avgEl) return;
      var values = scoresIn(sec);
      var total = sec.querySelectorAll(".criterion").length;
      avgEl.textContent = values.length ? fmt(values.reduce(function (a, b) { return a + b; }, 0) / values.length) + "/5" : "—/5";
      var tab = form.querySelector('[data-tab="' + sec.id + '"]');
      if (tab) {
        tab.querySelector("[data-count]").textContent = values.length + "/" + total;
        tab.classList.toggle("complete", values.length === total);
      }
    });
    var all = scoresIn(form);
    var chip = document.querySelector("[data-rating]");
    if (chip) {
      if (all.length) {
        var mean = Math.round(all.reduce(function (a, b) { return a + b; }, 0) / all.length * 100) / 100;
        chip.textContent = String(mean) + " / 5";
        chip.hidden = false;
      } else {
        chip.hidden = true;
      }
    }
  }

  // ── Tap the selected score again to clear it ─────────────────────────────
  form.addEventListener("pointerdown", function (e) {
    var label = e.target.closest(".scale label");
    if (label) label.dataset.was = label.querySelector("input").checked ? "1" : "";
  });
  form.addEventListener("click", function (e) {
    var label = e.target.closest(".scale label");
    if (!label || label.classList.contains("scale-clear") || label.dataset.was !== "1") return;
    e.preventDefault();
    label.dataset.was = "";
    var clear = label.parentElement.querySelector(".scale-clear input");
    if (clear) {
      clear.checked = true;
      clear.dispatchEvent(new Event("change", { bubbles: true }));
    }
  });

  form.addEventListener("change", function (e) {
    if (e.target.type === "radio") { refresh(); schedule(300); }
  });
  form.addEventListener("input", function (e) {
    if (e.target.tagName === "TEXTAREA") schedule(1200);
  });

  // ── One section at a time ────────────────────────────────────────────────
  var sections = Array.prototype.slice.call(form.querySelectorAll("section[data-section]"));
  var tabs = form.querySelector(".sheet-tabs");
  if (!tabs || sections.length < 2) return;
  form.classList.add("is-tabbed");
  var saveBtn = form.querySelector("[data-save]");
  var prev = form.querySelector("[data-prev]");
  var next = form.querySelector("[data-next]");
  var finish = form.querySelector("[data-finish]");
  if (saveBtn) saveBtn.hidden = true;  // autosave does it; it comes back on a failed save

  function show(index, focus) {
    index = Math.max(0, Math.min(sections.length - 1, index));
    sections.forEach(function (sec, i) { sec.hidden = i !== index; });
    tabs.querySelectorAll("[data-tab]").forEach(function (tab, i) {
      if (i === index) tab.setAttribute("aria-current", "step"); else tab.removeAttribute("aria-current");
      if (i === index) tab.scrollIntoView({ block: "nearest", inline: "center" });
    });
    prev.hidden = index === 0;
    var last = index === sections.length - 1;
    next.hidden = last;
    finish.hidden = !last;
    if (!last) {
      var label = tabs.querySelectorAll("[data-tab]")[index + 1].querySelector(".tab-label").textContent;
      next.textContent = label + " →";
      next.setAttribute("aria-label", "Siguiente sección: " + label);
    }
    if (history.replaceState) history.replaceState(null, "", "#" + sections[index].id);
    if (focus) {
      var title = sections[index].querySelector(".sheet-section-title");
      window.scrollTo({ top: 0 });
      if (title) { title.setAttribute("tabindex", "-1"); title.focus({ preventScroll: true }); }
    }
  }
  function current() {
    for (var i = 0; i < sections.length; i++) if (!sections[i].hidden) return i;
    return 0;
  }

  tabs.addEventListener("click", function (e) {
    var tab = e.target.closest("[data-tab]");
    if (!tab) return;
    e.preventDefault();
    show(sections.findIndex(function (s) { return s.id === tab.dataset.tab; }), true);
  });
  prev.addEventListener("click", function () { show(current() - 1, true); });
  next.addEventListener("click", function () { show(current() + 1, true); });

  var start = sections.findIndex(function (s) { return "#" + s.id === window.location.hash; });
  if (start < 0) {
    // Open on the first section that still has something to score.
    start = sections.findIndex(function (s) {
      return s.querySelector('.scale-clear input:checked') !== null && s.querySelector("[data-avg]");
    });
  }
  show(start < 0 ? 0 : start, false);
})();
