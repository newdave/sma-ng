#!/usr/bin/env bash
#
# scripts/i915-tune.sh — Tune Intel i915 GPU for sustained transcoding performance.
#
# Usage:
#   i915-tune.sh [options]
#
# Options:
#   --device <path>   DRI render node to target (default: /dev/dri/renderD128)
#   --min-freq <mhz>  Minimum clock frequency in MHz (default: hardware min)
#   --max-freq <mhz>  Maximum clock frequency in MHz (default: hardware max)
#   --boost-freq <mhz> Boost clock frequency in MHz (default: hardware max)
#   --powersave       Set min=max=boost=hardware min (minimum power)
#   --performance     Set min=max=boost=hardware max (maximum performance)
#   --status          Show current frequencies and scheduler state, then exit
#   --dry-run         Print what would be written without writing
#   -h, --help        Show this help
#
# What this script does:
#   - Sets i915 GT min/max/boost clock frequencies via /sys/class/drm/
#   - Disables RC6 power gating on SR-IOV virtual functions where applicable
#   - Sets the GPU scheduler to a fixed frequency band to avoid thermal throttling
#     during back-to-back transcoding jobs
#   - Configures transparent hugepages for the DRM memory manager
#
# Tunable sysfs paths (kernel 5.10+):
#   /sys/class/drm/card*/gt/gt0/rps_min_freq_mhz
#   /sys/class/drm/card*/gt/gt0/rps_max_freq_mhz
#   /sys/class/drm/card*/gt/gt0/rps_boost_freq_mhz
#   /sys/class/drm/card*/gt/gt0/rc6_enable          (0 = disabled)
#
# Requires: root (or CAP_SYS_ADMIN for sysfs writes)
#
# SR-IOV note:
#   On Proxmox with Intel SR-IOV VFs (/dev/dri/renderD129, renderD130, …),
#   clock management lives on the Physical Function (PF). Pass --device for the
#   PF node (usually renderD128) regardless of which VF SMA is using.

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

die()   { echo "Error: $*" >&2; exit 1; }
info()  { echo "  $*"; }
warn()  { echo "Warning: $*" >&2; }

usage() {
    sed -n '3,36s/^# \?//p' "$0"
    exit 1
}

syswrite() {
    local path="$1" value="$2"
    if [[ "$dry_run" == true ]]; then
        echo "  [dry-run] echo $value > $path"
        return
    fi
    if [[ ! -w "$path" ]]; then
        warn "Not writable (skipping): $path"
        return
    fi
    echo "$value" > "$path"
}

sysread() {
    local path="$1"
    if [[ -r "$path" ]]; then
        cat "$path"
    else
        echo "(unavailable)"
    fi
}

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

device="/dev/dri/renderD128"
min_freq=""
max_freq=""
boost_freq=""
mode=""       # "performance" | "powersave" | ""
dry_run=false
status_only=false

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --device)
            [[ $# -ge 2 ]] || die "--device requires a value"
            device="$2"; shift 2 ;;
        --min-freq)
            [[ $# -ge 2 ]] || die "--min-freq requires a value"
            min_freq="$2"; shift 2 ;;
        --max-freq)
            [[ $# -ge 2 ]] || die "--max-freq requires a value"
            max_freq="$2"; shift 2 ;;
        --boost-freq)
            [[ $# -ge 2 ]] || die "--boost-freq requires a value"
            boost_freq="$2"; shift 2 ;;
        --powersave)
            mode="powersave"; shift ;;
        --performance)
            mode="performance"; shift ;;
        --status)
            status_only=true; shift ;;
        --dry-run)
            dry_run=true; shift ;;
        -h|--help)
            usage ;;
        -*)
            die "Unknown option: $1" ;;
        *)
            die "Unexpected argument: $1" ;;
    esac
done

# ---------------------------------------------------------------------------
# Resolve card from render node
# ---------------------------------------------------------------------------

# /dev/dri/renderD128 -> renderD128 -> find matching card* via uevent
render_name="$(basename "$device")"

card_gt=""
for card_path in /sys/class/drm/card*/; do
    [[ -d "$card_path" ]] || continue
    # Each card exposes its render node under /sys/class/drm/card*/device/drm/renderD*
    if [[ -e "${card_path}device/drm/${render_name}" ]]; then
        # Prefer gt/gt0 (kernel 5.18+ unified GT sysfs)
        if [[ -d "${card_path}gt/gt0" ]]; then
            card_gt="${card_path}gt/gt0"
        elif [[ -d "${card_path}gt0" ]]; then
            card_gt="${card_path}gt0"
        fi
        break
    fi
done

