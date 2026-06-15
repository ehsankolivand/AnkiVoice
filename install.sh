#!/usr/bin/env bash
#
# AnkiVoice one-command installer for Debian/Ubuntu (feature 003).
#
# Idempotent: safe to re-run to update. Run as root:
#   sudo ./install.sh --token <BOT_TOKEN> --archive-id <CHAT_ID>
# or run with no value flags to be prompted. See README "Deploy on a Debian/Ubuntu VPS" and
# specs/003-one-command-deploy/ for the full contract.
#
# This file is written to be *source-friendly*: it only runs main() when executed directly, so the
# deploy unit tests can `source` it and call individual helpers without root or host mutation.

# --- defaults (overridable by flags/env) -------------------------------------------------------
SERVICE_NAME="ankivoice"
DEFAULT_USER="ankivoice"
DEFAULT_PREFIX="/opt/ankivoice"
UV_BIN_DIR="/usr/local/bin"

# --- tiny logging helpers (never print secrets) ------------------------------------------------
log()  { printf '\033[1;34m[ankivoice]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[ankivoice] WARNING:\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31m[ankivoice] ERROR:\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

usage() {
  cat >&2 <<'EOF'
AnkiVoice installer (Debian/Ubuntu, run as root).

Usage: sudo ./install.sh [options]

  --token <BOT_TOKEN>        ANKIVOICE_BOT_TOKEN (else $ANKIVOICE_BOT_TOKEN, else prompt)
  --archive-id <CHAT_ID>     ANKIVOICE_ARCHIVE_CHAT_ID (else $ANKIVOICE_ARCHIVE_CHAT_ID, else prompt)
  --user <NAME>              service user (else $ANKIVOICE_USER, else "ankivoice")
  --prefix <DIR>             install dir (else $INSTALL_DIR / $ANKIVOICE_PREFIX, else "/opt/ankivoice")
  --non-interactive          never prompt; fail if a required value is missing and no .env exists
  --skip-warmup              skip the one-time model download (tests/CI)
  -h, --help                 show this help

Re-running is the supported update path; it never overwrites an existing .env.
EOF
}

# --- guards ------------------------------------------------------------------------------------

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    die "This installer must run as root (it creates a service user, installs a system package, and
    registers a boot service). Re-run with: sudo ./install.sh"
  fi
}

# _osrel_get KEY FILE -> the value of KEY (matching quotes stripped). Parses key=value rather than
# sourcing the file, so a tampered os-release can never execute arbitrary shell.
_osrel_get() {
  sed -n "s/^$1=//p" "$2" 2>/dev/null | tail -1 \
    | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

# is_supported_distro: 0 on Debian/Ubuntu (or a debian-like derivative), non-zero otherwise.
# Reads ${OS_RELEASE_FILE:-/etc/os-release} (overridable for tests).
is_supported_distro() {
  local osrel="${OS_RELEASE_FILE:-/etc/os-release}"
  if [ ! -r "$osrel" ]; then
    err "Cannot read $osrel — unable to identify the OS. AnkiVoice's installer supports only
    Debian/Ubuntu (apt-based) hosts."
    return 1
  fi
  local _id _id_like
  _id="$(_osrel_get ID "$osrel")"
  _id_like="$(_osrel_get ID_LIKE "$osrel")"
  case "$_id" in
    debian|ubuntu) return 0 ;;
  esac
  local tok
  for tok in $_id_like; do
    [ "$tok" = "debian" ] && return 0
  done
  err "Unsupported OS (ID='${_id:-unknown}'). AnkiVoice's installer supports only Debian/Ubuntu
    (or a debian-like derivative). No changes were made."
  return 1
}

# --- value resolution & .env -------------------------------------------------------------------

# resolve_required NAME FLAG_VALUE ENV_VALUE -> echoes flag||env, or returns 1 if both empty.
resolve_required() {
  local _name="$1" flagval="$2" envval="$3"
  if [ -n "$flagval" ]; then printf '%s\n' "$flagval"; return 0; fi
  if [ -n "$envval" ];  then printf '%s\n' "$envval";  return 0; fi
  return 1
}

# write_env_file PATH TOKEN ARCHIVE_ID MODEL_DIR -> writes a 0600 .env (no chown here). Never echoes
# the token to stdout/stderr.
write_env_file() {
  local path="$1" token="$2" archive="$3" modeldir="$4"
  ( umask 177
    cat >"$path" <<EOF
# AnkiVoice service configuration — created by install.sh. Mode 0600, owned by the service user.
# Environment-only config (Constitution P8). See .env.example for every optional key + default.
ANKIVOICE_BOT_TOKEN=$token
ANKIVOICE_ARCHIVE_CHAT_ID=$archive
# Offline model cache the service reads (a stable path under the install dir; maps to HF_HOME).
ANKIVOICE_MODEL_DIR=$modeldir
EOF
  )
  chmod 600 "$path"
}

