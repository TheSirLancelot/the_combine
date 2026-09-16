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
    }
    stop();
  });

  window.addEventListener("popstate", () => go(location.href, false, ""));
})();
