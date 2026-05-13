---
name: creating-skills
description: Use when adding or updating a reusable Claude skill for this repository.
---

# Creating Skills

Create skills only for recurring workflows that are worth loading into agent context.
Keep them short and specific.

## Rules

- Prefer updating `CLAUDE.md` for repository policy.
- Prefer docs under `docs/` for user/operator reference.
- A skill should include only trigger conditions, workflow, constraints, and compact output shape.
- Avoid generic programming advice that is already covered by global tooling.

## Template

```markdown
---
name: skill-name
description: Use when [specific trigger].
---

# Skill Name

## Use When

- [condition]

## Workflow

1. [step]

## Output

- [shape]
```
