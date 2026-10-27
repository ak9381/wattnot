"""The WattNot site (web/site): the browser verifier agrees with the Python one, the committed
data checks out, local links resolve, and the copy stays inside the wording rules."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from wattnot.certificate.rules import load_register, verify_certificate, verify_register
from wattnot.verify import load_trusted, verify_receipt

SITE = Path(__file__).resolve().parents[1] / "web" / "site"
DATA = SITE / "data"
PAGES = sorted(SITE.glob("*.html"))


def _j(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_browser_verifier_node_tests():
    r = subprocess.run(["node", "--test", "tests/"], cwd=SITE, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_sample_receipts_grade_as_listed_with_the_python_verifier():
    trusted = load_trusted(None)
    for s in _j(DATA / "receipts" / "index.json"):
        rep = verify_receipt(DATA / "receipts" / f"{s['id']}.json",
                             DATA / "receipts" / f"{s['id']}.sigstore.json", trusted=trusted)
        assert rep.grade == s["expected_grade"], s["id"]


def test_certificates_and_register_in_the_site_data_check_out():
    reg = load_register(DATA / "certificate" / "register.json")
    assert verify_register(reg) == []
    for m in _j(DATA / "certificate" / "index.json")["minted"]:
        cert = _j(DATA / "certificate" / f"{m['serial']}.json")
        assert cert["status"] == "MOCK"
        assert verify_certificate(
            cert, receipt_path=DATA / "receipts" / f"{m['receipt']}.json",
            bundle=DATA / "receipts" / f"{m['receipt']}.sigstore.json", register=reg) == []


def test_flex_data_is_labelled_simulated():
    for p in DATA.glob("flex/*.trace.json"):
        assert _j(p)["label"] == "SIMULATED"
    for p in DATA.glob("flex/*.receipt.json"):
        assert _j(p)["flex"]["label"] == "SIMULATED"


def test_local_links_and_assets_resolve():
    for page in PAGES:
        text = page.read_text(encoding="utf-8")
        for ref in re.findall(r'(?:href|src)="([^"#?]+)', text):
            if re.match(r"(https?:|mailto:|\.\./run/)", ref):
                continue
            assert (SITE / ref).exists(), f"{page.name}: {ref} is missing"


def test_pages_exist_and_have_a_nav():
    names = {p.name for p in PAGES}
    assert {"index.html", "features.html", "verify.html", "flex.html", "route.html",
            "certificate.html"} <= names
    for p in PAGES:
        assert 'aria-label="Main"' in p.read_text(encoding="utf-8")


BANNED = [r"carbon[- ]neutral", r"(?<![:-])\bfirst\b(?!-)", r"revolutionary", r"\bclaude\b",
          r"\bgrok\b", r"\bgemini\b", r"\banthropic\b", r"midjourney"]


def test_site_text_avoids_banned_wording():
    files = [p for p in SITE.rglob("*") if p.suffix in {".html", ".mjs", ".md", ".json"}
             and "tests" not in p.parts]
    assert files
    for p in files:
        text = p.read_text(encoding="utf-8")
        if p.suffix in {".mjs"}:
            text = re.sub(r"//.*", "", text)
        for pat in BANNED:
            assert not re.search(pat, text, re.I), f"{p.relative_to(SITE)}: {pat}"


def test_every_page_script_builds_dom_without_inner_html():
    for p in (SITE / "js").glob("*.mjs"):
        code = re.sub(r"//.*", "", p.read_text(encoding="utf-8"))
        assert "innerHTML" not in code, p.name
