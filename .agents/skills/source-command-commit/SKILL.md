---
name: "source-command-commit"
description: "Generate a Conventional Commits message from staged or PR changes"
---

# source-command-commit

Use this skill when the user asks to run the migrated source command `commit`.

## Command Template

# Commit Message Generator

Analyze changes and generate a Conventional Commits message.

## Rules

- Subject only, no body or footer
- All lowercase
- Scope only from obvious code structure (folder, module, component)
- No scope if changes span multiple areas

## Output

Return only the message:

```text
feat: add google oauth support
```
