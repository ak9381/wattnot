# ruff: noqa: E501  (HTML template lines)
"""Receipt view for cloud mode: a terminal summary and one self-contained HTML page.

Every number carries its label. The page shows, per prompt class, which reasoning
effort was kept, the levels that were tried and why they failed, what the same
requests cost at `xhigh` (the API default), and the saving. Energy is a range and
always says ESTIMATE.
"""

from __future__ import annotations

import html
from collections.abc import Mapping

LABEL_CLASS = {"MEASURED": "m", "ESTIMATE": "e", "SIMULATED": "s", "ADVICE": "a",
               "NOT_AVAILABLE": "n"}


def usd(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"${x:.4f}" if abs(x) < 1 else f"${x:,.2f}"


def _tok(x) -> str:
    return "n/a" if x is None else f"{int(x):,}"


def chain(c: Mapping) -> str:
    """e.g. 'low pass | medium skipped | high skipped'."""

    parts = []
    for t in c["tried"]:
        if t.get("skipped"):
            parts.append(f"{t['effort']} skipped")
        else:
            why = "" if t["passed"] else " (" + "+".join(t["failed_gates"]) + ")"
            parts.append(f"{t['effort']} {'pass' if t['passed'] else 'fail'}{why}")
    return " | ".join(parts)


def summary_lines(r: Mapping) -> list[str]:
    t = r["totals"]
    lab = r["label"]
    out = [f"WattNot cloud receipt {r['receipt_id']}  [{lab}]",
           f"provider {r['provider']}  model {r['model']}  mode {r['mode']}",
           "",
           f"{'class':<9}{'kept':<8}{'tuned USD':>11}{'xhigh USD':>11}{'saving':>9}  tried"]
    for c in r["classes"]:
        m, b = c["measured"], c["would_have_cost_xhigh"]
        out.append(f"{c['class']:<9}{c['chosen_effort']:<8}{usd(m['usd']):>11}{usd(b['usd']):>11}"
                   f"{c['saving']['pct']:>8.1f}%  {chain(c)}")
    out += ["",
            f"[{lab}] total {usd(t['tuned_usd'])} vs {usd(t['would_have_cost_xhigh_usd'])} "
            f"at xhigh: saved {usd(t['saving_usd'])} ({t['saving_pct']:.1f}%) over "
            f"{t['requests']} requests",
            f"[{lab}] completion tokens {_tok(t['completion_tokens_tuned'])} vs "
            f"{_tok(t['completion_tokens_xhigh'])}; cached share {t['cached_share']:.1%}",
            f"[{lab}] this tuning run cost {usd(t['experiment_usd'])}"
            + (f"; it pays back in {t['payback_runs']:g} runs of the workload"
               if t.get("payback_runs") else "")]
    e = r["energy"]["saved_wh"]
    out.append(f"[ESTIMATE] energy avoided {e['low']:.2f} to {e['high']:.2f} Wh "
               "(H100-class J/FLOP range, not measured on Baseten's GPUs)")
    if r.get("meter_audit"):
        a = r["meter_audit"]
        out.append(f"[{a['label']}] meter audit: {a['status']}"
                   + (f", worst gap {a['worst_gap_pct']}%" if a.get("rows") else
                      f" ({a.get('reason', '')})"))
    else:
        out.append("meter audit: not run yet (wattnot cloud audit)")
    for n in r.get("unverified", []):
        out.append(f"not verified: {n}")
    return out


def _badge(label: str) -> str:
    return f'<span class="b {LABEL_CLASS.get(label, "n")}">{html.escape(label)}</span>'


def _bar(a: float, b: float, w: int = 220) -> str:
    top = max(a, b, 1e-12)
    wa, wb = max(1, round(w * a / top)), max(1, round(w * b / top))
    return (f'<svg width="{w}" height="26" role="img" aria-label="tuned vs xhigh">'
            f'<rect x="0" y="2" width="{wa}" height="9" fill="#0a6b2b"/>'
            f'<rect x="0" y="14" width="{wb}" height="9" fill="#999"/></svg>')


def render(r: Mapping, *, audit: Mapping | None = None) -> str:
    esc = html.escape
    t, lab = r["totals"], r["label"]
    audit = audit or r.get("meter_audit")
    rows = []
    for c in r["classes"]:
        m, b = c["measured"], c["would_have_cost_xhigh"]
        steps = "".join(
            f'<li class="{"skip" if x.get("skipped") else "ok" if x["passed"] else "bad"}">'
            f'{esc(x["effort"])}: '
            + (esc(x["reason"]) if x.get("skipped") else
               f'quality {x["quality_pct"]:.0f}% of {x["reference_pct"]:.0f}%, '
               f'p99 {x["p99_effective_s"]:.1f}s of {x["p99_budget_s"]:.1f}s'
               + ("" if x["passed"] else f' &rarr; rejected ({esc("+".join(x["failed_gates"]))})')
               + (f', {x["truncated"]} ran out of tokens' if x.get("truncated") else ""))
            + "</li>" for x in c["tried"])
        rows.append(
            f'<tr><td><b>{esc(c["class"])}</b><br><small>{c["items"]} items x {c["repeats"]}'
            f'</small></td><td class="eff">{esc(c["chosen_effort"])}</td>'
            f'<td><ul class="steps">{steps}</ul></td>'
            f'<td class="n">{_tok(m["completion_tokens"])}<br><small>vs {_tok(b["completion_tokens"])}'
            f'</small></td><td class="n">{usd(m["usd"])}<br><small>would have cost {usd(b["usd"])}'
            f'</small></td><td>{_bar(m["usd"], b["usd"])}</td>'
            f'<td class="n sav">{c["saving"]["pct"]:.1f}%</td></tr>')
    e = r["energy"]
    aud = ""
    if audit:
        ar = "".join(f'<tr><td>{esc(x["field"])}</td><td class="n">{x["receipt"]:,}</td>'
                     f'<td class="n">{x["meter"]:,}</td><td class="n">{x["gap_pct"]}%</td>'
                     f'<td>{"ok" if x["ok"] else "over tolerance"}</td></tr>'
                     for x in audit.get("rows", []))
        aud = (f'<h2>Meter audit {_badge(audit["label"])}</h2>'
               f'<p>Status: <b>{esc(audit["status"])}</b>'
               + (f' (tolerance {audit["tolerance_pct"]:g}%)' if audit.get("rows") else
                  f' &mdash; {esc(audit.get("reason", ""))}') + "</p>"
               + (f"<table><tr><th>field</th><th>our receipt</th><th>Baseten usage</th>"
                  f"<th>gap</th><th></th></tr>{ar}</table>" if ar else ""))
    notes = "".join(f"<li>{esc(n)}</li>" for n in r.get("unverified", []))
    sim = ('<p class="warn">SIMULATED: this page was built from a made-up fixture. No API call '
           "was made.</p>" if lab == "SIMULATED" else "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>WattNot cloud receipt {esc(r["receipt_id"])}</title>
<style>
body{{font:14px/1.45 system-ui,sans-serif;margin:2rem;color:#111;max-width:1100px}}
table{{border-collapse:collapse;width:100%;margin:.5rem 0 1rem}}
th,td{{border:1px solid #ccc;padding:.4rem .5rem;text-align:left;vertical-align:top}}
th{{background:#f3f3f3}}td.n{{text-align:right;white-space:nowrap}}td.eff{{font-weight:700}}
td.sav{{color:#0a6b2b;font-weight:700}}ul.steps{{margin:0;padding-left:1rem}}
li.ok{{color:#0a6b2b}}li.bad{{color:#a11}}li.skip{{color:#777}}
.b{{display:inline-block;padding:.05rem .45rem;border-radius:3px;font-size:12px;font-weight:700;color:#fff}}
.b.m{{background:#0a6b2b}}.b.e{{background:#8a5a00}}.b.s{{background:#555}}.b.a{{background:#245}}
.b.n{{background:#777}}.warn{{background:#fff4d6;padding:.5rem;border-left:4px solid #8a5a00}}
.big{{font-size:1.5rem;margin:.5rem 0}}small{{color:#555}}code{{background:#f3f3f3;padding:0 .25rem}}
</style></head><body>
<h1>Cloud mode receipt {_badge(lab)}</h1>
<p>{esc(r["provider"])} &middot; <code>{esc(r["model"])}</code> &middot; mode {esc(r["mode"])}
&middot; {esc(r["created_at"])}<br><small>receipt {esc(r["receipt_id"])} &middot; sha256
{esc(r["integrity"]["sha256"][:16])}&hellip; &middot; chained to
{esc((r["integrity"].get("prev_sha256") or "none")[:16])}</small></p>
{sim}
<p class="big">{usd(t["tuned_usd"])} instead of {usd(t["would_have_cost_xhigh_usd"])}
at xhigh: <b>{t["saving_pct"]:.1f}% less</b> {_badge(lab)}</p>
<p>Each class keeps the lowest reasoning effort that matched the xhigh answers and stayed inside
the p99 budget, including the cost of retrying at xhigh when a cheap answer fails its check.
{t["requests"]} requests, cached prompt share {t["cached_share"]:.1%}. Tuning itself cost
{usd(t["experiment_usd"])}{
    f" and pays back in {t['payback_runs']:g} runs of this workload" if t.get("payback_runs")
    else ""}.</p>
<table><thead><tr><th>class</th><th>kept</th><th>levels tried</th><th>completion tokens</th>
<th>USD</th><th>tuned / xhigh</th><th>saving</th></tr></thead><tbody>
{"".join(rows)}
</tbody></table>
<h2>Energy {_badge("ESTIMATE")}</h2>
<p>Work avoided: about <b>{e["saved_wh"]["low"]:.2f} to {e["saved_wh"]["high"]:.2f} Wh</b> across
this run. We cannot see Baseten's GPUs, so this is a FLOP count from the token counts times a
stated joules-per-FLOP range for H100-class hardware. It is not a measurement and not a claim
about anyone's grid.</p>
{aud}
<h2>Not verified</h2><ul>{notes or "<li>nothing flagged</li>"}</ul>
<p><small>Labels: MEASURED comes from the API usage block and our clock. ESTIMATE is derived.
SIMULATED came from a made-up fixture. ADVICE is a suggestion, not a result. Money in USD at
{esc(r["rates"]["source"])}: {r["rates"]["input_per_m"]}/{r["rates"]["cached_per_m"]}/
{r["rates"]["output_per_m"]} per 1M tokens (input/cached/output).</small></p>
</body></html>
"""
