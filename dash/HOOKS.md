# WattNot UI Integration Hooks

This document specifies the integration hooks provided by the WattNot dashboard UI for voice guide controls and explanation popovers.

## 1. Top Bar Voice / Mic Slot

Every primary dashboard page provides a dedicated, fixed-dimension container in its top bar:

```html
<span id="wn-mic-slot" class="wn-mic-slot" aria-live="polite"></span>
```

### Pages with the Mic Slot
- `runs.html` (All runs dashboard)
- `report.html` (Run report breakdown)
- `v2.html` (Live / compare mind map dashboard)
- `graph.html` (Architecture graph)

*Note: The minimal landing page (`index.html`) intentionally does not include the top navigation bar or the mic slot.*

### Slot Specifications
- **Element ID**: `wn-mic-slot`
- **Class**: `wn-mic-slot`
- **Dimensions**: Reserved 32×32px (`min-width: 32px; min-height: 32px; flex: 0 0 32px;`) to prevent layout shifts when a button is injected.
- **Positioning**: Right-aligned in the top navigation bar directly preceding the source/replay status chip.
- **Accessibility**: Includes `aria-live="polite"` for non-intrusive voice/status updates.

---

## 2. Explain Attributes on Number Cards (`data-explain`)

Every key numeric metric, breakdown card, and mind-map node exposes structured attributes for contextual explainers and inspection popovers:

| Attribute | Purpose | Example Values |
|---|---|---|
| `data-explain` | Stable hierarchical identifier | `report.saving`, `runs.run2-llama-350w.saving`, `compare.power.agent-waste` |
| `data-explain-value` | Formatted value string | `27.1%`, `93.7%`, `15.96 J`, `Passed`, `Signed` |
| `data-explain-label` | Data provenance classification | `MEASURED`, `ESTIMATE`, `MOCK`, `SIMULATED`, `REPLAY`, `pending` |
| `data-explain-source` | Field or formula origin | `phases.wattnot.metrics.saved_pct`, `receipt.context_saver.tokens_cut_pct` |

---

## 3. Stable Identifier Conventions

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
