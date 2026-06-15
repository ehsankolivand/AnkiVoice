# Quickstart / Validation: cycle 003 (one-command install & deployment)

How to validate the feature end-to-end. The app's behavior validation is unchanged — see
[001 quickstart](../001-ankivoice-audio-decks/quickstart.md) and
[002 quickstart](../002-quality-bugfix-perf/quickstart.md). This file covers only the new
install/deploy surface. All proving happens in a **throwaway local Debian container** — never on a
real VPS.

## Prerequisites

- Docker available on the dev host (the container harness self-skips if not).
- The app's default suite already green: `uv run pytest` (fast, offline, unchanged).

## A. App suite unchanged (regression gate — FR-016, FR-018, SC-006)

```bash
uv run pytest            # same fast offline suite, same count as before this feature, all green
uv run pytest -m live    # still self-skips when the model isn't cached
```

## B. `en_core_web_sm` survives `uv sync` (the cycle-002 fix — FR-005, D3)

```bash
# after the pyproject pin + uv lock:
grep -A2 'name = "en-core-web-sm"' uv.lock        # locked at 3.8.0 with a wheel URL + sha256
# in a clean throwaway env (the deploy test does this in a container):
uv sync --locked --no-dev
uv run python -c "import en_core_web_sm; print('en_core_web_sm OK')"
```

Expected: the model is present after a plain locked sync — no `spacy download` needed.

## C. systemd unit validity (FR-008, D5)

```bash
# render the template with a user + dir, install it, then (inside the Debian container):
systemd-analyze verify /etc/systemd/system/ankivoice.service        # exits clean post-install
```

## D. End-to-end install in a clean Debian container (SC-001, SC-004)

Run by `tests/deploy/test_install_container.py` (self-skips without Docker). Manual form:

```bash
# 1) clean systemd-enabled debian:12 container (privileged so systemd runs as PID 1)
docker run -d --name av --privileged --cgroupns=host \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  -v "$PWD":/src:ro debian:12 /sbin/init

# 2) copy the repo to a writable place and run the ONE command with a placeholder token
docker exec av bash -lc 'cp -a /src /root/ankivoice && cd /root/ankivoice && \
  ./install.sh --token 123456:PLACEHOLDER_TOKEN_FOR_TEST --archive-id -1001234567890 --non-interactive'

# 3) assertions
docker exec av systemctl is-enabled ankivoice           # -> enabled
docker exec av journalctl -u ankivoice --no-pager | grep -E "preflight|long-polling"
#   -> shows the startup preflight passing and "Starting AnkiVoice (long-polling)…"
#   (with the placeholder token a Telegram 401 follows — expected; a REAL token keeps it active)
docker exec av bash -lc 'test "$(stat -c %a /opt/ankivoice/.env)" = "600"'   # 0600
docker exec av bash -lc 'stat -c %U /opt/ankivoice/.env'                     # -> ankivoice
```

Expected boundary (placeholder token): **unit started + preflight green + long-polling line in the
journal**. A real operator token keeps `systemctl is-active ankivoice` = `active` continuously.

## E. Idempotent re-run preserves secrets (FR-010, SC-002)

```bash
docker exec av bash -lc 'sha256sum /opt/ankivoice/.env > /root/env.before'
docker exec av bash -lc 'cd /root/ankivoice && ./install.sh --non-interactive'   # no token args needed
docker exec av bash -lc 'sha256sum /opt/ankivoice/.env > /root/env.after && diff /root/env.before /root/env.after'
#   -> identical; service still enabled; no prompt occurred
```

## F. Refusals (FR-012, SC-007)

```bash
# non-root:
docker exec -u 1000 av bash -lc 'cd /root/ankivoice && ./install.sh' ; echo "exit=$?"   # non-zero, clear message
# unsupported distro (e.g. an alpine container): ./install.sh refuses naming Debian/Ubuntu, changes nothing
```

## G. Uninstall is clean and scoped (FR-013, SC-005)

```bash
docker exec av bash -lc 'cd /root/ankivoice && ./uninstall.sh'                 # default: unit gone, files kept
docker exec av systemctl is-enabled ankivoice ; echo "exit=$?"                 # -> not enabled (non-zero)
docker exec av bash -lc 'cd /root/ankivoice && ./uninstall.sh --purge --yes'   # full removal
docker exec av bash -lc 'test ! -e /opt/ankivoice && ! getent passwd ankivoice && echo CLEAN'
```

## H. Operator commands (documented; FR-014, SC-003)

```bash
journalctl -u ankivoice -f                     # view live logs
systemctl status ankivoice                     # status (active/enabled)
systemctl restart ankivoice                    # graceful restart (drains in-flight job)
sudo ./install.sh                              # update (re-run; preserves .env)
sudo ./uninstall.sh            # remove service ;  sudo ./uninstall.sh --purge   # remove everything
```

## Done When

- A through H pass in the container; the app suite count is unchanged and green; `systemd-analyze
  verify` is clean; `.env` is `0600`/service-user-owned and never clobbered on re-run; uninstall
  `--purge` leaves no residue.
