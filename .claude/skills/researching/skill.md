---
name: researching
description: Use when the technical approach for an SMA-NG change is uncertain or high-risk.
---

# Researching

Choose an implementation approach with evidence from the codebase.

## Use When

- Multiple approaches could work.
- The change affects FFmpeg options, config compatibility, daemon state, persistence, API behavior, or security.
- External behavior needs confirmation from primary docs.

Skip for changes that clearly match an existing local pattern.

## Workflow

1. Gather evidence from code, tests, docs, and recent git history.
2. Compare the smallest viable approaches.
3. Recommend one path and explain risks.
4. List exact files and validation commands needed next.

## Output

```markdown
## Decision
- [chosen approach]

## Evidence
- [fact] - path:line

## Risks
- [risk and mitigation]

## Next
- [files and tests]
```
