---
name: implementing
description: Execute an agreed SMA-NG implementation plan with verification. Activate immediately after blueprinting finishes, or when the user asks to implement/build/wire-up a change with clear scope (file list, behavior, validation). Pair with python-testing before reporting done.
---

# Implementing

Execute the agreed plan with verification.

## Rules

- Follow the plan unless code evidence proves it is wrong.
- Keep changes minimal and local to the affected transcoding, daemon, config, or integration path.
- Preserve existing Python style and public behavior unless the task changes it.
- Run the listed validation, or the smallest meaningful substitute.
- Stop and report if the plan would break config compatibility, media output expectations, or daemon safety.

## Report

```markdown
## Completed
- [step/path]

## Validation
- [command]: [result]

## Deviations
- [deviation or none]
```
