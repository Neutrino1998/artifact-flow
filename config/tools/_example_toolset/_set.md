---
# ============================================================================
# EXAMPLE TOOL-SET (multi-tool unit) — the format that the singleton
# `../_example.md` does NOT cover. This directory is `_`-prefixed, so the
# loader SKIPS it; it is documentation only and is never materialized.
#
# TOOL-SET DIRECTORY CONVENTION:
#   - A DIRECTORY under config/tools/ = one unit (kind=toolset). The directory
#     name is the default unit name; the `name` field below can override it.
#   - `_set.md` MUST exist (reconcile loud-fails without it). It carries only
#     unit-level metadata — it is NOT itself a member/tool.
#   - Every other non-`_` / non-`.` `*.md` file in this dir is one MEMBER tool.
#     Each member's frontmatter is the same shape as the singleton example.
#   - A member's callable full_name is: <unit-name> + double-underscore +
#     <member-name>  (e.g. this unit's `search_users` member → the full_name
#     formed by joining the unit name and member name with two underscores).
#   - The unit name MUST NOT contain a double-underscore (that sequence is
#     reserved as the unit/member prefix separator). Member names may.
#   - Authorization (in an agent MD `tools:` block): grant the whole unit by
#     unit name, or grant a single member by its full_name.
#
# A comment must NOT contain three consecutive dashes (the frontmatter
# splitter would treat them as the closing delimiter).
# ============================================================================

# name: the unit name (overrides the directory name if they differ).
name: example_toolset

# description: unit-level description shown to the model.
description: "Example multi-tool unit (fake endpoints, never executed) — a template for the tool-set directory format"

# visibility: public | department.
visibility: public

# defer: true | false.
#   - true  = at injection time only an index line (member name) is rendered;
#             the model must call the builtin search_tools to load full params
#             before it can call a member. Good for large units.
#   - false = full member docs are injected directly into the prompt.
defer: true
---

Example multi-tool unit demonstrating the tool-set directory format. The
endpoints are fake (api.example.com) and this unit is never materialized.
