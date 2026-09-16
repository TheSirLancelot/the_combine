// Turn ordinary links into partial swaps, and never leave a tap looking ignored.
//
// Every link here already works on its own: the server renders the whole page
// for any URL. This only makes the common case cheaper, by asking for the same
// URL with X-Partial and replacing the main region. If anything at all goes
// wrong it falls through to a normal navigation, which is why there is no error
// handling beyond letting the browser do its job.
//
// The waiting behaviour is the part that needed care. Dimming the page you are
// leaving and holding it until the next one arrives is indistinguishable from
// the tap not registering, and the wire view can take a few seconds. So the
// page changes at once: a bar starts at the top and the content becomes an
// outline of what is coming.

(function () {
  const main = () => document.querySelector("main");

  const bar = document.createElement("div");
  bar.id = "bar";
  document.addEventListener("DOMContentLoaded", () => document.body.append(bar));

  function start() {
    bar.className = "";
    void bar.offsetWidth;
    bar.className = "on";
  }
  function stop() {
    bar.className = "done";
  }

  // An outline, not a spinner. A spinner says "something is happening"; this
  // says "a heading and a list of rows are happening", which is a smaller step
  // from what was there to what arrives.
  function skeleton(title, rows) {
    const line = () =>
      '<div class="skel-row"><div class="skel a"></div>' +
      '<div><div class="skel b"></div><div class="skel c"></div></div>' +
      '<div class="skel d"></div></div>';
    return (
      (title ? `<h1>${title}</h1>` : '<div class="skel skel-h"></div>') +
      '<div class="skel skel-p"></div>' +
      '<div class="card">' + line().repeat(rows || 6) + "</div>"
    );
  }

  async function go(url, push, title) {
    const el = main();
    if (!el) { location.href = url; return; }
    start();
    el.innerHTML = skeleton(title, 6);
    let html;
    try {
      const res = await fetch(url, { headers: { "X-Partial": "1" } });
      if (!res.ok) throw new Error(res.status);
      html = await res.text();
    } catch (e) {
      location.href = url;
      return;
    }
    stop();
    el.innerHTML = html;
    el.classList.remove("swap");
    void el.offsetWidth;
    el.classList.add("swap");
    if (push) history.pushState({}, "", url);
    mark(url);
    window.scrollTo({ top: 0 });
  }

  // Keep the tab bar and the league chips showing where you actually are.
  function mark(url) {
    const at = new URL(url, location.origin);
    document.querySelectorAll("nav.tabs a").forEach((a) => {
      const to = new URL(a.getAttribute("href"), location.origin);
      a.setAttribute("aria-current",
        to.pathname === at.pathname ? "page" : "false");
    });
    document.querySelectorAll(".chip[data-league]").forEach((a) => {
      a.setAttribute("aria-current",
        String(a.dataset.league === (at.searchParams.get("league") || "")));
    });
  }

  document.addEventListener("click", (ev) => {
    const a = ev.target.closest("a[data-nav]");
    if (!a || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
    ev.preventDefault();
    // The tab's own label is the heading of the page it is fetching, so the
    // outline can be titled rather than starting as another grey block.
    const label = a.querySelector("span");
    go(a.href, true, a.dataset.title || (label ? label.textContent : ""));
  });

  // A tool form posts to its own endpoint and renders in place, so working
  // through half a dozen trade ideas never reloads the page.
  document.addEventListener("submit", async (ev) => {
    const form = ev.target.closest("form[data-inline]");
    if (!form) return;
    ev.preventDefault();
    const box = document.querySelector(form.dataset.inline);
    if (!box) return;
    start();
    box.innerHTML = '<div class="card">' +
      '<div class="skel-row"><div class="skel a"></div>' +
      '<div><div class="skel b"></div><div class="skel c"></div></div>' +
      '<div class="skel d"></div></div>'.repeat(3) + "</div>";
    const body = new URLSearchParams(new FormData(form));
    try {
      const res = await fetch(form.action, { method: "POST", body });
      box.innerHTML = await res.text();
    } catch (e) {
      box.innerHTML = '<p class="note bad">That did not come back. Try again.</p>';
      stop();
      return;
    }
    // A ticket rather than an answer: keep the bar running and start polling.
    if (box.querySelector("[data-job]")) { watch(box); return; }
    stop();
  });

  // --- searchable pickers ------------------------------------------------
  //
  // Progressive enhancement, not replacement. The <select> stays in the DOM
  // holding the value, so the form posts exactly what it posted before and a
  // browser with this script blocked still gets a working page.

  const SHOW = 40;   // rendered at a time; 350 options is a slow list to paint

  function enhance(sel) {
    if (sel.dataset.picked) return;
    sel.dataset.picked = "1";
    const multi = sel.multiple;
    const all = [...sel.options].map((o) => ({ v: o.value, t: o.textContent.trim() }));

    const box = document.createElement("div");
    box.className = "picker";
    const chosen = document.createElement("div");
    chosen.className = "chosen";
    const input = document.createElement("input");
    input.type = "search";
    input.autocomplete = "off";
    input.setAttribute("autocapitalize", "off");
    input.setAttribute("autocorrect", "off");
    input.placeholder = multi ? "Search, tap to add" : "Search";
    const opts = document.createElement("div");
    opts.className = "opts";

    sel.parentNode.insertBefore(box, sel);
    box.append(chosen, input, opts, sel);
    sel.style.display = "none";
    sel.size = 0;

    const picked = () => [...sel.selectedOptions].map((o) => o.value);

    function drawChips() {
      chosen.innerHTML = "";
      if (!multi) return;
      picked().forEach((v) => {
        const row = all.find((a) => a.v === v);
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = row ? row.t : v;
        const x = document.createElement("button");
        x.type = "button";
        x.textContent = "×";
        x.setAttribute("aria-label", "Remove " + (row ? row.t : v));
        x.onclick = () => { setValue(v, false); };
        tag.append(x);
        chosen.append(tag);
      });
    }

    function setValue(v, on) {
      for (const o of sel.options) {
        if (o.value === v) o.selected = on;
        else if (!multi && on) o.selected = false;
      }
      drawChips();
      if (!multi) {
        const row = all.find((a) => a.v === v);
        input.value = row ? row.t : "";
        box.classList.remove("open");
      }
      draw();
    }

    function draw() {
      const q = input.value.trim().toLowerCase();
      const on = new Set(picked());
      const hits = all.filter((a) => !q || a.t.toLowerCase().includes(q));
      opts.innerHTML = "";
      if (!hits.length) {
        opts.innerHTML = '<div class="none">Nobody by that name.</div>';
        return;
      }
      hits.slice(0, SHOW).forEach((a) => {
        const el = document.createElement("div");
        el.className = "opt" + (on.has(a.v) ? " here" : "");
        el.innerHTML = '<span class="tick">' + (on.has(a.v) ? "✓" : "") +
                       "</span><span></span>";
        el.lastChild.textContent = a.t;
        el.onmousedown = (ev) => {
          ev.preventDefault();
          setValue(a.v, multi ? !on.has(a.v) : true);
          if (multi) input.select();
        };
        opts.append(el);
      });
      if (hits.length > SHOW) {
        const more = document.createElement("div");
        more.className = "more";
        more.textContent = hits.length - SHOW + " more. Keep typing.";
        opts.append(more);
      }
    }

    input.addEventListener("focus", () => { box.classList.add("open"); draw(); });
    input.addEventListener("input", () => { box.classList.add("open"); draw(); });
    input.addEventListener("blur", () =>
      setTimeout(() => box.classList.remove("open"), 120));
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") { input.blur(); }
      if (ev.key === "Enter") {
        ev.preventDefault();
        const first = opts.querySelector(".opt");
        if (first) first.onmousedown(new Event("x"));
      }
    });

    if (!multi && sel.selectedIndex >= 0) {
      input.value = all[sel.selectedIndex].t;
    }
    drawChips();
  }

  // --- long jobs ----------------------------------------------------------
  //
  // A trade search outlives the request that asked for it. The POST comes back
  // with a ticket, and this walks it: poll, swap when the answer lands, count
  // the seconds in the meantime so the card is visibly alive rather than
  // merely present.

  const POLL = 1500;

  function watch(box) {
    const card = box.querySelector("[data-job]");
    if (!card) return;
    const id = card.dataset.job;
    const tick = card.querySelector(".tick");
    let at = parseInt((tick && tick.dataset.since) || "0", 10);

    const count = setInterval(() => {
      at += 1;
      const now = box.querySelector(".tick");
      if (now) now.textContent = at + "s";
    }, 1000);

    const poll = setInterval(async () => {
      // The card going away means the person navigated or asked for something
      // else. The job carries on; we simply stop caring about it.
      if (!box.querySelector("[data-job]")) {
        clearInterval(poll); clearInterval(count); return;
      }
      let html;
      try {
        const res = await fetch("/jobs/" + id);
        if (!res.ok) return;              // a dropped poll is a missed tick
        html = await res.text();
      } catch (e) {
        return;
      }
      if (!box.querySelector("[data-job]")) {
        clearInterval(poll); clearInterval(count); return;
      }
      box.innerHTML = html;
      if (!box.querySelector("[data-job]")) {
        clearInterval(poll); clearInterval(count); stop();
      }
    }, POLL);
  }

  // --- auto refresh ------------------------------------------------------
  //
  // Only the scores page asks for this, and only while you are looking at it.
  // Two things make a silent reload different from the navigation above: there
  // is no skeleton, because replacing a live score with a grey box every thirty
  // seconds is worse than a stale one, and the page has to come back the way
  // you left it — the same cards open, the same scroll position. The server has
  // no idea which cards you opened, so that has to be carried across the swap
  // here.

  const TICK = 30000;   // matches the memo behind /scores; faster only re-reads
  let beat = null;
  let left = 0;

  function on() {
    try { return localStorage.getItem("autorefresh") === "1"; }
    catch (e) { return false; }
  }
  function remember(v) {
    try { localStorage.setItem("autorefresh", v ? "1" : "0"); } catch (e) {}
  }

  function label() {
    const note = document.getElementById("autowhen");
    if (!note) return;
    const box = document.getElementById("autorefresh");
    const live = box && box.checked;
    note.textContent = live ? "in " + Math.max(0, Math.round(left / 1000)) + "s"
                            : "off";
    note.className = live ? "on" : "";
  }

  async function beat_once() {
    const box = document.getElementById("autorefresh");
    const el = main();
    if (!box || !box.checked || !el) return;
    const open = [...document.querySelectorAll("details.game[open]")]
      .map((d) => d.dataset.game);
    const y = window.scrollY;
    let html;
    try {
      const res = await fetch(location.href, { headers: { "X-Partial": "1" } });
      if (!res.ok) return;
      html = await res.text();
    } catch (e) {
      return;   // a dropped request is a missed tick, not a broken page
    }
    if (!document.getElementById("autorefresh")) return;   // navigated mid-flight
    el.innerHTML = html;
    open.forEach((k) => {
      const d = document.querySelector('details.game[data-game="' + k + '"]');
      if (d) d.open = true;
    });
    const again = document.getElementById("autorefresh");
    if (again) again.checked = true;
    window.scrollTo({ top: y });
    arrived();
  }

  function pump() {
    if (beat) clearInterval(beat);
    beat = setInterval(() => {
      const box = document.getElementById("autorefresh");
      if (!box) { clearInterval(beat); beat = null; return; }
      if (!box.checked) { left = TICK; label(); return; }
      left -= 1000;
      if (left <= 0) { left = TICK; beat_once(); }
      label();
    }, 1000);
  }

  function autowire() {
    const box = document.getElementById("autorefresh");
    if (!box || box.dataset.wired) { if (box) label(); return; }
    box.dataset.wired = "1";
    box.checked = on();
    box.addEventListener("change", () => {
      remember(box.checked);
      left = TICK;
      label();
      if (box.checked) beat_once();
    });
    left = TICK;
    label();
    pump();
  }

  function enhanceAll() {
    document.querySelectorAll("form.tool select").forEach(enhance);
  }

  // Everything that has to happen to a freshly arrived page, in one place, so
  // a first load and a partial swap cannot drift apart.
  function arrived() {
    enhanceAll();
    autowire();
  }

  const swapped = new MutationObserver(arrived);
  document.addEventListener("DOMContentLoaded", () => {
    arrived();
    const el = main();
    if (el) swapped.observe(el, { childList: true });
  });

  window.addEventListener("popstate", () => go(location.href, false, ""));
})();
