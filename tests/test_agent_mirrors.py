"""Drift tests for generated agent mirrors (.agents/skills, .codex/agents) and shared hooks."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import sync_agent_mirrors as gen  # noqa: E402

ROOT = Path(gen.ROOT)
HOOK = ROOT / ".claude" / "hooks" / "skill-reminder.sh"


def test_mirrors_match_generator():
  changed, stale = gen.drift()
  assert not changed and not stale, f"agent mirrors are stale; run `mise run dev:agents` (drift: {[str(p.relative_to(ROOT)) for p in changed]}, stale: {[str(p.relative_to(ROOT)) for p in stale]})"


def test_ffmpeg_skills_identical():
  for name in ("ffmpeg-cli", "ffmpeg-hardware-acceleration"):
    src, dst = ROOT / ".claude" / "skills" / name, ROOT / ".agents" / "skills" / name
    assert gen._tree(src) == gen._tree(dst) != {}


def test_render_agent_basic():
  out = gen.render_agent('---\nname: a\ndescription: Say "hi"\ntools: X\n---\n\n# A\n\nbody\n')
  assert out == 'name = "a"\ndescription = "Say \\"hi\\""\ndeveloper_instructions = """\n# A\n\nbody"""\n'


def test_render_agent_rejects_bad_input():
  with pytest.raises(ValueError):
    gen.render_agent("no frontmatter")
  with pytest.raises(ValueError):
    gen.render_agent('---\nname: a\ndescription: b\n---\nx """ y')


def test_check_flag_reports_clean():
  assert gen.main(["--check"]) == 0


def _run_hook(host, prompt):
  return subprocess.run([str(HOOK), host], input=json.dumps({"prompt": prompt}), capture_output=True, text=True, cwd=ROOT, env=os.environ)


def test_hook_claude_plain_text():
  out = _run_hook("claude", "hello").stdout
  assert "available_skills" in out and "FFmpeg" not in out
  assert "ffmpeg-cli" in _run_hook("claude", "tune ffmpeg").stdout


def test_hook_codex_json():
  assert "FFmpeg" not in json.loads(_run_hook("codex", "hello").stdout)["hookSpecificOutput"]["additionalContext"]
  ctx = json.loads(_run_hook("codex", "tune ffmpeg").stdout)["hookSpecificOutput"]["additionalContext"]
  assert ".agents/skills/ffmpeg-cli/SKILL.md" in ctx


def test_hook_configs_reference_shared_script():
  assert "hooks/skill-reminder.sh claude" in (ROOT / ".claude" / "settings.json").read_text()
  assert "hooks/skill-reminder.sh codex" in (ROOT / ".codex" / "hooks.json").read_text()
