"""scripts/vm_docker.sh installs docker + the NVIDIA container toolkit on a fresh VM (RUN3, Oct 31:
the image had no docker, and the http Ubuntu archive + Docker Hub timed out from the VM).
Runs against stubs only: apt-get, curl, systemctl, gpg, tee, tar, sudo, python3."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "vm_docker.sh"


def _stub(d: Path, name: str, body: str) -> None:
    (d / name).write_text("#!/bin/bash\n" + body + "\n")
    (d / name).chmod(0o755)


def _run(tmp_path, *, apt_works=True, http_archive=True, env_extra=None):
    b = tmp_path / "bin"; b.mkdir()
    st = tmp_path / "st"; st.mkdir()
    log = tmp_path / "calls.log"
    docker = (f'#!/bin/bash\n[ "$1" = info ] && {{ echo ok; [ -e {st}/nvidia ] && '
              f'echo "Runtimes: nvidia runc"; exit 0; }}\nexit 0\n')
    (st / "docker").write_text(docker)
    inst = (f'for p in "$@"; do case $p in docker.io) cp {st}/docker {b}/docker; chmod +x {b}/docker;; '
            f'nvidia-container-toolkit) printf "#!/bin/bash\\ntouch {st}/nvidia\\n" > {b}/nvidia-ctk; '
            f'chmod +x {b}/nvidia-ctk;; esac; done')
    _stub(b, "apt-get", f'echo "apt-get $*" >> {log}; case " $* " in *" install "*) '
          + (inst if apt_works else "exit 100") + ';; esac')
    _stub(b, "sudo", '"$@"')
    _stub(b, "id", "echo 1000")
    _stub(b, "timeout", 'shift; "$@"')
    for n in ("systemctl", "gpg", "tee", "groupadd", "pkill", "sed", "python3"):
        _stub(b, n, f'echo "{n} $*" >> {log}; cat >/dev/null 2>&1 || true')
    # curl: -w prints a code; http archive can be "blocked" (000); static listing serves a tarball
    _stub(b, "curl", f'''url="${{@: -1}}"; for a in "$@"; do case $a in -o) o=1;; esac; done
case "$*" in *"-w"*) if [ "{int(http_archive)}" = 0 ] && [[ "$url" == http://archive* ]]; then echo 000; else echo 200; fi; exit 0;; esac
case "$*" in *static/stable/x86_64/docker-*) for ((i=1;i<=$#;i++)); do [ "${{!i}}" = -o ] && j=$((i+1)) && touch "${{!j}}"; done; exit 0;; esac
case "$url" in */static/stable/x86_64/) echo '<a href="docker-27.3.1.tgz">';; *) echo "deb https://nvidia.github.io/x /";; esac''')
    _stub(b, "tar", f'cp {st}/docker {b}/docker; chmod +x {b}/docker; echo "tar $*" >> {log}')
    _stub(b, "dockerd", "exit 0")
    env = {"PATH": f"{b}:/usr/bin:/bin", "VMD_CURL_TIMEOUT": "1", **(env_extra or {})}
    p = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=120)
    return p, (log.read_text() if log.exists() else "")


def test_installs_docker_and_toolkit_via_apt(tmp_path):
    p, calls = _run(tmp_path)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert "install -y -qq docker.io" in calls and "nvidia-container-toolkit" in calls
    assert "docker + NVIDIA runtime ok" in out


def test_apt_fails_falls_back_to_static_docker(tmp_path):
    p, calls = _run(tmp_path, apt_works=False)
    out = p.stdout + p.stderr
    assert "static docker-27.3.1.tgz installed" in out, out
    assert "tar " in calls
    assert p.returncode == 15 and "no NVIDIA runtime" in out   # toolkit also came via apt


def test_http_archive_blocked_switches_apt_to_https(tmp_path):
    p, calls = _run(tmp_path, http_archive=False)
    out = p.stdout + p.stderr
    assert "switching apt to https" in out and p.returncode == 0, out


def test_setup_calls_helper_and_keeps_opt_out():
    s = (ROOT / "scripts" / "h100_vm_setup.sh").read_text()
    assert 'bash "$REPO/scripts/vm_docker.sh" || exit 15' in s
    assert 'SETUP_NO_DOCKER_INSTALL:-0' in s
