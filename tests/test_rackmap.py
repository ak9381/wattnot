from __future__ import annotations

from tests.fixtures.nvml_fake import FakeNVML

from wattnot.meter.base import Reading, write_trace
from wattnot.meter.nvml import NvmlMeter
from wattnot.sam import (
    FALLBACK_BANNER,
    gpu_watts_from_trace,
    load_manual_overlay,
    render_svg,
    slot_regions,
)
from wattnot.sam.segment import repo_root

IMG = repo_root() / "rack" / "rack.jpg"


def test_watts_per_gpu_from_trace(tmp_path):
    rows = [Reading(t / 10, g, 200.0 + 100 * g + t, None, 1800, 2619, 40, 90)
            for t in range(4) for g in (0, 2)]
    path = tmp_path / "t.csv"
    write_trace(path, rows)
    assert gpu_watts_from_trace(path) == {0: 201.5, 2: 401.5}


def test_watts_from_fake_nvml_meter(tmp_path):
    t = {"now": 0.0}

    def sleep(s):
        t["now"] += s

    nv = FakeNVML(power_mw=312_000)
    meter = NvmlMeter(nv, run_id="rackmap", traces_root=tmp_path,
                      clock=lambda: t["now"], sleep=sleep)
    meter.start("w", seconds=1)
    window = meter.stop()
    assert gpu_watts_from_trace(window.csv_path) == {0: 312.0}


def test_slot_regions_mark_touched():
    regions = slot_regions(load_manual_overlay(), {0: 300.0, 1: 250.0}, [1, 3])
    by_gpu = {r["gpu"]: r for r in regions}
    assert sorted(by_gpu) == [0, 1, 2, 3]
    assert by_gpu[1]["touched"] and by_gpu[1]["watts"] == 250.0
    assert by_gpu[3]["touched"] and by_gpu[3]["watts"] is None
    assert not by_gpu[0]["touched"]


def test_render_highlights_and_banner(tmp_path):
    out = render_svg(load_manual_overlay(), IMG, {1: 287.4}, [1], tmp_path / "m.svg",
                     fallback=True)
    svg = out.read_text()
    assert svg.startswith("<svg") and "data:image/jpeg;base64," in svg
    assert "GPU 1  287 W" in svg and "GPU 0  " not in svg
    assert FALLBACK_BANNER in svg
    svg2 = render_svg(load_manual_overlay(), IMG, {}, [], tmp_path / "n.svg",
                      fallback=False).read_text()
    assert "source: manual" in svg2 and FALLBACK_BANNER not in svg2


def _rack_dir(tmp_path):
    import shutil

    d = tmp_path / "rack"
    d.mkdir()
    shutil.copy(IMG, d / "rack.jpg")
    shutil.copy(repo_root() / "rack" / "masks.manual.json", d / "masks.manual.json")
    return d


def test_pick_falls_back_without_sam(tmp_path):
    from wattnot.sam.pick import pick_masks

    _rack_dir(tmp_path)
    doc, fallback = pick_masks(tmp_path)
    assert fallback and doc["source"] == "manual"


def test_pick_falls_back_on_bad_or_cut_sam(tmp_path):
    import json

    from wattnot.sam.pick import pick_masks

    d = _rack_dir(tmp_path)
    (d / "masks.sam.json").write_text("{not json")
    assert pick_masks(tmp_path)[1]
    doc = json.loads((d / "masks.manual.json").read_text())
    doc.update(source="sam3.1", iou_vs_manual=0.5, status="cut")
    (d / "masks.sam.json").write_text(json.dumps(doc))
    assert pick_masks(tmp_path)[1]
    doc.update(iou_vs_manual=0.9, status="ok")
    (d / "masks.sam.json").write_text(json.dumps(doc))
    picked, fallback = pick_masks(tmp_path)
    assert not fallback and picked["source"] == "sam3.1"
