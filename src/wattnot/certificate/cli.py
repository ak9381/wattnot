"""`wattnot certificate ...`: mock efficiency certificates from verified receipts.

    wattnot certificate mint --receipt receipts/<id>.json --out certificates
    wattnot certificate verify certificates/WNC-MOCK-0001.json --receipt receipts/<id>.json
    wattnot certificate register --register certificates/register.json
    wattnot certificate roadmap

Every certificate is a MOCK and says so on every page. There is no flag to remove that.
"""

from __future__ import annotations

import json
from pathlib import Path

from wattnot.certificate import render as _render
from wattnot.certificate import rules as _mint

ROADMAP = Path(__file__).resolve().parents[3] / "data" / "certificate" / "roadmap.json"
DEFAULT_REGISTER = "certificates/register.json"


def add_parser(sub) -> None:
    c = sub.add_parser("certificate", help="MOCK efficiency certificates from verified receipts")
    s = c.add_subparsers(dest="cert_cmd")
    m = s.add_parser("mint", help="check the mint rules, then write the certificate")
    m.add_argument("--receipt", required=True, help="receipt JSON (bundle beside it)")
    m.add_argument("--bundle", help="bundle path if not beside the receipt")
    m.add_argument("--trust", help="trusted-keys JSON (default verifier/trusted-keys.json)")
    m.add_argument("--out", default="certificates", help="output directory (default %(default)s)")
    m.add_argument("--register", help="register file (default <out>/register.json)")
    m.add_argument("--allow-simulated", action="store_true",
                   help="also mint from a SIMULATED receipt (demo only; the page says so)")
    m.add_argument("--issued-at", help="ISO time to stamp (default now, UTC)")
    m.add_argument("--verify-url", default=_mint.DEFAULT_VERIFY_URL,
                   help="page the QR code points to (the receipt hash is added)")
    m.add_argument("--pdf", action="store_true", help="also write a PDF (needs Chrome or Chromium)")
    m.add_argument("--json", action="store_true", help="print the certificate (or refusal) as JSON")
    v = s.add_parser("verify", help="check a certificate: hash, watermark, receipt, register")
    v.add_argument("certificate", help="certificate JSON")
    v.add_argument("--receipt", help="also re-verify the receipt and re-derive every figure")
    v.add_argument("--bundle")
    v.add_argument("--trust")
    v.add_argument("--register", help="also check the serial and the chain in this register")
    v.add_argument("--json", action="store_true")
    r = s.add_parser("render", help="draw an existing certificate again (HTML, optional PDF)")
    r.add_argument("certificate")
    r.add_argument("--out", help="HTML path (default next to the certificate)")
    r.add_argument("--pdf", action="store_true")
    g = s.add_parser("register", help="list the register and check its chain")
    g.add_argument("--register", default=DEFAULT_REGISTER)
    g.add_argument("--lookup", help="show one serial")
    g.add_argument("--json", action="store_true")
    rm = s.add_parser("roadmap", help="what a real scheme would need (ADVICE)")
    rm.add_argument("--json", action="store_true")


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _do_mint(a) -> int:
    out = Path(a.out)
    reg = a.register or str(out / "register.json")
    try:
        cert = _mint.mint(a.receipt, reg, bundle=a.bundle, trusted_path=a.trust,
                          allow_simulated=a.allow_simulated, issued_at=a.issued_at,
                          verify_url=a.verify_url)
    except _mint.MintRefused as e:
        if a.json:
            print(json.dumps({"minted": False, "reasons": e.reasons}, indent=2))
        else:
            print("not minted:")
            for x in e.reasons:
                print(f"  {x['code']}: {x['detail']}")
        return 2
    out.mkdir(parents=True, exist_ok=True)
    jp = out / f"{cert['serial']}.json"
    hp = out / f"{cert['serial']}.html"
    jp.write_text(json.dumps(cert, indent=2) + "\n", encoding="utf-8")
    hp.write_text(_render.render_html(cert), encoding="utf-8")
    files = [str(jp), str(hp)]
    if a.pdf:
        files.append(str(_render.render_pdf(hp, out / f"{cert['serial']}.pdf")))
    if a.json:
        print(json.dumps({"minted": True, "certificate": cert, "files": files,
                          "register": reg}, indent=2))
    else:
        print(f"MOCK certificate {cert['serial']} for receipt {cert['receipt']['receipt_id']} "
              f"[{cert['receipt']['grade']}]")
        print("\n".join(f"  {f}" for f in files))
        print(f"  register: {reg}")
    return 0


def _do_verify(a) -> int:
    cert = _load(a.certificate)
    reg = _load(a.register) if a.register else None
    bad = _mint.verify_certificate(cert, receipt_path=a.receipt, bundle=a.bundle, register=reg,
                                   trusted_path=a.trust)
    if a.json:
        print(json.dumps({"ok": not bad, "serial": cert.get("serial"), "problems": bad}))
    elif bad:
        print(f"{cert.get('serial')}: FAILED")
        print("\n".join(f"  - {x}" for x in bad))
    else:
        print(f"{cert.get('serial')}: ok (still a MOCK)")
    return 1 if bad else 0


def _do_render(a) -> int:
    cert = _load(a.certificate)
    bad = _mint.verify_certificate(cert)
    if bad:
        print("refusing to draw a certificate that fails its own check:")
        print("\n".join(f"  - {x}" for x in bad))
        return 1
    hp = Path(a.out) if a.out else Path(a.certificate).with_suffix(".html")
    hp.write_text(_render.render_html(cert), encoding="utf-8")
    print(hp)
    if a.pdf:
        print(_render.render_pdf(hp, hp.with_suffix(".pdf")))
    return 0


def _do_register(a) -> int:
    reg = _mint.load_register(a.register)
    bad = _mint.verify_register(reg)
    if a.lookup:
        e = _mint.lookup(reg, a.lookup)
        if a.json:
            print(json.dumps({"found": e is not None, "entry": e, "chain_ok": not bad}))
        else:
            print(json.dumps(e, indent=2) if e else f"{a.lookup}: not in the register")
        return 0 if e else 1
    if a.json:
        print(json.dumps({**reg, "chain_ok": not bad, "problems": bad}, indent=2))
    else:
        print(f"register {a.register}: {len(reg['entries'])} entries, "
              f"chain {'intact' if not bad else 'BROKEN'} (all MOCK)")
        for e in reg["entries"]:
            print(f"  {e['serial']}  {e['grade']:<10} receipt {e['receipt_sha256'][:12]}  "
                  f"{e['minted_at']}")
        print("\n".join(f"  ! {x}" for x in bad))
    return 1 if bad else 0


def _do_roadmap(a) -> int:
    d = _load(str(ROADMAP))
    if a.json:
        print(json.dumps(d, indent=2))
        return 0
    print(f"[{d['label']}] {d['title']}\n{d['status']}\n{d['caveat']}\n")
    for i, it in enumerate(d["items"], 1):
        print(f"{i}. {it['need']}\n   why: {it['why']}\n   we have: {it['wattnot_has']}\n"
              f"   gap: {it['gap']}\n")
    return 0


def dispatch(a) -> int:
    fn = {"mint": _do_mint, "verify": _do_verify, "render": _do_render,
          "register": _do_register, "roadmap": _do_roadmap}.get(a.cert_cmd)
    if fn is None:
        raise ValueError("use: wattnot certificate mint|verify|render|register|roadmap")
    return fn(a)
