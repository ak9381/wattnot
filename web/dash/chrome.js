// Shared navigation header & footer chrome across WattNot dashboard and run pages.
(function () {
  function initChrome() {
    if (document.querySelector("nav.wn-nav.gl")) return;

    const pathname = window.location.pathname || "";
    const isRunPage = pathname.includes("/run/") || pathname.includes("/room/") || document.querySelector('script[src*="../dash/chrome.js"]');
    const prefix = isRunPage ? "../dash/" : "";

    const isLanding = !!(
      document.querySelector(".hero") ||
      document.body.classList.contains("landing") ||
      pathname.endsWith("/dash/") ||
      pathname.endsWith("/dash/index.html") ||
      (pathname.endsWith("index.html") && !isRunPage)
    );

    if (isLanding) {
      document.body.classList.add("is-landing");
      const existingMic = document.getElementById("wn-mic-slot");
      if (existingMic) existingMic.remove();
    }

    // 1. Injected Header
    const isV2Page = pathname.includes("v2.html") || !!document.querySelector("#pReport");
    // One header rule: the shared sticky header is the only nav on every page.
    // v2 sections are reached by in-page anchors (#pCfg, #pRun, ...) and the links below.

    const nav = document.createElement("nav");
    nav.className = "wn-nav gl";
    nav.setAttribute("aria-label", "Main navigation");
    nav.innerHTML = `
      <div class="wn-nav-inner">
        <a class="wn-brand" href="${prefix}index.html">Watt<i>Not</i></a>
        <button class="wn-menu" id="wnMenu" aria-controls="wnSide" aria-expanded="false" aria-label="Open the page menu">Menu</button>
        <div class="wn-links" style="margin-left:auto">
          <a class="wn-link" data-top="configure" href="${prefix}plug.html#setup">Plug in</a>
          <a class="wn-link" data-top="run" href="${prefix}plug.html#live">Live savings</a>
          <a class="wn-link" data-top="results" href="${prefix}report.html">Results</a>
          <a class="wn-link" data-top="proof" href="${prefix}approvals.html">Proof</a>
          <a class="wn-link" data-top="costs" href="${prefix}costbudget.html">Costs</a>
          <a class="wn-link" data-top="connected" href="${prefix}metatools.html">Connected tools</a>
        </div>
        <div class="wn-slot-wrap" id="wn-slot-wrap"></div>
      </div>
    `;

    document.body.prepend(nav);
    // Left sidebar: every route, grouped. The current group is open; its page sections are filled in by sidenav.js.
    const side = document.createElement("aside");
    side.className = "wn-side"; side.id = "wnSide"; side.setAttribute("aria-label", "All pages");
    const G = [
      ["overview", "Start here", [["dashboard", "v2.html#pHome", "Home"], ["learn", "v2.html#pLearn", "How it works"]]],
      ["configure", "Plug in", [["plug", "plug.html#setup", "Plug in (3 steps)"], ["configure", "v2.html#pCfg", "Test run setup (advanced)"]]],
      ["run", "Live savings", [["plugl", "plug.html#live", "Live savings"], ["graph", "graph.html", "Test run view (replay)"], ["runs", "runs.html", "Past runs"], ["matrix", "matrix.html", "All test rounds"], ["replay", "v2.html#pRun", "Replay a run"]]],
      ["results", "Results", [["reports", "report.html", "Report"], ["compare", "v2.html#pReport", "With vs without"]]],
      ["proof", "Proof", [["approvals", "approvals.html", "Approvals"], ["proofs", "v2.html#pProof", "Signed receipts"]]],
      ["costs", "Costs", [["costbudget", "costbudget.html", "Cost budget"], ["eu", "eu.html", "EU energy report"]]],
      ["connected", "Connected tools", [["metatools", "metatools.html", "Connected tools"]]],
    ];
    const LBL = {};
    side.innerHTML = `<div class="wn-side-run" id="wnSideRun"></div>` + G.map(([g, name, items]) => `<details class="wn-grp" data-group="${g}"><summary>${name}</summary><ul>${items.map(([k, h, t]) =>
      `<li><a class="wn-link" data-nav="${k}" href="${prefix}${h}">${LBL[k] || t}</a><ul class="wn-secs" data-secs-for="${k}"></ul></li>`).join("")}</ul></details>`).join("");
    document.body.insertBefore(side, nav.nextSibling);
    document.body.classList.add("wn-has-side");
    const scrim = document.createElement("div"); scrim.className = "wn-side-scrim"; scrim.hidden = true; document.body.appendChild(scrim);
    const menu = nav.querySelector("#wnMenu");
    const setMenu = (on) => { document.body.classList.toggle("wn-side-open", on); menu.setAttribute("aria-expanded", String(on)); scrim.hidden = !on; };
    menu.addEventListener("click", () => setMenu(!document.body.classList.contains("wn-side-open")));
    scrim.addEventListener("click", () => setMenu(false));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") setMenu(false); });
    side.addEventListener("click", (e) => { if (e.target.closest("a")) setMenu(false); });
    if (!document.querySelector('script[src$="sidenav.js"]')) {
      const sn = document.createElement("script"); sn.type = "module"; sn.src = prefix + "sidenav.js"; document.head.appendChild(sn);
    }
    // Shared eligibility bar + greying (configuration from the Configure screen).
    if (!document.querySelector('link[href$="elig.css"]')) {
      const l = document.createElement("link"); l.rel = "stylesheet"; l.href = prefix + "elig.css"; document.head.appendChild(l);
      const m = document.createElement("script"); m.type = "module"; m.src = prefix + "elig.js"; document.head.appendChild(m);
    }

    if (!document.querySelector('script[src$="runpick.js"]')) {
      const rp = document.createElement("script"); rp.type = "module"; rp.src = prefix + "runpick.js"; document.head.appendChild(rp);
    }
    if (!document.querySelector('script[src$="elig.js"]')) {
      const m2 = document.createElement("script"); m2.type = "module"; m2.src = prefix + "elig.js"; document.head.appendChild(m2);
    }

    // 2. Mic slot positioning on app pages (never on landing)
    if (!isLanding) {
      // Glow blobs behind content on app pages (never on landing)
      if (!document.querySelector(".wn-glow-blobs")) {
        const blobs = document.createElement("div");
        blobs.className = "wn-glow-blobs";
        blobs.setAttribute("aria-hidden", "true");
        blobs.innerHTML = `
          <div class="wn-blob wn-blob-1"></div>
          <div class="wn-blob wn-blob-2"></div>
          <div class="wn-blob wn-blob-3"></div>
        `;
        document.body.prepend(blobs);
      }

      const slotWrap = nav.querySelector("#wn-slot-wrap");
      let mic = document.getElementById("wn-mic-slot");
      if (mic) {
        slotWrap.appendChild(mic);
      } else {
        mic = document.createElement("span");
        mic.id = "wn-mic-slot";
        mic.className = "wn-mic-slot";
        mic.setAttribute("aria-live", "polite");
        slotWrap.appendChild(mic);
      }

      // Guarantee strictly ONE #wn-mic-slot in DOM
      document.querySelectorAll("#wn-mic-slot").forEach((el) => {
        if (el !== mic) el.remove();
      });

      // Mount voiceguide inside mic slot
      if (window.WattNotVoiceGuide && typeof window.WattNotVoiceGuide.mountVoiceGuide === "function") {
        window.WattNotVoiceGuide.mountVoiceGuide(mic);
      } else {
        import(prefix + "voiceguide.js")
          .then((mod) => {
            if (mod && mod.mountVoiceGuide) mod.mountVoiceGuide(mic);
          })
          .catch(() => {});
      }
    }

    // 2b. Bento layer (quiet labels, one legend, click popups) on every page except the landing page.
    if (!isLanding) {
      const css = document.createElement("link"); css.rel = "stylesheet"; css.href = prefix + "bento.css"; document.head.append(css);
      import(new URL(prefix + "bento.js", window.location.href).href).catch(() => {});
    }

    // 3. Active link updater
    function updateActive() {
      const path = window.location.pathname || "";
      const hash = window.location.hash || "";
      side.querySelectorAll(".wn-link").forEach((link) => {
        link.classList.remove("on");
        link.removeAttribute("aria-current");
      });

      let targetNav = null;
      if (path.includes("configure.html") || (path.includes("v2.html") && hash === "#pCfg")) {
        targetNav = "configure";
      } else if (path.includes("approvals.html")) {
        targetNav = "approvals";
      } else if (path.includes("runs.html")) {
        targetNav = "runs";
      } else if (path.includes("graph.html")) {
        targetNav = "graph";
      } else if (path.includes("matrix.html")) {
        targetNav = "matrix";
      } else if (path.includes("costbudget.html")) {
        targetNav = "costbudget";
      } else if (path.includes("eu.html")) {
        targetNav = "eu";
      } else if (path.includes("metatools.html")) {
        targetNav = "metatools";
      } else if (path.includes("report.html")) {
        targetNav = "reports";
      } else if (path.includes("v2.html") || isV2Page) {
        targetNav = { "#pReport": "compare", "#pLearn": "learn", "#pRun": "replay", "#pProof": "proofs" }[hash] || "dashboard";
      }

      if (targetNav) {
        const activeLink = side.querySelector(`[data-nav="${targetNav}"]`);
        const grp = activeLink && activeLink.closest(".wn-grp");
        side.querySelectorAll(".wn-grp").forEach((d) => { d.open = d === grp; d.classList.toggle("on", d === grp); });
        nav.querySelectorAll("[data-top]").forEach((l) => { const on = grp && l.dataset.top === grp.dataset.group; l.classList.toggle("on", !!on); if (on) l.setAttribute("aria-current", "page"); else l.removeAttribute("aria-current"); });
        if (activeLink) {
          activeLink.classList.add("on");
          activeLink.setAttribute("aria-current", "page");
        }
      }
    }

    updateActive();
    window.addEventListener("hashchange", updateActive);

    // 4. Injected Footer
    const footer = document.createElement("footer");
    footer.className = "wn-footer gl";
    footer.setAttribute("aria-label", "Site footer");
    footer.innerHTML = `
      <div class="wn-footer-inner">
        <div class="wn-footer-left">
          <span class="wn-logo">WattNot</span>
          <span class="wn-footer-sep">·</span>
          <span class="wn-footer-credit">Project by Akash K Raju</span>
          <span class="wn-footer-sep">·</span>
          <span class="wn-footer-track">Meta hackathon 2026 · Indie track</span>
          <span class="wn-footer-sep">·</span>
          <span class="wn-footer-year">2026</span>
        </div>
        <div class="wn-footer-right">
          <a class="wn-footer-gh" href="https://github.com/ak9381/wattnot" target="_blank" rel="noopener">GitHub</a>
        </div>
      </div>
    `;

    document.body.appendChild(footer);
    // The footer is a static full-width black bar at the end of the page, so it needs
    // no content clearance. Keep --wn-foot at 0 for layouts that still read it.
    document.documentElement.style.setProperty("--wn-foot", "0px");
    // Break the bar out of the body box (margin/border/padding — the left column reserves
    // padding-left for the fixed sidebar) so it reaches both viewport edges, edge to edge.
    const fitFooter = () => {
      const cs = getComputedStyle(document.body);
      const br = document.body.getBoundingClientRect();
      const pl = parseFloat(cs.paddingLeft) || 0, pr = parseFloat(cs.paddingRight) || 0;
      const bl = parseFloat(cs.borderLeftWidth) || 0, brw = parseFloat(cs.borderRightWidth) || 0;
      const leftGap = br.left + bl + pl;                         // footer content-box left vs viewport
      const rightGap = (window.innerWidth - br.right) + brw + pr; // and the matching right gap
      footer.style.marginLeft = -leftGap + "px";
      footer.style.marginRight = -rightGap + "px";
    };
    fitFooter();
    window.addEventListener("resize", fitFooter);
    if (window.ResizeObserver) new ResizeObserver(fitFooter).observe(document.body);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initChrome);
  } else {
    initChrome();
  }
})();

