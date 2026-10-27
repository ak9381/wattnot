"""The one-file verifier agrees with `wattnot verify` on every conformance vector."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from wattnot.receipt.canon import canonicalize as lib_canon
from wattnot.verify import load_trusted, verify_receipt

ROOT = Path(__file__).resolve().parents[1]
V = ROOT / "verifier" / "vectors"
SCRIPT = ROOT / "verifier" / "wattnot_verify.py"
EXPECTED = json.loads((V / "expected.json").read_text())

_spec = importlib.util.spec_from_file_location("wattnot_verify", SCRIPT)
sv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sv)


def test_nine_vectors():
    assert len(EXPECTED) == 9
    assert {k for k in EXPECTED if k.startswith("forged-")} == {
        "forged-edited", "forged-rehashed", "forged-otherkey", "forged-joules"}


def test_every_vector_file_is_listed():
    on_disk = {p.stem for p in V.glob("*.json")
               if not p.name.endswith(".sigstore.json") and p.stem != "expected"}
    assert on_disk == set(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_standalone_grade_matches_expected(name):
    r = sv.verify_path(V / f"{name}.json", trusted=sv.load_trusted(ROOT / "verifier"
                                                                   / "trusted-keys.json"))
    assert r["grade"] == EXPECTED[name]["grade"], name


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_standalone_and_library_agree_step_by_step(name):
    mine = sv.verify_path(V / f"{name}.json", trusted=sv.load_trusted(
        ROOT / "verifier" / "trusted-keys.json"))
    lib = verify_receipt(V / f"{name}.json", trusted=load_trusted()).to_dict()
    assert mine["grade"] == lib["grade"]
    assert [(s["name"], s["ok"]) for s in mine["steps"]] == [(s["name"], s["ok"])
                                                             for s in lib["steps"]]
    assert mine["joules"] == lib["joules"]


def test_uses_only_the_standard_library():
    tree = ast.parse(SCRIPT.read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    assert mods <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names)


def test_jcs_matches_the_library_on_the_rfc_vectors():
    for line in (ROOT / "tests" / "fixtures" / "jcs-vectors.jsonl").read_text().splitlines():
        v = json.loads(line)
        assert sv.canonicalize(v["in"]) == lib_canon(v["in"]) == v["out"]


def test_ed25519_matches_cryptography_on_good_and_bad_signatures():
    rnd = random.Random(7)
    for _ in range(12):
        key = Ed25519PrivateKey.from_private_bytes(rnd.randbytes(32))
        pub = key.public_key().public_bytes_raw()
        msg = rnd.randbytes(rnd.randint(0, 80))
        sig = key.sign(msg)
        assert sv.ed25519_verify(pub, msg, sig)
        bad = bytearray(sig)
        bad[rnd.randrange(64)] ^= 1 << rnd.randrange(8)
        assert not sv.ed25519_verify(pub, msg, bytes(bad))
        assert not sv.ed25519_verify(pub, msg + b"x", sig)


def test_ed25519_rejects_malformed_input():
    assert not sv.ed25519_verify(b"\x00" * 31, b"m", b"\x00" * 64)
    assert not sv.ed25519_verify(b"\x01" * 32, b"m", b"\x00" * 63)
    assert not sv.ed25519_verify(b"\xff" * 32, b"m", b"\xff" * 64)


def _run(*args, cwd=None):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True,
                          text=True, cwd=cwd, env=env)


def test_cli_exit_codes_and_json():
    ok = _run(str(V / "real-measured.json"))
    assert ok.returncode == 0 and "ESTIMATE" in ok.stdout
    bad = _run(str(V / "forged-edited.json"), "--json")
    assert bad.returncode == 1 and json.loads(bad.stdout)["grade"] == "UNVERIFIABLE"
    assert _run().returncode == 2
    assert _run(str(V / "real-measured.json"), "--nope").returncode == 2


def test_cli_missing_and_garbage_files(tmp_path):
    assert _run(str(tmp_path / "nope.json")).returncode == 1
    g = tmp_path / "g.json"
    g.write_text("{not json")
    assert _run(str(g)).returncode == 1
    g.write_text("[1, 2]")
    assert _run(str(g)).returncode == 1


def test_changing_one_digit_turns_it_red(tmp_path):
    """The demo moment: one edited digit, and the verifier fails the hash."""

    p = tmp_path / "r.json"
    d = json.loads((V / "real-cloud.json").read_text())
    (tmp_path / "r.sigstore.json").write_text((V / "real-cloud.sigstore.json").read_text())
    p.write_text(json.dumps(d))
    assert sv.verify_path(p)["grade"] == "SIMULATED"
    d["cloud"]["saving_usd"] = d["cloud"]["saving_usd"] + 0.01
    p.write_text(json.dumps(d))
    r = sv.verify_path(p)
    assert r["grade"] == "UNVERIFIABLE"
    assert not next(s for s in r["steps"] if s["name"] == "sha256")["ok"]
