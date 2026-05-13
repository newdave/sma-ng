---
name: creating-subagents
description: Use when adding or updating a focused Claude subagent for recurring SMA-NG work.
---

# Creating Subagents

Subagents should reduce context, not add generic process.

## Rules

- Create an agent only for recurring work with a distinct role.
- Scope tools tightly: read-only agents do not get edit tools.
- Keep prompts under 80 lines.
- Focus on SMA-NG domains: FFmpeg transcoding, daemon jobs, config/schema, tests, docs, integrations.
- Avoid broad frontend, TypeScript, or product-management guidance unless this repo needs it.

## Template

```markdown
---
name: role-name
description: [role and trigger]
tools: Read, Glob, Grep, LS
color: cyan
---

# Role Name

## Focus

- [paths/domains]

## Rules

- [constraints]

## Output

- [format]
```
