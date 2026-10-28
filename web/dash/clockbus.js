// The shared run bus: the main page broadcasts the clock tick, the phase and
// the active step; a solo pop-out (?view=arch&solo=1 and friends) listens.
// Same channel name on both ends so the two pages stay in sync.
export const CLOCK_CHANNEL = "wattnot-clock";

export function broadcast(msg) {
  try {
    const bc = new BroadcastChannel(CLOCK_CHANNEL);
    bc.postMessage(msg);
    bc.close();
  } catch {
    /* no broadcast support here; the page still works on its own */
  }
}

export function listen(fn) {
  try {
    const bc = new BroadcastChannel(CLOCK_CHANNEL);
    bc.onmessage = (ev) => {
      try { fn(ev.data); } catch { /* one bad message must not stop the rest */ }
    };
    return () => bc.close();
  } catch {
    return () => {};
  }
}
