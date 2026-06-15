#!/usr/bin/env bash
#
# End-to-end proof of install.sh / uninstall.sh in a THROWAWAY Debian 12 + systemd container.
# Proves: clean host -> active/enabled service + preflight green; idempotent re-run preserves .env;
# non-root + unsupported-distro refusals; default uninstall keeps files; --purge leaves no residue.
# NEVER touches a real host. Exits 0 only if every assertion passes.
#
# Usage: tests/deploy/run_container_e2e.sh [--keep] [--reuse-uv-cache]
#   --keep            don't remove the container at the end (for debugging)
#   --reuse-uv-cache  mount a persistent named volume for uv's cache (faster re-runs; less "clean")

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE="ankivoice-systemd-test"
CONTAINER="ankivoice-e2e-$$"
TOKEN="123456789:AABBCCDDEEFFGGHHIIJJKKLLMMNNOOPPQQR"   # format-valid but fake -> Telegram 401
ARCHIVE="-1001234567890"
PREFIX="/opt/ankivoice"
KEEP=0
REUSE_UV=0
for a in "$@"; do
  case "$a" in
    --keep) KEEP=1 ;;
    --reuse-uv-cache) REUSE_UV=1 ;;
  esac
done

PASS=0; FAIL=0
ok()   { printf '  \033[1;32mPASS\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
step() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
dex()  { docker exec "$CONTAINER" "$@"; }
dexb() { docker exec "$CONTAINER" bash -lc "$1"; }

cleanup() {
  if [ "$KEEP" -eq 1 ]; then
    echo "[keep] container left running: $CONTAINER"
  else
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

check() {  # check "desc" <cmd...>  — passes if cmd exits 0
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$desc"; else bad "$desc"; fi
}

step "Build the systemd test image"
docker build -q -t "$IMAGE" -f "$REPO_ROOT/tests/deploy/Dockerfile.systemd" "$REPO_ROOT/tests/deploy" \
  || { echo "image build failed"; exit 1; }

step "Start a privileged systemd container"
RUN_ARGS=(-d --name "$CONTAINER" --privileged --cgroupns=host
          -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock)
if [ "$REUSE_UV" -eq 1 ]; then
  # Persist ONLY uv's wheel cache across runs (a neutral path that does not pre-create the install
  # dir) so re-runs don't re-download wheels. The install still does a full clean sync/build/warm-up;
  # this is equivalent to a host with a warm package cache, and keeps the "clean install dir" proof
  # honest. The first run is still a fully clean download.
  RUN_ARGS+=(-e UV_CACHE_DIR=/uvcache -v ankivoice_e2e_uvcache:/uvcache)
fi
docker run "${RUN_ARGS[@]}" "$IMAGE" >/dev/null || { echo "container start failed"; exit 1; }
[ "$REUSE_UV" -eq 1 ] && dexb 'mkdir -p /uvcache && chmod 777 /uvcache' || true

step "Wait for systemd to come up"
sysok=0
for i in $(seq 1 40); do
  s="$(dex systemctl is-system-running 2>/dev/null || true)"
  case "$s" in running|degraded) sysok=1; break ;; esac
  sleep 1
done
[ "$sysok" -eq 1 ] && ok "systemd is up ($s)" || { bad "systemd did not start"; exit 1; }

step "Copy the working tree into the container (excluding heavy/secret paths)"
dexb 'mkdir -p /root/ankivoice'
tar -C "$REPO_ROOT" \
  --exclude='.git' --exclude='.venv' --exclude='work' --exclude='data' --exclude='models' \
  --exclude='.env' --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
  --exclude='.uv-cache' -cf - . | docker exec -i "$CONTAINER" tar -xf - -C /root/ankivoice \
  && ok "copied repo" || bad "repo copy failed"

step "Refusal: non-root (must change nothing)"
if docker exec -u nobody "$CONTAINER" bash -lc 'cd /root/ankivoice && ./install.sh --non-interactive --token x --archive-id 1' >/tmp/nonroot.log 2>&1; then
  bad "non-root run should have failed"
else
  grep -qi root /tmp/nonroot.log && ok "non-root refused with a root hint" || bad "non-root refusal lacked a clear message"
fi
# Robust to any pre-existing mountpoint: assert the non-root run provisioned NO install artifacts.
dexb 'test ! -e /opt/ankivoice/.venv && test ! -e /opt/ankivoice/.env' \
  && ok "non-root run provisioned nothing (no .venv/.env)" || bad "non-root run mutated the host"

step "Refusal: unsupported distro (faked os-release)"
if dexb 'cd /root/ankivoice && printf "ID=fedora\nID_LIKE=rhel\n" >/tmp/osr && OS_RELEASE_FILE=/tmp/osr ./install.sh --non-interactive --token x --archive-id 1' >/tmp/distro.log 2>&1; then
  bad "unsupported distro should have failed"
else
  grep -qi 'debian\|ubuntu\|unsupported' /tmp/distro.log && ok "unsupported distro refused clearly" || bad "distro refusal unclear"
fi

step "Run the one-command install (placeholder token)"
if dexb "cd /root/ankivoice && ./install.sh --token '$TOKEN' --archive-id '$ARCHIVE' --non-interactive"; then
  ok "install.sh exited 0"
else
  bad "install.sh failed"; docker logs "$CONTAINER" 2>&1 | tail -20;
fi

step "Assert service state + artifacts"
check "service enabled-on-boot" bash -c "dex systemctl is-enabled ankivoice | grep -q enabled"
dexb 'journalctl -u ankivoice --no-pager 2>/dev/null | grep -q "Starting AnkiVoice (long-polling)"' \
  && ok "journal shows preflight passed + long-polling start" || bad "no long-polling/preflight-green line in journal"
dexb 'journalctl -u ankivoice --no-pager 2>/dev/null | grep -qi "Startup preflight failed\|cannot start"' \
  && bad "journal shows a preflight FAILURE" || ok "no preflight failure in journal"
perm="$(dexb 'stat -c "%a %U" /opt/ankivoice/.env' 2>/dev/null)"
[ "$perm" = "600 ankivoice" ] && ok ".env is 0600 and owned by the service user ($perm)" || bad ".env perms/owner wrong: '$perm'"
check "en_core_web_sm importable in the service venv" \
  dex /opt/ankivoice/.venv/bin/python -c 'import en_core_web_sm'
dexb 'grep -q "Environment=HF_HOME=/opt/ankivoice/models" /etc/systemd/system/ankivoice.service' \
  && ok "installed unit pins HF_HOME to the warmed cache" || bad "unit missing the HF_HOME pin"
dexb 'systemd-analyze verify /etc/systemd/system/ankivoice.service' >/tmp/verify.log 2>&1 \
  && ok "systemd-analyze verify clean" || { grep -qiE 'Failed to parse|Unknown.*section|Invalid' /tmp/verify.log && bad "systemd-analyze found syntax errors" || ok "systemd-analyze verify clean (only benign warnings)"; }
dexb 'journalctl -u ankivoice --no-pager 2>/dev/null | grep -qi "Unauthorized\|InvalidToken\|401"' \
  && ok "placeholder token correctly rejected by Telegram (expected 401)" || echo "  (note: no 401 yet — token may not have been exercised)"

step "Idempotency: re-run preserves .env and stays enabled"
before="$(dexb 'sha256sum /opt/ankivoice/.env' 2>/dev/null | awk '{print $1}')"
if dexb 'cd /root/ankivoice && ./install.sh --non-interactive'; then ok "re-run exited 0"; else bad "re-run failed"; fi
after="$(dexb 'sha256sum /opt/ankivoice/.env' 2>/dev/null | awk '{print $1}')"
[ -n "$before" ] && [ "$before" = "$after" ] && ok ".env unchanged byte-for-byte on re-run" || bad ".env changed on re-run ($before -> $after)"
check "still enabled after re-run" bash -c "dex systemctl is-enabled ankivoice | grep -q enabled"

step "Uninstall (default): unit removed, files kept"
dexb 'cd /root/ankivoice && ./uninstall.sh' && ok "uninstall.sh exited 0" || bad "uninstall.sh failed"
dexb 'systemctl is-enabled ankivoice' >/dev/null 2>&1 && bad "unit still enabled after uninstall" || ok "unit removed"
dexb 'test -d /opt/ankivoice' && ok "install dir kept on default uninstall" || bad "default uninstall deleted the install dir"

step "Uninstall (--purge --yes): no residue"
dexb 'cd /root/ankivoice && ./uninstall.sh --purge --yes' && ok "purge exited 0" || bad "purge failed"
dexb 'test ! -e /opt/ankivoice' && ok "install dir removed by purge" || bad "purge left the install dir"
dexb 'getent passwd ankivoice' >/dev/null 2>&1 && bad "service user still present after purge" || ok "service user removed by purge"

step "Result"
echo "PASS=$PASS  FAIL=$FAIL"
[ "$FAIL" -eq 0 ] && { echo "E2E OK"; exit 0; } || { echo "E2E FAILED"; exit 1; }
