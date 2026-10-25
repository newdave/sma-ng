---
name: discovering
description: Clarify outcome before coding. Activate when the request is vague ("improve X", "make Y better"), names no files, omits acceptance criteria, or proposes a feature without saying who benefits or how to verify. Skip when scope is already concrete.
---

# Discovering

Clarify what outcome is needed before choosing an implementation path.

## Use When

- The request could mean multiple things.
- Operator impact, integration scope, or config behavior is unclear.
- The task might affect transcoding output, daemon behavior, or deployment workflow.

Skip for obvious bug fixes or clearly scoped code changes.

## Workflow

1. Inspect relevant repo context with `explorer`.
2. State the current behavior and the unclear decision.
3. Ask one focused question, or proceed with explicit assumptions if the risk is low.
4. Produce a short discovery brief:
   goal, non-goals, affected paths, success criteria, and assumptions.
