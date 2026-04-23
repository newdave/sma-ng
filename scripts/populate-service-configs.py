#!/usr/bin/env python3
"""
scripts/populate-service-configs.py <local-ini> <sample-ini> [--gpu <type>]

For each service section in <local-ini> that has a config_file key:
  - Create <config_file> from <sample-ini> if it does not already exist.
  - Stamp the service's credentials (host, port, ssl, apikey, webroot) into
    the [Sonarr] or [Radarr] section of that config file.
  - Apply --gpu to the [Video] gpu key when creating a new file.

Service sections recognised: Sonarr* and Radarr* (case-insensitive prefix).
Plex credentials are not written to autoProcess.ini files.

Recycle-bin handling: if a [Converter] recycle-bin is set and the service
section is known, the per-service recycle-bin is set to <base>/<ServiceName>.
"""

import argparse
import os
import re

# Keys that map directly from a service section in .local.ini to the
# corresponding INI section inside each autoProcess file.
# The INI section for Sonarr* is always "Sonarr"; for Radarr* it is "Radarr".
SERVICE_CREDENTIAL_KEYS = ("host", "port", "ssl", "apikey", "webroot")


def parse_ini(path):
  """Return {section: {key: value}} preserving case."""
  sections = {}
  cur = None
  with open(path) as f:
    for line in f:
      s = line.strip()
      m = re.match(r"^\[(.+)\]", s)
      if m:
        cur = m.group(1)
        sections.setdefault(cur, {})
      elif cur and re.match(r"^[^#;].*=", s):
        k, _, v = s.partition("=")
        sections[cur][k.strip()] = v.strip()
  return sections


def write_ini_key(lines, section, key, value):
  """Return a new lines list with section.key overwritten to value.

  If the key is not present in the section, it is inserted immediately after
  the section header.
  """
  out = []
  cur = None
  key_found = False
  section_found = False

  for line in lines:
    m = re.match(r"^\[(.+)\]", line.strip())
    if m:
      # Entering a new section — if we were in the target section and
      # never found the key, insert it before leaving.
      if cur == section and not key_found:
        out.append(f"{key} = {value}\n")
        key_found = True
      cur = m.group(1)
      if cur == section:
        section_found = True
      out.append(line)
      continue

    if cur == section and not key_found:
      m2 = re.match(r"^(\s*)(\S[^=]*?)\s*=\s*(.*)", line)
      if m2:
        _, k, old_val = m2.groups()
        if k.strip() == key:
          if old_val.strip() != value:
            line = f"{k.strip()} = {value}\n"
          key_found = True

    out.append(line)

  # Target section was last in file and key was never seen
  if cur == section and not key_found:
    out.append(f"{key} = {value}\n")
  elif not section_found:
    if out and out[-1].strip():
      out.append("\n")
    out.append(f"[{section}]\n")
    out.append(f"{key} = {value}\n")

  return out


def stamp_credentials(ini_path, section_name, credentials, recycle_base, service_label):
  """Write credentials into ini_path's section_name and optionally Converter."""
  with open(ini_path) as f:
    lines = f.readlines()

  changed = False
  for key, value in credentials.items():
    new_lines = write_ini_key(lines, section_name, key, value)
    if new_lines != lines:
      print(f"  [{section_name}] {key} = {value!r}  ({os.path.basename(ini_path)})")
      lines = new_lines
      changed = True

  if recycle_base and service_label:
    new_val = f"{recycle_base}/{service_label}"
    new_lines = write_ini_key(lines, "Converter", "recycle-bin", new_val)
    if new_lines != lines:
      print(f"  [Converter] recycle-bin = {new_val!r}  ({os.path.basename(ini_path)})")
      lines = new_lines
      changed = True

  if changed:
    with open(ini_path, "w") as f:
      f.writelines(lines)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("local_ini", help="Path to setup/.local.ini")
  parser.add_argument("sample_ini", help="Path to setup/autoProcess.ini.sample")
  parser.add_argument("--gpu", default="", help="GPU type to stamp into new configs (nvenc, qsv, ...)")
  args = parser.parse_args()

  if not os.path.exists(args.local_ini):
    return  # nothing to do without a local.ini

  local_secs = parse_ini(args.local_ini)

  recycle_base = local_secs.get("Converter", {}).get("recycle-bin", "").strip()

  for sec_name, keys in local_secs.items():
    # Only Sonarr* and Radarr* service sections are stamped into autoProcess files.
    # Plex/Emby/Jellyfin credentials go into daemon.json / post_process/ scripts.
    is_sonarr = re.match(r"^Sonarr", sec_name, re.IGNORECASE)
    is_radarr = re.match(r"^Radarr", sec_name, re.IGNORECASE)
    if not is_sonarr and not is_radarr:
      continue

    config_file = keys.get("config_file", "").strip()
    if not config_file:
      continue

    ini_section = sec_name

    # Create the config file from sample if missing
    if not os.path.exists(config_file):
      os.makedirs(os.path.dirname(config_file) or ".", exist_ok=True)
      with open(args.sample_ini) as f:
        sample_content = f.read()
      # Patch GPU into new file if specified
      if args.gpu and args.gpu != "software":
        sample_content = re.sub(r"(?m)^gpu *=.*", f"gpu = {args.gpu}", sample_content)
      with open(config_file, "w") as f:
        f.write(sample_content)
      print(f"  created: {config_file}")

    # Collect credential keys to stamp
    credentials = {}
    for k in SERVICE_CREDENTIAL_KEYS:
      v = keys.get(k, "").strip()
      if v:
        credentials[k] = v

    if not credentials and not recycle_base:
      continue

    stamp_credentials(config_file, ini_section, credentials, recycle_base, sec_name)


if __name__ == "__main__":
  main()
