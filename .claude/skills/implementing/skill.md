---
name: implementing
description: Use when an SMA-NG implementation plan exists and code changes should begin.
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