# ensure_env_file PATH TOKEN ARCHIVE_ID MODEL_DIR -> preserve an existing .env byte-for-byte;
# otherwise create it. Returns 0.
ensure_env_file() {
  local path="$1"
  if [ -e "$path" ]; then
    log "Existing .env found — preserving it unchanged."
    return 0
  fi
  write_env_file "$@"
  log "Wrote a new .env (mode 0600)."
}

# effective_model_dir ENVFILE INSTALL_DIR -> the HF cache the service will actually read, so the
# warm-up populates the SAME location (clarification Q2). A preserved .env without ANKIVOICE_MODEL_DIR
# falls back to the service user's default HF cache under its home (= the install dir).
effective_model_dir() {
  local envfile="$1" install_dir="$2" md=""
  if [ -r "$envfile" ]; then
    # Strip a matching pair of surrounding quotes, mirroring systemd's EnvironmentFile semantics, so
    # warm-up and the running service resolve the SAME path even for a hand-quoted value.
    md="$(sed -n 's/^[[:space:]]*ANKIVOICE_MODEL_DIR=//p' "$envfile" 2>/dev/null | tail -1 \
            | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/")"
  fi
  if [ -n "$md" ]; then printf '%s\n' "$md"; else printf '%s\n' "$install_dir/.cache/huggingface"; fi
}

# --- install steps (require root; invoked from main) -------------------------------------------

ensure_packages() {
  log "Installing system packages (ffmpeg, curl, ca-certificates)…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  # ffmpeg is the ONLY required system binary; espeak-ng is bundled via espeakng_loader — do NOT install it.
  apt-get install -y --no-install-recommends ffmpeg curl ca-certificates
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    log "uv already present ($(command -v uv))."
    return 0
  fi
  log "Installing uv into $UV_BIN_DIR…"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$UV_BIN_DIR" UV_NO_MODIFY_PATH=1 sh
  command -v uv >/dev/null 2>&1 || die "uv install failed (uv not on PATH)."
}

ensure_user() {
  local user="$1" home="$2"
  if ! getent group "$user" >/dev/null 2>&1; then
    groupadd --system "$user"
  fi
  if ! getent passwd "$user" >/dev/null 2>&1; then
    log "Creating service user '$user'…"
    useradd --system --gid "$user" --home-dir "$home" --shell /usr/sbin/nologin "$user"
  else
    log "Service user '$user' already exists."
  fi
}

copy_tree() {
  local src="$1" dst="$2"
  log "Copying the application into $dst…"
  mkdir -p "$dst"
  # Portable copy (no rsync/git needed) excluding VCS, the venv, secrets, data, caches.
  tar -C "$src" \
    --exclude='.git' --exclude='.venv' --exclude='work' --exclude='data' \
    --exclude='models' --exclude='.env' --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='.pytest_cache' --exclude='.uv-cache' \
    -cf - . | tar -C "$dst" -xf -
}

as_user() { runuser -u "$SERVICE_USER" -- "$@"; }

run_uv_sync() {
  log "Provisioning dependencies (uv sync --locked --no-dev)…"
  ( cd "$INSTALL_DIR" && as_user env HOME="$INSTALL_DIR" PATH="$UV_BIN_DIR:$PATH" \
      UV_PROJECT_ENVIRONMENT="$INSTALL_DIR/.venv" uv sync --locked --no-dev )
}

run_warmup() {
  local md; md="$(effective_model_dir "$INSTALL_DIR/.env" "$INSTALL_DIR")"
  log "Running the one-time model warm-up (online) into: $md"
  mkdir -p "$md"; chown -R "$SERVICE_USER":"$SERVICE_USER" "$md" 2>/dev/null || true
  ( cd "$INSTALL_DIR" && as_user env HOME="$INSTALL_DIR" PATH="$UV_BIN_DIR:$PATH" \
      HF_HOME="$md" ANKIVOICE_ALLOW_DOWNLOADS=1 \
      "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/scripts/warmup.py" )
}

install_unit() {
  log "Installing the systemd unit…"
  local tmpl="$INSTALL_DIR/deploy/ankivoice.service"
  [ -r "$tmpl" ] || die "unit template not found at $tmpl"
  # Pin HF_HOME in the unit to the SAME effective cache the warm-up populated, so the offline
  # guarantee never depends on $HOME/XDG resolution at runtime (review HIGH, clarification Q2).
  local md; md="$(effective_model_dir "$INSTALL_DIR/.env" "$INSTALL_DIR")"
  sed "s|{{USER}}|$SERVICE_USER|g; s|{{INSTALL_DIR}}|$INSTALL_DIR|g; s|{{HF_HOME}}|$md|g" \
    "$tmpl" >"/etc/systemd/system/${SERVICE_NAME}.service"
  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME"
  # restart (not `enable --now`) so a re-run/update actually loads the new code — `start` is a no-op
  # against an already-running unit (review HIGH). restart starts a stopped unit and restarts a running one.
  systemctl restart "$SERVICE_NAME"
}

