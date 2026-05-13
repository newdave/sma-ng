---
name: refactoring
description: Use for behavior-preserving cleanup of SMA-NG Python, shell, config, or docs workflows.
---

# Refactoring

Improve structure without changing transcoding, daemon, or integration behavior.

## Rules

- Define the behavior that must not change.
- Add or identify characterization tests before risky edits.
- Prefer small steps: extract functions, clarify names, remove duplication, isolate side effects.
- Verify after each meaningful step.
- Do not mix refactors with unrelated feature work.

## Report

```markdown
## Invariants
- [behavior preserved]

## Changes
- [path]: [structure change]

## Validation
- [command]: [result]
```
