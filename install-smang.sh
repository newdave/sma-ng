#!/bin/sh
# install-smang.sh - install the `sma` command on a Docker host.
#
#   curl -fsSL https://raw.githubusercontent.com/newdave/sma-ng/main/install-smang.sh | sh
#
# Needs only Docker: no source checkout, Compose, mise or Python. It pulls the
# SMA-NG image, installs `sma` from that image, pins the image for `sma`, and
# creates $SMA_HOME/{config,logs,data} with a sample sma-ng.yml. Safe to re-run:
# existing config is never overwritten. See docs/sma-cli-quickstart.md.
#
# Options (or environment variables, e.g. `| SMA_IMAGE_TAG=3.2.23 sh`):
#   --tag TAG       image tag (SMA_IMAGE_TAG; default: latest, or the latest
#                   release on arm64 because latest is linux/amd64 only)
#   --bin-dir DIR   where to install sma (SMA_BIN_DIR; default /usr/local/bin,
#                   or ~/.local/bin without root or sudo)
#   --daemon        also set up the long-running daemon (not available yet)
#   SMA_HOME        data directory (default /opt/sma on Linux, ~/.local/share/sma
#                   on macOS)
#   DESTDIR         prefix for every host path (testing/staging only)

set -eu

REPO="newdave/sma-ng"
IMAGE_REPO="ghcr.io/$REPO"
API_URL="${SMA_API_URL:-https://api.github.com/repos/$REPO}"
SMA_IMAGE_TAG="${SMA_IMAGE_TAG:-}"
SMA_BIN_DIR="${SMA_BIN_DIR:-}"
SMA_HOME="${SMA_HOME:-}"
DESTDIR="${DESTDIR:-}"
WANT_DAEMON=0

say() { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag)
            SMA_IMAGE_TAG="${2:?--tag needs a value}"
            shift 2
            ;;
        --bin-dir)
            SMA_BIN_DIR="${2:?--bin-dir needs a value}"
            shift 2
            ;;
        --daemon)
            WANT_DAEMON=1
            shift
            ;;
        -h | --help)
            echo "usage: install-smang.sh [--tag TAG] [--bin-dir DIR] [--daemon]"
            exit 0
            ;;
        *) die "unknown option: $1" ;;
    esac
done

# Root for system paths: run directly as root, else via sudo (SUDO= disables it).
if [ "${SUDO+set}" != set ]; then
    if [ "$(id -u)" -eq 0 ]; then
        SUDO=""
    elif command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        SUDO="none"
    fi
fi
as_root() {
    if [ "$SUDO" = "none" ]; then return 1; fi
    $SUDO "$@"
}

# The user sma is installed for (the sudo caller when run as root via sudo).
if [ -n "${SUDO_USER:-}" ] && [ "$(id -u)" -eq 0 ]; then
    OWNER="$SUDO_USER"
    OWNER_HOME="$( (getent passwd "$SUDO_USER" || true) | cut -d: -f6)"
    OWNER_HOME="${OWNER_HOME:-$HOME}"
else
    OWNER="$(id -un)"
    OWNER_HOME="$HOME"
fi
OWNER_GROUP="$(id -gn "$OWNER" 2>/dev/null || id -gn)"

# Linux servers use /opt/sma (the compose file and cluster tooling expect it);
# on macOS the data lives in the user's home, never in /opt.
if [ -z "$SMA_HOME" ]; then
    case "$(uname -s)" in
        Darwin) SMA_HOME="${XDG_DATA_HOME:-$OWNER_HOME/.local/share}/sma" ;;
        *) SMA_HOME=/opt/sma ;;
    esac
fi

# ── preflight ────────────────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || die "Docker is required: https://docs.docker.com/engine/install/"
docker info >/dev/null 2>&1 || die "cannot talk to Docker; is it running, and is $OWNER in the docker group?"

if [ -z "$SMA_IMAGE_TAG" ]; then
    case "$(uname -m)" in
        aarch64 | arm64)
            command -v curl >/dev/null 2>&1 || die "curl is required to find the latest release; pass --tag"
            SMA_IMAGE_TAG="$(curl -fsSL "$API_URL/releases/latest" |
                sed -n 's/.*"tag_name": *"sma-ng-v\([0-9][0-9.]*\)".*/\1/p' | head -n 1)"
            [ -n "$SMA_IMAGE_TAG" ] || die "could not find the latest release for arm64; pass --tag"
            ;;
        *) SMA_IMAGE_TAG=latest ;;
    esac
