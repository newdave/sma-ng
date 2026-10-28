#!/bin/sh
# sma - run SMA-NG commands in the Docker container from the Docker host.
#
# Shipped in the image as /usr/local/bin/sma-host. Install on the host with:
#   docker cp sma-ng:/usr/local/bin/sma-host /usr/local/bin/sma   (see docs/sma-cli-quickstart.md)
#   chmod 755 /usr/local/bin/sma
#
# Every command runs through the image entrypoint's `run` mode so it gets the
# daemon's identity (ubuntu, same supplementary groups, same VAAPI env).
#
# Environment:
#   SMA_CONTAINER / CONTAINER  container name or id (skips discovery)
#   SMA_COMPOSE_SERVICE        compose service to resolve (default: first running)
#   SMA_COMPOSE_DIR            compose project directory (default: /opt/sma/docker)
#   DOCKER_BIN                 docker executable (default: docker)
#
# Exit codes: the container command's code; 2 usage error or path not visible
# in the container; 3 no running container.

SMA_HOST_VERSION="@SMA_GIT_SHA@"
DOCKER_BIN="${DOCKER_BIN:-docker}"
SMA_ENTRYPOINT="/usr/local/bin/entrypoint.sh"
SMA_CONTAINER_ID=""
SMA_MOUNTS=""
SMA_P=""

usage() {
    cat <<'EOF'
usage: sma <command> [args...]

  convert PATH [ARGS]   convert a file or directory (manual.py -i PATH -a)
  preview PATH [ARGS]   show the conversion plan only (manual.py -i PATH -oo -a)
  manual [ARGS]         run manual.py with raw arguments
  repair PATH... [ARGS] repair damaged media (repair.py)
  rename PATH [ARGS]    rename media (rename.py)
  audit PATH [ARGS]     library audit (manual.py --audit -i PATH)
  codecs                list supported codecs (manual.py -cl)
  smoke                 run the daemon smoke test
  shell [ARGS]          interactive bash as the daemon user
  logs [ARGS]           follow container logs (docker logs -f)
  status                container state
  version               host script and container image versions

Paths must be the same on the host and in the container (identity mounts).
EOF
}

err() { printf 'sma: %s\n' "$*" >&2; }

