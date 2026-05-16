#!/usr/bin/env python3
"""Probe hardware-acceleration capability on the host and emit a typed JSON snapshot.

Consumed by ``DaemonServer`` at startup; the snapshot is surfaced on
``/health`` as ``gpu_status`` plus ``capabilities``. Operators reading the
result get a single, structured answer to "is QSV/NVENC/VAAPI actually
working on this host", without having to grep daemon logs.

Design notes
~~~~~~~~~~~~

- **Fail open.** Every subprocess invocation has ``check=False`` and a 5s
  timeout. If the probe itself errors, the snapshot still writes with
  ``gpu_status: unreachable`` (or ``unknown`` if even the probe scaffolding
  blew up) and the cause goes into ``errors[]``. Daemon startup must never
  block on this script.

- **Atomic write.** Snapshot is written to ``<output>.tmp`` and then
  ``os.replace``'d into place so a killed writer leaves the previous
  snapshot intact.

- **No third-party deps.** Imports stdlib only — this is the bare-metal
  bootstrap path before the daemon's venv pulls in anything else.

CLI
~~~

::

    probe-hw.py --output /config/cache/hw_capabilities.json \\
                --ffmpeg /usr/local/bin/ffmpeg \\
                --ffprobe /usr/local/bin/ffprobe \\
                [--image-version 2.4.0]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------

_TIMEOUT = 5


def _run(argv: list[str]) -> tuple[int, str, str]:
  """Run argv with a 5s timeout. Returns (rc, stdout, stderr); never raises."""
  try:
    proc = subprocess.run(
      argv,
      capture_output=True,
      text=True,
      timeout=_TIMEOUT,
      check=False,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""
  except FileNotFoundError as e:
    return 127, "", "binary not found: %s" % e
  except PermissionError as e:
    # Stub binaries from one OS shimmed into another (e.g. a placeholder
    # /usr/local/bin/nvidia-smi on macOS) — treat as absent.
    return 127, "", "permission denied: %s" % e
  except subprocess.TimeoutExpired:
    return 124, "", "timeout after %ds" % _TIMEOUT
  except OSError as e:
    return 1, "", "OSError: %s" % e


def probe_vainfo() -> dict:
  """Probe ``vainfo``. Returns a dict with driver, version, entrypoints, and errors[]."""
  rc, stdout, stderr = _run(["vainfo"])
  out = (stdout + "\n" + stderr).strip()
  driver = ""
  version = ""
  entrypoints: list[str] = []
  m = re.search(r"vainfo:\s*Driver version:\s*(.+)", out)
  if m:
    version = m.group(1).strip()
  # libva driver name typically appears as "Driver name: iHD" or in
  # "Trying to open /dev/dri/.../iHD_drv_video.so"
  m = re.search(r"Driver name:\s*(\S+)", out, re.IGNORECASE)
  if m:
    driver = m.group(1).strip()
  else:
    m = re.search(r"([a-zA-Z0-9_]+)_drv_video\.so", out)
    if m:
      driver = m.group(1)
  for line in out.splitlines():
    if "VAEntrypoint" in line:
      for token in line.split():
        if token.startswith("VAEntrypoint"):
          entrypoints.append(token)
  errors: list[str] = []
  if rc != 0:
    errors.append("vainfo exit=%d" % rc)
  if re.search(r"libva error|failed to initialize|VA-API not available", out, re.IGNORECASE):
    errors.append("vainfo reported libva initialization failure")
  return {
    "driver": driver,
    "version": version,
    "entrypoints": sorted(set(entrypoints)),
    "errors": errors,
  }


def probe_ffmpeg_hwaccels(ffmpeg: str) -> tuple[list[str], list[str]]:
  """Return (hwaccels, errors). ``ffmpeg -hwaccels``."""
  rc, stdout, stderr = _run([ffmpeg, "-hide_banner", "-hwaccels"])
  if rc != 0:
    return [], ["ffmpeg -hwaccels exit=%d: %s" % (rc, (stderr or stdout).strip()[:200])]
  hwaccels: list[str] = []
  for line in stdout.splitlines():
    line = line.strip()
    if not line or line.lower().startswith("hardware acceleration"):
      continue
    hwaccels.append(line)
  return sorted(set(hwaccels)), []


# Encoder/decoder name suffixes we care about. Each suffix maps a single
# backend so /health can summarise capability per backend without a
# per-codec table.
_BACKEND_SUFFIXES: tuple[tuple[str, str], ...] = (
  ("_qsv", "qsv"),
  ("_nvenc", "nvenc"),
  ("_cuvid", "nvdec"),
  ("_vaapi", "vaapi"),
  ("_videotoolbox", "videotoolbox"),
  ("_amf", "amf"),
  ("_v4l2m2m", "v4l2"),
)


def _parse_codec_table(text: str) -> dict[str, bool]:
  """Extract the third column (codec name) from `ffmpeg -encoders`/-decoders output.

  ffmpeg prints a header table then one codec per line. We only keep names
  that match a known backend suffix.
  """
  out: dict[str, bool] = {}
  for raw in text.splitlines():
    line = raw.rstrip()
    if not line or "------" in line:
      continue
    parts = line.split()
    if len(parts) < 2:
      continue
    name = parts[1]
    if not re.match(r"^[a-zA-Z0-9_]+$", name):
      continue
    if any(name.endswith(sfx) for sfx, _ in _BACKEND_SUFFIXES):
      out[name] = True
  return out


def probe_ffmpeg_encoders(ffmpeg: str) -> tuple[dict[str, bool], list[str]]:
  rc, stdout, stderr = _run([ffmpeg, "-hide_banner", "-encoders"])
  if rc != 0:
    return {}, ["ffmpeg -encoders exit=%d: %s" % (rc, (stderr or stdout).strip()[:200])]
  return _parse_codec_table(stdout), []


def probe_ffmpeg_decoders(ffmpeg: str) -> tuple[dict[str, bool], list[str]]:
  rc, stdout, stderr = _run([ffmpeg, "-hide_banner", "-decoders"])
  if rc != 0:
    return {}, ["ffmpeg -decoders exit=%d: %s" % (rc, (stderr or stdout).strip()[:200])]
  return _parse_codec_table(stdout), []


def probe_render_nodes() -> list[str]:
  """Enumerate /dev/dri/renderD* render nodes. Empty list if /dev/dri is missing."""
  nodes: list[str] = []
  drm = pathlib.Path("/dev/dri")
  if not drm.is_dir():
    return nodes
  try:
    for entry in sorted(drm.iterdir()):
      if entry.name.startswith("renderD"):
        nodes.append(str(entry))
  except OSError:
    return nodes
  return nodes


def probe_nvidia_smi() -> tuple[bool, list[str]]:
  """Return (nvidia_present, errors). ``nvidia-smi -L`` exit==0 ⇒ present."""
  rc, _stdout, stderr = _run(["nvidia-smi", "-L"])
  if rc == 127:
    return False, []  # binary absent — not an error on non-NVIDIA hosts
  if rc != 0:
    return False, ["nvidia-smi -L exit=%d: %s" % (rc, stderr.strip()[:200])]
  return True, []


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


def select_backend(capabilities: dict, hwaccels: list[str]) -> str:
  """Pick the best backend to default to. Mirrors detect-gpu.sh ordering."""
  encoders = capabilities.get("encoders", {}) or {}

  def _any_encoder(suffix: str) -> bool:
    return any(name.endswith(suffix) for name in encoders)

  if capabilities.get("nvidia") and "cuda" in hwaccels and _any_encoder("_nvenc"):
    return "nvenc"
  if "qsv" in hwaccels and _any_encoder("_qsv"):
    return "qsv"
  if "vaapi" in hwaccels and _any_encoder("_vaapi"):
    return "vaapi"
  if "videotoolbox" in hwaccels and _any_encoder("_videotoolbox"):
    return "videotoolbox"
  return "software"


def compute_host_signature(render_nodes: list[str], image_version: str) -> str:
  payload = "|".join(render_nodes) + "::" + image_version
  return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Snapshot construction
# ---------------------------------------------------------------------------


def build_snapshot(ffmpeg: str, ffprobe: str, image_version: str) -> dict:
  errors: list[str] = []

  hwaccels, hw_errs = probe_ffmpeg_hwaccels(ffmpeg)
  errors.extend(hw_errs)

  encoders, enc_errs = probe_ffmpeg_encoders(ffmpeg)
  errors.extend(enc_errs)

  decoders, dec_errs = probe_ffmpeg_decoders(ffmpeg)
  errors.extend(dec_errs)

  vainfo = (
    probe_vainfo()
    if any(h in hwaccels for h in ("vaapi", "qsv"))
    else {
      "driver": "",
      "version": "",
      "entrypoints": [],
      "errors": [],
    }
  )
  errors.extend(vainfo["errors"])

  render_nodes = probe_render_nodes()
  nvidia_present, nv_errs = probe_nvidia_smi()
  errors.extend(nv_errs)

  ffmpeg_rc, ffmpeg_stdout, _ = _run([ffmpeg, "-hide_banner", "-version"])
  ffmpeg_version = ""
  if ffmpeg_rc == 0:
    m = re.search(r"ffmpeg version (\S+)", ffmpeg_stdout)
    if m:
      ffmpeg_version = m.group(1)
  else:
    errors.append("ffmpeg -version unreachable")

  ffprobe_rc, _ffprobe_stdout, _ = _run([ffprobe, "-hide_banner", "-version"])
  if ffprobe_rc != 0:
    errors.append("ffprobe -version unreachable")

  capabilities: dict = {
    "hwaccels": hwaccels,
    "encoders": encoders,
    "decoders": decoders,
    "render_nodes": render_nodes,
    "vainfo_driver": vainfo["driver"],
    "vainfo_version": vainfo["version"],
    "ffmpeg_version": ffmpeg_version,
    "nvidia": nvidia_present,
  }

  selected = select_backend(capabilities, hwaccels)

  # Status taxonomy:
  #   ok          — ffmpeg + (vainfo OR no GPU expected) all probed cleanly.
  #   degraded    — ffmpeg works but a backend-specific probe (vainfo /
  #                 nvidia-smi) reported a soft failure.
  #   unreachable — ffmpeg itself didn't respond; nothing else is trustworthy.
  if ffmpeg_rc != 0:
    gpu_status = "unreachable"
  elif vainfo["errors"] or nv_errs:
    gpu_status = "degraded"
  else:
    gpu_status = "ok"

  return {
    "schema_version": 1,
    "probed_at": _dt.datetime.now(_dt.UTC).isoformat(),
    "host_signature": compute_host_signature(render_nodes, image_version),
    "image_version": image_version,
    "gpu_status": gpu_status,
    "selected_backend": selected,
    "capabilities": capabilities,
    "errors": errors,
  }


def write_snapshot(path: pathlib.Path, snapshot: dict) -> None:
  """Atomic write — temp file then os.replace."""
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_suffix(path.suffix + ".tmp")
  tmp.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
  os.replace(tmp, path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--output", required=True, help="path to write snapshot JSON")
  parser.add_argument("--ffmpeg", default="ffmpeg", help="path to ffmpeg binary")
  parser.add_argument("--ffprobe", default="ffprobe", help="path to ffprobe binary")
  parser.add_argument("--image-version", default="unknown", help="container/image version to embed in the snapshot")
  args = parser.parse_args(argv)

  try:
    snapshot = build_snapshot(args.ffmpeg, args.ffprobe, args.image_version)
  except Exception as e:
    snapshot = {
      "schema_version": 1,
      "probed_at": _dt.datetime.now(_dt.UTC).isoformat(),
      "host_signature": "sha256:unknown",
      "image_version": args.image_version,
      "gpu_status": "unknown",
      "selected_backend": "software",
      "capabilities": {},
      "errors": ["probe_failed: %s" % e],
    }

  try:
    write_snapshot(pathlib.Path(args.output), snapshot)
  except OSError as e:
    sys.stderr.write("probe-hw: failed to write snapshot: %s\n" % e)
    return 1
  return 0


if __name__ == "__main__":
  sys.exit(main())
