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
