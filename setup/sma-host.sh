#!/bin/sh
# sma - run SMA-NG commands on this Docker host.
#
# Installed as /usr/local/bin/sma by install-smang.sh (the image ships it as
# /usr/local/bin/sma-host). This script is only a launcher: it collects host
# facts, runs the SMA-NG client inside the image, and executes the plan the
# client writes. The plan is a list of docker commands, for example a
# throwaway `docker run --rm` that mounts exactly the paths you named.
# Plan lines become docker arguments through `set --`; nothing is eval'd.
# See docs/sma-cli-quickstart.md.
#
# Environment:
#   SMA_IMAGE        image to run (default: ~/.config/sma/image, else ghcr.io/newdave/sma-ng:latest)
#   SMA_HOME         host data directory with config/, logs/, data/ (default: /opt/sma on Linux,
#                    ~/.local/share/sma on macOS)
#   SMA_CONFIG_HOME  client settings directory (default: ~/.config/sma)
#   SMA_DAEMON_NAME  name of the daemon container (default: sma-ng)
#   DOCKER_BIN       docker executable (default: docker)
#
# Exit codes: the tool's own code for a run; 2 usage error or bad path;
# 3 Docker or target unreachable; 4 authentication failed; 5 plan error or
# version mismatch (run `sma upgrade`).

SMA_HOST_VERSION="@SMA_GIT_SHA@"
DOCKER_BIN="${DOCKER_BIN:-docker}"
DEFAULT_IMAGE="ghcr.io/newdave/sma-ng:latest"
PLAN_HEADER="sma-plan 1"
# Keep in sync with ALLOWED_SUBCOMMANDS in resources/cli/plan.py.
PLAN_ALLOWED=" run pull rm stop restart logs compose exec create cp inspect "
NL='
'
TAB="$(printf '\t')"

err() { printf 'sma: %s\n' "$*" >&2; }

# The invoking user's home, also under sudo (for ~/.config/sma).
user_home() {
    if [ -n "${SUDO_USER:-}" ] && [ "$(id -u)" = "0" ]; then
        _h="$( (getent passwd "$SUDO_USER" || true) | cut -d: -f6)"
        [ -n "$_h" ] && { printf '%s\n' "$_h"; return 0; }
    fi
    printf '%s\n' "${HOME:-/tmp}"
}

SMA_CONFIG_HOME="${SMA_CONFIG_HOME:-$(user_home)/.config/sma}"

# Linux servers use /opt/sma (the compose file and cluster tooling expect it);
# on macOS the data lives in the user's home, never in /opt.
default_sma_home() {
    case "$(uname -s)" in
        Darwin) printf '%s\n' "${XDG_DATA_HOME:-$(user_home)/.local/share}/sma" ;;
        *) printf '%s\n' /opt/sma ;;
    esac
}

SMA_HOME="${SMA_HOME:-$(default_sma_home)}"

image_ref() {
    if [ -n "${SMA_IMAGE:-}" ]; then
        printf '%s\n' "$SMA_IMAGE"
    elif [ -r "$SMA_CONFIG_HOME/image" ] && read -r _img <"$SMA_CONFIG_HOME/image" && [ -n "$_img" ]; then
        printf '%s\n' "$_img"
    else
        printf '%s\n' "$DEFAULT_IMAGE"
    fi
}

# Lexically normalise absolute path $1 (collapse //, ., ..).
normalize() {
    _out=""
    _oldifs="$IFS"
    IFS=/
    set -f
    for _seg in $1; do
        case "$_seg" in
            "" | .) ;;
            ..) _out="${_out%/*}" ;;
            *) _out="$_out/$_seg" ;;
        esac
    done
    set +f
    IFS="$_oldifs"
    printf '%s\n' "${_out:-/}"
}

# ── upgrade (handled here: it replaces this script) ─────────────────────────

