// Turn ordinary links into partial swaps.
//
// Every link here already works on its own: the server renders the whole page
// for any URL. This only makes the common case cheaper, by asking for the same
// URL with X-Partial and replacing the main region. If anything at all goes
// wrong it falls through to a normal navigation, which is why there is no error
// handling beyond letting the browser do its job.

(function () {
  const main = () => document.querySelector("main");

  async function go(url, push) {
    const el = main();
    if (!el) { location.href = url; return; }
    el.classList.add("busy");
    let html;
    try {
      const res = await fetch(url, { headers: { "X-Partial": "1" } });
      if (!res.ok) throw new Error(res.status);
      html = await res.text();
    } catch (e) {
      location.href = url;
      return;
    }
    el.classList.remove("busy");
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
    go(a.href, true);
  });

  // A tool form posts to its own endpoint and renders in place, so working
  // through half a dozen trade ideas never reloads the page.
  document.addEventListener("submit", async (ev) => {
    const form = ev.target.closest("form[data-inline]");
    if (!form) return;
    ev.preventDefault();
    const box = document.querySelector(form.dataset.inline);
    if (!box) return;
    box.classList.add("busy");
    const body = new URLSearchParams(new FormData(form));
    try {
      const res = await fetch(form.action, { method: "POST", body });
      box.innerHTML = await res.text();
    } catch (e) {
      box.innerHTML = '<p class="note bad">That did not come back. Try again.</p>';
    }
    box.classList.remove("busy");
  });

  window.addEventListener("popstate", () => go(location.href, false));
})();
