# WattNot UI Integration Hooks

This document specifies the integration hooks and components provided by the WattNot dashboard UI for voice guide controls, explanation popovers, and chrome navigation.

## 1. Shared Chrome Components (`chrome.js` / `chrome.css`)

All pages include `chrome.css` and `chrome.js`:
- Landing page (`index.html`)
- Runs dashboard (`runs.html`)
- Run report (`report.html`)
- Live / Compare mind map (`v2.html`)
- Live graph (`graph.html`)
- Live run monitor (`web/run/index.html` via `../dash/chrome.js` and `../dash/chrome.css`)

### Floating Navigation Header (`nav.wn-nav.gl`)
A fixed/sticky floating glass bar styled to match the visionOS/Liquid Glass aesthetic:
- **Brand**: Wordmark `WattNot` linking to `index.html`.
- **Primary Links**:
  - `Dashboard` (`runs.html`)
  - `Runs` (`runs.html#runs`)
  - `Compare` (`v2.html#pReport`)
  - `Reports` (`report.html?run=run2-llama-350w`)
  - `Live graph` (`graph.html`)
- **Active Navigation State**: Highlighted via `.on` class and `aria-current="page"`, updated automatically on page load and on `hashchange` events.
- **Voice / Mic Slot Container**: Right-aligned inside `.wn-slot-wrap` on app pages.

### Minimal Glass Footer (`footer.wn-footer.gl`)
A glass footer at the bottom of the page containing:
- "Project by Akash K Raju", "Meta hackathon 2026 · Indie track", and year 2026.
- GitHub repository link (`https://github.com/ak9381/wattnot`).
- Compact data-label legend (`MEASURED`, `ESTIMATE`, `MOCK`) with lavender/dusk indicators.
- "No compliance claims" note.

---

## 2. Top Bar Voice / Mic Slot (`#wn-mic-slot`)

On all app pages, a dedicated container is provided in the floating navigation header:

```html
<span id="wn-mic-slot" class="wn-mic-slot" aria-live="polite"></span>
```

### Pages with the Mic Slot
- `runs.html` (All runs dashboard)
- `report.html` (Run report breakdown)
- `v2.html` (Live / compare mind map dashboard)
- `graph.html` (Live graph)
- `web/run/index.html` (Live run monitor)

*Note: The landing page (`index.html`) does not host the mic slot.*

### Slot Specifications
- **Element ID**: `wn-mic-slot`
- **Class**: `wn-mic-slot`
- **Dimensions**: Reserved 32×32px (`min-width: 32px; min-height: 32px; flex: 0 0 32px;`) to prevent layout shifts when a button is injected.
- **Location**: Injected/moved into `.wn-slot-wrap` inside `nav.wn-nav.gl`.
- **Deduplication**: Strictly one `#wn-mic-slot` exists in the DOM.
- **Accessibility**: Includes `aria-live="polite"` for non-intrusive voice/status updates.

---

## 3. Explain Attributes on Number Cards (`data-explain`)

Every key numeric metric, breakdown card, and mind-map node exposes structured attributes for contextual explainers and inspection popovers:

| Attribute | Purpose | Example Values |
|---|---|---|
| `data-explain` | Stable hierarchical identifier | `report.saving`, `runs.run2-llama-350w.saving`, `compare.power.agent-waste` |
| `data-explain-value` | Formatted value string | `27.1%`, `93.7%`, `15.96 J`, `Passed`, `Signed` |
| `data-explain-label` | Data provenance classification | `MEASURED`, `ESTIMATE`, `MOCK`, `SIMULATED`, `REPLAY`, `pending` |
| `data-explain-source` | Field or formula origin | `phases.wattnot.metrics.saved_pct`, `receipt.context_saver.tokens_cut_pct` |

---

## 4. Stable Identifier Conventions

### Report Page (`report.js`)
- `report.saving`: Hero energy savings percentage.
- `report.quality`: Hero quality retention and gate verdict.
- `report.receipt`: Hero cryptographic receipt status.
- `report.step.room`: Breakdown step 1 (SAM room scan).
- `report.step.baseline`: Breakdown step 2 (Stock unconstrained baseline).
- `report.step.context`: Breakdown step 3 (Context saver token compression).
- `report.step.gate`: Breakdown step 4 (Quality and latency gate evaluation).
- `report.step.change`: Breakdown step 5 (Change tried and power cap).
- `report.step.kept`: Breakdown step 6 (Decision outcome - kept or rolled back).
- `report.step.receipt`: Breakdown step 7 (Offline receipt signature and verification).

### Runs Dashboard (`runs.js`)
- `runs.total.energy`: Total measured energy saved across runs.
- `runs.total.water`: Estimated cooling water avoided.
- `runs.total.cost`: Estimated electricity cost savings.
- `runs.total.runs`: Total recorded runs count.
- `runs.<runid>`: Entire table row for the specified run.
- `runs.<runid>.saving`: Energy saving percentage cell for `<runid>`.
- `runs.<runid>.p99`: Tail latency change (p99) cell for `<runid>`.
- `runs.<runid>.quality`: Output quality score cell for `<runid>`.

### Compare Mind Map (`v2.js`)
- `compare.power`: Primary Power metric node.
- `compare.cost`: Primary Cost metric node.
- `compare.water`: Primary Water metric node.
- `compare.<metric>.<lever>`: Individual levers under the active metric, including:
  - `compare.<metric>.agent-waste`: Agent-waste saver lever.
  - `compare.<metric>.where`: Workload placement router lever.
  - `compare.<metric>.gate`: Quality gate evaluation node.
  - `compare.<metric>.sam`: Cooling hotspots lever.
  - Deeper levers: `caps`, `topk`, `quant`, `capsweep`, `clocks`, `prefix`, `memcomp`, `dupcut`, etc.

---

## 5. UI Events and Interaction Hooks

- **Hash Navigation (`hashchange`)**:
  - The shared navigation chrome and `v2.js` listen to `hashchange` events to synchronize active states and page view transitions (`#pReport`, `#pCfg`, `#pProof`, etc.).
- **Popover Triggering**:
  - Popover controllers should attach delegated click or hover listeners on `[data-explain]` elements:
    ```javascript
    document.addEventListener("click", (e) => {
      const target = e.target.closest("[data-explain]");
      if (!target) return;
      const id = target.getAttribute("data-explain");
      const val = target.getAttribute("data-explain-value");
      const label = target.getAttribute("data-explain-label");
      const source = target.getAttribute("data-explain-source");
      // Open contextual explanation popover
    });
    ```
