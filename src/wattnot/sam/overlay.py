"""Draw the rack audit on the photo: every unit SAM found, its slot, and the GPU it backs.

Needs Pillow (`pip install wattnot[vision]`). Boxes are SAM's own; green = a declared GPU has a
unit here, amber = a unit the inventory does not list, dashed = the box was inferred from the
height of a stack SAM outlined as one piece. GPUs with no unit are listed in a banner.
"""

from __future__ import annotations

from pathlib import Path

from wattnot.sam.physical import RackAudit

GREEN, AMBER, RED = (46, 160, 67), (230, 140, 0), (200, 40, 40)


def _dashed(d, box, color, width):
    x1, y1, x2, y2 = box
    step = 14
    for x in range(int(x1), int(x2), step * 2):
        d.line([(x, y1), (min(x + step, x2), y1)], fill=color, width=width)
        d.line([(x, y2), (min(x + step, x2), y2)], fill=color, width=width)
    for y in range(int(y1), int(y2), step * 2):
        d.line([(x1, y), (x1, min(y + step, y2))], fill=color, width=width)
        d.line([(x2, y), (x2, min(y + step, y2))], fill=color, width=width)


def render(audit: RackAudit, out: str | Path, *, max_side: int = 1100) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    s = audit.spec
    img = Image.open(Path(s.directory) / s.photo).convert("RGB")
    k = min(1.0, max_side / max(img.size))
    if k < 1:
        img = img.resize((round(img.width * k), round(img.height * k)))
    sx, sy = img.width / audit.scan.width, img.height / audit.scan.height
    d = ImageDraw.Draw(img)
    size = max(14, img.height // 45)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except OSError:
        font = ImageFont.load_default()
    by_slot = {slot: gpu for gpu, slot in audit.verified.items()}
    w = max(2, img.height // 300)
    for u in audit.scan.units:
        x1, y1, x2, y2 = u.box[0] * sx, u.box[1] * sy, u.box[2] * sx, u.box[3] * sy
        gpu = by_slot.get(u.slot)
        color = GREEN if gpu is not None else AMBER
        (d.rectangle if u.how == "outlined" else lambda b, outline, width: _dashed(
            d, b, outline, width))([x1, y1, x2, y2], outline=color, width=w)
        tag = f"slot {u.slot}  GPU {gpu}" if gpu is not None else f"slot {u.slot}  not in inventory"
        if u.how == "inferred":
            tag += "  (inferred)"
        tw = d.textlength(tag, font=font)
        d.rectangle([x1, y1, x1 + tw + 8, y1 + size + 6], fill=color)
        d.text((x1 + 4, y1 + 2), tag, fill="white", font=font)
    lines = [f"{s.id}: {len(audit.scan.units)} units seen, inventory lists {len(s.declared)}",
             f"ceiling {audit.ceiling_w} W per node of {s.budget_w} W (ESTIMATE)",
             "photo: MEASURED   inventory, budget: SIMULATED"]
    if audit.unverified:
        lines.append("no unit seen for GPU " + ", ".join(map(str, audit.unverified))
                     + ": left untouched")
    h = (size + 8) * len(lines) + 8
    d.rectangle([0, img.height - h, img.width, img.height], fill=(20, 20, 20))
    for i, t in enumerate(lines):
        c = RED if t.startswith("no unit") else "white"
        d.text((8, img.height - h + 6 + i * (size + 8)), t, fill=c, font=font)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=88)
    return out
