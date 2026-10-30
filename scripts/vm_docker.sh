#!/usr/bin/env bash
# Install docker + the NVIDIA container toolkit on a fresh E2E GPU VM, robust to the egress
# problems RUN3 hit on Oct 31: archive.ubuntu.com (http) and registry-1.docker.io timed out from
# the VM while download.docker.com, nvidia.github.io and huggingface.co answered.
#   * every network call has a timeout, so nothing hangs for minutes;
#   * apt tries https mirrors when the http archive is unreachable;
#   * docker falls back to the static binaries from download.docker.com when apt cannot install;
#   * docker pulls go through mirror.gcr.io (a Docker Hub cache) as a registry mirror.
# Exit 0 = docker with the NVIDIA runtime is ready; 15 = could not get there (caller stops).
set -uo pipefail
SUDO=""; [ "$(id -u)" = 0 ] || SUDO="sudo"
CT="${VMD_CURL_TIMEOUT:-15}"
APT=(env DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=20
     -o Acquire::https::Timeout=20 -o Dpkg::Lock::Timeout=120)
reach() { curl -s -o /dev/null -m "$CT" -w '%{http_code}' "$1" 2>/dev/null | grep -qv '^000$'; }
log() { echo "[docker-setup] $*"; }

for u in http://archive.ubuntu.com/ubuntu/ https://archive.ubuntu.com/ubuntu/ \
         https://download.docker.com/ https://nvidia.github.io/libnvidia-container/gpgkey \
         https://registry-1.docker.io/v2/ https://mirror.gcr.io/v2/; do
  if reach "$u"; then log "reach ok: $u"; else log "reach FAIL: $u"; fi
done

apt_sources_https() {   # http archive blocked -> point apt at https (same hosts)
  reach http://archive.ubuntu.com/ubuntu/ && return 0
  reach https://archive.ubuntu.com/ubuntu/ || return 0
  log "http archive unreachable: switching apt to https"
  for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list; do
    [ -f "$f" ] && $SUDO sed -i 's#http://\([a-z.]*\)\(archive\|security\)\.ubuntu\.com#https://\1\2.ubuntu.com#g' "$f"
  done
}

docker_static() {
  local base=https://download.docker.com/linux/static/stable/x86_64 v tgz
  v="$(curl -s -m "$CT" "$base/" | grep -o 'docker-[0-9][0-9.]*\.tgz' | sort -V | tail -n1)"
  [ -n "$v" ] || { log "static docker: no tarball listed"; return 1; }
  tgz="$(mktemp)"
  curl -fsSL -m 300 "$base/$v" -o "$tgz" || return 1
  $SUDO tar -xzf "$tgz" -C /usr/local/bin --strip-components=1 && rm -f "$tgz"
  $SUDO groupadd -f docker
  log "static $v installed; starting dockerd"
  $SUDO sh -c 'nohup /usr/local/bin/dockerd >/var/log/dockerd.log 2>&1 &'
  for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && return 0; sleep 2; done
  return 1
}

restart_docker() {
  if $SUDO systemctl restart docker >/dev/null 2>&1; then return 0; fi
  $SUDO pkill -x dockerd 2>/dev/null; sleep 3
  $SUDO sh -c 'nohup dockerd >/var/log/dockerd.log 2>&1 &'
  for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && return 0; sleep 2; done
}

registry_mirror() {   # Docker Hub cache, used only if Hub itself is slow/unreachable
  $SUDO mkdir -p /etc/docker
  $SUDO python3 - <<'PY'
import json, os
p = "/etc/docker/daemon.json"
d = json.load(open(p)) if os.path.exists(p) and os.path.getsize(p) else {}
m = d.setdefault("registry-mirrors", [])
if "https://mirror.gcr.io" not in m:
    m.append("https://mirror.gcr.io")
json.dump(d, open(p, "w"), indent=2)
PY
}

apt_sources_https
if ! command -v docker >/dev/null; then
  log "docker missing: apt install docker.io"
  $SUDO timeout 300 "${APT[@]}" update -qq >/dev/null 2>&1 || log "apt-get update failed or timed out"
  $SUDO timeout 600 "${APT[@]}" install -y -qq docker.io >/dev/null 2>&1 || log "apt-get install docker.io failed"
  $SUDO systemctl enable --now docker >/dev/null 2>&1 || true
  command -v docker >/dev/null || docker_static || log "static docker install failed"
fi
command -v docker >/dev/null || { log "docker missing: the Glimmer server cannot start"; exit 15; }
registry_mirror

if ! docker info 2>/dev/null | grep -qi nvidia; then
  if ! command -v nvidia-ctk >/dev/null; then
    log "installing nvidia-container-toolkit"
    curl -fsSL -m 60 https://nvidia.github.io/libnvidia-container/gpgkey \
      | $SUDO gpg --batch --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -fsSL -m 60 https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
      | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
      | $SUDO tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
    $SUDO timeout 300 "${APT[@]}" update -qq >/dev/null 2>&1 || log "apt-get update (toolkit) failed or timed out"
    $SUDO timeout 600 "${APT[@]}" install -y -qq nvidia-container-toolkit >/dev/null 2>&1 \
      || log "nvidia-container-toolkit install failed"
  fi
  if command -v nvidia-ctk >/dev/null; then
    $SUDO nvidia-ctk runtime configure --runtime=docker >/dev/null 2>&1 || log "nvidia-ctk configure failed"
  fi
fi
restart_docker
docker info 2>/dev/null | grep -qi nvidia || { log "docker has no NVIDIA runtime (nvidia-container-toolkit)"; exit 15; }
log "docker + NVIDIA runtime ok"
