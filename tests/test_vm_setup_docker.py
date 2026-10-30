"""The VM setup installs docker + the NVIDIA container toolkit when the image lacks them
(RUN3 on Oct 31 stopped at exit 15: the E2E Ubuntu CUDA image ships no docker). Runs only the
docker block of scripts/h100_vm_setup.sh against stub apt-get/systemctl/curl/nvidia-ctk."""
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "h100_vm_setup.sh"


def _block() -> str:
    s = SCRIPT.read_text()
    a = s.index('  if [ "${SETUP_NO_DOCKER_INSTALL')
    b = s.index('echo "[setup] docker + NVIDIA runtime ok"')
    return s[a:b] + 'echo "[setup] docker + NVIDIA runtime ok"\n'


def _stub(d: Path, name: str, body: str) -> None:
    (d / name).write_text("#!/bin/bash\n" + body + "\n")
    (d / name).chmod(0o755)


def _run(tmp_path, extra_env=None, apt_works=True):
    b = tmp_path / "bin"; b.mkdir()
    st = tmp_path / "state"; st.mkdir()
    log = tmp_path / "calls.log"
    docker = f'#!/bin/bash\n[ "$1" = info ] && {{ [ -e {st}/nvidia ] && echo "Runtimes: nvidia runc"; exit 0; }}\n'
    inst = (f'for p in "$@"; do case $p in docker.io) printf %s \'{docker}\' > {b}/docker; chmod +x {b}/docker;; '
            f'nvidia-container-toolkit) printf "#!/bin/bash\\ntouch {st}/nvidia\\n" > {b}/nvidia-ctk; chmod +x {b}/nvidia-ctk;; esac; done')
    _stub(b, "apt-get", f'echo "apt-get $*" >> {log}; ' + (inst if apt_works else "exit 100"))
    _stub(b, "sudo", '"$@"')
    _stub(b, "id", 'echo 1000')
    for n in ("systemctl", "gpg", "tee"):
        _stub(b, n, f'echo "{n} $*" >> {log}; cat >/dev/null 2>&1 || true')
    _stub(b, "curl", 'echo "deb https://nvidia.github.io/x /"')
    env = {"PATH": f"{b}:/usr/bin:/bin", **(extra_env or {})}
    p = subprocess.run(["bash", "-c", "set -u\n" + _block()], env=env, capture_output=True,
                       text=True, timeout=60)
    return p, (log.read_text() if log.exists() else "")


def test_installs_docker_and_toolkit_when_missing(tmp_path):
    p, calls = _run(tmp_path)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert "installing docker.io" in out and "installing nvidia-container-toolkit" in out
    assert "docker + NVIDIA runtime ok" in out
    assert "install -y -qq docker.io" in calls and "nvidia-container-toolkit" in calls


def test_failed_install_still_exits_15(tmp_path):
    p, _ = _run(tmp_path, apt_works=False)
    assert p.returncode == 15 and "docker missing" in p.stdout


def test_opt_out_keeps_check_only(tmp_path):
    p, calls = _run(tmp_path, {"SETUP_NO_DOCKER_INSTALL": "1"})
    assert p.returncode == 15 and "apt-get" not in calls