fi
IMAGE="$IMAGE_REPO:$SMA_IMAGE_TAG"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT INT TERM

# ── image and command ────────────────────────────────────────────────────────
say "Pulling $IMAGE"
docker pull "$IMAGE"

say "Extracting the sma command from the image"
CID="$(docker create "$IMAGE")"
if ! docker cp "$CID:/usr/local/bin/sma-host" "$TMP/sma"; then
    docker rm "$CID" >/dev/null 2>&1 || true
    die "the image has no sma command (/usr/local/bin/sma-host); pick a newer --tag"
fi
docker rm "$CID" >/dev/null 2>&1 || true
chmod 755 "$TMP/sma"

if [ -z "$SMA_BIN_DIR" ]; then
    if [ -w "$DESTDIR/usr/local/bin" ] || [ "$SUDO" != "none" ]; then
        SMA_BIN_DIR=/usr/local/bin
    else
        SMA_BIN_DIR="$OWNER_HOME/.local/bin"
    fi
fi
BIN="$DESTDIR$SMA_BIN_DIR/sma"
if mkdir -p "$(dirname "$BIN")" 2>/dev/null && cp "$TMP/sma" "$BIN.new" 2>/dev/null; then
    mv -f "$BIN.new" "$BIN"
elif ! { as_root mkdir -p "$(dirname "$BIN")" && as_root cp "$TMP/sma" "$BIN.new" && as_root mv -f "$BIN.new" "$BIN"; }; then
    die "cannot write $BIN; re-run with sudo or pass --bin-dir"
fi
say "Installed $SMA_BIN_DIR/sma"
case ":$PATH:" in
    *":$SMA_BIN_DIR:"*) ;;
    *) warn "$SMA_BIN_DIR is not on your PATH; add it, e.g. export PATH=\"$SMA_BIN_DIR:\$PATH\"" ;;
esac

# ── per-user image pin ───────────────────────────────────────────────────────
CONF="$DESTDIR$OWNER_HOME/.config/sma"
mkdir -p "$CONF"
chmod 700 "$CONF"
printf '%s\n' "$IMAGE" >"$CONF/image"
if [ "$(id -u)" -eq 0 ] && [ "$OWNER" != "root" ]; then
    chown -R "$OWNER:$OWNER_GROUP" "$CONF" 2>/dev/null || true
fi

# ── host data directory ──────────────────────────────────────────────────────
HOME_DIR="$DESTDIR$SMA_HOME"
say "Preparing $SMA_HOME/{config,logs,data}"
if ! mkdir -p "$HOME_DIR/config" "$HOME_DIR/logs" "$HOME_DIR/data" 2>/dev/null; then
    as_root mkdir -p "$HOME_DIR/config" "$HOME_DIR/logs" "$HOME_DIR/data" ||
        die "cannot create $SMA_HOME; re-run with sudo or set SMA_HOME"
    as_root chown "$OWNER:$OWNER_GROUP" "$HOME_DIR" "$HOME_DIR/config" "$HOME_DIR/logs" "$HOME_DIR/data" ||
        warn "could not give $OWNER ownership of $SMA_HOME"
fi

if [ -f "$HOME_DIR/config/sma-ng.yml" ]; then
    say "Keeping existing $SMA_HOME/config/sma-ng.yml"
else
    say "Writing a sample $SMA_HOME/config/sma-ng.yml"
    docker run --rm --entrypoint cat "$IMAGE" /app/setup/sma-ng.yml.sample >"$TMP/sma-ng.yml"
    if ! cp "$TMP/sma-ng.yml" "$HOME_DIR/config/sma-ng.yml" 2>/dev/null; then
        as_root cp "$TMP/sma-ng.yml" "$HOME_DIR/config/sma-ng.yml" ||
            die "cannot write $SMA_HOME/config/sma-ng.yml"
        as_root chown "$OWNER:$OWNER_GROUP" "$HOME_DIR/config/sma-ng.yml" || true
    fi
fi

if [ "$WANT_DAEMON" = "1" ]; then
    warn "--daemon: 'sma daemon up' is not available yet; for a daemon today see docs/docker-compose-quickstart.md"
fi

cat <<EOF

sma is installed (image $IMAGE).

  sma version
  sma preview /path/to/file.mkv     # show the conversion plan
  sma repair -n /path/to/library    # dry-run repair report
  sma help

Each command runs in a throwaway container that mounts the paths you name.
Settings: $SMA_HOME/config/sma-ng.yml.  Update: sma upgrade
EOF
