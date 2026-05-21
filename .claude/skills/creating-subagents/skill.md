---
name: creating-subagents
description: Add or update a Claude subagent under .claude/agents/. Activate when the user asks to "create/add a subagent" or when a recurring review/audit (FFmpeg args, schema drift, security, performance) deserves a focused context window separate from the main thread.
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