# Resolve the container id/name into SMA_CONTAINER_ID or exit 3.
resolve_container() {
    [ -n "$SMA_CONTAINER_ID" ] && return 0
    _c="${SMA_CONTAINER:-${CONTAINER:-}}"
    if [ -z "$_c" ]; then
        _dir="${SMA_COMPOSE_DIR:-/opt/sma/docker}"
        if [ -d "$_dir" ]; then
            if [ -n "${SMA_COMPOSE_SERVICE:-}" ]; then
                _c="$("$DOCKER_BIN" compose --project-directory "$_dir" ps -q "$SMA_COMPOSE_SERVICE" 2>/dev/null | head -n 1)"
            else
                _c="$("$DOCKER_BIN" compose --project-directory "$_dir" ps -q 2>/dev/null | head -n 1)"
            fi
        fi
    fi
    if [ -z "$_c" ]; then
        _c="sma-ng"
    fi
    _running="$("$DOCKER_BIN" inspect -f '{{.State.Running}}' "$_c" 2>/dev/null)" || _running=""
    if [ "$_running" != "true" ]; then
        err "no running sma-ng container (set SMA_CONTAINER)"
        exit 3
    fi
    SMA_CONTAINER_ID="$_c"
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

# Set SMA_P to the absolute host path for $1 (target need not exist).
abs_path() {
    case "$1" in
        /*) _p="$1" ;;
        *) _p="$PWD/$1" ;;
    esac
    _p="$(normalize "$_p")"
    _d="$(dirname "$_p")"
    if [ -d "$_d" ] && _real="$(cd "$_d" 2>/dev/null && pwd -P)"; then
        if [ "$_p" = "/" ]; then
            SMA_P="/"
        elif [ "$_real" = "/" ]; then
            SMA_P="/$(basename "$_p")"
        else
            SMA_P="$_real/$(basename "$_p")"
        fi
    else
        SMA_P="$_p"
    fi
}

load_mounts() {
    [ -n "$SMA_MOUNTS" ] && return 0
    resolve_container
    _raw="$("$DOCKER_BIN" inspect -f '{{range .Mounts}}{{.Source}}|{{.Destination}}{{"\n"}}{{end}}' "$SMA_CONTAINER_ID" 2>/dev/null)" || _raw=""
    # Keep only identity mounts (Source == Destination), one path per line.
    SMA_MOUNTS=""
    _nl='
'
    _oldifs="$IFS"
    IFS="$_nl"
    for _line in $_raw; do
        _src="${_line%%|*}"
        _dst="${_line#*|}"
        if [ -n "$_src" ] && [ "$_src" = "$_dst" ]; then
            SMA_MOUNTS="${SMA_MOUNTS}${_src}${_nl}"
        fi
    done
    IFS="$_oldifs"
    [ -n "$SMA_MOUNTS" ] || SMA_MOUNTS="-"
}

# Validate $1 against the identity mounts; set SMA_P to the absolute path.
check_path() {
    abs_path "$1"
    load_mounts
    _nl='
'
    _oldifs="$IFS"
    IFS="$_nl"
    _ok=1
    _list=""
    for _m in $SMA_MOUNTS; do
        [ "$_m" = "-" ] && continue
        _list="${_list:+$_list }$_m"
        if [ "$_m" = "/" ] || [ "$SMA_P" = "$_m" ]; then
            _ok=0
        else
            case "$SMA_P" in
                "$_m"/*) _ok=0 ;;
            esac
        fi
    done
    IFS="$_oldifs"
    if [ "$_ok" -ne 0 ]; then
        err "$1 is not visible at the same path inside the container (mounted: ${_list:-none})"
        exit 2
    fi
}

# Run "$@" in the container through the entrypoint's run mode.
run_in() {
    resolve_container
    _flags=""
    if [ -t 0 ] || [ -p /dev/stdin ] || [ -f /dev/stdin ]; then
        _flags="-i"
    fi
    if [ -t 0 ] && [ -t 1 ]; then
        _flags="-it"
    fi
    # shellcheck disable=SC2086
    exec "$DOCKER_BIN" exec $_flags "$SMA_CONTAINER_ID" "$SMA_ENTRYPOINT" run "$@"
}

# run_checked MODE PREFIX ARGS...
#   MODE positional: check every non-option argument that looks like a path
#   MODE input:      check the value of -i / --input
# Relative paths are passed to the container as absolute host paths.
run_checked() {
    _mode="$1"
    _prefix="$2"
    shift 2
    _n=$#
    _want=0
    while [ "$_n" -gt 0 ]; do
        _a="$1"
        shift
        _chk=0
        if [ "$_want" -eq 1 ]; then
            _chk=1
            _want=0
        else
            case "$_a" in
                -c | --config) _want=2 ;;
                -i | --input) [ "$_mode" = "input" ] && _want=1 ;;
                -*) ;;
                *)
                    if [ "$_mode" = "positional" ]; then
                        case "$_a" in
                            */*) _chk=1 ;;
                            *) [ -e "$_a" ] && _chk=1 ;;
                        esac
                    fi
                    ;;
            esac
        fi
        if [ "$_want" -eq 2 ]; then
            _want=0
            set -- "$@" "$_a"
            _n=$((_n - 1))
            [ "$_n" -gt 0 ] || break
            _a="$1"
            shift
            _n=$((_n - 1))
            set -- "$@" "$_a"
            continue
        fi
        if [ "$_chk" -eq 1 ]; then
            check_path "$_a"
            case "$_a" in
                /*) ;;
                *) _a="$SMA_P" ;;
            esac
        fi
        set -- "$@" "$_a"
        _n=$((_n - 1))
    done
    # shellcheck disable=SC2086
    run_in $_prefix "$@"
}

need_path() {
    if [ "$1" -lt 1 ]; then
        err "$2 needs a PATH (see 'sma help')"
        exit 2
    fi
}

cmd_version() {
    case "$SMA_HOST_VERSION" in
        "@SMA_"*) _host="dev" ;;
        *) _host="$SMA_HOST_VERSION" ;;
    esac
    printf 'sma host script: %s\n' "$_host"
    resolve_container
    _img="$("$DOCKER_BIN" exec "$SMA_CONTAINER_ID" printenv SMA_GIT_SHA 2>/dev/null)" || _img="unknown"
    printf 'container image: %s\n' "$_img"
    if [ "$_host" != "dev" ] && [ "$_host" != "$_img" ]; then
        err "warning: host script and container image versions differ; reinstall sma from the image"
    fi
}

cmd_status() {
    resolve_container
    "$DOCKER_BIN" inspect -f 'container={{.Name}} state={{.State.Status}} started={{.State.StartedAt}}' "$SMA_CONTAINER_ID"
}

main() {
    if [ "$#" -lt 1 ]; then
        usage >&2
        exit 2
    fi
    _sub="$1"
    shift
    case "$_sub" in
        help | -h | --help) usage ;;
        version) cmd_version ;;
        status) cmd_status ;;
        logs)
            resolve_container
            exec "$DOCKER_BIN" logs -f "$SMA_CONTAINER_ID" "$@"
            ;;
        shell) run_in bash "$@" ;;
        codecs) run_in python manual.py -cl "$@" ;;
        smoke) run_in python daemon.py --smoke-test "$@" ;;
        manual) run_checked input "python manual.py" "$@" ;;
        convert)
            need_path "$#" convert
            _first="$1"
            shift
            check_path "$_first"
            run_in python manual.py -i "$SMA_P" -a "$@"
            ;;
        preview)
            need_path "$#" preview
            _first="$1"
            shift
            check_path "$_first"
            run_in python manual.py -i "$SMA_P" -oo -a "$@"
            ;;
        audit)
            need_path "$#" audit
            _first="$1"
            shift
            check_path "$_first"
            run_in python manual.py --audit -i "$SMA_P" "$@"
            ;;
        repair)
            need_path "$#" repair
            run_checked positional "python repair.py" "$@"
            ;;
        rename)
            need_path "$#" rename
            run_checked positional "python rename.py" "$@"
            ;;
        *)
            err "unknown command: $_sub (see 'sma help')"
            exit 2
            ;;
    esac
}

main "$@"