verify_service() {
  log "Verifying startup (preflight + polling)…"
  local logs="" i
  for i in $(seq 1 40); do
    logs="$(journalctl -u "$SERVICE_NAME" --no-pager 2>/dev/null || true)"
    if printf '%s' "$logs" | grep -qi "cannot start\|Startup preflight failed"; then
      err "The service failed its startup preflight:"
      printf '%s\n' "$logs" | grep -i "cannot start\|preflight" >&2 || true
      die "Fix the reported problem and re-run. (Set ANKIVOICE_SKIP_PREFLIGHT=1 only for dev.)"
    fi
    if printf '%s' "$logs" | grep -q "Starting AnkiVoice (long-polling)"; then
      log "Startup preflight passed; the bot is polling."
      break
    fi
    sleep 1
  done
  if printf '%s' "$logs" | grep -qi "Unauthorized\|InvalidToken\|Not Found\|401"; then
    warn "Telegram rejected the bot token (the service started and passed its self-check, but the"
    warn "token is wrong). Edit $INSTALL_DIR/.env, then: systemctl restart $SERVICE_NAME"
  fi
}

print_summary() {
  local active enabled
  active="$(systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
  enabled="$(systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)"
  printf '\n\033[1;32m[ankivoice] Done.\033[0m  service=%s  state=%s  boot=%s  dir=%s\n' \
    "$SERVICE_NAME" "$active" "$enabled" "$INSTALL_DIR" >&2
  cat >&2 <<EOF

Next steps:
  • View logs:     journalctl -u $SERVICE_NAME -f
  • Status:        systemctl status $SERVICE_NAME
  • Restart:       systemctl restart $SERVICE_NAME
  • Update:        re-run  sudo ./install.sh   (preserves your .env)
  • Uninstall:     sudo ./uninstall.sh         (add --purge to remove app/data/model/user)

Send your bot a tab-separated Anki export (Front<TAB>Back) to get an audio .apkg back.
EOF
}

# --- main --------------------------------------------------------------------------------------

main() {
  set -euo pipefail

  local flag_token="" flag_archive="" non_interactive=0 skip_warmup=0
  SERVICE_USER="${ANKIVOICE_USER:-$DEFAULT_USER}"
  INSTALL_DIR="${INSTALL_DIR:-${ANKIVOICE_PREFIX:-$DEFAULT_PREFIX}}"

  while [ $# -gt 0 ]; do
    case "$1" in
      --token)       flag_token="${2:-}"; shift 2 ;;
      --archive-id)  flag_archive="${2:-}"; shift 2 ;;
      --user)        SERVICE_USER="${2:-}"; shift 2 ;;
      --prefix)      INSTALL_DIR="${2:-}"; shift 2 ;;
      --non-interactive) non_interactive=1; shift ;;
      --skip-warmup) skip_warmup=1; shift ;;
      -h|--help)     usage; exit 0 ;;
      *) usage; die "unknown argument: $1" ;;
    esac
  done

  # GUARDS FIRST — make no host changes before these pass.
  require_root
  is_supported_distro || die "Refusing to install on an unsupported OS."

  local script_dir; script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local envfile="$INSTALL_DIR/.env"

  # Resolve the two required values ONLY when we'll create a fresh .env (an existing one is preserved).
  local token="" archive=""
  if [ ! -e "$envfile" ]; then
    if ! token="$(resolve_required ANKIVOICE_BOT_TOKEN "$flag_token" "${ANKIVOICE_BOT_TOKEN:-}")"; then
      [ "$non_interactive" -eq 1 ] && die "ANKIVOICE_BOT_TOKEN is required (pass --token or set the env var)."
      read -rs -p "Telegram bot token (from @BotFather): " token; echo >&2
    fi
    if ! archive="$(resolve_required ANKIVOICE_ARCHIVE_CHAT_ID "$flag_archive" "${ANKIVOICE_ARCHIVE_CHAT_ID:-}")"; then
      [ "$non_interactive" -eq 1 ] && die "ANKIVOICE_ARCHIVE_CHAT_ID is required (pass --archive-id or set the env var)."
      read -r -p "Archive chat/channel id (e.g. -1001234567890): " archive
    fi
    [ -n "$token" ] || die "empty bot token; aborting."
    [ -n "$archive" ] || die "empty archive id; aborting."
  fi

  ensure_packages
  ensure_uv
  ensure_user "$SERVICE_USER" "$INSTALL_DIR"
  copy_tree "$script_dir" "$INSTALL_DIR"
  mkdir -p "$INSTALL_DIR/work" "$INSTALL_DIR/data" "$INSTALL_DIR/models"
  chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"

  ensure_env_file "$envfile" "$token" "$archive" "$INSTALL_DIR/models"
  chown "$SERVICE_USER":"$SERVICE_USER" "$envfile"; chmod 600 "$envfile"

  run_uv_sync
  if [ "$skip_warmup" -eq 0 ]; then run_warmup; else warn "Skipping warm-up (--skip-warmup)."; fi
  install_unit
  verify_service
  print_summary
}

# Run main only when executed directly (so tests can source the helpers).
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