cmd_upgrade() {
    _cur="$(image_ref)"
    case "${_cur##*/}" in
        *@*) _repo="${_cur%@*}" ;;
        *:*) _repo="${_cur%:*}" ;;
        *) _repo="$_cur" ;;
    esac
    _new="${1:+$_repo:$1}"
    _new="${_new:-$_cur}"
    case "$0" in
        */*) _self="$0" ;;
        *) _self="$(command -v -- "$0")" ;;
    esac
    _dir="$(dirname -- "$_self")"
    if [ ! -w "$_dir" ] || { [ -e "$_self" ] && [ ! -w "$_self" ]; }; then
        err "cannot replace $_self; run: sudo sma upgrade${1:+ $1}"
        exit 2
    fi
    "$DOCKER_BIN" pull "$_new" || exit $?
    _cid="$("$DOCKER_BIN" create "$_new")" || exit $?
    _tmp="$_self.new.$$"
    if ! "$DOCKER_BIN" cp "$_cid:/usr/local/bin/sma-host" "$_tmp"; then
        "$DOCKER_BIN" rm "$_cid" >/dev/null 2>&1
        rm -f "$_tmp"
        exit 1
    fi
    "$DOCKER_BIN" rm "$_cid" >/dev/null 2>&1
    if ! { chmod 755 "$_tmp" && mv -f "$_tmp" "$_self"; }; then
        rm -f "$_tmp"
        exit 1
    fi
    mkdir -p "$SMA_CONFIG_HOME" && printf '%s\n' "$_new" >"$SMA_CONFIG_HOME/image"
    if [ -n "${SUDO_USER:-}" ] && [ "$(id -u)" = "0" ]; then
        chown -R "$SUDO_USER" "$SMA_CONFIG_HOME" 2>/dev/null || true
    fi
    printf 'sma: upgraded to %s\n' "$_new"
    exit 0
}

# ── host facts ───────────────────────────────────────────────────────────────

# Write <index>\t<d|f|->\t<abspath> for every argument (index 0 = first word).
write_argstat() {
    _cwd="$(pwd -P)"
    _i=0
    for _a in "$@"; do
        case "$_a" in
            *"$NL"*)
                err "arguments cannot contain newlines"
                exit 2
                ;;
        esac
        case "$_a" in
            /*) _p="$_a" ;;
            *) _p="$_cwd/$_a" ;;
        esac
        _p="$(normalize "$_p")"
        if [ -d "$_a" ]; then
            _t=d
        elif [ -e "$_a" ]; then
            _t=f
        else
            _t=-
        fi
        printf '%s%s%s%s%s\n' "$_i" "$TAB" "$_t" "$TAB" "$_p"
        _i=$((_i + 1))
    done
}

collect_facts() {
    _sec="$("$DOCKER_BIN" info --format '{{.SecurityOptions}}' 2>/dev/null)" || {
        err "cannot talk to Docker; is it running, and can this user use it?"
        exit 3
    }
    SMA_HOST_PWD="$(pwd -P)"
    SMA_HOST_UID="$(id -u)"
    SMA_HOST_GID="$(id -g)"
    SMA_HOST_OS="$(uname -s)"
    SMA_HOST_HOME="$(user_home)"
    SMA_HOST_TTY=0
    if [ -t 0 ] && [ -t 1 ]; then SMA_HOST_TTY=1; fi
    SMA_HOST_STDIN=0
    if [ -t 0 ] || [ -p /dev/stdin ] || [ -f /dev/stdin ]; then SMA_HOST_STDIN=1; fi
    _dri="${SMA_DEV_DRI:-/dev/dri}"
    SMA_HOST_DRI_GIDS=""
    if [ -d "$_dri" ]; then
        # shellcheck disable=SC2012  # only the numeric group column of device nodes is read, never names
        SMA_HOST_DRI_GIDS="$(ls -ln "$_dri" 2>/dev/null | awk '$1 ~ /^c/ {print $4}' | sort -un | paste -s -d, -)"
    fi
    SMA_HOST_NVIDIA=0
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then SMA_HOST_NVIDIA=1; fi
    SMA_HOST_ROOTLESS=0
    SMA_HOST_SELINUX=0
    case "$_sec" in *name=rootless*) SMA_HOST_ROOTLESS=1 ;; esac
    case "$_sec" in *name=selinux*) SMA_HOST_SELINUX=1 ;; esac
    SMA_HOST_LOGS_WRITABLE=0
    if [ -d "$SMA_HOME/logs" ] && [ -w "$SMA_HOME/logs" ]; then SMA_HOST_LOGS_WRITABLE=1; fi
    SMA_HOST_CONFIG=0
    if [ -d "$SMA_HOME/config" ]; then SMA_HOST_CONFIG=1; fi
    SMA_IMAGE="$(image_ref)"
    SMA_DAEMON_NAME="${SMA_DAEMON_NAME:-sma-ng}"
    export SMA_HOST_PWD SMA_HOST_UID SMA_HOST_GID SMA_HOST_OS SMA_HOST_HOME SMA_HOST_TTY SMA_HOST_STDIN \
        SMA_HOST_DRI_GIDS SMA_HOST_NVIDIA SMA_HOST_ROOTLESS SMA_HOST_SELINUX SMA_HOST_LOGS_WRITABLE \
        SMA_HOST_CONFIG SMA_HOST_VERSION SMA_HOME SMA_IMAGE SMA_DAEMON_NAME
}

# ── plan execution ───────────────────────────────────────────────────────────

# Run the docker steps in $plan_text: all but the last in order (a failure
# aborts with its code), then exec the last. The plan is read on fd 3 so
# docker keeps the terminal on stdin.
run_plan() {
    _first="${plan_text%%"$NL"*}"
    if [ "$_first" != "$PLAN_HEADER" ]; then
        err "plan version '$_first' unsupported; run sma upgrade"
        exit 5
    fi
    _total="$(printf '%s\n' "$plan_text" | grep -c '^end$')"
    [ "$_total" -gt 0 ] || exit 0
    _n=0
    _z=""
    [ "$SMA_HOST_SELINUX" = "1" ] && _z=":z"
    while IFS= read -r _line <&3; do
        case "$_line" in
            "$PLAN_HEADER") ;;
            "step docker") set -- ;;
            "arg "*) set -- "$@" "${_line#arg }" ;;
            "optmount "*)
                _p="${_line#optmount }"
                if [ -d "$_p" ]; then set -- "$@" -v "$_p:$_p$_z"; fi
                ;;
            end)
                if [ "$#" -eq 0 ]; then
                    err "empty plan step"
                    exit 5
                fi
                case "$PLAN_ALLOWED" in
                    *" $1 "*) ;;
                    *)
                        err "plan uses docker $1, which is not allowed"
                        exit 5
                        ;;
                esac
                _n=$((_n + 1))
                if [ "$_n" -eq "$_total" ]; then
                    exec "$DOCKER_BIN" "$@" 3<&-
                fi
                "$DOCKER_BIN" "$@" 3<&- || exit $?
                ;;
            *)
                err "unknown plan line: $_line"
                exit 5
                ;;
        esac
    done 3<<EOF
$plan_text
EOF
}

main() {
    case "${1:-}" in
        upgrade)
            shift
            cmd_upgrade "$@"
            ;;
        version)
            case "$SMA_HOST_VERSION" in
                "@SMA_"*) printf 'sma host command: dev\n' ;;
                *) printf 'sma host command: %s\n' "$SMA_HOST_VERSION" ;;
            esac
            ;;
    esac
    collect_facts
    plan_dir="$(mktemp -d "${TMPDIR:-/tmp}/sma-plan.XXXXXX")" || exit 1
    chmod 700 "$plan_dir"
    write_argstat "$@" >"$plan_dir/argstat" || {
        rm -rf "$plan_dir"
        exit 2
    }
    "$DOCKER_BIN" inspect "$SMA_DAEMON_NAME" >"$plan_dir/daemon.json" 2>/dev/null ||
        rm -f "$plan_dir/daemon.json"
    mkdir -p "$SMA_CONFIG_HOME" 2>/dev/null
    _z=""
    [ "$SMA_HOST_SELINUX" = "1" ] && _z=":z"

    # Client container options are prepended to the user's arguments.
    set -- --entrypoint python -w /app "$SMA_IMAGE" -m resources.cli "$@"
    if [ "$SMA_HOST_CONFIG" = "1" ]; then set -- -v "$SMA_HOME/config:/app/config:ro${_z:+,z}" "$@"; fi
    if [ -d "$SMA_CONFIG_HOME" ]; then set -- -v "$SMA_CONFIG_HOME:/home-config$_z" "$@"; fi
    set -- -v "$plan_dir:/plan$_z" -e HOME=/tmp "$@"
    # Reach a daemon on this host's 127.0.0.1 (Docker Desktop uses host.docker.internal instead).
    if [ "$SMA_HOST_OS" = "Linux" ]; then set -- --network host "$@"; fi
    # Only `target add --api-key-stdin` reads stdin; never hold it open otherwise, so a
    # later throwaway step still gets it.
    for _a in "$@"; do
        if [ "$_a" = "--api-key-stdin" ]; then
            set -- -i "$@"
            break
        fi
    done
    if [ "$SMA_HOST_ROOTLESS" != "1" ] && [ "$SMA_HOST_UID" != "0" ]; then
        set -- --user "$SMA_HOST_UID:$SMA_HOST_GID" "$@"
    fi
    for _v in SMA_HOST_PWD SMA_HOST_UID SMA_HOST_GID SMA_HOST_OS SMA_HOST_HOME SMA_HOST_TTY SMA_HOST_STDIN \
        SMA_HOST_DRI_GIDS SMA_HOST_NVIDIA SMA_HOST_ROOTLESS SMA_HOST_SELINUX SMA_HOST_LOGS_WRITABLE \
        SMA_HOST_CONFIG SMA_HOST_VERSION SMA_HOME SMA_IMAGE SMA_DAEMON_NAME; do
        set -- -e "$_v" "$@"
    done
    "$DOCKER_BIN" run --rm "$@"
    _rc=$?
    if [ "$_rc" -ne 0 ]; then
        rm -rf "$plan_dir"
        exit "$_rc"
    fi
    plan_text=""
    if [ -f "$plan_dir/plan" ]; then plan_text="$(cat "$plan_dir/plan")"; fi
    rm -rf "$plan_dir"
    [ -n "$plan_text" ] || exit 0
    run_plan
}

main "$@"
