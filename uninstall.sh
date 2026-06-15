#!/usr/bin/env bash
#
# AnkiVoice uninstaller (feature 003). Run as root.
#   sudo ./uninstall.sh            # remove the service only (app/data/.env kept)
#   sudo ./uninstall.sh --purge    # ALSO remove the install dir, data, model cache, and the user
#
# Scoped strictly to the app footprint: --purge refuses to delete anything but the resolved install
# dir. Source-friendly (main runs only when executed directly) so tests can check the scope guard.

SERVICE_NAME="ankivoice"
DEFAULT_USER="ankivoice"
DEFAULT_PREFIX="/opt/ankivoice"

log()  { printf '\033[1;34m[ankivoice]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[1;33m[ankivoice] WARNING:\033[0m %s\n' "$*" >&2; }
err()  { printf '\033[1;31m[ankivoice] ERROR:\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

usage() {
  cat >&2 <<'EOF'
AnkiVoice uninstaller (run as root).

Usage: sudo ./uninstall.sh [--purge] [--yes] [--user NAME] [--prefix DIR]

  (default)        stop + disable + remove the systemd unit; leave app/data/.env in place
  --purge          ALSO remove the install dir, data, model cache, and the service user
  --yes            skip the confirmation that --purge otherwise requires
  --user NAME      service user (else $ANKIVOICE_USER, else "ankivoice")
  --prefix DIR     install dir (else $INSTALL_DIR / $ANKIVOICE_PREFIX, else "/opt/ankivoice")
EOF
}

require_root() {
  [ "$(id -u)" -eq 0 ] || die "This uninstaller must run as root. Re-run with: sudo ./uninstall.sh"
}

# is_safe_install_dir DIR -> on success echoes the cleaned, canonical path and returns 0; otherwise
# returns non-zero. Scope guard (Constitution P5): refuses '/', top-level system dirs, any bare
# /home/<user>, $HOME, paths with <2 components, and — critically — any path containing a '.' or '..'
# component (so a traversal like /opt/x/../../etc can never resolve outside the footprint). The caller
# MUST delete the echoed cleaned path, not the raw input, so validation and deletion agree.
is_safe_install_dir() {
  local dir="${1:-}"
  case "$dir" in /*) ;; *) return 1 ;; esac          # must be absolute
  local comp clean="" had=0
  local IFS=/
  for comp in $dir; do
    case "$comp" in
      '') continue ;;                                  # collapse // and a trailing /
      .|..) return 1 ;;                                # refuse '.'/'..' (closes the traversal bypass)
      *) clean="$clean/$comp"; had=$((had + 1)) ;;
    esac
  done
  [ "$had" -ge 2 ] || return 1                         # require >=2 components (e.g. /opt/ankivoice)
  case "$clean/" in
    /usr/*|/etc/*|/var/*|/bin/*|/sbin/*|/lib/*|/lib64/*|/boot/*|/dev/*|/proc/*|/sys/*|/run/*) return 1 ;;
  esac
  case "$clean" in /home/*/*) ;; /home/*) return 1 ;; esac   # deny a bare /home/<user>; allow deeper
  [ "$clean" = "${HOME%/}" ] && return 1
  printf '%s\n' "$clean"
  return 0
}

remove_service() {
  if [ -e "/etc/systemd/system/${SERVICE_NAME}.service" ] \
     || systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}.service"; then
    log "Stopping and removing the systemd service…"
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload 2>/dev/null || true
  else
    log "No systemd service installed (nothing to remove)."
  fi
}

purge_footprint() {
  local real
  if ! real="$(is_safe_install_dir "$INSTALL_DIR")"; then
    die "Refusing to remove an unsafe path: '$INSTALL_DIR' (scope guard)."
  fi
  # Delete the validated, cleaned path (never the raw input).
  if [ -d "$real" ]; then rm -rf "$real"; log "Removed $real."; fi
  if getent passwd "$SERVICE_USER" >/dev/null 2>&1; then
    userdel "$SERVICE_USER" 2>/dev/null || true; log "Removed service user '$SERVICE_USER'."
  fi
  getent group "$SERVICE_USER" >/dev/null 2>&1 && groupdel "$SERVICE_USER" 2>/dev/null || true
}

main() {
  set -euo pipefail
  local purge=0 assume_yes=0
  SERVICE_USER="${ANKIVOICE_USER:-$DEFAULT_USER}"
  INSTALL_DIR="${INSTALL_DIR:-${ANKIVOICE_PREFIX:-$DEFAULT_PREFIX}}"

  while [ $# -gt 0 ]; do
    case "$1" in
      --purge) purge=1; shift ;;
      --yes|-y) assume_yes=1; shift ;;
      --user) SERVICE_USER="${2:-}"; shift 2 ;;
      --prefix) INSTALL_DIR="${2:-}"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) usage; die "unknown argument: $1" ;;
    esac
  done

  require_root
  remove_service

  if [ "$purge" -eq 1 ]; then
    if [ "$assume_yes" -ne 1 ]; then
      printf '%s' "PURGE will delete $INSTALL_DIR (incl. data + model cache) and user '$SERVICE_USER'. Type 'yes' to confirm: " >&2
      local ans; read -r ans
      [ "$ans" = "yes" ] || die "purge aborted."
    fi
    purge_footprint
  else
    log "Service removed. App, data, and .env were kept (use --purge to remove everything)."
  fi
  log "Uninstall complete."
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
