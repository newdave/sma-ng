---
name: blueprinting
description: Plan multi-step SMA-NG changes before writing code. Activate when the request touches more than one of (converter/, resources/, triggers/, .mise/tasks/, docs/), introduces a new config field, daemon option, codec, integration, or mise task, or requires same-commit doc/sample/projection updates. Skip for single-file edits and trivial fixes.
---

# Blueprinting

Turn an agreed approach into small implementation steps.

## Use When

- The change spans multiple modules, docs, tests, or config samples.
- Sequencing matters for daemon, FFmpeg, config, or integration behavior.

Skip for trivial single-file fixes.

## Blueprint

```markdown
# [Change] Blueprint

Goal:
Non-goals:
Assumptions:

## Steps
1. [path] [small action]
   Verify: [command or check]

## Final Validation
- [command]

## Docs/Config Sync
- docs:
- wiki:
- resources/docs.html:
```
