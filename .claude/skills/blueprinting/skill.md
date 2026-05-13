---
name: blueprinting
description: Use for multi-step SMA-NG changes after the approach is chosen and before implementation.
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
