// The six runs the UI lists. Every other run under replays/ stays on disk but is not shown.
// Plugin = Nov 4 runs through the WattNot plug-in; Test = the Nov 2-3 full test rounds.
export const RUN_NAMES = {
  "plugin-h100-1104": "Plugin H100",
  "plugin-modal-1104": "Plugin Modal",
  "plugin-baseten-1104": "Plugin Baseten",
  "h100-max-1103": "Test H100",
  "modal-r2": "Test run Modal",
  "baseten-r1": "Test run Baseten",
};
export const REPLAY_IDS = Object.keys(RUN_NAMES);
export const DEFAULT_RUN = "plugin-h100-1104";
export const runName = (id) => RUN_NAMES[String(id).replace(/^replay:/, "")] || id;
// What each run was configured as, from its own manifest (replays/<id>/manifest.json).
// The header shows this for a replayed run instead of the box's current settings.
// energy is only set where the run's own record labels it.
export const RUN_CFG = {
  "plugin-h100-1104": { provider: "E2E Networks H100 80GB (vLLM, WattNot gateway)", model: "Muse Glimmer 30B", energy: "MEASURED" },
  "plugin-modal-1104": { provider: "Modal A100", model: "Muse Glimmer 30B" },
  "plugin-baseten-1104": { provider: "Baseten (hosted Spark Open, WattNot gateway)", model: "Muse Spark Open", energy: "ESTIMATE" },
  "h100-max-1103": { provider: "E2E Networks H100 80GB (vLLM)", model: "Muse Glimmer 30B" },
  "modal-r2": { provider: "Modal" },
  "baseten-r1": { provider: "Baseten" },
};
