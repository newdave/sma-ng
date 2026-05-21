---
name: researching
description: Choose an SMA-NG implementation approach with codebase evidence. Activate when the change involves a new FFmpeg pipeline, hwaccel device, codec, container, integration API, schema split, or daemon persistence path — anywhere multiple approaches exist and the wrong one risks transcode regressions. Skip for well-trodden patterns.
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
