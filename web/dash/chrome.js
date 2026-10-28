// Shared navigation header & footer chrome across WattNot dashboard and run pages.
(function () {
  function initChrome() {
    if (document.querySelector("nav.wn-nav.gl")) return;

    const pathname = window.location.pathname || "";
    const isRunPage = pathname.includes("/run/") || document.querySelector('script[src*="../dash/chrome.js"]');
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
    const nav = document.createElement("nav");
    nav.className = "wn-nav gl";
    nav.setAttribute("aria-label", "Main navigation");
    nav.innerHTML = `
      <div class="wn-nav-inner">
        <a class="wn-brand" href="${prefix}index.html">Watt<i>Not</i></a>
        <div class="wn-links">
          <a class="wn-link" data-nav="dashboard" href="${prefix}runs.html">Dashboard</a>
          <a class="wn-link" data-nav="runs" href="${prefix}runs.html#runs">Runs</a>
          <a class="wn-link" data-nav="compare" href="${prefix}v2.html#pReport">Compare</a>
          <a class="wn-link" data-nav="reports" href="${prefix}report.html?run=run2-llama-350w">Reports</a>
          <a class="wn-link" data-nav="graph" href="${prefix}graph.html">Live graph</a>
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
        if (hash === "#runs") targetNav = "runs";
        else targetNav = "dashboard";
      } else if (path.includes("graph.html")) {
        targetNav = "graph";
      } else if (path.includes("report.html")) {
        targetNav = "reports";
      } else if (path.includes("v2.html")) {
        targetNav = "compare";
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
        <div class="wn-footer-legend" aria-label="Data provenance legend">
          <span class="wn-leg-item"><i class="wn-leg-dot lab-measured" aria-hidden="true"></i>MEASURED</span>
          <span class="wn-leg-item"><i class="wn-leg-dot lab-estimate" aria-hidden="true"></i>ESTIMATE</span>
          <span class="wn-leg-item"><i class="wn-leg-dot lab-mock" aria-hidden="true"></i>MOCK</span>
        </div>
        <div class="wn-footer-right">
          <span class="wn-footer-note">No compliance claims</span>
          <a class="wn-footer-gh" href="https://github.com/ak9381/wattnot" target="_blank" rel="noopener">GitHub</a>
        </div>
      </div>
    `;

    document.body.appendChild(footer);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initChrome);
  } else {
    initChrome();
  }
})();
