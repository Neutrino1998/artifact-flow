---
name: precise-artifact-edits
description: >
  Use when revising an existing artifact (a document, report, or code file the
  user is iterating on) instead of regenerating it. Guides surgical
  edits — read the current content, match exact text, replace only what changes —
  so version history stays meaningful and large artifacts are not rewritten wholesale.
license: MIT
metadata:
  version: "0.1.0"
allowed-tools:
  - read_artifact
  - update_artifact
---

# Precise artifact edits

When the user asks for a change to an artifact that already exists, **edit it in
place** — do not rewrite the whole thing from memory. Wholesale rewrites lose the
diff, churn the version history, and risk dropping content the user still wants.

## Workflow

1. **Read first.** Call `read_artifact` to see the current content (use `offset`/
   `limit` to page through large artifacts). Never edit from your recollection of
   an earlier turn — the artifact may have changed.
2. **Match exactly.** With `update_artifact`, set `old_string` to a unique snippet
   of the *current* text (copy it verbatim, including indentation) and `new_string`
   to the replacement. Keep the match span as small as the change allows.
3. **One change per edit when they are unrelated.** Several small, well-scoped
   edits read better in the version history than one sprawling replacement.
4. **Rewrite only when restructuring.** If the change is a true rewrite (new
   structure, not a local edit), say so and rewrite deliberately — otherwise prefer
   `update_artifact`.

## Avoid

- Regenerating an artifact to apply a one-line fix.
- `old_string` values so short they match in several places (the edit becomes
  ambiguous) — extend the snippet until it is unique.
- Editing without reading the latest version first.
