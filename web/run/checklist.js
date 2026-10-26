// Recommendations -> a checklist with one primary action per item. Pure; storage is injected.

const ACTIONS = {
  power_cap_w: { kind: "apply_cap", button: "Apply cap", icon: "zap" },
  knob: { kind: "copy_flag", button: "Copy vLLM flag", icon: "copy" },
  usd_saved: { kind: "download_receipt", button: "Download receipt", icon: "download" },
  water_saved_l: { kind: "download_receipt", button: "Download receipt", icon: "download" },
  wh_saved_pct: { kind: "download_receipt", button: "Download receipt", icon: "download" },
  headroom_w_per_gpu: { kind: "copy_note", button: "Copy for capacity plan", icon: "copy" },
  headroom_w_per_rack: { kind: "copy_note", button: "Copy for capacity plan", icon: "copy" },
  extra_gpus_per_rack: { kind: "copy_note", button: "Copy for capacity plan", icon: "copy" },
  reverify: { kind: "schedule_rerun", button: "Schedule re-run", icon: "calendar-clock" },
};

export function fmtValue(a) {
  if (a.value === null || a.value === undefined || a.value === "") return "";
  if (a.unit === "USD") return `$${Number(a.value).toFixed(2)}`;
  if (typeof a.value === "number") return `${a.value.toLocaleString("en-US")}${a.unit ? " " + a.unit : ""}`.replace(" %", "%");
  return String(a.value);
}

export function buildChecklist(rec) {
  if (!rec) return [];
  return rec.actions.map((a, i) => {
    const act = ACTIONS[a.key] || { kind: "mark", button: "Mark done", icon: "check" };
    return { id: a.key || `item-${i}`, label: a.label, text: a.text, value: fmtValue(a), ...act, raw: a };
  });
}

// What the primary button hands the user. Nothing is executed on any machine.
export function actionPayload(item, ctx = {}) {
  const gpu = ctx.gpuIndex ?? 0;
  switch (item.kind) {
    case "apply_cap":
      return { type: "copy", text: `sudo nvidia-smi -i ${gpu} -pl ${item.raw.value}` };
    case "copy_flag": {
      const [k, v] = String(item.raw.value).split("=");
      const flag = "--" + k.replace(/_/g, "-");
      return { type: "copy", text: v === "True" || v === "true" ? flag : v === "False" || v === "false" ? `--no-${flag.slice(2)}` : `${flag} ${v}` };
    }
    case "copy_note":
      return { type: "copy", text: `${item.label}: ${item.text}` };
    case "download_receipt":
      return { type: "download", filename: `wattnot-${ctx.runId || "run"}-receipt.json`, json: ctx.receipt || {} };
    case "schedule_rerun": {
      const days = ctx.days ?? 30;
      return { type: "download", filename: "wattnot-rerun.ics", text: ics(ctx.now || new Date(), days, ctx.runId) };
    }
    default:
      return { type: "none" };
  }
}

function ics(now, days, runId) {
  const t = new Date(now.getTime() + days * 86400000);
  const z = (d) => d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  return ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//WattNot//rerun//EN", "BEGIN:VEVENT",
    `UID:wattnot-rerun-${runId || "run"}`, `DTSTAMP:${z(now)}`, `DTSTART:${z(t)}`,
    `SUMMARY:Re-run WattNot gate (${runId || "run"})`,
    "DESCRIPTION:Re-verify the power cap after model or traffic changes.",
    "END:VEVENT", "END:VCALENDAR"].join("\r\n");
}

export const storageKey = (runId) => `wattnot.checklist.${runId || "run"}`;

export function loadDone(storage, runId) {
  try { return new Set(JSON.parse(storage.getItem(storageKey(runId)) || "[]")); } catch { return new Set(); }
}

export function toggle(storage, runId, done, id) {
  const next = new Set(done);
  if (next.has(id)) next.delete(id); else next.add(id);
  storage.setItem(storageKey(runId), JSON.stringify([...next].sort()));
  return next;
}

export function progress(items, done) {
  const n = items.filter((i) => done.has(i.id)).length;
  return { done: n, total: items.length, text: `${n} of ${items.length} done`, pct: items.length ? Math.round((100 * n) / items.length) : 0 };
}
