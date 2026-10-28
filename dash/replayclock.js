// One shared replay clock for the dashboard tabs. The Architecture replay
// publishes (play/pause/reset, progress 0..1 across both phases, phase 1/2);
// the Power scene replay and the Experts tick replay subscribe and seek or
// pause to match. The live modes keep their own event streams and ignore it.
export function createClock() {
  const subs = new Set();
  const c = {
    phase: 1, progress: 0, playing: false,
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
    emit(ev) { subs.forEach((fn) => { try { fn(ev); } catch { /* one bad listener must not stop the rest */ } }); },
    setPlaying(on) { c.playing = !!on; c.emit({ type: c.playing ? "play" : "pause", phase: c.phase, progress: c.progress }); },
    setPhase(p) { if (p !== 1 && p !== 2) return; c.phase = p; c.emit({ type: "phase", phase: c.phase, progress: c.progress }); },
    setProgress(f) { c.progress = Math.max(0, Math.min(1, f || 0)); c.emit({ type: "progress", phase: c.phase, progress: c.progress }); },
    reset() { c.phase = 1; c.progress = 0; c.playing = false; c.emit({ type: "reset", phase: 1, progress: 0 }); },
  };
  return c;
}

// The singleton the tabs share. Modules import it directly; it is also on
// window so any tab script can reach the same object without an import cycle.
export const clock = createClock();
if (typeof window !== "undefined") window.__replayClock = clock;
