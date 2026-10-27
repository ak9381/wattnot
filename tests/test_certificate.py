"""Mock certificates: the QR encoder, mint rules, register chain, watermark, CLI."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from wattnot.certificate import (
    WATERMARK_LINES,
    MintRefused,
    mint,
    qr,
    render_html,
    rules,
    verify_certificate,
    verify_register,
)
from wattnot.certificate import render as render_mod
from wattnot.cli import main
from wattnot.receipt.canon import canonical_sha256
from wattnot.receipt.sign import sign
from wattnot.verify import load_trusted, verify_receipt

ROOT = Path(__file__).resolve().parents[1]
VEC = ROOT / "verifier" / "vectors"
KEY = ROOT / "verifier" / "samples" / "test-key.pem"
T0 = "2026-10-28T10:00:00+00:00"


def _copy(tmp_path, name: str, mutate=None) -> Path:
    """Copy a vector (and its bundle). With `mutate`, rehash and re-sign with the test key."""
    src = VEC / f"{name}.json"
    dst = tmp_path / f"{name}.json"
    if mutate is None:
        shutil.copy(src, dst)
        shutil.copy(VEC / f"{name}.sigstore.json", tmp_path / f"{name}.sigstore.json")
        return dst
    r = json.loads(src.read_text())
    mutate(r)
    r["integrity"]["sha256"] = canonical_sha256(r)
    dst.write_text(json.dumps(r))
    sign(dst, no_rekor=True, key_path=KEY)
    return dst


@pytest.fixture
def reg(tmp_path):
    return tmp_path / "certs" / "register.json"


# ------------------------------------------------------------------ QR

def test_qr_sizes_and_finders():
    for text, version in (("a", 1), ("x" * 14, 1), ("x" * 15, 2), ("x" * 100, 6),
                          ("x" * 213, 10)):
        m = qr.encode(text)
        n = 17 + 4 * version
        assert len(m) == n and all(len(row) == n for row in m)
        for r0, c0 in ((0, 0), (0, n - 7), (n - 7, 0)):  # finder: 7x7 ring, 3x3 core
            assert all(m[r0][c0 + i] == 1 and m[r0 + 6][c0 + i] == 1 for i in range(7))
            assert all(m[r0 + i][c0] == 1 and m[r0 + i][c0 + 6] == 1 for i in range(7))
            assert m[r0 + 3][c0 + 3] == 1 and m[r0 + 1][c0 + 1] == 0
        assert m[n - 8][8] == 1  # the always-dark module


def test_qr_too_long_and_deterministic():
    with pytest.raises(ValueError, match="too long"):
        qr.encode("x" * 214)
    assert qr.encode("hello") == qr.encode("hello")
    assert qr.encode("hello") != qr.encode("hellp")


def _format_bits(m):
    n = len(m)
    return [m[8][n - 1 - i] for i in range(8)] + [m[n - 15 + i][8] for i in range(8, 15)]


def test_qr_format_info_is_a_valid_bch_word():
    for mask in range(8):
        m = qr.encode("wattnot", mask=mask)
        bits = _format_bits(m)  # same 15 bits as the copy near the top-left finder
        word = sum(b << i for i, b in enumerate(bits)) ^ 0x5412
        assert (word >> 10) == mask  # level M is 00, then the 3 mask bits
        rem = word >> 10
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        assert (((word >> 10) << 10) | rem) == word


def test_qr_decodes_with_an_independent_reader():
    zx = pytest.importorskip("zxingcpp")
    np = pytest.importorskip("numpy")
    texts = ["hi", "https://example.org/x#sha256=" + "ab" * 32, "z" * 100, "q" * 150, "w" * 213]
    for t in texts:
        g = np.array(qr.encode(t), dtype=np.uint8)
        img = np.kron(np.pad(1 - g, 4, constant_values=1) * 255,
                      np.ones((6, 6), dtype=np.uint8)).astype(np.uint8)
        got = zx.read_barcodes(img)
        assert got and got[0].text == t


def test_qr_svg_is_a_picture_with_a_title():
    svg = qr.to_svg(qr.encode("abc"), title="the link")
    assert svg.startswith("<svg") and 'aria-label="the link"' in svg and "<rect" in svg


# ------------------------------------------------------------------ mint rules

def test_mints_an_estimate_receipt(tmp_path, reg):
    c = mint(VEC / "real-estimate.json", reg, issued_at=T0)
    assert c["serial"] == "WNC-MOCK-0001"
    assert c["status"] == "MOCK" and c["receipt"]["grade"] == "ESTIMATE"
    assert c["watermark"] == {"lines": list(WATERMARK_LINES), "permanent": True}
    f = c["figures"]
    assert f["wh_per_request"]["saved"] == pytest.approx(0.0048, abs=1e-9)
    assert f["j_per_good_answer"]["change_pct"] == pytest.approx(-9.9958)
    assert f["saving_pct"]["ci_low"] > 0
    assert verify_certificate(c, receipt_path=VEC / "real-estimate.json",
                              register=json.loads(reg.read_text())) == []


def test_certificate_is_deterministic_for_a_fixed_time(tmp_path):
    a = mint(VEC / "real-estimate.json", tmp_path / "a.json", issued_at=T0)
    b = mint(VEC / "real-estimate.json", tmp_path / "b.json", issued_at=T0)
    assert a == b and a["integrity"]["sha256"] == canonical_sha256(a)


def test_noise_is_refused(reg):
    with pytest.raises(MintRefused) as e:
        mint(VEC / "real-noise.json", reg)
    assert e.value.reasons[0]["code"] == "grade" and "NOISE" in e.value.reasons[0]["detail"]
    assert not reg.exists()  # nothing written


@pytest.mark.parametrize("name", ["forged-edited", "forged-rehashed", "forged-otherkey",
                                  "forged-joules"])
def test_forged_receipts_are_refused(name, reg):
    with pytest.raises(MintRefused) as e:
        mint(VEC / f"{name}.json", reg)
    assert {r["code"] for r in e.value.reasons} <= {"not_verified", "grade"}
    assert not reg.exists()


def test_simulated_needs_the_flag_and_says_so(reg):
    with pytest.raises(MintRefused) as e:
        mint(VEC / "real-cloud.json", reg)
    assert e.value.reasons[0]["code"] == "simulated"
    c = mint(VEC / "real-cloud.json", reg, allow_simulated=True, issued_at=T0)
    assert c["receipt"]["grade"] == "SIMULATED" and c["labels"]["basis"] == "SIMULATED"
    assert c["figures"]["saving_pct"]["ci_low"] is None  # an estimate has no interval
    assert "SIMULATED DATA" in render_html(c)


def test_no_second_certificate_for_the_same_receipt(reg):
    mint(VEC / "real-estimate.json", reg, issued_at=T0)
    with pytest.raises(MintRefused) as e:
        mint(VEC / "real-estimate.json", reg)
    assert e.value.reasons[0]["code"] == "duplicate"
    assert "WNC-MOCK-0001" in e.value.reasons[0]["detail"]
    assert len(json.loads(reg.read_text())["entries"]) == 1


def test_same_run_in_a_rebuilt_receipt_is_still_a_duplicate(reg):
    # real-measured and real-joules are two receipts of one run_id
    mint(VEC / "real-measured.json", reg, issued_at=T0)
    with pytest.raises(MintRefused) as e:
        mint(VEC / "real-joules.json", reg)
    assert e.value.reasons[0]["detail"].startswith("run:")


def test_claim_keys_include_the_cloud_source():
    r = {"run_id": "run_1", "cloud": {"source_receipt": {"sha256": "cc" * 32}}}
    assert rules._claim_keys(r, "aa" * 32) == [f"receipt:{'aa' * 32}", "run:run_1",
                                               f"cloud:{'cc' * 32}"]


def _report(path, grade=None):
    rep = verify_receipt(path, trusted=load_trusted())
    if grade:
        rep.grade = grade
    return rep


def test_a_saving_must_be_above_zero_and_clear_of_zero():
    r = json.loads((VEC / "real-estimate.json").read_text())
    rep = _report(VEC / "real-estimate.json")
    assert rules.check_rules(r, rep) == []
    neg = copy.deepcopy(r)
    neg["result"]["saving_pct"]["mean"] = -2.0
    assert rules.check_rules(neg, rep)[0]["code"] == "no_saving"
    wide = copy.deepcopy(r)
    wide["result"]["saving_pct"]["ci_low"] = -0.2
    assert rules.check_rules(wide, rep)[0]["code"] == "ci_includes_zero"


def test_a_quality_drop_blocks_the_certificate():
    r = json.loads((VEC / "real-estimate.json").read_text())
    r["candidate"]["quality"]["fixed_pass_rate"] = 0.90  # base 0.95: five points down
    codes = [x["code"] for x in rules.check_rules(r, _report(VEC / "real-estimate.json"))]
    assert "quality_drop" in codes
    r["candidate"]["quality"]["fixed_pass_rate"] = 0.945  # half a point is allowed
    assert rules.check_rules(r, _report(VEC / "real-estimate.json")) == []


def test_resigned_receipt_with_no_saving_never_mints(tmp_path, reg):
    p = _copy(tmp_path, "real-estimate",
              lambda r: r["result"]["saving_pct"].update(mean=-1.0, ci_low=-2.0, ci_high=0.5))
    with pytest.raises(MintRefused):
        mint(p, reg)


def test_untrusted_signer_is_refused(tmp_path, reg):
    trust = tmp_path / "trust.json"
    trust.write_text(json.dumps({"keys": [{"signer": "offline:0000000000000000"}]}))
    with pytest.raises(MintRefused) as e:
        mint(VEC / "real-estimate.json", reg, trusted_path=trust)
    assert any("not a trusted key" in r["detail"] for r in e.value.reasons)


# ------------------------------------------------------------------ register

def test_register_chain_links_entries(reg):
    mint(VEC / "real-estimate.json", reg, issued_at=T0)
    mint(VEC / "real-cloud.json", reg, allow_simulated=True, issued_at=T0)
    d = json.loads(reg.read_text())
    e1, e2 = d["entries"]
    assert e1["prev_sha256"] is None and e2["prev_sha256"] == e1["integrity"]["sha256"]
    assert [e["serial"] for e in d["entries"]] == ["WNC-MOCK-0001", "WNC-MOCK-0002"]
    assert verify_register(d) == [] and d["label"] == "MOCK"
    assert oct(reg.stat().st_mode & 0o777) == "0o644"


def test_register_edit_removal_and_reorder_are_caught(reg):
    mint(VEC / "real-estimate.json", reg, issued_at=T0)
    mint(VEC / "real-cloud.json", reg, allow_simulated=True, issued_at=T0)
    mint(VEC / "real-measured.json", reg, issued_at=T0)
    d = json.loads(reg.read_text())
    edited = copy.deepcopy(d)
    edited["entries"][0]["grade"] = "VALID"
    assert any("edited" in p for p in verify_register(edited))
    dropped = copy.deepcopy(d)
    del dropped["entries"][1]
    assert verify_register(dropped)
    swapped = copy.deepcopy(d)
    swapped["entries"][0], swapped["entries"][1] = swapped["entries"][1], swapped["entries"][0]
    assert verify_register(swapped)


def test_a_broken_register_stops_minting(reg):
    mint(VEC / "real-estimate.json", reg, issued_at=T0)
    d = json.loads(reg.read_text())
    d["entries"][0]["grade"] = "VALID"
    reg.write_text(json.dumps(d))
    with pytest.raises(MintRefused) as e:
        mint(VEC / "real-cloud.json", reg, allow_simulated=True)
    assert e.value.reasons[0]["code"] == "register"


def test_refusal_leaves_the_register_untouched(reg):
    mint(VEC / "real-estimate.json", reg, issued_at=T0)
    before = reg.read_text()
    with pytest.raises(MintRefused):
        mint(VEC / "real-noise.json", reg)
    assert reg.read_text() == before


# ------------------------------------------------------------------ certificate checks

def test_edited_certificate_or_removed_watermark_fails(reg):
    c = mint(VEC / "real-estimate.json", reg, issued_at=T0)
    e = copy.deepcopy(c)
    e["figures"]["wh_per_request"]["saved"] = 1.0
    assert "certificate was edited (hash mismatch)" in verify_certificate(e)
    w = copy.deepcopy(c)
    w["watermark"]["lines"] = []
    w["integrity"]["sha256"] = canonical_sha256(w)  # even re-hashed
    assert "the MOCK watermark is missing or changed" in verify_certificate(w)
    s = copy.deepcopy(c)
    s["status"] = "FINAL"
    s["integrity"]["sha256"] = canonical_sha256(s)
    assert "the MOCK watermark is missing or changed" in verify_certificate(s)


def test_figures_are_re_derived_from_the_receipt(reg, tmp_path):
    c = mint(VEC / "real-estimate.json", reg, issued_at=T0)
    other = _copy(tmp_path, "real-measured")  # different receipt
    bad = verify_certificate(c, receipt_path=other)
    assert "receipt hash does not match this certificate" in bad
    f = copy.deepcopy(c)
    f["figures"]["wh_per_request"]["saved"] = 0.5
    f["integrity"]["sha256"] = canonical_sha256(f)
    assert "figures do not match the receipt" in verify_certificate(
        f, receipt_path=VEC / "real-estimate.json")


def test_register_must_hold_the_same_certificate(reg):
    c = mint(VEC / "real-estimate.json", reg, issued_at=T0)
    d = json.loads(reg.read_text())
    assert verify_certificate(c, register=d) == []
    other = copy.deepcopy(c)
    other["serial"] = "WNC-MOCK-0009"
    other["integrity"]["sha256"] = canonical_sha256(other)
    assert any("not in the register" in p for p in verify_certificate(other, register=d))
    forged = copy.deepcopy(c)
    forged["headline"] = "a different statement"
    forged["integrity"]["sha256"] = canonical_sha256(forged)
    assert any("different certificate" in p for p in verify_certificate(forged, register=d))


def test_banned_wording_fails_a_certificate(reg):
    c = mint(VEC / "real-estimate.json", reg, issued_at=T0)
    for word in ("carbon neutral", "an offset", "net zero"):
        b = copy.deepcopy(c)
        b["headline"] = f"This is {word}."
        b["integrity"]["sha256"] = canonical_sha256(b)
        assert any("banned wording" in p for p in verify_certificate(b))


def test_certificate_text_has_no_carbon_claim(reg):
    c = mint(VEC / "real-estimate.json", reg, issued_at=T0)
    low = json.dumps({k: v for k, v in c.items() if k != "watermark"}).lower()
    for w in ("neutral", "offset", "co2", "carbon credit", "net zero"):
        assert w not in low
    assert "gco2" not in render_html(c).lower()


# ------------------------------------------------------------------ rendering

def test_html_is_always_watermarked_and_has_the_hash_and_qr(reg):
    c = mint(VEC / "real-estimate.json", reg, issued_at=T0)
    h = render_html(c)
    assert h.count("NOT A CARBON CREDIT") == 2  # top and bottom band
    assert h.count("MOCK") >= 4 and "NOT TRADABLE" in h
    assert 'class="mark"' in h
    assert c["receipt"]["sha256"] in h and c["serial"] in h
    assert "<svg" in h and "ESTIMATE" in h
    assert "<script" not in h and "http://" not in h.replace("http://www.w3.org/2000/svg", "")


def test_html_escapes_text_from_the_receipt(reg):
    c = mint(VEC / "real-estimate.json", reg, issued_at=T0)
    c = copy.deepcopy(c)
    c["subject"]["model"] = "<script>alert(1)</script>"
    h = render_html(c)
    assert "<script>alert(1)" not in h and "&lt;script&gt;" in h


def test_qr_carries_the_receipt_hash(reg):
    c = mint(VEC / "real-estimate.json", reg, issued_at=T0, verify_url="https://example.org/v")
    assert c["verify"]["qr_text"] == f"https://example.org/v#sha256={c['receipt']['sha256']}"


def test_pdf_needs_a_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(render_mod, "find_browser", lambda: None)
    with pytest.raises(RuntimeError, match="no Chrome"):
        render_mod.render_pdf(tmp_path / "a.html", tmp_path / "a.pdf")


# ------------------------------------------------------------------ CLI

def test_cli_mint_verify_register_roundtrip(tmp_path, capsys):
    out = tmp_path / "certs"
    assert main(["certificate", "mint", "--receipt", str(VEC / "real-estimate.json"),
                 "--out", str(out), "--issued-at", T0]) == 0
    assert (out / "WNC-MOCK-0001.json").exists() and (out / "WNC-MOCK-0001.html").exists()
    capsys.readouterr()
    assert main(["certificate", "verify", str(out / "WNC-MOCK-0001.json"),
                 "--receipt", str(VEC / "real-estimate.json"),
                 "--register", str(out / "register.json")]) == 0
    assert "ok (still a MOCK)" in capsys.readouterr().out
    assert main(["certificate", "register", "--register", str(out / "register.json"),
                 "--lookup", "WNC-MOCK-0001", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["found"] and d["chain_ok"] and d["entry"]["grade"] == "ESTIMATE"
    assert main(["certificate", "register", "--register", str(out / "register.json"),
                 "--lookup", "WNC-MOCK-0042"]) == 1


def test_cli_refusal_exit_code_and_json(tmp_path, capsys):
    rc = main(["certificate", "mint", "--receipt", str(VEC / "real-noise.json"),
               "--out", str(tmp_path), "--json"])
    d = json.loads(capsys.readouterr().out)
    assert rc == 2 and d["minted"] is False and d["reasons"][0]["code"] == "grade"
    assert not list(tmp_path.glob("*.html"))


def test_cli_json_shape(tmp_path, capsys):
    main(["certificate", "mint", "--receipt", str(VEC / "real-estimate.json"),
          "--out", str(tmp_path), "--issued-at", T0, "--json"])
    d = json.loads(capsys.readouterr().out)
    assert d["minted"] is True and d["certificate"]["format"] == "wattnot.certificate/1"
    assert len(d["files"]) == 2 and d["register"].endswith("register.json")


def test_cli_verify_fails_on_an_edited_certificate(tmp_path, capsys):
    main(["certificate", "mint", "--receipt", str(VEC / "real-estimate.json"),
          "--out", str(tmp_path), "--issued-at", T0])
    p = tmp_path / "WNC-MOCK-0001.json"
    c = json.loads(p.read_text())
    c["watermark"]["permanent"] = False
    p.write_text(json.dumps(c))
    capsys.readouterr()
    assert main(["certificate", "verify", str(p)]) == 1
    assert "FAILED" in capsys.readouterr().out
    assert main(["certificate", "render", str(p)]) == 1  # will not draw it


def test_cli_render_redraws_a_good_certificate(tmp_path):
    main(["certificate", "mint", "--receipt", str(VEC / "real-estimate.json"),
          "--out", str(tmp_path), "--issued-at", T0])
    html = tmp_path / "WNC-MOCK-0001.html"
    first = html.read_text()
    html.unlink()
    assert main(["certificate", "render", str(tmp_path / "WNC-MOCK-0001.json")]) == 0
    assert html.read_text() == first


def test_cli_roadmap_is_labelled_advice(capsys):
    assert main(["certificate", "roadmap", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["label"] == "ADVICE" and len(d["items"]) == 5
    assert all({"need", "why", "wattnot_has", "gap"} <= set(i) for i in d["items"])
    low = json.dumps(d).lower()
    assert "neutral" not in low and "not studied any specific standard" in low
    assert main(["certificate", "roadmap"]) == 0


def test_bundled_sample_still_checks_out():
    d = ROOT / "data" / "certificate" / "sample"
    cert = json.loads((d / "WNC-MOCK-0001.json").read_text())
    reg = json.loads((d / "register.json").read_text())
    assert verify_certificate(cert, receipt_path=VEC / "real-estimate.json", register=reg) == []
    assert (d / "WNC-MOCK-0001.html").read_text() == render_html(cert)