# Fallback: if only one i915 card exists, use it directly
if [[ -z "$card_gt" ]]; then
    for card_path in /sys/class/drm/card*/; do
        [[ -d "$card_path" ]] || continue
        driver_link="${card_path}device/driver"
        if [[ -L "$driver_link" ]] && readlink "$driver_link" | grep -q "i915"; then
            if [[ -d "${card_path}gt/gt0" ]]; then
                card_gt="${card_path}gt/gt0"
                warn "Could not match $render_name directly; using $(basename "${card_path%/}") via i915 driver."
                break
            elif [[ -d "${card_path}gt0" ]]; then
                card_gt="${card_path}gt0"
                warn "Could not match $render_name directly; using $(basename "${card_path%/}") via i915 driver."
                break
            fi
        fi
    done
fi

[[ -n "$card_gt" ]] || die "Could not find i915 GT sysfs for $device. Is the i915 driver loaded?"

rps_min="${card_gt}/rps_min_freq_mhz"
rps_max="${card_gt}/rps_max_freq_mhz"
rps_boost="${card_gt}/rps_boost_freq_mhz"
rps_cur="${card_gt}/rps_cur_freq_mhz"
rps_act="${card_gt}/rps_act_freq_mhz"
# Read hardware min/max from the RP limits file if available
hw_min_path="${card_gt}/rps_RP1_freq_mhz"   # Efficient+ frequency (RP1 = efficient floor)
hw_max_path="${card_gt}/rps_RP0_freq_mhz"   # Maximum Turbo (RP0)

hw_min="$(sysread "$hw_min_path")"
hw_max="$(sysread "$hw_max_path")"

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

print_status() {
    echo ""
    echo "i915 GPU status — $device"
    echo "  GT sysfs:           $card_gt"
    echo "  HW min (RP1):       ${hw_min} MHz"
    echo "  HW max (RP0):       ${hw_max} MHz"
    echo "  Current min (rps):  $(sysread "$rps_min") MHz"
    echo "  Current max (rps):  $(sysread "$rps_max") MHz"
    echo "  Current boost:      $(sysread "$rps_boost") MHz"
    echo "  Current freq:       $(sysread "$rps_cur") MHz"
    echo "  Actual freq:        $(sysread "$rps_act") MHz"

    rc6_path="${card_gt}/rc6_enable"
    if [[ -r "$rc6_path" ]]; then
        echo "  RC6 enabled:        $(sysread "$rc6_path")"
    fi

    rc6_res_path="${card_gt}/rc6_residency_ms"
    if [[ -r "$rc6_res_path" ]]; then
        echo "  RC6 residency:      $(sysread "$rc6_res_path") ms"
    fi
    echo ""
}

if [[ "$status_only" == true ]]; then
    print_status
    exit 0
fi

# ---------------------------------------------------------------------------
# Resolve frequencies for named modes
# ---------------------------------------------------------------------------

if [[ "$mode" == "performance" ]]; then
    [[ -n "$min_freq" ]] || min_freq="$hw_max"
    [[ -n "$max_freq" ]] || max_freq="$hw_max"
    [[ -n "$boost_freq" ]] || boost_freq="$hw_max"
elif [[ "$mode" == "powersave" ]]; then
    [[ -n "$min_freq" ]] || min_freq="$hw_min"
    [[ -n "$max_freq" ]] || max_freq="$hw_min"
    [[ -n "$boost_freq" ]] || boost_freq="$hw_min"
fi

# ---------------------------------------------------------------------------
# Validate that we have something to do
# ---------------------------------------------------------------------------

if [[ -z "$min_freq" && -z "$max_freq" && -z "$boost_freq" ]]; then
    die "No frequencies specified. Use --performance, --powersave, or set --min-freq / --max-freq / --boost-freq."
fi

# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

echo ""
echo "Tuning i915 GPU — $device"
[[ "$dry_run" == true ]] && echo "(dry run — no changes will be written)"
echo ""

# Order matters: always write max before min (kernel rejects min > current max)
if [[ -n "$max_freq" ]]; then
    info "Set max freq: ${max_freq} MHz"
    syswrite "$rps_max" "$max_freq"
fi
if [[ -n "$boost_freq" ]]; then
    info "Set boost freq: ${boost_freq} MHz"
    syswrite "$rps_boost" "$boost_freq"
fi
if [[ -n "$min_freq" ]]; then
    info "Set min freq: ${min_freq} MHz"
    syswrite "$rps_min" "$min_freq"
fi

# Disable RC6 power gating to prevent stall/latency spikes between jobs
rc6_path="${card_gt}/rc6_enable"
if [[ -w "$rc6_path" ]]; then
    info "Disable RC6 power gating"
    syswrite "$rc6_path" "0"
else
    warn "RC6 control unavailable at ${rc6_path} (kernel may manage this automatically)"
fi

# Transparent hugepages for DRM — reduces TLB pressure on large surface allocations
thp_path="/sys/kernel/mm/transparent_hugepage/enabled"
if [[ -w "$thp_path" ]]; then
    info "Set transparent hugepages: madvise"
    syswrite "$thp_path" "madvise"
fi

echo ""
print_status
echo "Done."
