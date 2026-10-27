"""Render a certificate as one self-contained HTML page (and PDF if a browser is installed).

The watermark is drawn by this module every time. There is no option to leave it off.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from html import escape as _e
from pathlib import Path

from wattnot.certificate import qr
from wattnot.certificate.rules import WATERMARK_LINES

_CSS = """
@page { size: A4; margin: 12mm; }
* { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; color: #1b1f24;
       margin: 0; background: #fff; }
.page { position: relative; max-width: 760px; margin: 0 auto; padding: 28px 34px 24px;
        border: 2px solid #1b1f24; overflow: hidden; min-height: 980px; }
.band { background: #b00020; color: #fff; text-align: center; font-weight: 800; letter-spacing: .12em;
        padding: 8px 6px; margin: -28px -34px 22px; font-size: 13px; }
.mark { position: absolute; left: 0; right: 0; top: 36%; text-align: center; transform: rotate(-24deg);
        font-size: 150px; font-weight: 900; letter-spacing: .08em; color: rgba(176,0,32,.13);
        pointer-events: none; user-select: none; line-height: 1; }
.mark small { display: block; font-size: 30px; letter-spacing: .14em; margin-top: 10px; }
h1 { font-size: 24px; margin: 0 0 2px; }
.sub { color: #57606a; margin-bottom: 18px; }
.serial { font: 600 13px ui-monospace, Menlo, monospace; }
.chips span { display: inline-block; border: 1.5px solid #1b1f24; border-radius: 12px;
              padding: 1px 10px; font-weight: 700; font-size: 12px; margin-right: 6px; }
.chips .sim { border-color: #b26a00; color: #b26a00; }
.head { font-size: 18px; font-weight: 650; margin: 16px 0; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 14px; }
th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid #d0d7de; vertical-align: top; }
th { color: #57606a; font-weight: 600; width: 38%; }
td.n { font-variant-numeric: tabular-nums; }
.two { display: flex; gap: 22px; align-items: flex-start; }
.two > div:first-child { flex: 1; min-width: 0; }
.mono { font: 11px ui-monospace, Menlo, monospace; word-break: break-all; color: #3b434b; }
.limits { font-size: 12.5px; color: #3b434b; padding-left: 18px; margin: 6px 0 0; }
.foot { background: #b00020; color: #fff; text-align: center; font-weight: 800;
        letter-spacing: .1em; padding: 7px; font-size: 12px; margin: 22px -34px -24px; }
"""


def _fmt(x: object, nd: int = 4) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{nd}g}"
    return str(x)


def _range(rg: Mapping | None) -> str:
    return "" if not rg else f" (ESTIMATE range {_fmt(rg.get('low'))} to {_fmt(rg.get('high'))})"


def _rows(c: Mapping) -> list[tuple[str, str]]:
    f, sub, rc = c["figures"], c["subject"], c["receipt"]
    wh, sp = f["wh_per_request"], f["saving_pct"]
    rows = [
        ("Subject", f"{sub.get('model')} on {sub.get('gpu') or sub.get('provider')}"),
        ("Workload", f"{sub.get('workload')}, {c['period'].get('requests')} requests"),
        ("Receipt date", str(c["period"].get("receipt_created_at"))),
        ("Energy per request, baseline", f"{_fmt(wh['baseline'])} Wh{_range(wh['baseline_range'])}"),
        ("Energy per request, candidate",
         f"{_fmt(wh['candidate'])} Wh{_range(wh['candidate_range'])}"),
        ("Energy saved per request", f"{_fmt(wh['saved'])} Wh ({_fmt(sp['mean'])}%)"),
    ]
    if wh["saved_ci95"]:
        rows.append(("95% interval of the saving",
                     f"{_fmt(wh['saved_ci95']['low'])} to {_fmt(wh['saved_ci95']['high'])} Wh "
                     f"({_fmt(sp['ci_low'])}% to {_fmt(sp['ci_high'])}%)"))
    else:
        rows.append(("Interval", f"none. {sp.get('basis') or 'single estimate'}"))
    j = f.get("j_per_good_answer")
    if j:
        rows.append(("Joules per good answer",
                     f"{_fmt(j['baseline'])} J to {_fmt(j['candidate'])} J "
                     f"({_fmt(j['change_pct'])}%)"))
    u = f.get("usd_per_request")
    if u:
        rows.append(("Cost saved per request", f"USD {_fmt(u['saved'])}"))
    p = f.get("pass_rate")
    if p:
        rows.append(("Quality gate pass rate", f"{_fmt(p['baseline'])} to {_fmt(p['candidate'])}"))
    rows.append(("Receipt grade", f"{rc['grade']} (energy label {rc.get('label')})"))
    return rows


def render_html(cert: Mapping) -> str:
    """One page. Always carries the MOCK watermark, the top and bottom bands and the QR code."""

    c = cert
    rc = c["receipt"]
    m = qr.encode(c["verify"]["qr_text"])
    svg = qr.to_svg(m, scale=4, border=2, title="Link to the verifier with the receipt hash")
    banner = " &nbsp;|&nbsp; ".join(_e(x) for x in WATERMARK_LINES)
    chips = f'<span>{_e(rc["grade"])}</span>'
    if rc["grade"] == "SIMULATED" or c["labels"].get("basis") == "SIMULATED":
        chips += '<span class="sim">SIMULATED DATA</span>'
    rows = "".join(f"<tr><th>{_e(k)}</th><td class='n'>{_e(v)}</td></tr>" for k, v in _rows(c))
    limits = "".join(f"<li>{_e(x)}</li>" for x in c["limits"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{_e(c['title'])} {_e(c['serial'])}</title>
<style>{_CSS}</style></head>
<body><div class="page">
<div class="band">{banner}</div>
<div class="mark">MOCK<small>NOT TRADABLE</small></div>
<h1>{_e(c['title'])}</h1>
<div class="sub">Serial <span class="serial">{_e(c['serial'])}</span> &middot; issued {_e(c['issued_at'])}</div>
<div class="chips">{chips}</div>
<div class="head">{_e(c['headline'])}</div>
<table>{rows}</table>
<div class="two"><div>
<table>
<tr><th>Receipt</th><td class="mono">{_e(str(rc['receipt_id']))}</td></tr>
<tr><th>Receipt sha256</th><td class="mono">{_e(rc['sha256'])}</td></tr>
<tr><th>Signer</th><td class="mono">{_e(str(rc['signer']))}</td></tr>
<tr><th>Certificate sha256</th><td class="mono">{_e(c['integrity']['sha256'])}</td></tr>
</table>
<div class="mono">{_e(c['verify']['how'])}</div>
</div><div>{svg}</div></div>
<ul class="limits">{limits}</ul>
<div class="foot">{banner}</div>
</div></body></html>
"""


def find_browser() -> str | None:
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        if p := shutil.which(name):
            return p
    return None


def render_pdf(html_path: str | Path, pdf_path: str | Path) -> Path:
    """Print the HTML to PDF with a local headless browser. Raises if none is installed."""

    exe = find_browser()
    if exe is None:
        raise RuntimeError("no Chrome or Chromium found; open the HTML and print to PDF instead")
    html_path, pdf_path = Path(html_path).resolve(), Path(pdf_path).resolve()
    with tempfile.TemporaryDirectory() as prof:
        subprocess.run(
            [exe, "--headless", "--disable-gpu", "--no-sandbox", f"--user-data-dir={prof}",
             "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}", html_path.as_uri()],
            check=True, capture_output=True, timeout=45)
    return pdf_path