// Side drawers (left sidebar + right info drawers) start exactly below the header bar and end
// above the footer whenever it is on-screen. Recomputed on resize, scroll, and whenever a drawer
// opens — so the bounds are right no matter the page or width.
(function wnDrawerBounds() {
  const set = () => {
    const nav = document.querySelector("nav.wn-nav");
    const foot = document.querySelector(".wn-footer");
    const top = nav ? Math.max(0, Math.ceil(nav.getBoundingClientRect().bottom)) + 10 : 16;
    const ft = foot ? foot.getBoundingClientRect().top : innerHeight;
    // When the footer is visible in the viewport, stop the drawers above it; otherwise a small gap.
    const bottom = ft < innerHeight - 1 ? Math.ceil(innerHeight - ft) + 10 : 12;
    document.documentElement.style.setProperty("--wn-drawer-top", top + "px");
    document.documentElement.style.setProperty("--wn-drawer-bottom", bottom + "px");
  };
  let raf = 0;
  const schedule = () => { if (raf) return; raf = requestAnimationFrame(() => { raf = 0; set(); }); };
  addEventListener("resize", schedule);
  addEventListener("scroll", schedule, { passive: true });
  addEventListener("load", () => { set(); setTimeout(set, 600); });
  document.addEventListener("DOMContentLoaded", () => { set(); setTimeout(set, 600); });
  // Catch drawer open/close (body.drawer-open, body.wn-side-open, aside.*.on) and layout changes.
  new MutationObserver(schedule).observe(document.documentElement, { attributes: true, attributeFilter: ["class"], subtree: true, childList: true });
  set();
  window.__wnDrawerBounds = set;
})();
