// Draws a RUN2 room_scan payload (step.progress with data.kind == "room_scan") with the room module's node
// (web/room/node.js). The payload is a trimmed scan, so the picture size and image names come from the saved
// room folder next to it. Call again with the "after" payload to switch the node to the after view.
export async function mountRoomScan(host, pays, cache = {}) {
  const b = pays.before || pays.after, a = pays.after;
  const { mountNode } = await import("/room/node.js"), base = `/room/data/${b.room_id}/`;
  cache.base ||= await fetch(base + "scan.json").then((r) => r.json()).catch(() => ({}));
  const f = cache.base;
  const scan = { ...f, ...b, size: b.size || f.size, thermal_image: b.thermal_image || f.thermal_image || "thermal.jpg",
    thermal_after_image: a?.thermal_image || f.thermal_after_image, racks: (a || b).racks, before: b.summary,
    after: a ? a.summary : b.summary, labels: { ...(f.labels || {}), ...b.labels }, sam: b.sam };
  const m = await mountNode(host, { scan, base });
  if (a) { m.state.phase = "after"; m.draw(); }
  return { labels: b.labels || {} };
}
