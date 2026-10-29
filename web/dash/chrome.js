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
        <div class="wn-links" style="margin-left:auto">
          <a class="wn-link" data-nav="dashboard" href="${prefix}v2.html#pHome">Dashboard</a>
          <a class="wn-link" data-nav="runs" href="${prefix}runs.html">Runs</a>
          <a class="wn-link" data-nav="compare" href="${prefix}v2.html#pReport">Compare</a>
          <a class="wn-link" data-nav="reports" href="${prefix}report.html?run=run2-llama-350w">Reports</a>
          <a class="wn-link" data-nav="graph" href="${prefix}graph.html">Live graph</a>
          <a class="wn-link" data-nav="eu" href="${prefix}eu.html">EU Compliance</a>
        </div>
        <div class="wn-slot-wrap" id="wn-slot-wrap"></div>
      </div>
    `;

    document.body.prepend(nav);

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
      nav.querySelectorAll(".wn-link").forEach((link) => {
        link.classList.remove("on");
        link.removeAttribute("aria-current");
      });

      let targetNav = null;
      if (path.includes("runs.html")) {
        targetNav = "runs";
      } else if (path.includes("graph.html")) {
        targetNav = "graph";
      } else if (path.includes("eu.html")) {
        targetNav = "eu";
      } else if (path.includes("report.html")) {
        targetNav = "reports";
      } else if (path.includes("v2.html") || isV2Page) {
        targetNav = hash === "#pReport" ? "compare" : "dashboard";
      }

      if (targetNav) {
        const activeLink = nav.querySelector(`[data-nav="${targetNav}"]`);
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
    // Keep page content clear of the fixed footer: bottom padding = footer height + its offset + a gap.
    const padForFooter = () => {
      const need = footer.offsetHeight + 14 + 16;
      document.documentElement.style.setProperty("--wn-foot", need + "px");
      document.body.style.paddingBottom = "";
      const base = parseFloat(getComputedStyle(document.body).paddingBottom) || 0;
      if (base < need) document.body.style.paddingBottom = need + "px";
    };
    padForFooter();
    if (window.ResizeObserver) new ResizeObserver(padForFooter).observe(footer);
    window.addEventListener("resize", padForFooter);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initChrome);
  } else {
    initChrome();
  }
})();
